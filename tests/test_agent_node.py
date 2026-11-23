"""office/engine.py agent 노드 — Phase 6 6b Chunk B 단위·통합 테스트.

계획 ralplan-phase6b-2a-agent.md §5(L1·L2)·§6 US-P6B-2/3. 검증 대상(결정 C-A1·I·G·H·N3):
  ① 스텝 상한 → escalation(종료 안 하는 mock LLM·U-agent-1)
  ② 단일호출 출력 토큰 상한 8192(U-agent-2)
  ③ 누적 messages 입력 상한 윈도잉 + accumulated 보존(U-agent-5·N4)
  ④ 게이팅 단일 관문(비가역→interrupt·우회0·U-agent-3)
  ⑤ ast 보조((i)_gate 없는 _run_tool 0 + (ii)agent 분기 self._tools 직접 인덱싱 0·U-agent-4)
  ⑥ 합성 sub-id 도구별 dedup(가역 N개 각 sub-id·비가역 base-id·재시작 각각 1회·I-agent-3)
  ⑦ 11종 무변(I-agent-2)
  ⑧ 격리 워크플로 HITL 왕복(비가역 suspend→resume RunError0·1행·재승인 루프0·I-agent-1)

★전부 스텝 상한 바운드(종료 안 하는 mock도 _AGENT_STEP_LIMIT가 잡음). bare pytest 금지 —
scripts/run_tests.sh engine 경유(conftest 30s + ulimit + OS timeout 3중 가드).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import EVENT_REGISTRY, FailReason
from office.gating import GatingPolicy
from office.nodes import MAX_TOKENS_BY_KIND, Node

# ── 테스트용 정책 (가역 2 + 비가역 1 + 미등록 default) ────────────────────────

_TEST_POLICY: dict[str, Any] = {
    "tools": {
        "crm_get_customer": {"risk": "always-allow"},
        "order_get_latest": {"risk": "always-allow"},
        "agent_action": {"risk": "needs-approval", "when": "always_interrupt"},
    },
    "default_risk": "needs-approval",
}


# ── 드라이버 가능한 mock LLM ───────────────────────────────────────────────────


def _tc(name: str, args: dict[str, Any] | None = None) -> str:
    """prompt_json 도구호출 content JSON."""
    return json.dumps({"name": name, "arguments": args or {}}, ensure_ascii=False)


def _resp(content: str, finish: str = "stop") -> dict[str, Any]:
    """finish_reason 지정 fake 응답."""
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": [],
            "reasoning_content": "",
        },
        "content": content,
        "finish_reason": finish,
        "usage": {},
        "latency_ms": 1.0,
    }


class ScriptLLM:
    """스크립트 응답을 순서대로 반환하는 mock LLM(소진되면 마지막 반복).

    .calls로 호출 인자(max_tokens 등)를 캡처한다. 종료 안 하는 LLM은 마지막 응답이
    도구 호출이거나 done 마커 없는 추론이면 스텝 상한이 잡는다.
    """

    def __init__(self, contents: list[str]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._contents = contents
        self._idx = 0

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        content = self._contents[min(self._idx, len(self._contents) - 1)]
        if self._idx < len(self._contents) - 1:
            self._idx += 1
        return _resp(content)


# ── mock 도구(부수효과 카운트) ─────────────────────────────────────────────────


def _counting_tools() -> tuple[dict[str, Any], dict[str, list[str]]]:
    """호출을 카운트하는 결정론 mock 도구 + 호출 로그를 반환한다.

    로그[도구명] = [호출된 node_id...] — 합성 sub-id가 도구별로 들어오는지 단언용.
    """
    log: dict[str, list[str]] = {
        "crm_get_customer": [],
        "order_get_latest": [],
        "agent_action": [],
    }

    def crm(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        log["crm_get_customer"].append((context or {}).get("node_id", ""))
        return {"customer_id": "C-1", "name": "테스터"}

    def order(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        log["order_get_latest"].append((context or {}).get("node_id", ""))
        return {"order_id": "O-1", "status": "배송완료"}

    def action(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        log["agent_action"].append((context or {}).get("node_id", ""))
        return {"done": True, "action_id": f"A-{(context or {}).get('node_id', '')}"}

    return {
        "crm_get_customer": crm,
        "order_get_latest": order,
        "agent_action": action,
    }, log


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def policy(tmp_path: Path) -> GatingPolicy:
    p = tmp_path / "tools_policy.yaml"
    p.write_text(yaml.dump(_TEST_POLICY, allow_unicode=True), encoding="utf-8")
    return GatingPolicy(policy_path=p)


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    return EventStore(db_path=tmp_path / "office.db")


def _agent_node(tools: list[str] | None = None) -> Node:
    """agent 노드(도구 스키마는 이름만 — _gate는 _last_extracted를 봄)."""
    schemas = [{"function": {"name": t}} for t in (tools or ["crm_get_customer"])]
    return Node(
        id="agent_main",
        kind="agent",
        prompt="자율 수행",
        thinking="off",
        tools=schemas,
        next=None,
    )


def _engine(
    llm: Any, store: EventStore, policy: GatingPolicy, tools: dict[str, Any]
) -> WorkflowEngine:
    executor = NodeExecutor(llm_client=llm, event_store=store)
    return WorkflowEngine(
        executor=executor, event_store=store, tools=tools, gating_policy=policy
    )


def _types(store: EventStore, run_id: str) -> list[str]:
    return [e.to_dict()["type"] for e in store.read(run_id)]


def _events_of(store: EventStore, run_id: str, etype: str) -> list[dict[str, Any]]:
    return [e.to_dict() for e in store.read(run_id) if e.to_dict()["type"] == etype]


# ── ② 출력 토큰 상한 8192 (U-agent-2 · AC-2a) ────────────────────────────────


def test_agent_max_tokens_is_8192() -> None:
    """MAX_TOKENS_BY_KIND['agent']==8192이고 ≤16384(32k 금지)."""
    assert MAX_TOKENS_BY_KIND["agent"] == 8192
    assert MAX_TOKENS_BY_KIND["agent"] <= 16384


def test_agent_node_resolve_max_tokens_no_raise() -> None:
    """kind='agent' resolve_max_tokens가 NotImplementedError 안 내고 8192 반환(flip)."""
    assert Node(id="a", kind="agent").resolve_max_tokens() == 8192


def test_agent_step_uses_8192_output_cap(
    store: EventStore, policy: GatingPolicy
) -> None:
    """agent 단일 추론 스텝의 LLM 호출 max_tokens가 8192다(출력 상한)."""
    llm = ScriptLLM([_tc("crm_get_customer"), "완료 AGENT_DONE"])
    tools, _ = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    eng.run(nodes=[_agent_node()], run_id="r-cap", workflow="agent_smoke")
    assert llm.calls, "LLM이 호출되지 않았다"
    assert llm.calls[0]["max_tokens"] == 8192


# ── ① 스텝 상한 → escalation (U-agent-1 · AC-2b) ─────────────────────────────


def test_agent_step_limit_escalation(store: EventStore, policy: GatingPolicy) -> None:
    """종료 신호 없이 추론만 무한 반복하는 agent → 스텝 상한 → NODE_FAILED(escalation)."""
    # done 마커 없는 순수 추론(도구 호출 아님)만 반복 → 종료 불가 → 상한 도달.
    llm = ScriptLLM(["계속 생각 중..."])  # 영원히 같은 응답(마커 없음)
    tools, _ = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    state = eng.run(nodes=[_agent_node()], run_id="r-limit", workflow="agent_smoke")

    assert state["_workflow_status"] == "failed"
    fails = _events_of(store, "r-limit", "NODE_FAILED")
    assert len(fails) == 1
    assert fails[0]["reason"] == FailReason.escalation.value
    assert "상한" in fails[0]["detail"]
    # 스텝 상한만큼만 LLM 호출(무한 아님) — 바운드 증명.
    assert len(llm.calls) == WorkflowEngine._AGENT_STEP_LIMIT


def test_agent_tool_loop_also_bounded(store: EventStore, policy: GatingPolicy) -> None:
    """종료 신호 없이 가역 도구만 무한 호출해도 스텝 상한이 잡는다(escalation)."""
    llm = ScriptLLM([_tc("crm_get_customer")])  # 매 스텝 같은 가역 도구(마커 없음)
    tools, log = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    state = eng.run(nodes=[_agent_node()], run_id="r-tloop", workflow="agent_smoke")
    assert state["_workflow_status"] == "failed"
    # 각 스텝 다른 sub-id(agent_main#0..#11)라 B1 skip 없이 매번 실행 → 상한까지 호출.
    assert len(log["crm_get_customer"]) == WorkflowEngine._AGENT_STEP_LIMIT


# ── ③ 입력 상한 윈도잉 + accumulated 보존 (U-agent-5 · N4) ────────────────────


def test_agent_input_windowing_preserves_accumulated(
    store: EventStore, policy: GatingPolicy
) -> None:
    """누적 messages가 입력 상한 초과 시 윈도잉되나 accumulated는 보존된다(N4)."""
    eng = _engine(ScriptLLM(["x"]), store, policy, {})
    # 임계 초과 messages + accumulated 1건.
    big = "가" * (WorkflowEngine._AGENT_MSG_CHARS_LIMIT + 1000)
    state: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": big},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ],
        "accumulated": {"n1_result": {"k": "v"}},
    }
    out = eng._window_messages(state)
    msgs = out["messages"]
    # system 보존 + 최근 K개만(거대 user 메시지는 잘림).
    assert any(m.get("role") == "system" for m in msgs)
    assert all(big not in str(m.get("content", "")) for m in msgs)
    assert len(msgs) <= 1 + WorkflowEngine._AGENT_WINDOW_KEEP
    # ★accumulated 무손실(입력 상한이 carryforward를 안 깸).
    assert out["accumulated"] == {"n1_result": {"k": "v"}}


def test_agent_windowing_noop_under_limit(
    store: EventStore, policy: GatingPolicy
) -> None:
    """임계 이하면 윈도잉 무동작(정상 무변)."""
    eng = _engine(ScriptLLM(["x"]), store, policy, {})
    state = {"messages": [{"role": "user", "content": "짧음"}], "accumulated": {}}
    out = eng._window_messages(state)
    assert out["messages"] == state["messages"]


# ── ④ 게이팅 단일 관문 — 비가역 → interrupt → suspend (U-agent-3 · AC-2c) ──────


def test_agent_irreversible_suspends_via_gate(
    store: EventStore, policy: GatingPolicy
) -> None:
    """agent가 비가역(agent_action) 호출 → _gate interrupt → suspend(우회 0)."""
    llm = ScriptLLM([_tc("agent_action", {"note": "x"})])
    tools, log = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    node = _agent_node(["agent_action"])
    state = eng.run(nodes=[node], run_id="r-irr", workflow="agent_smoke", task_id=7)

    assert state["_workflow_status"] == "suspended"
    # ★비가역은 승인 전 실행 0(게이팅 우회 없음).
    assert log["agent_action"] == []
    # INTERRUPT_REQUESTED.node_id는 base agent id(1b 봉합) — sub-id 아님.
    irq = _events_of(store, "r-irr", "INTERRUPT_REQUESTED")
    assert len(irq) == 1
    assert irq[0]["node_id"] == "agent_main"
    # _resume_node_id도 base id(resume node_map.get 성공 보장).
    assert state["_resume_node_id"] == "agent_main"


def test_agent_allow_runs_reversible_tool(
    store: EventStore, policy: GatingPolicy
) -> None:
    """가역(always-allow) 도구는 _gate allow → _run_tool 실행 → 종료 마커로 완료."""
    llm = ScriptLLM([_tc("crm_get_customer"), "끝 AGENT_DONE"])
    tools, log = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    state = eng.run(nodes=[_agent_node()], run_id="r-allow", workflow="agent_smoke")
    assert state["_workflow_status"] == "success"
    assert len(log["crm_get_customer"]) == 1
    # 합성 sub-id로 발행(agent_main#0).
    assert log["crm_get_customer"][0] == "agent_main#0"


# ── ⑥ 합성 sub-id 도구별 dedup (I-agent-3 · AC-2f) ───────────────────────────


def test_agent_multi_reversible_distinct_subids(
    store: EventStore, policy: GatingPolicy
) -> None:
    """가역 2개가 각 sub-id(agent_main#0/#1)로 발행 → 도구별 TOOL_CALL_RESULT 1건씩."""
    llm = ScriptLLM(
        [_tc("crm_get_customer"), _tc("order_get_latest"), "완료 AGENT_DONE"]
    )
    tools, log = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    eng.run(
        nodes=[_agent_node(["crm_get_customer", "order_get_latest"])],
        run_id="r-multi",
        workflow="agent_smoke",
    )

    assert log["crm_get_customer"] == ["agent_main#0"]
    assert log["order_get_latest"] == ["agent_main#1"]
    results = _events_of(store, "r-multi", "TOOL_CALL_RESULT")
    node_ids = sorted(r["node_id"] for r in results)
    assert node_ids == ["agent_main#0", "agent_main#1"]


def test_agent_restart_dedup_each_once(store: EventStore, policy: GatingPolicy) -> None:
    """크래시 후 같은 run_id 재시작 → 이미 발행된 sub-id만 B1 skip → 각각 정확히 1회."""
    contents = [_tc("crm_get_customer"), _tc("order_get_latest"), "완료 AGENT_DONE"]
    tools, log = _counting_tools()

    # 1차 실행(완주).
    eng1 = _engine(ScriptLLM(list(contents)), store, policy, tools)
    eng1.run(
        nodes=[_agent_node(["crm_get_customer", "order_get_latest"])],
        run_id="r-restart",
        workflow="agent_smoke",
    )
    assert len(log["crm_get_customer"]) == 1
    assert len(log["order_get_latest"]) == 1

    # 2차 재시작(같은 run_id) — LLM이 같은 sub-id 순서를 재도출해도 B1이 도구별 skip.
    eng2 = _engine(ScriptLLM(list(contents)), store, policy, tools)
    eng2.run(
        nodes=[_agent_node(["crm_get_customer", "order_get_latest"])],
        run_id="r-restart",
        workflow="agent_smoke",
    )
    # ★재시작에도 부수효과 추가 0(각 도구 여전히 1회) — has_tool_result(run_id, sub-id) skip.
    assert len(log["crm_get_customer"]) == 1
    assert len(log["order_get_latest"]) == 1
    # TOOL_CALL_RESULT도 sub-id별 1건씩 유지(재발행 0).
    results = _events_of(store, "r-restart", "TOOL_CALL_RESULT")
    node_ids = sorted(r["node_id"] for r in results)
    assert node_ids == ["agent_main#0", "agent_main#1"]


# ── ⑧ HITL 왕복: 비가역 suspend → resume(G-RESTART) (I-agent-1 · AC-3a/3c) ────


def test_agent_hitl_roundtrip_resume_seal(
    store: EventStore, policy: GatingPolicy
) -> None:
    """가역+비가역 agent → 비가역 suspend → approve resume → RunError0·비가역 1행·재승인0."""
    # 스텝: crm(가역 sub#0) → agent_action(비가역 base→suspend) → 완료 마커. 1차 실행은
    # agent_action에서 suspend(마커 미도달). resume 재실행은 crm(B1 skip)→agent_action
    # (승인됨·base-id 실행)→마커로 완료.
    contents = [
        _tc("crm_get_customer"),
        _tc("agent_action", {"note": "go"}),
        "완료 AGENT_DONE",
    ]
    tools, log = _counting_tools()
    eng = _engine(ScriptLLM(list(contents)), store, policy, tools)
    node = _agent_node(["crm_get_customer", "agent_action"])

    suspended = eng.run(
        nodes=[node], run_id="r-hitl", workflow="agent_smoke", task_id=11
    )
    assert suspended["_workflow_status"] == "suspended"
    assert log["crm_get_customer"] == ["agent_main#0"]  # 가역 1회(sub-id)
    assert log["agent_action"] == []  # 비가역은 승인 전 실행 0

    # resume(approve) — G-RESTART: agent 노드 처음부터 재실행.
    eng2 = _engine(ScriptLLM(list(contents)), store, policy, tools)
    final = eng2.resume_after_approval(suspended, "approve")

    # ★RunError 0(1b 봉합 — _resume_node_id=base agent id라 node_map.get 성공).
    assert "RUN_ERROR" not in _types(store, "r-hitl")
    assert final["_workflow_status"] == "success"
    # 가역은 재시작에 B1 skip(여전히 1회), 비가역은 승인된 base-id로 정확히 1회.
    assert len(log["crm_get_customer"]) == 1
    assert log["agent_action"] == ["agent_main"]  # 비가역 base-id 1행
    # ★재승인 루프 0 — INTERRUPT_REQUESTED는 최초 1건뿐(resume 재진입이 재발 안 함).
    assert len(_events_of(store, "r-hitl", "INTERRUPT_REQUESTED")) == 1


def test_agent_resume_reject(store: EventStore, policy: GatingPolicy) -> None:
    """비가역 suspend 후 reject → RUN_FINISHED(rejected)·비가역 실행 0."""
    contents = [_tc("agent_action", {"note": "x"})]
    tools, log = _counting_tools()
    eng = _engine(ScriptLLM(list(contents)), store, policy, tools)
    node = _agent_node(["agent_action"])
    suspended = eng.run(nodes=[node], run_id="r-rej", workflow="agent_smoke", task_id=3)
    assert suspended["_workflow_status"] == "suspended"

    final = eng.resume_after_approval(suspended, "reject")
    assert final["_workflow_status"] == "rejected"
    assert log["agent_action"] == []  # 거부 시 비가역 실행 0


# ── ⑦ 11종 무변 (I-agent-2 · AC-2d) ───────────────────────────────────────────


def test_event_registry_unchanged_11() -> None:
    """EVENT_REGISTRY는 정확히 11종(agent 새 타입 0)."""
    assert len(EVENT_REGISTRY) == 11


def test_failreason_unchanged_3() -> None:
    """FailReason은 정확히 3종(agent escalation은 기존 값 재사용)."""
    assert len(list(FailReason)) == 3


def test_agent_emits_only_known_event_types(
    store: EventStore, policy: GatingPolicy
) -> None:
    """agent 실행이 발행하는 이벤트 타입이 11종 EVENT_REGISTRY ⊆(새 타입 0)."""
    llm = ScriptLLM(
        [_tc("crm_get_customer"), _tc("order_get_latest"), "완료 AGENT_DONE"]
    )
    tools, _ = _counting_tools()
    eng = _engine(llm, store, policy, tools)
    eng.run(
        nodes=[_agent_node(["crm_get_customer", "order_get_latest"])],
        run_id="r-types",
        workflow="agent_smoke",
    )
    emitted = set(_types(store, "r-types"))
    assert emitted <= set(EVENT_REGISTRY.keys())
    # agent 루프는 TEXT_MESSAGE·TOOL_CALL_*·STATE_DELTA·RUN_*만 쓴다.
    assert "TEXT_MESSAGE" in emitted
    assert "TOOL_CALL_RESULT" in emitted


# ── ⑤ ast 보조 회귀 가드 (U-agent-4 · AC-2c) ─────────────────────────────────


def _agent_branch_source() -> str:
    """engine.py에서 agent 관련 메서드(_run_agent·_agent_invoke_tool) 본문 텍스트."""
    import ast

    src = (
        Path(__file__)
        .parent.parent.joinpath("office/engine.py")
        .read_text(encoding="utf-8")
    )
    tree = ast.parse(src)
    chunks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "_run_agent",
            "_agent_invoke_tool",
        ):
            chunks.append(ast.get_source_segment(src, node) or "")
    return "\n".join(chunks)


def test_ast_agent_branch_no_direct_tools_indexing() -> None:
    """(ii) agent 분기에서 self._tools[...] 직접 인덱싱 0(관문 우회 직접 호출 차단)."""
    import ast

    src = _agent_branch_source()
    assert src, "agent 메서드를 찾지 못했다"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # self._tools[...] subscript 직접 접근 금지.
        if isinstance(node, ast.Subscript):
            val = node.value
            if (
                isinstance(val, ast.Attribute)
                and val.attr == "_tools"
                and isinstance(val.value, ast.Name)
                and val.value.id == "self"
            ):
                raise AssertionError("agent 분기에서 self._tools 직접 인덱싱 발견")


def test_ast_agent_invoke_tool_gates_before_run_tool() -> None:
    """(i) _agent_invoke_tool은 _run_tool 호출 전 _gate를 부른다(게이팅 단일 관문)."""
    import ast

    src = (
        Path(__file__)
        .parent.parent.joinpath("office/engine.py")
        .read_text(encoding="utf-8")
    )
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_agent_invoke_tool":
            target = node
    assert target is not None
    gate_line = None
    run_tool_line = None
    for node in ast.walk(target):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "_gate" and gate_line is None:
                gate_line = node.lineno
            if node.func.attr == "_run_tool" and run_tool_line is None:
                run_tool_line = node.lineno
    assert gate_line is not None, "_agent_invoke_tool에 _gate 호출이 없다"
    assert run_tool_line is not None, "_agent_invoke_tool에 _run_tool 호출이 없다"
    assert gate_line < run_tool_line, "_gate가 _run_tool보다 먼저 와야 한다(관문 순서)"
