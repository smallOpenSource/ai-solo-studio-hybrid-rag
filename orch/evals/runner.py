"""SR/renewal P4a — 캡처 runner (capture-then-batch · gemma 핫패스 · 격리 invoke).

각 골든 앵커에 대해 그래프를 invoke 해 채점 신호를 *캡처 레코드*(dict, JSONL 1줄)로
전량 기록한다. judge(qwen·P4b)는 이 캡처를 별도 배치에서 채점 — per-anchor 스왑 0
(DR-1·F5a=(ii) Owner 비준·gemma-affinity 무손상).

★[C-1 격리·MAJOR-1] runner 는 ``app.create_app``·``OutboundEmitter``·
``blackboard_sink``·``_adr_sink`` 를 **import·호출하지 않는다**(정적 grep 가드 대상).
``build_cycle_graph``/``build_org_graph`` 를 **app._run 우회 직접 invoke**하고
outbound emitter·Redis store·OTLP exporter 를 **미주입**한다. 부수효과(_adr_sink·
blackboard_sink·digest·outbound emit)는 전부 app._run 에 있고 그래프 노드는 I/O-0
(graph.py:321/422·org_graph.py 노드 "순수·동기·I/O 0") → eval 런이 라이브 Discord
emit·Redis ``aigs:*`` 오염·OTLP 스팸 0(이력: [[discord-replay-demo-cleanup]] 재방송).

★checkpointer/interrupt 처리(주석 명시):
  ``build_*_graph(checkpointer=None)`` 로 invoke 한다(프로덕션 durable Redis/
  orch_ckpt.db 미사용 = 격리). ESCALATE tier 앵커가 ``authority_gate``/``escalate`` 의
  ``interrupt()`` 에 도달하면, langgraph 는 checkpointer 가 없을 때 **예외 없이**
  반환 dict 에 ``__interrupt__`` 키(Interrupt 목록)를 실어 돌려준다(실측 확인).
  → 이 키 존재로 escalate 를 graceful 감지해 route="escalate" + pre-interrupt state
  를 캡처한다(에러 0). MemorySaver 도 ephemeral 격리지만, checkpointer=None +
  ``__interrupt__`` 감지가 더 단순하고 durable/Redis/outbound 를 *구조적으로* 미접촉
  (체크포인터 인스턴스 자체가 없음)이라 이 경로를 택한다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from orch.graph import _gemma_caller, build_cycle_graph, use_caller, use_searcher
from orch.org_graph import build_org_graph, use_strategy_caller

# 캡처 레코드의 final_state 에 보존할 키 화이트리스트(callable·거대 messages 제외).
_CYCLE_STATE_KEYS = (
    "route",
    "route_reason",
    "decision",
    "authority_tier",
    "decision_confidence",
    "knowledge_source",
    "knowledge_count",
    "bluf",
    "model",
)
_ORG_STATE_KEYS = (
    "route",
    "route_reason",
    "decision",
    "authority_tier",
    "confidence",
    "departments",
    "bluf",
)


def _capturing_searcher(
    inner: Callable[[str, int], dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    search_results: list[list[str]],
) -> Callable[[str, int], dict[str, Any]]:
    """knowledge 검색을 *도구호출*로 캡처하는 래퍼(cycle 그래프의 유일 도구).

    stage_research 가 호출하는 searcher 를 감싼다. 매 호출을 tool_calls 에
    ``{"name":"knowledge_search","args":{"query","k"}}`` 로, 결과를 search_results 에
    순위순 ``[text, source]`` 목록으로 적재한다(scorer 채점 신호 존재 보장·C-4).
    """

    def wrapped(query: str, k: int) -> dict[str, Any]:
        tool_calls.append(
            {"name": "knowledge_search", "args": {"query": query, "k": k}}
        )
        res = inner(query, k)
        for row in res.get("results") or []:
            search_results.append(
                [str(row.get("text") or ""), str(row.get("source") or "")]
            )
        return res

    return wrapped


def _subset(state: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """state 에서 화이트리스트 키만 추린 직렬화-안전 subset."""
    return {k: state[k] for k in keys if k in state}


async def _ainvoke_isolated(graph: Any, directive: str) -> tuple[dict[str, Any], bool]:
    """checkpointer=None 그래프를 직접 invoke. (state, hit_interrupt) 반환.

    langgraph 는 checkpointer 부재 시 interrupt 도달을 예외 없이 반환 dict 의
    ``__interrupt__`` 키로 surface 한다 → escalate graceful 감지(에러 0).
    """
    out = await graph.ainvoke({"directive": directive})
    hit_interrupt = "__interrupt__" in out
    return out, hit_interrupt


def run_anchor(
    anchor: dict[str, Any],
    *,
    caller: Callable[[str, float, int], str] | None = None,
    searcher: Callable[[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """한 앵커에 그래프를 invoke 해 캡처 레코드(dict)를 만든다.

    anchor.graph ∈ {"cycle","org"} 에 따라 build_cycle_graph/build_org_graph 를
    **app._run 우회 직접 invoke**(checkpointer=None·emitter/store/OTLP 미주입).
    caller=gemma stub(결정론)·searcher=no-network stub 을 ContextVar 로 주입.

    Returns 캡처 레코드(고정 계약):
        {anchor_id, type, graph, route, decision_confidence, bluf,
         tool_calls:[{name,args}], search_results:[[text,source],...],
         final_state:dict}
    """
    graph_kind = anchor.get("graph", "cycle")
    directive = anchor.get("input", "")
    tool_calls: list[dict[str, Any]] = []
    search_results: list[list[str]] = []

    if graph_kind == "org":
        graph = build_org_graph(None)
        state_keys = _ORG_STATE_KEYS
        conf_key = "confidence"
        # org 그래프는 knowledge searcher 를 직접 쓰지 않으나, 계약 일관성을 위해
        # cycle 과 동일하게 caller/searcher 를 바인딩(org 부서 노드는 gemma caller).
    else:
        graph = build_cycle_graph(None)
        state_keys = _CYCLE_STATE_KEYS
        conf_key = "decision_confidence"

    # ★[C1 eval 격리] caller 미주입(None)이면 **로컬 gemma**(graph._gemma_caller)를 명시
    # 바인딩한다 — ORCH_LLM_PRIMARY=azure 라도 use_caller override 가 Azure-wrapped 기본
    # (graph._ACTIVE_CALLER)에 *도달하지 못하게* 한다. eval 은 항상 로컬 모델로 측정(회귀
    # 게이트가 Azure-vs-Azure 되는 침묵오측 차단). 결정론 테스트는 명시 caller=stub 주입
    # (이 분기 미사용) → 동작 불변(동일 _gemma_caller 객체). org 부서/CEO 도 같은 로컬 caller.
    eff_caller = caller if caller is not None else _gemma_caller
    import contextlib

    with contextlib.ExitStack() as stack:
        stack.enter_context(use_caller(eff_caller))
        if graph_kind == "org":
            stack.enter_context(use_strategy_caller(eff_caller))
        if searcher is not None:
            wrapped = _capturing_searcher(searcher, tool_calls, search_results)
            stack.enter_context(use_searcher(wrapped))
        state, hit_interrupt = asyncio.run(_ainvoke_isolated(graph, directive))

    # escalate: interrupt 도달이면 route 를 "escalate" 로 권위 캡처(pre-interrupt state).
    route = "escalate" if hit_interrupt else state.get("route", "")
    final_state = _subset(state, state_keys)
    if hit_interrupt:
        final_state["route"] = "escalate"
        # interrupt 페이로드에서 confidence 보강(escalate 브리프·graph.py escalate_payload).
        intr = state.get("__interrupt__") or []
        if intr:
            payload = getattr(intr[0], "value", None) or {}
            if isinstance(payload, dict) and "confidence" in payload:
                final_state.setdefault(conf_key, payload["confidence"])

    return {
        "anchor_id": anchor.get("id", ""),
        "type": anchor.get("type", ""),
        "graph": graph_kind,
        "route": route,
        "decision_confidence": final_state.get(conf_key),
        "bluf": state.get("bluf", ""),
        "tool_calls": tool_calls,
        "search_results": search_results,
        "final_state": final_state,
    }


def run_golden(
    path: str,
    *,
    caller: Callable[[str, float, int], str] | None = None,
    searcher: Callable[[str, int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """골든 JSONL 의 앵커 전량을 캡처한다(앵커 유한·루프 0·termination P0).

    각 줄(앵커)에 ``run_anchor`` 를 적용해 캡처 레코드 목록을 반환한다(JSONL 직렬화는
    호출자 책임). 빈 줄은 건너뛴다.
    """
    import json
    from pathlib import Path

    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        anchor = json.loads(line)
        records.append(run_anchor(anchor, caller=caller, searcher=searcher))
    return records
