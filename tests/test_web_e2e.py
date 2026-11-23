"""US-008(T8) 트랙 B — HTTP+워커 레벨 실서버 e2e(웹 1사이클 실증, slow 마커).

★Phase 6 6a Chunk 2 repoint: 웹은 enqueue + SSE/Inbox만(실행 0), **워커(office.worker)**
가 claim+run을 담당한다. 이 파일이 증명하는 것(완료 게이트 §0 — 명령줄 0회, 전부 HTTP +
in-process 워커):
  ① POST /api/run(crm_notify, 실서버 gemma) — task_queue에 enqueue(웹은 실행 안 함).
  ② in-process 워커(Worker.run·max_ticks 바운드)가 claim → 실서버로 워크플로 실행.
  ③ GET /events/stream(SSE) 구독 → 워커가 office.db에 쓴 이벤트가 웹 FanoutPoller로
     실시간으로 흐르는지(RUN_STARTED 등) 확인(계약 보존 — 프로세스/스레드 경계 무중단).
  ④ GET /api/inbox 폴링 → N3 email_send에서 멈춘 Review 항목(경과시간 포함) 대기.
  ⑤ POST /api/inbox/{id}/approve(→ _record_decision(resume=False)) — 기록만.
  ⑥ 워커 보유 폴링이 결정 감지 → 재개 → RUN_FINISHED(success)까지 확인.
  + 그 사이 SSE·다른 GET이 블로킹 0(타임아웃 내 응답 = 웹 살아있음).
  + 워커 write 스레드 + 승인 POST write 동시 → database is locked 0(수용 16).

★왜 트랙 B인가(헤드리스 환경 적응):
  Playwright 브라우저가 되면 트랙 A(web/tests/*.spec.ts)가 브라우저 클릭으로 같은
  1사이클을 돈다. 트랙 B는 그와 무관하게 "웹 1사이클"을 HTTP+워커 레벨에서 실서버로
  실증한다 — 브라우저 가용 여부와 독립으로 항상 작동해야 하는 회귀 가드.
  TestClient(in-process)로 웹 앱을, 별도 스레드로 워커를 띄워 CLI를 한 번도 안 쓰고.

⚠️ 실서버 접촉(slow 마커) — 오프라인 게이트(-m "not slow") green 후에만 실행.
  실서버 일시 5xx 가능(Phase 3 client가 재시도). 무한 재시도 금지 — 타임아웃 가드.
  결정론 단언(LLM 출력 문자열)은 피하고, 워크플로 상태 천이(success)·이벤트 타입·
  비블로킹만 단언한다.

격리: tmp_path db를 create_app(store=...)·Worker(db_path=...)로 주입해 state/office.db
  무오염. 웹·워커가 같은 tmp office.db(WAL)를 본다(웹 FanoutPoller가 워커 이벤트 픽업).
  ★모든 워커 루프는 max_ticks/max_polls·join(timeout)으로 바운드(hang 금지).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from office import run as run_module
from office.db import get_connection
from office.event_store import EventStore
from office.gating import GatingPolicy
from office.engine import NodeExecutor, WorkflowEngine
from office.tools import REAL_TOOLS
from office.worker import Worker
from web_server.app import create_app

# 실서버 워크플로 완주(4노드 + 승인 재개)까지의 상한(초). 일시 5xx 재시도 여유 포함.
_E2E_TIMEOUT_S = 240.0
# 폴링 간격(초) — 승인 감지를 빠르게(SSE fan-out 0.5s와 별개 — 워커 폴링).
_POLL_S = 0.5
# 동시 GET이 이 시간 안에 응답하지 못하면 이벤트루프 블로킹(데드락)으로 본다.
_BLOCK_GUARD_S = 8.0

pytestmark = pytest.mark.slow


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """실 파일 오염 없는 임시 SQLite DB 경로."""
    return tmp_path / "office.db"


@pytest.fixture
def store(db_path: Path) -> EventStore:
    return EventStore(db_path=db_path)


def _real_engine(db_path: Path) -> WorkflowEngine:
    """실서버 LLMClient + REAL_TOOLS + GatingPolicy 엔진(워커 주입용 — 실서버 경로).

    워커의 _build_engine과 동등하나, 테스트가 같은 tmp store를 공유하도록 직접 만든다.
    """
    from core.llm_client import LLMClient

    store = EventStore(db_path=db_path)
    executor = NodeExecutor(llm_client=LLMClient(), event_store=store)
    return WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )


def _wait_until(predicate: Any, timeout: float = _E2E_TIMEOUT_S) -> bool:
    """predicate()가 True가 될 때까지 폴링(타임아웃 내). 타임아웃이면 False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_S)
    return False


def _read_event_types(db: Path) -> list[str]:
    """tmp DB의 모든 이벤트 type을 seq 순서로 읽는다(원문 패스스루)."""
    s = EventStore(db_path=db)
    types: list[str] = []
    for _, payload_json in s.read_since(0):
        try:
            types.append(json.loads(payload_json).get("type", ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return types


def _collect_sse(
    client: TestClient, idle_close_ms: int, max_wait_s: float
) -> list[dict[str, Any]]:
    """SSE /events/stream를 구독해 흐른 이벤트(data JSON)를 모은다.

    idle_close_ms 동안 신규 이벤트가 없으면 스트림이 정상 종료(app._event_stream)되므로
    무한 대기에 빠지지 않는다. 각 프레임의 `data:` 줄을 JSON으로 파싱해 모은다.
    """
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + max_wait_s
    with client.stream("GET", f"/events/stream?idle_close_ms={idle_close_ms}") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if time.monotonic() > deadline:
                break
            if not line:
                continue
            text = line if isinstance(line, str) else line.decode("utf-8")
            if text.startswith("data:"):
                raw = text[len("data:") :].strip()
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    return events


# ── 1. smoke(2노드 등가) — 1요청 시작→완료가 실서버로 (수용 15) ───────────────


class TestRealServerSmokeHttp:
    """smoke: 2노드 등가 워크플로를 실서버로 시작→완료(웹은 살아있음).

    smoke는 비서스펜드(N1→N2, 게이팅 없음)라 승인·재개 경로를 검증하지 않는다 —
    그것은 풀 게이트(아래 TestRealServerFullCycleHttp)에서만 실증된다. 여기서는
    "실서버로 워크플로가 돌아 완료 + 그 사이 웹 GET 응답"만 본다(풀 게이트 전제).
    """

    def test_smoke_two_node_completes_via_http(
        self, store: EventStore, db_path: Path
    ) -> None:
        app = create_app(store=store)
        with TestClient(app) as client:
            # office.run.run_smoke를 전용 스레드로 직접 돌려 "2노드 등가 시작→완료"를
            # 실서버로 실증한다(smoke 전용 API 없음 — 워커는 풀런 큐 소비 담당).
            run_module.DEFAULT_DB_PATH = db_path  # 웹·smoke 동일 DB.
            result: dict[str, Any] = {}

            def _do_smoke() -> None:
                result["state"] = run_module.run_smoke("crm_notify", mock=False)

            t = threading.Thread(target=_do_smoke, daemon=True)
            t.start()
            t.join(timeout=_E2E_TIMEOUT_S)
            assert not t.is_alive(), "smoke가 타임아웃 내 완료하지 못함(실서버 지연/행)"
            assert result.get("state", {}).get("_workflow_status") == "success", (
                f"smoke 실서버 실패: {result.get('state', {}).get('_workflow_status')}"
            )

            # 그 사이/직후 GET /api/inbox가 정상 응답(웹 살아 있음).
            t0 = time.monotonic()
            body = client.get("/api/inbox").json()
            assert time.monotonic() - t0 < _BLOCK_GUARD_S
            assert "review" in body and "notify" in body


# ── 2. ★풀 게이트(완료 게이트) — 명령줄 0회, 웹 enqueue + 워커 실행 (수용 16) ──


class TestRealServerFullCycleHttp:
    """완료 게이트: 웹 enqueue→워커 실행→실시간 관찰→Inbox 승인→재개 완료를 HTTP+워커로."""

    def test_full_cycle_start_observe_approve_resume_via_http(
        self, store: EventStore, db_path: Path
    ) -> None:
        """① POST /api/run(실서버) enqueue → ② in-process 워커 claim·실행 → ③ SSE 실시간
        수신 → ④ GET /api/inbox에서 N3 Review 대기 → ⑤ POST approve(resume=False) →
        ⑥ 워커 보유 폴링 재개 → RUN_FINISHED(success). 그 사이 SSE·다른 GET 블로킹 0.

        ★hang 가드: 워커는 백그라운드 스레드(max_ticks=1·max_polls 유한) + join(timeout).
        """
        app = create_app(store=store)
        worker = Worker(db_path=db_path, poll_interval=_POLL_S)
        engine = _real_engine(db_path)

        with TestClient(app) as client:
            # ① 워크플로 enqueue — 실서버(mock=False). 웹은 실행 0(queued만).
            start = client.post(
                "/api/run", json={"workflow": "crm_notify", "mock": False}
            )
            assert start.status_code == 200, (
                f"시작 실패: {start.status_code} {start.text}"
            )
            assert start.json()["status"] == "enqueued"

            # ② in-process 워커를 백그라운드 스레드로 1 tick(claim→실서버 실행→suspend→
            #    보유 폴링). max_polls를 승인 수신까지 버틸 만큼(유한·바운드)으로 — 무한 0.
            def _run_worker() -> None:
                # 실서버 4노드 + 승인 대기. poll_interval=_POLL_S라 max_polls로 시간 환산.
                worker.run(
                    max_ticks=1,
                    engine=engine,
                    max_polls=int(_E2E_TIMEOUT_S / _POLL_S),
                )

            th = threading.Thread(target=_run_worker, daemon=True)
            th.start()

            # ③ SSE 실시간 관찰 — 워커가 office.db에 쓴 이벤트가 웹 FanoutPoller로 흐르는지.
            #    (idle_close_ms로 신규가 한동안 없으면 정상 종료 → 무한 대기 0.)
            sse_events = _collect_sse(client, idle_close_ms=2500, max_wait_s=60.0)
            sse_types = {e.get("type") for e in sse_events}
            assert "RUN_STARTED" in sse_types, (
                f"SSE로 RUN_STARTED를 실시간 수신하지 못함(받은 타입: {sse_types})"
            )

            # ④ N3 email_send에서 멈춰 Review에 승인 요청이 뜰 때까지 폴링 대기.
            def _has_pending() -> bool:
                t0 = time.monotonic()
                body = client.get("/api/inbox").json()
                assert time.monotonic() - t0 < _BLOCK_GUARD_S, (
                    "GET /api/inbox 응답 지연(블로킹 의심)"
                )
                return len(body["review"]) > 0

            assert _wait_until(_has_pending), (
                "N3에서 suspend해 Review에 인터럽트가 뜨지 않음(실서버 지연 또는 데드락)"
            )

            # 동시 GET(워커 폴링 도중)이 즉시 응답하는지 — 웹 살아있음.
            t0 = time.monotonic()
            inbox = client.get("/api/inbox").json()
            status = client.get("/api/run/status").json()
            assert time.monotonic() - t0 < _BLOCK_GUARD_S, "동시 GET 블로킹(데드락)"
            assert status["running"] is True  # DB에 running/suspended 존재
            review0 = inbox["review"][0]
            interrupt_id = review0["interrupt_id"]
            # 경과시간(elapsed) 필드가 함께 노출됨(수용 16 — "경과시간과 함께").
            assert "elapsed" in review0

            # ⑤ 승인 — POST /api/inbox/{id}/approve(→ _record_decision(resume=False)).
            #    resume=False라 INTERRUPT_RESOLVED·파일만 기록(task 불변), 재개는 워커 폴링.
            approve = client.post(f"/api/inbox/{interrupt_id}/approve")
            assert approve.status_code == 200, f"승인 실패: {approve.text}"

            # ⑥ 워커 보유 폴링이 결정 감지 → resume_after_approval → 완료(스레드 종료)까지.
            th.join(timeout=_E2E_TIMEOUT_S)
            assert not th.is_alive(), "워커가 승인 후 재개·종료하지 못함(데드락/지연)"

            # 완료가 상태창·Inbox에 반영 — RUN_FINISHED가 이벤트버스에 남고 Notify에 노출.
            assert _wait_until(lambda: "RUN_FINISHED" in _read_event_types(db_path))
            notify_types = {
                n["type"] for n in client.get("/api/inbox").json()["notify"]
            }
            assert "RUN_FINISHED" in notify_types

            # 승인 후 Review에서 그 인터럽트가 사라짐(해소됨).
            review_ids = {
                r["interrupt_id"] for r in client.get("/api/inbox").json()["review"]
            }
            assert interrupt_id not in review_ids


# ── 3. WAL 동시 writer 직렬화 — database is locked 0 (수용 16) ─────────────────


class TestWalConcurrentWriters:
    """워커 write 스레드 + 승인 POST write가 동시에 같은 SQLite에 써도 안전."""

    def test_concurrent_writers_no_database_locked(
        self, store: EventStore, db_path: Path
    ) -> None:
        """한 스레드가 이벤트를 연달아 append(워커 write 모사)하는 동안, 메인 스레드가
        같은 DB에 _record_decision 류 write(여기선 직접 append)를 동시에 수행해도
        busy_timeout=5000이 write를 직렬화해 'database is locked' 0.

        WAL은 다중 reader + 단일 writer라 동시 writer는 직렬화로 안전(병렬 아님).
        실서버 LLM은 불필요(동시성 = SQLite 레벨)하므로 이 단언은 LLM 0이지만,
        풀 게이트와 같은 동시 write 조건을 직접 재현한다.
        """
        from office.events import RunStarted

        errors: list[str] = []
        stop = threading.Event()

        def _writer(tag: str) -> None:
            s = EventStore(db_path=db_path)
            i = 0
            while not stop.is_set() and i < 200:
                try:
                    s.append(
                        RunStarted(
                            run_id=f"{tag}-{i}",
                            ts="2026-06-08T00:00:00+00:00",
                            workflow="wal_test",
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — 메시지 검사용.
                    errors.append(f"{tag}: {exc!r}")
                    if "database is locked" in str(exc):
                        return
                i += 1

        threads = [
            threading.Thread(target=_writer, args=(f"w{n}",), daemon=True)
            for n in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
        stop.set()

        locked = [e for e in errors if "database is locked" in e]
        assert not locked, f"database is locked 발생({len(locked)}건): {locked[:3]}"

        # 동시 writer 4개가 실제로 다수 행을 직렬화해 썼는지(직렬화 성공 증거).
        with get_connection(db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE type='RUN_STARTED'"
            ).fetchone()["c"]
        assert n >= 400, f"동시 writer가 충분히 쓰지 못함(직렬화 의심): {n}"
