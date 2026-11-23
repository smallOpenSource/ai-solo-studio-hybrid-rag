"""US-006 위험기반 게이팅 단위 테스트 — GatingPolicy(프로덕션 경로) 기준.

수용 기준:
  (수용 5) 분류·when(request)→bool·needs면 INTERRUPT_REQUESTED 발행+suspended 전환
  (수용 6) 승인 주입 시 resume→완료 / 거부 시 RUN_FINISHED(rejected) 분기
  Plan Mode: interrupt를 allow로 격상(block은 불가)

엔진 결합(INTERRUPT payload 계약·resume·Plan Mode TEXT_MESSAGE)은
tests/test_gating_resume.py 가 담당한다.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from office.event_store import EventStore
from office.events import RunFinished
from office.gating import (
    WHEN_PREDICATES,
    GatingPolicy,
    ToolCallRequest,
    Verdict,
)
from office.nodes import Node

# ── 테스트용 인라인 정책 dict ────────────────────────────────────────────────


_TEST_POLICY: dict[str, Any] = {
    "tools": {
        "crm_get_customer": {"risk": "always-allow"},
        "order_get_latest": {"risk": "always-allow"},
        "email_send": {"risk": "needs-approval", "when": "always_interrupt"},
        "db_drop_table": {"risk": "block"},
    },
    "default_risk": "needs-approval",
}


# ── GatingPolicy 단위 테스트 ─────────────────────────────────────────────────


def test_policy_always_allow() -> None:
    """always-allow 도구는 Verdict.allow를 반환한다."""
    policy = GatingPolicy(_write_tmp_policy(_TEST_POLICY))
    req = ToolCallRequest(name="crm_get_customer", args={})
    assert policy.evaluate(req) == Verdict.allow

    req2 = ToolCallRequest(name="order_get_latest", args={})
    assert policy.evaluate(req2) == Verdict.allow


def test_policy_block() -> None:
    """block 도구는 Verdict.block을 반환한다."""
    policy = GatingPolicy(_write_tmp_policy(_TEST_POLICY))
    req = ToolCallRequest(name="db_drop_table", args={})
    assert policy.evaluate(req) == Verdict.block


def test_policy_needs_approval_with_always_interrupt() -> None:
    """always_interrupt 술어 → Verdict.interrupt."""
    policy = GatingPolicy(_write_tmp_policy(_TEST_POLICY))
    req = ToolCallRequest(name="email_send", args={})
    assert policy.evaluate(req) == Verdict.interrupt


def test_policy_unknown_tool_defaults_to_interrupt() -> None:
    """미등록 도구는 default_risk=needs-approval → Verdict.interrupt."""
    policy = GatingPolicy(_write_tmp_policy(_TEST_POLICY))
    req = ToolCallRequest(name="unknown_tool_xyz", args={})
    assert policy.evaluate(req) == Verdict.interrupt


def test_policy_plan_mode_upgrades_interrupt_to_allow() -> None:
    """Plan Mode(plan_mode=True) → interrupt를 allow로 격상."""
    policy = GatingPolicy(_write_tmp_policy(_TEST_POLICY))
    req = ToolCallRequest(name="email_send", args={})
    assert policy.evaluate(req, plan_mode=True) == Verdict.allow


def test_policy_block_not_overridden_by_plan_mode() -> None:
    """block은 plan_mode=True여도 Verdict.block."""
    policy = GatingPolicy(_write_tmp_policy(_TEST_POLICY))
    req = ToolCallRequest(name="db_drop_table", args={})
    assert policy.evaluate(req, plan_mode=True) == Verdict.block


def test_policy_default_path_loads_production_yaml() -> None:
    """기본 경로(config/tools_policy.yaml — 프로덕션 정책)로 판정이 일치한다.

    run.py가 GatingPolicy() 무인자 생성으로 쓰는 실제 정책 파일을 검증한다.
    (구 load_policy 기본 경로 테스트의 대체 — 커버리지 보존)
    """
    policy = GatingPolicy()  # config/tools_policy.yaml
    assert policy.evaluate(ToolCallRequest(name="crm_get_customer")) == Verdict.allow
    assert policy.evaluate(ToolCallRequest(name="email_send")) == Verdict.interrupt
    assert policy.evaluate(ToolCallRequest(name="db_drop_table")) == Verdict.block
    # 미등록 도구 — default_risk=needs-approval
    assert (
        policy.evaluate(ToolCallRequest(name="unknown_tool_xyz")) == Verdict.interrupt
    )


# ── WorkflowEngine 연동 — needs-approval → suspended / block → rejected ──────


def test_workflow_engine_suspended_on_needs_approval() -> None:
    """WorkflowEngine에 GatingPolicy 주입 시 needs-approval → _workflow_status=suspended."""
    from office.engine import NodeExecutor, WorkflowEngine

    class StubLLM:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "message": {
                    "role": "assistant",
                    "content": '{"name":"email_send","arguments":{}}',
                    "tool_calls": [],
                    "reasoning_content": "",
                },
                "content": '{"name":"email_send","arguments":{}}',
                "finish_reason": "stop",
                "usage": {},
                "latency_ms": 1.0,
            }

    node = Node(
        id="n_email",
        kind="tool",
        prompt="이메일 발송",
        tools=[{"function": {"name": "email_send"}}],
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = EventStore(db_path)
        policy_path = _write_tmp_policy(_TEST_POLICY)
        gating_policy = GatingPolicy(policy_path)
        executor = NodeExecutor(llm_client=StubLLM(), event_store=store)
        engine = WorkflowEngine(
            executor=executor, event_store=store, gating_policy=gating_policy
        )

        final_state = engine.run([node], run_id="run-sus")

        # needs-approval → suspended (사람 승인 대기)
        assert final_state.get("_workflow_status") == "suspended"
    finally:
        os.unlink(db_path)


def test_workflow_engine_rejected_on_block() -> None:
    """WorkflowEngine에 GatingPolicy 주입 시 block → _workflow_status=rejected."""
    from office.engine import NodeExecutor, WorkflowEngine

    class StubLLM:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "message": {
                    "role": "assistant",
                    "content": '{"name":"db_drop_table","arguments":{}}',
                    "tool_calls": [],
                    "reasoning_content": "",
                },
                "content": '{"name":"db_drop_table","arguments":{}}',
                "finish_reason": "stop",
                "usage": {},
                "latency_ms": 1.0,
            }

    node = Node(
        id="n_drop",
        kind="tool",
        prompt="테이블 삭제",
        tools=[{"function": {"name": "db_drop_table"}}],
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = EventStore(db_path)
        policy_path = _write_tmp_policy(_TEST_POLICY)
        gating_policy = GatingPolicy(policy_path)
        executor = NodeExecutor(llm_client=StubLLM(), event_store=store)
        engine = WorkflowEngine(
            executor=executor, event_store=store, gating_policy=gating_policy
        )

        final_state = engine.run([node], run_id="run-rej")

        assert final_state.get("_workflow_status") == "rejected"
        events = store.read("run-rej")
        run_finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "rejected" for e in run_finished)
    finally:
        os.unlink(db_path)


# ── when 술어 레지스트리 확인 ────────────────────────────────────────────────


def test_when_predicates_contains_always_interrupt() -> None:
    """WHEN_PREDICATES에 always_interrupt가 등록되어 있다."""
    assert "always_interrupt" in WHEN_PREDICATES
    req = ToolCallRequest(name="email_send", args={})
    assert WHEN_PREDICATES["always_interrupt"](req) is True


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────


def _write_tmp_policy(policy: dict[str, Any]) -> Path:
    """인라인 정책 dict를 임시 yaml 파일로 저장하고 경로를 반환한다."""
    import yaml

    tmp = tempfile.NamedTemporaryFile(
        suffix=".yaml", delete=False, mode="w", encoding="utf-8"
    )
    yaml.dump(policy, tmp, allow_unicode=True)
    tmp.close()
    return Path(tmp.name)
