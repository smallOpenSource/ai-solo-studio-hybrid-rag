"""F2 워커(office.worker) 단위/통합 테스트 — Phase 6 6a Chunk 1.

★절대 규칙(이 프로젝트 hang 전례): 모든 워커 루프·폴링 테스트는 max_ticks/max_polls로
바운드하고 오직 scripts/run_tests.sh engine으로만 실행한다(bare pytest 금지). 별도 OS
프로세스를 띄우지 않고 워커 루프 함수(Worker.run)를 in-process로 직접 호출하되 항상
max_ticks 바운드를 준다.

테스트 범위(계획 §5·프롬프트):
  ① claim+run 1건(mock LLM·max_ticks=1·in-process)
  ② advisory 락 — 보유 중 2번째 획득 시도 실패(fcntl non-blocking)
  ③ cancel — 워커 루프가 is_cancel_requested True 보면 탈출(should_cancel·max_polls 바운드)
  ④ heartbeat 갱신 확인

office만 import한다(fastapi/starlette 무관 — bench/knowledge env에서도 수집 가능).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.gating import GatingPolicy
from office.nodes import Node
from office.task_queue import TaskQueue
from office.tools import MOCK_TOOLS
from office.worker import SingleInstanceLock, Worker, recover_running_orphans

# ── Fake LLM (test_crm_notify_mock의 패턴 재사용) ─────────────────────────────


class SequenceLLM:
    """노드별 응답을 순서대로 반환하는 Fake LLM."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses
        self._idx = 0

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "messages": list(messages)})
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


def _stop(content: str, reasoning: str = "") -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "reasoning_content": reasoning,
            "tool_calls": [],
        },
        "content": content,
        "reasoning_content": reasoning,
        "finish_reason": "stop",
        "usage": {},
        "latency_ms": 5.0,
    }


def _crm_responses() -> list[dict[str, Any]]:
    """crm_notify 4노드용 표준 성공 응답."""
    return [
        _stop('{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}'),
        _stop('{"name":"order_get_latest","arguments":{"customer_id":"C-2041"}}'),
        _stop("고객 김민준님(C-2041)의 최근 주문이 배송완료 상태입니다.", "추론..."),
        _stop(
            '{"name":"email_send","arguments":'
            '{"to":"minjun.kim@example.com","subject":"주문 확인","body":"안녕하세요"}}'
        ),
    ]


def _mock_engine(store: EventStore, responses: list[dict[str, Any]]) -> WorkflowEngine:
    """mock LLM + MOCK_TOOLS + GatingPolicy 엔진(N3 needs-approval에서 suspend)."""
    llm = SequenceLLM(responses)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    return WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=MOCK_TOOLS,
        gating_policy=GatingPolicy(),
    )


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


def _event_types(db_path: str) -> list[str]:
    """events 테이블의 모든 type을 seq 순으로 반환한다(이벤트 발행 검증용)."""
    from office.db import get_connection

    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT type FROM events ORDER BY seq ASC").fetchall()
    return [r["type"] for r in rows]


def _node_failed_reasons(db_path: str) -> list[str]:
    """NODE_FAILED 이벤트의 reason 값 목록(escalation 검증용)."""
    import json

    from office.db import get_connection

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type='NODE_FAILED' ORDER BY seq ASC"
        ).fetchall()
    return [json.loads(r["payload_json"]).get("reason", "") for r in rows]


# ── 테스트용 2노드 워크플로(suspend 없이 complete) ───────────────────────────


def _two_node_nodes() -> list[Node]:
    """게이팅 없는 2개 tool 노드 — N3 승인 경로 없이 곧장 success로 끝난다."""
    from office.tools import CRM_GET_CUSTOMER_SCHEMA, ORDER_GET_LATEST_SCHEMA

    n1 = Node(
        id="n1",
        kind="tool",
        prompt="고객 조회",
        tools=[CRM_GET_CUSTOMER_SCHEMA],
        next="n2",
    )
    n2 = Node(
        id="n2",
        kind="tool",
        prompt="주문 조회",
        tools=[ORDER_GET_LATEST_SCHEMA],
        next=None,
    )
    return [n1, n2]


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "worker_test.db")


# ── ① claim+run 1건 (mock LLM·max_ticks=1·in-process) ────────────────────────


def test_worker_claims_and_runs_one_task(tmp_db: str, monkeypatch: Any) -> None:
    """워커 루프가 enqueue된 task 1건을 claim→run→complete 한다(max_ticks=1)."""
    # 2노드(suspend 없는) 워크플로를 _load_workflow가 반환하도록 패치.
    nodes = _two_node_nodes()
    monkeypatch.setattr("office.worker._load_workflow", lambda name: (nodes, name))

    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    task_id = queue.enqueue("two_node_wf", payload={})

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    # max_ticks=1: 정확히 1 tick(claim+run) 후 종료(무한 루프 0).
    processed = worker.run(max_ticks=1, engine=engine, max_polls=3)

    assert processed == 1
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "done"  # suspend 없이 success → complete


def test_worker_run_empty_queue_no_task(tmp_db: str) -> None:
    """큐가 비어 있으면 워커가 1 tick 돌고 처리 0건으로 종료한다(hang 0)."""
    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    store = EventStore(tmp_db)
    engine = _mock_engine(store, _crm_responses())
    processed = worker.run(max_ticks=2, engine=engine, max_polls=2)
    assert processed == 0


# ── ② advisory 락 (fcntl non-blocking — 2번째 획득 실패) ─────────────────────


def test_advisory_lock_second_acquire_fails(tmp_path: Path) -> None:
    """락 보유 중 2번째 SingleInstanceLock.acquire()는 즉시 False(동시성 1)."""
    lock_path = tmp_path / "worker.lock"
    first = SingleInstanceLock(lock_path)
    assert first.acquire() is True
    try:
        second = SingleInstanceLock(lock_path)
        # non-blocking: 즉시 False(블로킹 안 함 — hang 0).
        assert second.acquire() is False
        second.release()  # no-op(획득 못 했으므로)
    finally:
        first.release()


def test_advisory_lock_reacquire_after_release(tmp_path: Path) -> None:
    """락 해제 후에는 다시 획득할 수 있다(자동 해제 의미 검증)."""
    lock_path = tmp_path / "worker.lock"
    first = SingleInstanceLock(lock_path)
    assert first.acquire() is True
    first.release()

    second = SingleInstanceLock(lock_path)
    assert second.acquire() is True
    second.release()


# ── ③ cancel (워커 루프가 is_cancel_requested True 보면 탈출) ────────────────


def test_worker_cancel_via_db_signal_exits(tmp_db: str) -> None:
    """suspend된 워크플로에 cancel 플래그 set → 워커가 should_cancel 경유로 탈출.

    cancel을 미리 set한 상태로 워커를 1 tick 돌리면, run_claimed_task가 N3 suspend
    후 폴링 첫 폴에서 is_cancel_requested=True를 보고 cancelled로 탈출→fail 마킹.
    max_polls 바운드로 무한 루프 0.
    """
    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    task_id = queue.enqueue("crm_notify", payload={})
    # 워커가 claim하기 전에 cancel 요청을 미리 set(claim 후 suspend→첫 폴에서 감지).
    queue.request_cancel(task_id)

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    processed = worker.run(max_ticks=1, engine=engine, max_polls=3)

    assert processed == 1
    task = queue.get(task_id)
    assert task is not None
    # cancelled는 tasks CHECK 5상태에 없어 fail로 흡수(run_claimed_task ⑥).
    assert task["status"] == "failed"
    # ★종료 시 cancel 플래그 리셋(AC-P6-2c — 잔재 0).
    assert queue.is_cancel_requested(task_id) is False
    # RUN_ERROR(취소 신호) 이벤트가 발행됐는지 확인(_emit_cancelled).
    assert "RUN_ERROR" in _event_types(tmp_db)


def test_worker_no_cancel_suspends_normally(tmp_db: str) -> None:
    """cancel 없으면 N3에서 suspend로 남는다(max_polls 소진 — 무한 대기 0)."""
    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    task_id = queue.enqueue("crm_notify", payload={})

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    # cancel·승인 모두 없음 → max_polls 소진 후 suspended_state 그대로 → fail 흡수.
    worker.run(max_ticks=1, engine=engine, max_polls=2)

    task = queue.get(task_id)
    assert task is not None
    # 결정·취소 없이 폴 소진 → run_claimed_task가 suspended_state(success 아님) fail 마킹.
    assert task["status"] == "failed"


# ── ④ heartbeat 갱신 확인 ────────────────────────────────────────────────────


def test_worker_updates_heartbeat(tmp_db: str) -> None:
    """워커가 1 tick 돌면 heartbeat가 갱신된다(age가 None→실수)."""
    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    # 기동 전: heartbeat 행 없음 → age None.
    assert queue.heartbeat_age_seconds() is None

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    worker.run(max_ticks=1, engine=engine, max_polls=2)

    age = queue.heartbeat_age_seconds()
    assert age is not None
    assert age >= 0.0


# ── ⑤ 부팅 복구(2b만): running 고아 fail + NodeFailed Notify ─────────────────


def test_recover_running_orphan_failed_and_notified(tmp_db: str) -> None:
    """running 고아를 fail 마킹 + NodeFailed(escalation) 발행한다(2b 복제·이식)."""
    queue = TaskQueue(tmp_db)
    task_id = queue.enqueue("crm_notify", payload={})
    queue.claim()  # running으로 전환(고아 상황 모사)

    failed = recover_running_orphans(tmp_db)

    assert failed == [task_id]
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "failed"
    # NodeFailed(escalation) 이벤트 발행 확인(Inbox Notify 노출).
    reasons = _node_failed_reasons(tmp_db)
    assert reasons == ["escalation"]


def test_recover_running_orphan_none_when_no_running(tmp_db: str) -> None:
    """running 고아가 없으면 복구는 빈 목록을 반환한다(no-op)."""
    TaskQueue(tmp_db)  # 테이블만 생성
    failed = recover_running_orphans(tmp_db)
    assert failed == []


# ── ⑥ graceful shutdown 플래그 ───────────────────────────────────────────────


def test_worker_stop_flag_breaks_loop(tmp_db: str) -> None:
    """request_stop() 후 워커 루프는 다음 tick 진입 전 탈출한다(처리 0건)."""
    queue = TaskQueue(tmp_db)
    queue.enqueue("crm_notify", payload={})

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    worker.request_stop()  # 미리 stop 플래그 set
    store = EventStore(tmp_db)
    engine = _mock_engine(store, _crm_responses())
    processed = worker.run(max_ticks=5, engine=engine, max_polls=2)

    # stop 플래그가 첫 루프 진입을 막아 task를 claim하지 않는다.
    assert processed == 0


# ── ⑦ poison-pill 격리 (P6: 잘못된 enqueue·예기치 못한 오류로 워커가 죽지 않음) ──


def test_worker_unregistered_workflow_fails_fast_and_survives(tmp_db: str) -> None:
    """미등록 워크플로가 enqueue돼도 워커는 죽지 않고 그 task만 fail-fast 격리한다.

    P6①: _load_workflow(run.py)가 미등록에 ValueError를 던지는데, _run_one이 이를 잡아
    _fail_and_notify_orphan(queue.fail + NodeFailed Notify)로 처리하고 계속한다(잘못된
    enqueue 1건이 단일워커 전체를 멈추는 poison-pill 차단). _load_workflow는 패치하지
    않는다 — 실제 ValueError 경로를 검증한다.
    """
    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    task_id = queue.enqueue("no_such_workflow_xyz", payload={})

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    # 예외 전파로 죽지 않고 정상 반환해야 한다(워커 생존).
    processed = worker.run(max_ticks=1, engine=engine, max_polls=2)

    assert processed == 1  # task를 처리(격리)했다
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "failed"  # 미등록 → fail 마킹
    # NodeFailed(escalation) Notify 발행(Inbox 노출).
    assert _node_failed_reasons(tmp_db) == ["escalation"]


def test_worker_unexpected_run_error_isolated_and_survives(
    tmp_db: str, monkeypatch: Any
) -> None:
    """run_claimed_task가 예기치 못한 오류를 던져도 메인루프가 격리한다(워커 생존).

    P6②: _run_one 내부(run_claimed_task·엔진·도구)에서 임의 예외가 나도 메인루프
    BaseException 방어가 task만 fail+Notify하고 다음 tick으로 계속한다. _load_workflow는
    유효 노드를 반환하도록, run_claimed_task는 RuntimeError를 던지도록 패치한다.
    """
    nodes = _two_node_nodes()
    monkeypatch.setattr("office.worker._load_workflow", lambda name: (nodes, name))

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("예기치 못한 실행 오류(테스트)")

    monkeypatch.setattr("office.worker.run_claimed_task", _boom)

    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    task_id = queue.enqueue("two_node_wf", payload={})

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    # RuntimeError가 메인루프 밖으로 전파되면 이 호출이 raise → 테스트 실패(=회귀 감지).
    processed = worker.run(max_ticks=1, engine=engine, max_polls=2)

    assert processed == 1
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "failed"
    # 예기치 못한 오류도 NodeFailed Notify로 노출.
    assert "NODE_FAILED" in _event_types(tmp_db)


def test_worker_graceful_signal_propagates(tmp_db: str, monkeypatch: Any) -> None:
    """graceful shutdown 신호(KeyboardInterrupt/SystemExit)는 격리하지 않고 재전파한다.

    P6 단서: BaseException 방어가 KeyboardInterrupt/SystemExit까지 삼키면 Ctrl-C·종료가
    안 먹는다. 이 둘만은 반드시 메인루프 밖으로 재전파돼야 한다.
    """
    nodes = _two_node_nodes()
    monkeypatch.setattr("office.worker._load_workflow", lambda name: (nodes, name))

    def _interrupt(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("office.worker.run_claimed_task", _interrupt)

    queue = TaskQueue(tmp_db)
    store = EventStore(tmp_db)
    queue.enqueue("two_node_wf", payload={})

    worker = Worker(db_path=tmp_db, poll_interval=0.0)
    engine = _mock_engine(store, _crm_responses())
    with pytest.raises(KeyboardInterrupt):
        worker.run(max_ticks=1, engine=engine, max_polls=2)
