"""US-P7A-2 NIT-1(부분) — ⊆REAL_TOOLS 단언 + None skip WARN 가드 계약.

계획 ralplan-phase7-agent-production.md §3.4 7a · §8. 검증 대상:
  · AC-2a: 프로덕션 워크플로(crm_notify)의 모든 노드 도구 ∈ REAL_TOOLS(tools.py:399).
           agent_action ∉ REAL_TOOLS(MOCK 전용·tools.py:232) → agent_smoke 테스트 전용 박제.
  · AC-2b/2c: engine.py None skip 분기(`if tool_fn is None`)가 logger.warning을 1회 내되
           ★이벤트가 아님(EventStore 미경유·새 타입 0). 정상(등록 도구)에선 미발화.
           WARN 후 EVENT_REGISTRY 11·FailReason 3 무변.

★전부 오프라인 결정론(실서버·LLM 0 — ScriptLLM). bare pytest 금지 —
scripts/run_tests.sh engine 경유(conftest 30s + ulimit + OS timeout 3중 가드).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import EVENT_REGISTRY, FailReason
from office.gating import GatingPolicy
from office.nodes import Node
from office.tools import MOCK_TOOLS, REAL_TOOLS
from workflows.crm_notify import NODES as CRM_NODES

# ── 헬퍼: 노드가 선언한 도구 이름 추출 ────────────────────────────────────────


def _declared_tool_names(node: Node) -> list[str]:
    """노드 tools 스키마에서 function.name 목록을 뽑는다(없으면 빈 리스트)."""
    names: list[str] = []
    for schema in node.tools or []:
        fn = schema.get("function", {}) if isinstance(schema, dict) else {}
        name = fn.get("name")
        if name:
            names.append(name)
    return names


# ── AC-2a: crm_notify 모든 도구 ⊆ REAL_TOOLS ──────────────────────────────────


def test_crm_notify_tools_subset_of_real_tools() -> None:
    """프로덕션 crm_notify의 모든 노드 도구가 REAL_TOOLS 키에 ∈(미등록 0)."""
    for node in CRM_NODES:
        for name in _declared_tool_names(node):
            assert name in REAL_TOOLS, (
                f"crm_notify 노드 {node.id}의 도구 '{name}'가 REAL_TOOLS 미등록 — "
                "프로덕션 사일런트 무실행(구성오류) 위험"
            )


def test_agent_action_not_in_real_tools_but_in_mock() -> None:
    """agent_action은 REAL_TOOLS에 없고 MOCK_TOOLS에만 있다(테스트 전용 박제)."""
    assert "agent_action" not in REAL_TOOLS
    assert "agent_action" in MOCK_TOOLS


def test_real_tools_keys_are_production_set() -> None:
    """REAL_TOOLS 키는 프로덕션 실도구(crm/order/email/knowledge + game_data_write/read/validate)뿐(회귀 가드)."""
    assert set(REAL_TOOLS.keys()) == {
        "crm_get_customer",
        "order_get_latest",
        "email_send",
        "knowledge_search",
        "game_data_write",  # G005 US-G5-1 — game/ 로컬 파일쓰기(가역·always-allow)
        "game_data_read",  # G005 US-G5-4 — game/ 로컬 파일읽기(읽기전용·always-allow)
        "game_validate",  # G005 US-G5-6 — game/data 스키마 검증(읽기전용·always-allow)
    }


# ── AC-2b/2c: None skip WARN 가드 + 이벤트 아님 계약 ───────────────────────────


def _tool_node(tool_name: str) -> Node:
    """선언만 하고(스키마) 엔진 tools엔 없을 수 있는 tool 노드."""
    return Node(
        id="orphan_tool",
        kind="tool",
        prompt="조회",
        thinking="off",
        tools=[{"function": {"name": tool_name}}],
        next=None,
    )


def _allow_llm(tool_name: str) -> Any:
    """해당 도구를 1회 호출하는 prompt_json content를 내는 mock LLM."""

    class _LLM:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            content = json.dumps(
                {"name": tool_name, "arguments": {}}, ensure_ascii=False
            )
            return {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [],
                    "reasoning_content": "",
                },
                "content": content,
                "finish_reason": "stop",
                "usage": {},
                "latency_ms": 1.0,
            }

    return _LLM()


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    return EventStore(db_path=tmp_path / "office.db")


def test_none_skip_emits_warning_not_event(
    store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    """선언 도구가 엔진 tools에 미등록(always-allow) → WARN 1회·도구 미실행.

    crm_get_customer는 정책상 always-allow라 _gate가 allow → _run_tool 진입 → tools에
    crm_get_customer 부재 → tool_fn None → WARN. ★logger 출력이지 이벤트가 아님:
    TOOL_CALL_RESULT 0건. (tools는 비지 않게 다른 도구를 1개 둔다 — _run_tool의
    `not self._tools` 조기탈출은 *빈 dict* 경로이고, 본 가드는 *선언 도구 미등록* 경로다.)
    """

    def _other(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {}

    eng = WorkflowEngine(
        executor=NodeExecutor(
            llm_client=_allow_llm("crm_get_customer"), event_store=store
        ),
        event_store=store,
        # ★tools는 비지 않지만 선언된 crm_get_customer는 미등록(구성오류 모사).
        tools={"order_get_latest": _other},
        gating_policy=GatingPolicy(),
    )
    with caplog.at_level(logging.WARNING, logger="office.engine"):
        eng.run(
            nodes=[_tool_node("crm_get_customer")],
            run_id="r-none-warn",
            workflow="crm_notify",
        )

    # ★WARN 1회(도구명·미등록 메시지 포함).
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("crm_get_customer" in r.getMessage() for r in warns)
    assert any("미등록" in r.getMessage() for r in warns)

    # ★이벤트 아님: TOOL_CALL_RESULT 0건(store 미경유 — 도구 미실행).
    types = [e.to_dict()["type"] for e in store.read("r-none-warn")]
    assert "TOOL_CALL_RESULT" not in types
    # 발행된 타입은 전부 11종 EVENT_REGISTRY ⊆(새 타입 0).
    assert set(types) <= set(EVENT_REGISTRY.keys())


def test_warning_does_not_mutate_contract() -> None:
    """WARN 경로가 EVENT_REGISTRY 11·FailReason 3을 안 바꾼다(계약 무변)."""
    assert len(EVENT_REGISTRY) == 11
    assert len(list(FailReason)) == 3


def test_registered_tool_does_not_warn(
    store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    """정상 경로(등록 도구)에선 WARN 미발화(거짓 양성 0)."""

    def crm(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"customer_id": "C-1"}

    eng = WorkflowEngine(
        executor=NodeExecutor(
            llm_client=_allow_llm("crm_get_customer"), event_store=store
        ),
        event_store=store,
        tools={"crm_get_customer": crm},  # ★등록됨 — None skip 미발생
        gating_policy=GatingPolicy(),
    )
    with caplog.at_level(logging.WARNING, logger="office.engine"):
        eng.run(
            nodes=[_tool_node("crm_get_customer")],
            run_id="r-ok",
            workflow="crm_notify",
        )
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warns == [], f"등록 도구인데 WARN 발화: {[w.getMessage() for w in warns]}"
    # 도구 실행됨 — TOOL_CALL_RESULT 1건.
    types = [e.to_dict()["type"] for e in store.read("r-ok")]
    assert "TOOL_CALL_RESULT" in types
