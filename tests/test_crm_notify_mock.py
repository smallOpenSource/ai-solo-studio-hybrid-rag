"""US-008 crm_notify 워크플로 mock 통합 테스트 (오프라인 게이트).

산출물 4의 요구를 덮는다:
  A. 이벤트 발행 순서 (RUN_STARTED → ... → INTERRUPT_REQUESTED → suspended)
  B. approve 주입 → resume_after_approval → RUN_FINISHED(success)
  C. reject → RUN_FINISHED(rejected)
  D. error 도구 → NODE_FAILED 경로 (수용 17)
  E. N2.5(chat ON)가 N1·N2 결과를 캐리포워드로 받는지 단언
  F. run.py 폴링 결합을 함수 단위로 (전체 CLI는 실서버 영역):
     enqueue → suspend → 결정 주입 → wait_for_decision_and_resume → resume

모두 FakeLLM + mock 도구로 결정론, 실서버 접촉 없음.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from office import run as run_module
from office.approve import _all_pending_interrupts, _write_approval_file
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import (
    FailReason,
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunFinished,
    RunStarted,
    StateDelta,
    TextMessage,
    ToolCallResult,
    ToolCallStart,
)
from office.gating import GatingPolicy
from office.task_queue import TaskQueue
from office.tools import MOCK_TOOLS
from workflows.crm_notify import NODES, WORKFLOW_NAME


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class SequenceLLM:
    """노드별 응답을 순서대로 반환하는 Fake LLM. messages를 호출 기록에 남긴다."""

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
        self.calls.append(
            {
                "model": model,
                "messages": list(messages),
                "max_tokens": max_tokens,
                "extra_body": extra_body,
            }
        )
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
        "latency_ms": 7.0,
    }


def _standard_responses() -> list[dict[str, Any]]:
    """crm_notify 4노드용 표준 성공 응답 (N2.5만 reasoning_content 있음)."""
    return [
        _stop('{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}'),
        _stop('{"name":"order_get_latest","arguments":{"customer_id":"C-2041"}}'),
        _stop(
            "고객 김민준님(C-2041)의 최근 주문 노트북 Pro 15가 배송완료 상태입니다.",
            "요약을 위한 추론 내용...",
        ),
        _stop(
            '{"name":"email_send","arguments":'
            '{"to":"minjun.kim@example.com","subject":"주문 확인","body":"안녕하세요"}}'
        ),
    ]


def _make_engine(
    responses: list[dict[str, Any]],
    db_path: str,
    with_gating: bool = False,
) -> tuple[WorkflowEngine, SequenceLLM, EventStore]:
    store = EventStore(db_path)
    llm = SequenceLLM(responses)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    gating_policy = GatingPolicy() if with_gating else None
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=MOCK_TOOLS,
        gating_policy=gating_policy,
    )
    return engine, llm, store


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


# ── A. 이벤트 발행 순서 (suspend까지) ────────────────────────────────────────


def test_event_sequence_until_suspend() -> None:
    """RUN_STARTED → (N1/N2 ToolCallStart·Result) → (N2.5 TextMessage)
    → (N3 ToolCallStart) → INTERRUPT_REQUESTED → suspended."""
    db_path = _tmp_db()
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=True
        )
        state = engine.run(nodes=list(NODES), run_id="seq", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "suspended"
        events = store.read("seq")
        types = [type(e).__name__ for e in events]

        # 첫 이벤트는 RUN_STARTED
        assert types[0] == "RunStarted"
        # 마지막은 INTERRUPT_REQUESTED (suspend 직전 발행)
        assert types[-1] == "InterruptRequested"
        # 아직 RUN_FINISHED 없음 (suspend 상태)
        assert not any(isinstance(e, RunFinished) for e in events)

        # N1·N2는 도구 실행까지 완료(ToolCallResult), N3는 게이팅에서 멈춤(Result 없음)
        results = [e for e in events if isinstance(e, ToolCallResult)]
        result_nodes = {e.node_id for e in results}
        assert "n1_crm_get" in result_nodes
        assert "n2_order_get" in result_nodes
        assert "n3_email_send" not in result_nodes

        # N2.5(chat)는 TextMessage로 발행
        texts = [e for e in events if isinstance(e, TextMessage)]
        assert any(t.content for t in texts)

        # INTERRUPT_REQUESTED는 N3, payload에 task_id 키 존재(계약)
        irq = [e for e in events if isinstance(e, InterruptRequested)]
        assert len(irq) == 1
        assert irq[0].node_id == "n3_email_send"
        assert irq[0].tool == "email_send"
        assert "task_id" in irq[0].payload
        assert "args" in irq[0].payload

        # 첫 이벤트 RunStarted 타입 확인
        assert isinstance(events[0], RunStarted)
    finally:
        os.unlink(db_path)


# ── B. approve 주입 → resume → success ───────────────────────────────────────


def test_approve_injection_then_resume_success() -> None:
    """suspend → approve._write_approval_file 격 결정 기록 → resume → success.

    여기서는 엔진 직접 경로(approve→resume_after_approval)를 단언한다.
    """
    db_path = _tmp_db()
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=True
        )
        state = engine.run(nodes=list(NODES), run_id="appr", workflow=WORKFLOW_NAME)
        assert state.get("_workflow_status") == "suspended"

        final = engine.resume_after_approval(state, decision="approve")
        assert final.get("_workflow_status") == "success"

        events = store.read("appr")
        # INTERRUPT_RESOLVED(approve) 발행
        resolved = [e for e in events if isinstance(e, InterruptResolved)]
        assert any(e.decision == "approve" for e in resolved)
        # RUN_FINISHED(success)
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)
        # N3 email_send 도구가 resume에서 실제 실행됨(ToolCallResult)
        n3_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n3_email_send"
        ]
        assert len(n3_results) == 1
    finally:
        os.unlink(db_path)


# ── C. reject → rejected ─────────────────────────────────────────────────────


def test_reject_then_rejected() -> None:
    """suspend → reject → RUN_FINISHED(rejected), 도구 미실행."""
    db_path = _tmp_db()
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=True
        )
        state = engine.run(nodes=list(NODES), run_id="rej", workflow=WORKFLOW_NAME)
        assert state.get("_workflow_status") == "suspended"

        final = engine.resume_after_approval(state, decision="reject")
        assert final.get("_workflow_status") == "rejected"

        events = store.read("rej")
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "rejected" for e in finished)
        # reject 시 email_send 도구는 실행되지 않음
        n3_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n3_email_send"
        ]
        assert len(n3_results) == 0
    finally:
        os.unlink(db_path)


# ── D. error 도구 → NODE_FAILED (수용 17) ────────────────────────────────────


def test_error_tool_emits_node_failed() -> None:
    """error 반환 도구로 노드 실패→NODE_FAILED(escalation) 경로를 실증한다.

    error 시나리오: error 전용 도구를 N1에 주입해 도구 실행 결과가 error면
    엔진이 NODE_FAILED(escalation)를 발행하고 워크플로를 failed로 종료한다.
    """
    db_path = _tmp_db()
    try:
        store = EventStore(db_path)
        # N1이 error를 유발하는 customer_id="error-id"를 생성하도록 응답 구성
        # → mock_crm_get_customer가 {"error": ...}를 반환
        responses = [
            _stop('{"name":"crm_get_customer","arguments":{"customer_id":"error-id"}}'),
        ]
        llm = SequenceLLM(responses)
        executor = NodeExecutor(llm_client=llm, event_store=store)

        # error_fails_node=True: 도구 결과 error → NODE_FAILED(escalation) (수용 17)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools=MOCK_TOOLS,
            error_fails_node=True,
        )
        state = engine.run(nodes=list(NODES), run_id="err", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "failed"
        events = store.read("err")
        failed = [e for e in events if isinstance(e, NodeFailed)]
        assert len(failed) >= 1
        assert failed[0].node_id == "n1_crm_get"
        assert failed[0].reason == FailReason.escalation
        # 워크플로가 더 진행되지 않음(N2 ToolCallStart 없음)
        starts = [e for e in events if isinstance(e, ToolCallStart)]
        start_nodes = {e.node_id for e in starts}
        assert "n2_order_get" not in start_nodes
    finally:
        os.unlink(db_path)


# ── E. N2.5(chat ON)가 N1·N2 결과를 캐리포워드로 받는지 ──────────────────────


def test_n2_5_chat_receives_carryforward() -> None:
    """N2.5(chat ON) 호출 시 messages에 N1·N2 도구 결과가 캐리포워드로 들어온다.

    - N1 crm_get_customer 결과(김민준/C-2041 등)
    - N2 order_get_latest 결과(노트북 Pro 15/배송완료 등)
    둘 다 N2.5 프롬프트 컨텍스트(messages)에 존재해야 한다.
    또한 N2.5는 thinking=ON이라 extra_body가 None(=enable_thinking 억제 안 함).
    """
    db_path = _tmp_db()
    try:
        engine, llm, store = _make_engine(
            _standard_responses(), db_path, with_gating=False
        )
        engine.run(nodes=list(NODES), run_id="cf", workflow=WORKFLOW_NAME)

        # 호출 순서: N1, N2, N2.5(chat), N3 → 인덱스 2가 N2.5
        n2_5_call = llm.calls[2]
        all_content = " ".join(m.get("content", "") for m in n2_5_call["messages"])
        # N1 도구 결과가 캐리포워드로 주입됨
        assert "김민준" in all_content or "C-2041" in all_content, (
            "N1 crm 결과가 N2.5 컨텍스트에 없음"
        )
        # N2 도구 결과가 캐리포워드로 주입됨
        assert "노트북 Pro 15" in all_content or "배송완료" in all_content, (
            "N2 order 결과가 N2.5 컨텍스트에 없음"
        )
        # chat ON 노드는 thinking 억제 extra_body가 없어야 한다
        assert n2_5_call["extra_body"] is None

        # 반대로 tool 노드(N1)는 OFF라 enable_thinking=False extra_body가 실린다
        n1_call = llm.calls[0]
        assert n1_call["extra_body"] == {
            "chat_template_kwargs": {"enable_thinking": False}
        }
    finally:
        os.unlink(db_path)


def test_n2_5_carryforward_in_state_delta_events() -> None:
    """N1·N2 도구 결과가 STATE_DELTA 이벤트로도 노출된다(캐리포워드 단일 진실)."""
    db_path = _tmp_db()
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=False
        )
        engine.run(nodes=list(NODES), run_id="cfd", workflow=WORKFLOW_NAME)
        events = store.read("cfd")
        deltas = [e for e in events if isinstance(e, StateDelta)]
        # N1·N2 결과 키가 어느 STATE_DELTA에든 등장
        all_keys: set[str] = set()
        for d in deltas:
            all_keys.update(d.accumulated.keys())
        assert "n1_crm_get_result" in all_keys
        assert "n2_order_get_result" in all_keys
    finally:
        os.unlink(db_path)


# ── F. run.py 폴링 결합 (함수 단위) ──────────────────────────────────────────


def _suspend_via_orchestration(db_path: str) -> tuple[Any, Any, dict[str, Any], int]:
    """run_full 오케스트레이션의 enqueue→claim→run→suspend 부분을 모사한다.

    Returns: (engine, store, suspended_state, task_id)
    """
    store = EventStore(db_path)
    queue = TaskQueue(db_path)
    llm = SequenceLLM(_standard_responses())
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=MOCK_TOOLS,
        gating_policy=GatingPolicy(),
    )
    task_id = queue.enqueue(WORKFLOW_NAME, payload={})
    queue.claim()
    state = engine.run(
        nodes=list(NODES), run_id="orch", workflow=WORKFLOW_NAME, task_id=task_id
    )
    assert state.get("_workflow_status") == "suspended"
    run_module._persist_suspended_state(task_id, state, db_path)
    queue.suspend(task_id)
    loaded = run_module._load_suspended_state(task_id, db_path)
    assert loaded is not None
    return engine, store, loaded, task_id


def test_polling_resume_via_cli_decision_db() -> None:
    """폴링 함수가 CLI(DB INTERRUPT_RESOLVED) 결정을 감지해 resume→success."""
    db_path = _tmp_db()
    approvals_dir = tempfile.mkdtemp()
    try:
        engine, store, loaded, task_id = _suspend_via_orchestration(db_path)

        # CLI가 결정을 DB에 기록한 상황을 모사 (_record_decision 경유)
        from office.approve import _record_decision

        iid = _all_pending_interrupts(db_path=db_path)[0]["interrupt_id"]
        _record_decision(iid, "approve", db_path=db_path)

        final = run_module.wait_for_decision_and_resume(
            engine,
            loaded,
            task_id,
            store,
            db_path,
            approvals_dir=approvals_dir,
            poll_interval=0.0,
            max_polls=3,
        )
        assert final.get("_workflow_status") == "success"
    finally:
        os.unlink(db_path)


def test_polling_resume_via_file_fallback() -> None:
    """폴링 함수가 파일 폴백(approvals/<id>.json) 결정을 감지해 resume→success."""
    db_path = _tmp_db()
    approvals_dir = tempfile.mkdtemp()
    try:
        engine, store, loaded, task_id = _suspend_via_orchestration(db_path)

        iid = _all_pending_interrupts(db_path=db_path)[0]["interrupt_id"]
        # 파일만 떨굼 (경로 B)
        _write_approval_file(
            iid, "approve", approvals_dir=approvals_dir, db_path=db_path
        )

        final = run_module.wait_for_decision_and_resume(
            engine,
            loaded,
            task_id,
            store,
            db_path,
            approvals_dir=approvals_dir,
            poll_interval=0.0,
            max_polls=3,
        )
        assert final.get("_workflow_status") == "success"
    finally:
        os.unlink(db_path)


def test_polling_reject_via_file_fallback() -> None:
    """폴링 함수가 reject 결정을 감지해 RUN_FINISHED(rejected)."""
    db_path = _tmp_db()
    approvals_dir = tempfile.mkdtemp()
    try:
        engine, store, loaded, task_id = _suspend_via_orchestration(db_path)

        iid = _all_pending_interrupts(db_path=db_path)[0]["interrupt_id"]
        _write_approval_file(
            iid, "reject", approvals_dir=approvals_dir, db_path=db_path
        )

        final = run_module.wait_for_decision_and_resume(
            engine,
            loaded,
            task_id,
            store,
            db_path,
            approvals_dir=approvals_dir,
            poll_interval=0.0,
            max_polls=3,
        )
        assert final.get("_workflow_status") == "rejected"
    finally:
        os.unlink(db_path)


def test_polling_no_decision_stays_suspended() -> None:
    """결정이 없으면 max_polls 소진 후 suspended_state를 그대로 반환(무한루프 방지)."""
    db_path = _tmp_db()
    approvals_dir = tempfile.mkdtemp()
    try:
        engine, store, loaded, task_id = _suspend_via_orchestration(db_path)
        final = run_module.wait_for_decision_and_resume(
            engine,
            loaded,
            task_id,
            store,
            db_path,
            approvals_dir=approvals_dir,
            poll_interval=0.0,
            max_polls=2,
        )
        assert final.get("_workflow_status") == "suspended"
    finally:
        os.unlink(db_path)


# ── G. 런 요약 포맷 ──────────────────────────────────────────────────────────


def test_format_run_summary_bidirectional_reasoning() -> None:
    """런 요약에 노드별 reasoning_chars가 양방향으로 찍힌다 (수용 12 오프라인 예행).

    N1·N2·N3(OFF)=0, N2.5(ON)>0.
    """
    db_path = _tmp_db()
    try:
        engine, _, _ = _make_engine(_standard_responses(), db_path, with_gating=False)
        state = engine.run(nodes=list(NODES), run_id="sum", workflow=WORKFLOW_NAME)
        metrics = state["_node_metrics"]
        assert metrics["n1_crm_get"]["reasoning_chars"] == 0
        assert metrics["n2_order_get"]["reasoning_chars"] == 0
        assert metrics["n2_5_summary"]["reasoning_chars"] > 0
        assert metrics["n3_email_send"]["reasoning_chars"] == 0

        summary = run_module.format_run_summary(state)
        assert "n2_5_summary" in summary
        assert "reasoning_chars" in summary
    finally:
        os.unlink(db_path)
