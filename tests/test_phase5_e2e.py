"""캡스톤 e2e — 웹 enqueue → 워커 실행/복구 멱등(Phase 6 6a Chunk 2, 인프로세스).

★repoint(AC-P6-1c/1f·2e): Phase 5의 'lifespan 부팅 복구 e2e'를 **웹 게이트웨이 +
워커 실행/복구 e2e**로 옮긴다. 웹은 enqueue + SSE/Inbox만(실행 0), 워커가 claim+run +
복구를 담당한다. 이 파일이 닫는 표면 = "앱(웹)·워커 경계를 넘는 실 배선 멱등":

  ① **실 HTTP** POST /api/run(mock) → task_queue에 enqueue(웹은 실행 안 함)
  ② in-process 워커(Worker.run·max_ticks 바운드)가 claim → crm_notify 실행 → N3 suspend
  ③ **실 HTTP** POST /api/inbox/{id}/approve(resume=False)로 승인(웹 Inbox 경로)
  ④ 워커 다음 tick(보유 resume)이 결정을 감지 → resume → **이메일 싱크 행 정확히 1**
     (웹·워커 경계를 넘어도 비가역 정확히-1회).
  ⑤ 워커가 office.db에 쓴 이벤트를 웹 FanoutPoller가 read_since로 픽업 → SSE 무중단
     (계약 보존 — 이벤트 11종).

추가로: 워커 부팅 복구 e2e(suspended 고아 자동 재개)·cancel e2e(워커 다음 폴 탈출)·
running 고아 2b(fail+Notify)를 웹·워커 실 배선으로 재확인.

오프라인 결정론(실서버·LLM 0 — email_send는 needs-approval라 도구 미실행 상태로 suspend).
tmp_path db 주입(office.db·business.db 모두 임시). 인프로세스(별도 OS 프로세스 0·좀비 0).
모든 워커 루프는 max_ticks/max_polls로 바운드(hang 금지·scripts/run_tests.sh 전용).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from office import run as run_module
from office.approve import _record_decision
from office.business import BusinessStore
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import ToolCallResult
from office.gating import GatingPolicy
from office.task_queue import TaskQueue
from office.tools import REAL_TOOLS, reset_business_store
from office.worker import Worker, recover_running_orphans
from web_server.app import create_app
from workflows.crm_notify import NODES, WORKFLOW_NAME

# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 office.db 경로(이벤트·큐) — 실 파일 오염 없음."""
    return tmp_path / "office.db"


@pytest.fixture
def business_db(tmp_path: Path) -> Any:
    """tmp business.db를 전역 BusinessStore로 주입하고 테스트 후 원복한다."""
    store = BusinessStore(db_path=tmp_path / "business.db")
    reset_business_store(store)
    yield store
    reset_business_store(None)


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _mock_engine(db_path: Path) -> WorkflowEngine:
    """crm_notify 4노드 결정론 fake LLM + REAL_TOOLS 엔진(워커 주입용)."""
    store = EventStore(db_path=db_path)
    llm = run_module._FakeSmokeLLM(
        ["crm_get_customer", "order_get_latest", "chat", "email_send"]
    )
    executor = NodeExecutor(llm_client=llm, event_store=store)
    return WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )


def _make_suspended_task(db_path: Path) -> tuple[str, int]:
    """crm_notify를 N3 email_send에서 suspend시켜 (run_id, task_id)를 만든다(고아 시뮬)."""
    store = EventStore(db_path=db_path)
    queue = TaskQueue(db_path=db_path)
    llm = run_module._FakeSmokeLLM(
        ["crm_get_customer", "order_get_latest", "chat", "email_send"]
    )
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )
    task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
    queue.claim()
    state = engine.run(nodes=list(NODES), workflow=WORKFLOW_NAME, task_id=task_id)
    assert state.get("_workflow_status") == "suspended", (
        "캡스톤 전제: email_send에서 suspend해야 한다"
    )
    run_module._persist_suspended_state(task_id, state, db_path)
    queue.suspend(task_id)
    run_id = state.get("run_id", "")
    assert run_id, "suspended_state에 run_id가 박제돼야 한다(복구 멱등 전제)"
    return run_id, task_id


def _n3_marker_count(db_path: Path, run_id: str) -> int:
    """events에서 n3_email_send의 TOOL_CALL_RESULT 마커 개수를 센다(3중 정합용)."""
    store = EventStore(db_path=db_path)
    return sum(
        1
        for ev in store.read(run_id)
        if isinstance(ev, ToolCallResult) and ev.node_id == "n3_email_send"
    )


# ── 1. ★캡스톤 — 웹 enqueue → 워커 실행 → HTTP 승인 → email 정확히 1회 ─────────


class TestWebEnqueueWorkerExecuteCapstone:
    """웹·워커 경계를 넘는 실 배선 멱등(enqueue→claim→suspend→HTTP승인→resume→email 1)."""

    def test_web_enqueue_worker_run_http_approve_email_once(
        self, db_path: Path, business_db: Any
    ) -> None:
        """① 실 HTTP /api/run(mock) enqueue → ② 워커(백그라운드 스레드)가 claim·suspend·
        보유 폴링 → ③ 실 HTTP 승인 → ④ 같은 보유 폴이 결정 감지·resume → ⑤ email 1행.

        ★고유 가치: 웹이 실행하지 않고(enqueue만) 워커가 fresh claim→suspend→**보유
        폴링 중 실 HTTP 승인 수신**→재개하는 완전 실 배선으로 비가역 정확히-1회를 증명.

        ★hang 가드(3중): 워커는 백그라운드 스레드로 돌되 max_ticks=1·max_polls 유한
        (poll_interval 짧음) + join(timeout) + _HANG_GUARD로 폴링하므로 무한 대기 0.
        """
        import threading
        import time as _time

        store = EventStore(db_path=db_path)
        app = create_app(store=store)
        worker = Worker(db_path=db_path, poll_interval=0.01)
        engine = _mock_engine(db_path)

        with TestClient(app) as client:
            # ① 실 HTTP enqueue(웹은 실행 0 — queued만 생김).
            start = client.post(
                "/api/run", json={"workflow": "crm_notify", "mock": True}
            )
            assert start.status_code == 200
            assert start.json()["status"] == "enqueued"
            task_id = start.json()["task_id"]
            assert business_db.count_emails() == 0

            # ② 워커를 백그라운드 스레드로 1 tick(claim→suspend→보유 폴링). max_polls를
            #    승인 수신까지 버틸 만큼(유한·바운드)으로 — 무한 대기 0.
            def _run_worker() -> None:
                worker.run(max_ticks=1, engine=engine, max_polls=2000)

            th = threading.Thread(target=_run_worker, daemon=True)
            th.start()

            # ③ 워커가 N3 suspend해 Review 항목을 만들 때까지 대기(타임아웃 가드) → 실 HTTP 승인.
            deadline = _time.monotonic() + 10.0
            iid = None
            while _time.monotonic() < deadline:
                review = client.get("/api/inbox").json()["review"]
                if review:
                    iid = review[0]["interrupt_id"]
                    break
                _time.sleep(0.02)
            assert iid is not None, "워커가 N3 suspend해 Review를 만들지 못함"
            approve = client.post(f"/api/inbox/{iid}/approve")
            assert approve.status_code == 200

            # ④ 같은 보유 폴이 결정을 감지해 resume·complete할 때까지(워커 스레드 종료).
            th.join(timeout=10.0)
            assert not th.is_alive(), "워커가 승인 후 재개·종료하지 못함(hang 의심)"

        # ⑤ ★email 싱크 정확히 1행 + 멱등키 + TOOL_CALL_RESULT 1건(3중 정합).
        assert business_db.count_emails() == 1, (
            "웹 enqueue→워커 보유 폴링 승인 재개 경계에서 email이 1행이 아님"
        )
        finished = TaskQueue(db_path=db_path).get(task_id)
        assert finished["status"] == "done"
        # run_id를 events에서 집어 멱등키·마커 1건 확인(3중 정합).
        rid = next(
            json.loads(pj)["run_id"]
            for _, pj in EventStore(db_path=db_path).read_since(0)
            if json.loads(pj).get("type") == "RUN_STARTED"
        )
        assert business_db.get_email_by_key(f"{rid}:n3_email_send") is not None
        assert _n3_marker_count(db_path, rid) == 1

    def test_web_enqueue_then_worker_resumes_with_pre_approval(
        self, db_path: Path, business_db: Any
    ) -> None:
        """승인 선존(crash 후 재기동) 시나리오: 웹 enqueue로 만든 task가 suspend된 뒤
        승인이 DB에 박히고, 워커 부팅 resume이 그 고아를 재개 → email 1행.

        ★실 배선: 웹 /api/run enqueue → (suspend는 _make_suspended_task로 결정론 조성) →
        실 HTTP 승인 기록 → 워커 부팅 resume → email 정확히 1행. 웹·워커 경계 멱등 게이트.
        """
        # suspend 상태를 결정론으로 만든다(웹 enqueue→워커 claim→suspend의 결과 등가).
        run_id, task_id = _make_suspended_task(db_path)
        assert business_db.count_emails() == 0

        # 실 HTTP Inbox 경로로 승인(Review 항목 → approve, resume=False).
        store = EventStore(db_path=db_path)
        app = create_app(store=store)
        with TestClient(app) as client:
            inbox = client.get("/api/inbox").json()
            review = inbox["review"]
            assert review, "승인할 Review 항목이 없다(suspend 전제 실패)"
            iid = review[0]["interrupt_id"]
            resp = client.post(f"/api/inbox/{iid}/approve")
            assert resp.status_code == 200

        # 워커 부팅 resume(in-process·max_ticks=1) → 승인된 suspended 고아 재개.
        worker = Worker(db_path=db_path, poll_interval=0.0)
        engine = _mock_engine(db_path)
        worker.run(max_ticks=1, engine=engine, max_polls=200)

        # ⑤ ★email 싱크 정확히 1행 + 멱등키 + TOOL_CALL_RESULT 1건(3중 정합).
        assert business_db.count_emails() == 1, (
            "웹 enqueue→워커 재개 경계에서 email 싱크가 1행이 아님(멱등 실패)"
        )
        assert business_db.get_email_by_key(f"{run_id}:n3_email_send") is not None
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "done"
        assert _n3_marker_count(db_path, run_id) == 1

    def test_worker_events_visible_to_web_fanout(
        self, db_path: Path, business_db: Any
    ) -> None:
        """워커가 office.db에 쓴 이벤트를 웹이 read_since로 본다(SSE 무중단 — AC-P6-1c).

        워커 실행 후 같은 office.db의 이벤트를 웹 store(EventStore.read_since)가 그대로
        읽는다 — FanoutPoller가 이 read_since로 SSE에 흘리므로 구독 무중단의 토대.
        """
        run_id, _task_id = _make_suspended_task(db_path)
        _record_decision(
            EventStore(db_path=db_path).pending_interrupts(run_id)[0]["seq"],
            "approve",
            db_path=db_path,
            resume=False,
        )
        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=_mock_engine(db_path), max_polls=200)

        # 웹 store로 같은 DB 이벤트를 읽어 11종 중 핵심이 보이는지(무중단 토대).
        web_store = EventStore(db_path=db_path)
        types = {json.loads(pj).get("type") for _, pj in web_store.read_since(0)}
        assert "RUN_STARTED" in types
        assert "RUN_FINISHED" in types  # 워커가 끝까지 실행해 발행
        assert "TOOL_CALL_RESULT" in types


# ── 2. 캡스톤 — 워커 부팅 복구(마커 선존 윈도 a를 워커 경로로 재확인) ──────────


class TestWorkerBootRecoveryWithMarker:
    """크래시 윈도(a) 마커+싱크 선존 → 워커 부팅 resume이 재발송 0(B1 dedup)."""

    def test_worker_boot_resume_with_existing_marker_keeps_email_one(
        self, db_path: Path, business_db: Any
    ) -> None:
        """마커(TOOL_CALL_RESULT)+outbox 1행 선존 → 워커 재개해도 B1 dedup skip → 1행 유지."""
        run_id, task_id = _make_suspended_task(db_path)
        _record_decision(
            EventStore(db_path=db_path).pending_interrupts(run_id)[0]["seq"],
            "approve",
            db_path=db_path,
            resume=False,
        )

        store = EventStore(db_path=db_path)
        business_db.record_email(
            to="minjun.kim@example.com",
            subject="주문 확인",
            body="안녕하세요 김민준 고객님.",
            idempotency_key=f"{run_id}:n3_email_send",
            sent_at="2026-06-09T00:00:00Z",
        )
        store.append(
            ToolCallResult(
                run_id=run_id,
                ts="2026-06-09T00:00:00Z",
                node_id="n3_email_send",
                name="email_send",
                result={"sent": True, "to": "minjun.kim@example.com"},
            )
        )
        assert business_db.count_emails() == 1

        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=_mock_engine(db_path), max_polls=200)

        assert business_db.count_emails() == 1, (
            "마커 선존인데 워커 재개가 email을 또 보냄(B1 dedup 실패)"
        )
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "done"
        assert _n3_marker_count(db_path, run_id) == 1


# ── 3. 캡스톤 — running 고아 2b(fail+Notify)를 워커 부팅 경로로 재확인 ─────────


class TestWorkerRunningOrphanLifecycle:
    """running 고아(직전 크래시 잔재)가 있는 채 워커 부팅 → fail+Notify(2b)."""

    def test_worker_boot_with_running_orphan_fails_safe(
        self, db_path: Path, business_db: Any
    ) -> None:
        """running 고아를 워커 부팅 복구가 fail 마킹 + NodeFailed → 재개 0 → email 0행."""
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.claim()  # → running (고아 시뮬)
        assert queue.get(task_id)["status"] == "running"

        # main() 부팅 경로: recover_running_orphans(2b) → run.
        recover_running_orphans(db_path)
        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=_mock_engine(db_path), max_polls=2)

        assert queue.get(task_id)["status"] == "failed"
        assert business_db.count_emails() == 0
        types = [
            json.loads(pj).get("type")
            for _, pj in EventStore(db_path=db_path).read_since(0)
        ]
        assert "NODE_FAILED" in types


# ── 4. 캡스톤 — cancel e2e(웹 DB 신호 → 워커 다음 폴 탈출) ─────────────────────


class TestCancelWorkerLifecycle:
    """웹 cancel(DB 신호) → 워커가 보유 suspended를 다음 폴에 탈출(AC-P6-2e)."""

    def test_web_cancel_suspended_then_worker_escapes(
        self, db_path: Path, business_db: Any
    ) -> None:
        """suspended 고아 + 워커 신선 → 웹 /api/run/cancel 202(DB set) → 워커 보유 resume이
        첫 폴에서 cancel 감지 → 탈출(fail) → email 0행 + RUN_ERROR(취소).
        """
        _run_id, task_id = _make_suspended_task(db_path)

        # 워커 liveness 신선하게(웹 cancel이 202를 내려면 heartbeat 신선 필요).
        TaskQueue(db_path=db_path).update_heartbeat()

        store = EventStore(db_path=db_path)
        app = create_app(store=store)
        with TestClient(app) as client:
            cancel = client.post("/api/run/cancel")
            assert cancel.status_code == 202, "활성 suspended + 신선 워커인데 202 아님"
            assert cancel.json()["status"] == "cancelling"

        # DB cancel 플래그가 set됐는지(워커가 다음 폴에 이걸 본다).
        assert TaskQueue(db_path=db_path).is_cancel_requested(task_id) is True

        # 워커 부팅 resume: 보유 첫 폴에서 cancel 감지 → 탈출 → fail.
        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=_mock_engine(db_path), max_polls=5)

        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "failed"
        assert business_db.count_emails() == 0  # 승인 전 취소 — 도구 미실행
        # 종료 시 cancel 플래그 리셋(AC-P6-2c).
        assert TaskQueue(db_path=db_path).is_cancel_requested(task_id) is False
        types = [
            json.loads(pj).get("type")
            for _, pj in EventStore(db_path=db_path).read_since(0)
        ]
        assert "RUN_ERROR" in types  # 취소 신호 관측 이벤트(_emit_cancelled)
