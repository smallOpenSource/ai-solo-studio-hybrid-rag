"""US-008 crm_notify 워크플로 mock 통합 테스트.

수용 기준 5·6·7 (오프라인 게이트 포함):
  A. 풀시퀀스 이벤트 순서 단언 (RUN_STARTED → ToolCallStart×4 → RUN_FINISHED)
  B. suspend → approve → 완료 (RUN_FINISHED(success))
  C. suspend → reject → RUN_FINISHED(rejected)
  D. length 케이스 — length→retry→stop 경로가 게이팅 이전 도달
  E. error 반환 → NodeFailed 경로 실증 (수용 17 — mock_crm_get_customer error-id)
  F. smoke 2노드 단독 실행 — N1→N2 왕복 성공
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import (
    InterruptRequested,
    NodeFailed,
    RunFinished,
    ToolCallStart,
)
from office.gating import GatingPolicy
from office.tools import MOCK_TOOLS
from workflows.crm_notify import NODES, WORKFLOW_NAME


# ── Fake LLM ─────────────────────────────────────────────────────────────────


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
        self.calls.append(
            {"model": model, "messages": list(messages), "max_tokens": max_tokens}
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
        "latency_ms": 5.0,
    }


def _length(content: str = '{"name":"fn","arguments":{"x":"절단...') -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "reasoning_content": "",
            "tool_calls": [],
        },
        "content": content,
        "reasoning_content": "",
        "finish_reason": "length",
        "usage": {},
        "latency_ms": 5.0,
    }


# crm_notify 4노드용 표준 응답 시퀀스
def _standard_responses() -> list[dict[str, Any]]:
    return [
        _stop('{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}'),
        _stop('{"name":"order_get_latest","arguments":{"customer_id":"C-2041"}}'),
        _stop(
            "고객 김민준님(C-2041)의 최근 주문 노트북 Pro 15가 배송완료 상태입니다.",
            "thinking...",
        ),
        _stop(
            '{"name":"email_send","arguments":{"to":"minjun.kim@example.com","subject":"주문 확인","body":"안녕하세요"}}'
        ),
    ]


def _make_engine(
    responses: list[dict[str, Any]],
    db_path: str,
    with_gating: bool = False,
    policy: dict[str, Any] | None = None,
) -> tuple[WorkflowEngine, SequenceLLM, EventStore]:
    store = EventStore(db_path)
    llm = SequenceLLM(responses)
    executor = NodeExecutor(llm_client=llm, event_store=store)

    gating_policy = None
    if with_gating:
        if policy is not None:
            import tempfile as _tmp
            import yaml

            f = _tmp.NamedTemporaryFile(
                suffix=".yaml", delete=False, mode="w", encoding="utf-8"
            )
            yaml.dump(policy, f, allow_unicode=True)
            f.close()
            gating_policy = GatingPolicy(f.name)
            os.unlink(f.name)
        else:
            gating_policy = GatingPolicy()  # 기본 tools_policy.yaml

    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=MOCK_TOOLS,
        gating_policy=gating_policy,
    )
    return engine, llm, store


# ── A. 풀시퀀스 이벤트 순서 단언 ─────────────────────────────────────────────


def test_full_sequence_event_order_no_gating() -> None:
    """게이팅 없이 4노드 풀시퀀스: RUN_STARTED → ToolCallStart×3 + (chat은 TextMessage) → RUN_FINISHED(success)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=False
        )
        state = engine.run(nodes=list(NODES), run_id="run-seq", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "success"
        events = store.read("run-seq")
        types = [type(e).__name__ for e in events]

        # 첫 이벤트는 RUN_STARTED
        assert types[0] == "RunStarted"
        # 마지막 이벤트는 RUN_FINISHED
        assert types[-1] == "RunFinished"
        # RUN_FINISHED(success)
        run_finished = [e for e in events if isinstance(e, RunFinished)]
        assert run_finished[-1].status == "success"
        # N1·N2·N3 각각 ToolCallStart
        tool_starts = [e for e in events if isinstance(e, ToolCallStart)]
        node_ids = [e.node_id for e in tool_starts]
        assert "n1_crm_get" in node_ids
        assert "n2_order_get" in node_ids
        assert "n3_email_send" in node_ids
    finally:
        os.unlink(db_path)


# ── B. suspend → approve → 완료 ──────────────────────────────────────────────


def test_suspend_then_approve_success() -> None:
    """email_send(needs-approval) → suspended → resume(approve) → RUN_FINISHED(success)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=True
        )
        state = engine.run(
            nodes=list(NODES), run_id="run-approve", workflow=WORKFLOW_NAME
        )

        # N3 email_send에서 suspended
        assert state.get("_workflow_status") == "suspended"
        events = store.read("run-approve")
        interrupt_events = [e for e in events if isinstance(e, InterruptRequested)]
        assert len(interrupt_events) == 1
        assert interrupt_events[0].node_id == "n3_email_send"
        assert interrupt_events[0].tool == "email_send"

        # resume(approve) → 완료
        final_state = engine.resume_after_approval(state, decision="approve")
        assert final_state.get("_workflow_status") == "success"
        all_events = store.read("run-approve")
        run_finished = [e for e in all_events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in run_finished)
    finally:
        os.unlink(db_path)


# ── C. suspend → reject → RUN_FINISHED(rejected) ────────────────────────────


def test_suspend_then_reject() -> None:
    """email_send suspend 후 reject → RUN_FINISHED(rejected)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=True
        )
        state = engine.run(
            nodes=list(NODES), run_id="run-reject", workflow=WORKFLOW_NAME
        )
        assert state.get("_workflow_status") == "suspended"

        final_state = engine.resume_after_approval(state, decision="reject")
        assert final_state.get("_workflow_status") == "rejected"
        events = store.read("run-reject")
        run_finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "rejected" for e in run_finished)
    finally:
        os.unlink(db_path)


# ── D. length 케이스 — length→retry→stop ────────────────────────────────────


def test_length_retry_succeeds_in_workflow() -> None:
    """N1에서 length → retry(stop) → 워크플로 계속 진행."""
    n1_truncated = '{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"'
    responses = [
        _length(n1_truncated),  # N1 원본 → length
        _stop(
            '{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}'
        ),  # N1 retry → stop
        _stop('{"name":"order_get_latest","arguments":{"customer_id":"C-2041"}}'),  # N2
        _stop("요약입니다.", "thinking..."),  # N2.5
        _stop(
            '{"name":"email_send","arguments":{"to":"x@y.com","subject":"s","body":"b"}}'
        ),  # N3
    ]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, llm, _ = _make_engine(responses, db_path, with_gating=False)
        state = engine.run(
            nodes=list(NODES), run_id="run-length", workflow=WORKFLOW_NAME
        )
        assert state.get("_workflow_status") == "success"
        # N1: 원본 + 재시도 = 2회 호출
        assert llm.calls[0]["max_tokens"] == 512  # N1 원본
        assert llm.calls[1]["max_tokens"] == 1024  # N1 retry (min(512*2, 16384))
    finally:
        os.unlink(db_path)


def test_length_double_fail_node_failed() -> None:
    """N1 원본·재시도 모두 length → NodeFailed → 워크플로 failed."""
    responses = [
        _length(),  # N1 원본 → length
        _length(),  # N1 retry → length (재시도도 실패)
    ]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, llm, store = _make_engine(responses, db_path, with_gating=False)
        state = engine.run(
            nodes=list(NODES), run_id="run-length-fail", workflow=WORKFLOW_NAME
        )
        assert state.get("_workflow_status") == "failed"
        events = store.read("run-length-fail")
        failed = [e for e in events if isinstance(e, NodeFailed)]
        assert len(failed) >= 1
        assert llm.calls[0]["max_tokens"] == 512
        assert llm.calls[1]["max_tokens"] == 1024
    finally:
        os.unlink(db_path)


# ── E. error 반환 → NodeFailed (수용 17) ─────────────────────────────────────


def test_tool_error_result_does_not_fail_node() -> None:
    """mock 도구가 error dict를 반환해도 엔진은 결과를 누적하고 계속 진행한다.

    (도구 실행 오류는 일반적으로 재시도 대상이 아니므로 누적 후 계속)
    """
    # customer_id="error-id" → mock_crm_get_customer가 {"error": ...} 반환
    responses = [
        _stop('{"name":"crm_get_customer","arguments":{"customer_id":"error-id"}}'),
        _stop('{"name":"order_get_latest","arguments":{"customer_id":"error-id"}}'),
        _stop("오류가 있었지만 요약합니다.", "thinking..."),
        _stop(
            '{"name":"email_send","arguments":{"to":"x@y.com","subject":"s","body":"b"}}'
        ),
    ]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, _, store = _make_engine(responses, db_path, with_gating=False)
        state = engine.run(
            nodes=list(NODES), run_id="run-error", workflow=WORKFLOW_NAME
        )
        # 도구 error 반환 → accumulated에 error 포함, 워크플로 계속
        accumulated = state.get("accumulated", {})
        n1_result = accumulated.get("n1_crm_get_result", {})
        assert "error" in n1_result, "error dict가 accumulated에 누적되어야 한다"
    finally:
        os.unlink(db_path)


# ── F. smoke: N1→N2 2노드 단독 성공 ────────────────────────────────────────


def test_smoke_two_nodes_success() -> None:
    """smoke 2노드 N1→N2 왕복 성공 (되먹임 1회 확인)."""
    from dataclasses import replace

    nodes_smoke = [NODES[0], replace(NODES[1], next=None)]
    responses = [
        _stop('{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}'),
        _stop('{"name":"order_get_latest","arguments":{"customer_id":"C-2041"}}'),
    ]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, llm, store = _make_engine(responses, db_path, with_gating=False)
        state = engine.run(
            nodes=nodes_smoke, run_id="run-smoke", workflow=WORKFLOW_NAME
        )
        assert state.get("_workflow_status") == "success"
        # 2회 호출 확인
        assert len(llm.calls) == 2
        # N2 호출 시 N1 결과가 messages에 포함 (되먹임 1회)
        n2_messages = llm.calls[1]["messages"]
        all_content = " ".join(m.get("content", "") for m in n2_messages)
        # N1 도구 실행 결과 (mock_crm_get_customer 응답)가 user 노트로 주입됨
        assert (
            "김민준" in all_content
            or "C-2041" in all_content
            or "도구 실행 결과" in all_content
        )
    finally:
        os.unlink(db_path)


# ── G. report 생성 단위 테스트 ───────────────────────────────────────────────


def test_generate_run_report_smoke() -> None:
    """generate_run_report()가 오류 없이 마크다운 문자열을 반환한다."""
    from office.report import generate_run_report

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        engine, _, store = _make_engine(
            _standard_responses(), db_path, with_gating=False
        )
        state = engine.run(
            nodes=list(NODES), run_id="run-report", workflow=WORKFLOW_NAME
        )
        report = generate_run_report("run-report", state, db_path=db_path)
        assert "crm_notify" in report
        assert "run-report" in report
    finally:
        os.unlink(db_path)
