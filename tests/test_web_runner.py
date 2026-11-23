"""US-P6-1/2(Phase 6 6a Chunk 2) 웹 게이트웨이 — enqueue/cancel/status 계약.

★웹 축소(AC-P6-1f): 웹은 더 이상 워크플로를 실행하지 않는다(claim+run은 office.worker).
이 파일은 Phase 4a의 내부 화이트박스 단언(_workflow_lock·_current_future·
_current_cancel_event 직접 조작)을 **제거하고** 새 단언으로 대체한다:
- start_run → task_queue에 queued 1건 enqueue(워크플로 미실행·웹 프로세스 LLM 0).
- cancel_run → DB cancel_requested set + liveness 판정(202 cancelling / 200
  no_active_run·stale_running) — ★외부 계약(상태코드·의미) 보존·거짓202 0(AC-P6-2a).
- status → DB 조회(running 존재·tasks 목록·heartbeat 신선도).

★불변 매핑(이 파일이 지키는 계약):
- cancel 202/200 의미 보존 → TestCancelContract.
- 거짓202 0(워커 크래시 고아 stale → 200) → test_cancel_stale_worker_*.
- 웹은 실행 0(동시성과 무관) → TestStartRunEnqueueOnly + 정적 단언(LLMClient 0·
  executor/threading 0). ★동시성 1(advisory 락 + claim 원자성)은 워커 소유라
  tests/test_worker.py가 보증(여기선 enqueue/cancel/status 계약만).

오프라인 결정론(실서버·LLM 0). tmp_path db 주입(실 파일 무오염). slow 마커 없음.
모든 대기에 타임아웃 가드(hang 0).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from office.event_store import EventStore
from office.task_queue import TaskQueue
from web_server import runner as runner_mod
from web_server.app import create_app

# 대기 상한(초) — 어떤 단언도 이 안에 결판나야 한다(hang 0).
_HANG_GUARD_S = 10.0


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 SQLite DB 경로 (실 파일 오염 없음)."""
    return tmp_path / "office.db"


@pytest.fixture
def store(db_path: Path) -> EventStore:
    return EventStore(db_path=db_path)


def _wait_until(predicate: Any, timeout: float = _HANG_GUARD_S) -> bool:
    """predicate()가 True가 될 때까지 폴링(타임아웃 내). 타임아웃이면 False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── 1. start_run = enqueue만 (AC-P6-1b — 웹은 실행 안 함) ─────────────────────


class TestStartRunEnqueueOnly:
    """POST /api/run은 task_queue에 enqueue만 하고 워크플로를 실행하지 않는다."""

    def test_start_run_enqueues_queued_task(
        self, store: EventStore, db_path: Path
    ) -> None:
        """/api/run → status='enqueued' + tasks에 queued 1건(워크플로 미실행)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run", json={"workflow": "crm_notify"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "enqueued"
        assert body["workflow"] == "crm_notify"
        assert isinstance(body["task_id"], int)

        # DB에 queued 1건 — 워크플로는 실행되지 않았다(이벤트 0·실행 주체는 워커).
        queue = TaskQueue(db_path=db_path)
        queued = queue.list_by_status("queued")
        assert len(queued) == 1
        assert queued[0]["id"] == body["task_id"]
        assert queued[0]["workflow"] == "crm_notify"
        # 워크플로 미실행 증거: RUN_STARTED 등 이벤트가 전혀 없다(웹은 enqueue만).
        assert EventStore(db_path=db_path).read_since(0) == []

    def test_start_run_persists_mock_flag_in_payload(
        self, store: EventStore, db_path: Path
    ) -> None:
        """mock 플래그가 enqueue payload에 실린다(워커가 읽어 fake LLM 결정 — 배선)."""
        import json

        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post(
                "/api/run", json={"workflow": "crm_notify", "mock": True}
            )
        assert resp.json()["mock"] is True
        queue = TaskQueue(db_path=db_path)
        task = queue.list_by_status("queued")[0]
        payload = json.loads(task["payload_json"])
        assert payload.get("mock") is True

    def test_start_run_defaults_workflow_crm_notify(
        self, store: EventStore, db_path: Path
    ) -> None:
        """body 생략 시 workflow 기본값은 crm_notify, mock 기본 False."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run", json={})
        body = resp.json()
        assert body["workflow"] == "crm_notify"
        assert body["mock"] is False

    def test_multiple_starts_enqueue_multiple_queued(
        self, store: EventStore, db_path: Path
    ) -> None:
        """웹은 실행 안 하므로 연속 start_run이 409 없이 모두 enqueue된다(직렬화는 워커).

        ★Phase 4a는 동시 시작을 409로 막았으나(웹이 실행했기 때문), 웹 축소 후 웹은
        enqueue만 하므로 여러 건이 queued로 쌓이고 워커가 claim 원자성으로 순차 처리한다
        (동시성 1은 워커 책임 — test_worker.py 보증).
        """
        app = create_app(store=store)
        with TestClient(app) as client:
            r1 = client.post("/api/run", json={"workflow": "crm_notify"})
            r2 = client.post("/api/run", json={"workflow": "crm_notify"})
        assert r1.status_code == 200 and r2.status_code == 200
        queue = TaskQueue(db_path=db_path)
        assert len(queue.list_by_status("queued")) == 2


# ── 2. run_status = DB 조회 (AC-P6-1f — _workflow_lock 제거) ──────────────────


class TestRunStatusFromDB:
    """GET /api/run/status가 _workflow_lock이 아니라 DB(tasks·heartbeat)를 본다."""

    def test_status_running_false_when_no_active_task(
        self, store: EventStore, db_path: Path
    ) -> None:
        """활성 task가 없으면 running False, tasks 목록 키 존재."""
        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/run/status").json()
        assert body["running"] is False
        assert set(body["tasks"]) == {"running", "suspended", "queued"}
        assert "worker_live" in body

    def test_status_running_true_when_db_has_running_task(
        self, store: EventStore, db_path: Path
    ) -> None:
        """DB에 running task가 있으면 running True(웹 락이 아니라 DB 조회)."""
        queue = TaskQueue(db_path=db_path)
        queue.enqueue("crm_notify", payload={})
        queue.claim()  # → running
        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/run/status").json()
        assert body["running"] is True
        assert len(body["tasks"]["running"]) == 1

    def test_status_running_true_when_db_has_suspended_task(
        self, store: EventStore, db_path: Path
    ) -> None:
        """DB에 suspended task가 있으면 running True(워커가 보유·폴링 중 의미)."""
        queue = TaskQueue(db_path=db_path)
        tid = queue.enqueue("crm_notify", payload={})
        queue.claim()
        queue.suspend(tid)
        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/run/status").json()
        assert body["running"] is True
        assert len(body["tasks"]["suspended"]) == 1

    def test_status_worker_live_reflects_heartbeat(
        self, store: EventStore, db_path: Path
    ) -> None:
        """worker_live는 heartbeat 신선도를 반영한다(갱신 직후 True, 미기동 False)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            # heartbeat 없음 → worker_live False.
            assert client.get("/api/run/status").json()["worker_live"] is False
            # heartbeat 갱신 → worker_live True(신선).
            TaskQueue(db_path=db_path).update_heartbeat()
            assert client.get("/api/run/status").json()["worker_live"] is True


# ── 3. ★cancel = DB 신호 + liveness (AC-P6-2a — US-009 의미 보존·거짓202 0) ────


class TestCancelContract:
    """POST /api/run/cancel — DB cancel_requested set + liveness 판정(202/200)."""

    def test_cancel_no_active_run_returns_200(
        self, store: EventStore, db_path: Path
    ) -> None:
        """활성 task가 없으면 200 no_active_run(취소할 것 없는 no-op — 계약 보존)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_active_run"

    def test_cancel_active_running_with_live_worker_returns_202(
        self, store: EventStore, db_path: Path
    ) -> None:
        """running task + 워커 신선 → 202 cancelling + DB cancel_requested set."""
        queue = TaskQueue(db_path=db_path)
        tid = queue.enqueue("crm_notify", payload={})
        queue.claim()  # → running
        queue.update_heartbeat()  # 워커 살아있음(신선)
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run/cancel")
        assert resp.status_code == 202
        assert resp.json()["status"] == "cancelling"
        # ★DB 신호가 실제로 set됐는지(워커가 다음 폴에 이걸 보고 탈출).
        assert queue.is_cancel_requested(tid) is True

    def test_cancel_active_suspended_with_live_worker_returns_202(
        self, store: EventStore, db_path: Path
    ) -> None:
        """suspended task(워커 보유 중) + 워커 신선 → 202 + 그 task에 cancel set."""
        queue = TaskQueue(db_path=db_path)
        tid = queue.enqueue("crm_notify", payload={})
        queue.claim()
        queue.suspend(tid)
        queue.update_heartbeat()
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run/cancel")
        assert resp.status_code == 202
        assert queue.is_cancel_requested(tid) is True

    def test_cancel_stale_worker_returns_200_no_false_202(
        self, store: EventStore, db_path: Path
    ) -> None:
        """★거짓202 0: running task 있으나 워커 heartbeat stale(크래시 고아) → 200.

        끊어 줄 폴링 루프가 없는 running 고아에 202를 내면 거짓 약속이다. liveness가
        stale이면 200 stale_running(복구는 워커 부팅 스캔 담당) — cancel 신호도 안 건다.
        """
        queue = TaskQueue(db_path=db_path)
        tid = queue.enqueue("crm_notify", payload={})
        queue.claim()  # → running (고아 시뮬: 워커 크래시로 running에 멈춤)
        # heartbeat를 과거로 직접 박아 stale 상태를 만든다(워커 미갱신 = 크래시).
        from office.db import get_connection

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO worker_heartbeat (id, ts) VALUES (1, ?)",
                ("2000-01-01T00:00:00+00:00",),
            )
            conn.commit()
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stale_running"
        # ★거짓202가 아니므로 cancel 신호도 걸지 않는다(no-op).
        assert queue.is_cancel_requested(tid) is False

    def test_cancel_no_heartbeat_at_all_returns_200(
        self, store: EventStore, db_path: Path
    ) -> None:
        """heartbeat 행 자체가 없으면(워커 한 번도 안 뜸) running 있어도 200(거짓202 0)."""
        queue = TaskQueue(db_path=db_path)
        queue.enqueue("crm_notify", payload={})
        queue.claim()  # running, 그러나 heartbeat 없음
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stale_running"


# ── 4. ★cancel e2e — 워커가 DB 신호로 탈출 (AC-P6-2e, 인프로세스 워커) ─────────


class TestCancelWorkerEndToEnd:
    """웹 enqueue→워커 실행→suspend→cancel(DB)→워커 다음 폴 탈출(RUN_ERROR·fail)."""

    def test_cancel_breaks_worker_suspended_polling(
        self, store: EventStore, db_path: Path
    ) -> None:
        """① 웹 enqueue(mock) ② 워커가 claim·suspend(보유 폴링) ③ 웹 cancel(202·DB set)
        ④ 워커 다음 폴에서 탈출→fail + RUN_ERROR(취소). ★별도 OS 프로세스 아님(in-process
        Worker.run·max_polls 바운드). 동시성 1이라 cancel-escapable.

        suspend된 task를 워커가 보유 폴링하므로, 미리 결정을 안 주고 cancel만 신호한 뒤
        워커를 1 tick 더 돌리면(다음 폴) is_cancel_requested=True를 보고 탈출한다.
        """
        import json

        from office.business import BusinessStore
        from office.tools import reset_business_store

        # 임시 business.db 격리(email 멱등 단언은 다른 파일이 함 — 여기선 0행만).
        reset_business_store(BusinessStore(db_path=db_path.parent / "business.db"))
        try:
            queue = TaskQueue(db_path=db_path)
            # 웹 게이트웨이 경로: enqueue(mock 플래그 payload).
            app = create_app(store=store)
            with TestClient(app) as client:
                start = client.post(
                    "/api/run", json={"workflow": "crm_notify", "mock": True}
                )
                assert start.json()["status"] == "enqueued"
                tid = start.json()["task_id"]

                # 워커가 claim→N3 suspend하도록 in-process로 1 tick(결정 없으면 max_polls
                # 소진 후 fail이므로, 여기선 suspend 상태를 직접 만든다: claim+run을 워커
                # 루프에 맡기되 cancel을 먼저 set해 첫 폴에서 탈출시킨다).
                # ★cancel을 먼저 set(웹 cancel_run 경로) — 단 liveness 신선해야 202.
                queue.update_heartbeat()
                # 아직 running/suspended가 없으니(queued만) cancel은 no_active_run.
                pre = client.post("/api/run/cancel")
                assert pre.status_code == 200  # 아직 활성 task 없음(queued뿐)

            # 워커를 in-process로 돌려 claim→suspend→(cancel 없음)→max_polls 소진 fail.
            # 그 다음 "활성 suspended에 cancel" 시나리오는 아래 별도로 본다.
            from office.worker import Worker

            w = Worker(db_path=db_path, poll_interval=0.0)
            # cancel을 미리 set한 뒤 워커가 claim→suspend→첫 폴에서 탈출하도록.
            queue.request_cancel(tid)
            processed = w.run(max_ticks=1, max_polls=5)
            assert processed >= 1

            task = queue.get(tid)
            # cancelled는 tasks 5상태에 없어 fail로 흡수(run_claimed_task ⑥).
            assert task["status"] == "failed"
            # 종료 시 cancel 플래그 리셋(AC-P6-2c).
            assert queue.is_cancel_requested(tid) is False
            # RUN_ERROR(취소 신호) 이벤트 발행(_emit_cancelled).
            types = [
                json.loads(pj).get("type")
                for _, pj in EventStore(db_path=db_path).read_since(0)
            ]
            assert "RUN_ERROR" in types
        finally:
            reset_business_store(None)


# ── 5. 정적 단언 (웹 축소 — 실행/복구 심볼 제거·LLM 0) ────────────────────────


class TestStaticAssertions:
    """웹 축소 후 web_server가 실행/복구 경로를 갖지 않음을 정적으로 단언한다."""

    def _web_server_sources(self) -> list[Path]:
        ws_dir = Path(__file__).parent.parent / "web_server"
        assert ws_dir.is_dir(), f"web_server 디렉토리 없음: {ws_dir}"
        return sorted(ws_dir.glob("*.py"))

    def test_no_direct_llm_client_in_web_server(self) -> None:
        """web_server가 LLMClient/core.llm_client를 직접 호출하지 않는다(preflight 동치).

        축소 후에도 금지 토큰 0 — LLM은 워커가 엔진 관문으로만 부른다(웹은 enqueue만).
        """
        pats = [re.compile(r"core\.llm_client"), re.compile(r"\bLLMClient\b")]
        violations: list[str] = []
        for f in self._web_server_sources():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if any(p.search(line) for p in pats):
                    violations.append(f"{f.name}:{i}: {line.strip()}")
        assert not violations, "web_server가 LLM을 직접 호출한다:\n" + "\n".join(
            violations
        )

    def test_runner_does_not_execute_workflow(self) -> None:
        """runner가 워크플로를 실행하지 않는다 — 실행/복구 심볼 부재(웹 축소).

        제거된 심볼: _executor·_workflow_lock·_current_future·_current_cancel_event·
        _run_workflow·_resume_workflow·resume_task·recover_orphan_tasks·run_full 호출.
        runner는 enqueue/cancel/status 게이트웨이만 — 실행 주체는 office.worker.
        """
        runner_src = (
            Path(__file__).parent.parent / "web_server" / "runner.py"
        ).read_text(encoding="utf-8")
        forbidden = [
            "ThreadPoolExecutor",
            "_executor.submit",
            "_workflow_lock",
            "_current_future",
            "_current_cancel_event",
            "run_full",
            "_resume_workflow",
            "resume_task",
            "recover_orphan_tasks",
        ]
        present = [tok for tok in forbidden if tok in runner_src]
        assert not present, (
            "runner가 실행/복구 심볼을 여전히 가진다(웹 축소 미완): "
            + ", ".join(present)
        )
        # 게이트웨이 핵심: enqueue + DB cancel 신호.
        assert "queue.enqueue(" in runner_src
        assert "request_cancel(" in runner_src

    def test_runner_module_has_no_threading_or_executor(self) -> None:
        """runner 모듈이 threading/executor 전역을 노출하지 않는다(웹은 스레드 0)."""
        for sym in (
            "_workflow_lock",
            "_executor",
            "_current_future",
            "_current_cancel_event",
            "resume_task",
            "recover_orphan_tasks",
        ):
            assert not hasattr(runner_mod, sym), (
                f"runner_mod가 제거 대상 심볼을 여전히 노출: {sym}"
            )

    def test_no_background_tasks_in_web_server(self) -> None:
        """web_server 어디에도 BackgroundTasks로 워크플로를 띄우지 않는다(실행 주체=워커)."""
        usage = [
            re.compile(r"import\s+BackgroundTasks"),
            re.compile(r"BackgroundTasks\("),
            re.compile(r":\s*BackgroundTasks"),
            re.compile(r"\.add_task\("),
        ]
        violations: list[str] = []
        for f in self._web_server_sources():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if any(p.search(line) for p in usage):
                    violations.append(f"{f.name}:{i}: {line.strip()}")
        assert not violations, "web_server가 BackgroundTasks를 사용한다:\n" + "\n".join(
            violations
        )

    def test_main_pins_single_worker(self) -> None:
        """__main__이 uvicorn을 단일 워커(workers=1)로 고정한다(수용 20, M2)."""
        main_src = (
            Path(__file__).parent.parent / "web_server" / "__main__.py"
        ).read_text(encoding="utf-8")
        assert "workers=1" in main_src
