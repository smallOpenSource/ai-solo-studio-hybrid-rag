"""orch/org_graph.py 결정론 단위 + integration (stub caller · MemorySaver).

다부서 map-reduce Send · CEO 전략(qwen) seam · escalate goto · OrgState reducer 누적 검증.
라이브 미접촉(stub·인메모리 checkpointer).
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from orch.graph import use_caller
from orch.org_graph import _PERSONA_PROMPTS, build_org_graph, use_strategy_caller

_PERSONA_IDS = [
    "ceo",
    "coo",
    "dev_lead",
    "research",
    "art",
    "librarian",
    "intake",
    "dev",
    "qa",
]


def make_stub(ceo: str = "dev_lead", coo_pick: str = "research", dev_pick: str = "dev"):
    """프롬프트 내용으로 노드를 구분하는 결정론 stub(부서 gemma + CEO 전략 양쪽 겸용)."""

    def stub(prompt: str, temperature: float, max_tokens: int) -> str:
        if "[라우팅 작업]" in prompt:  # ceo_route (전략 seam)
            return f"ROUTE: {ceo} | test"
        if "research(조사)" in prompt:  # coo dispatch
            return f"pick: {coo_pick}"
        if "dev(구현 스펙)" in prompt:  # dev_lead dispatch
            return f"pick: {dev_pick}"
        if "BLUF" in prompt:  # finalize
            return "결론: ok\n필요행동: none\n근거:\n- test"
        return "stub-artifact"  # department work

    return stub


def _run(stub, directive: str, thread: str, strategy_stub=None):
    g = build_org_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": thread}}
    with use_caller(stub), use_strategy_caller(strategy_stub or stub):
        return g.invoke({"directive": directive}, cfg)


def test_compiles():
    assert build_org_graph(MemorySaver()) is not None


def test_persona_prompts_loaded():
    for pid in _PERSONA_IDS:
        assert pid in _PERSONA_PROMPTS and _PERSONA_PROMPTS[pid].strip()


# ── 단일부서 회귀 ──────────────────────────────────────────────────────────────
def test_route_tech_single_dev():
    out = _run(make_stub(ceo="dev_lead", dev_pick="dev"), "fix the build", "t-tech")
    assert out["route"] == "dev_lead"
    assert out["routing_trace"] == ["ceo_route", "dev_lead", "dev", "finalize"]
    assert out["departments"] == ["dev"]
    assert "dev" in out["artifacts"]
    assert out["bluf"]


def test_route_nontech_single_art():
    out = _run(make_stub(ceo="coo", coo_pick="art"), "make concept art", "t-non")
    assert out["route"] == "coo"
    assert out["routing_trace"] == ["ceo_route", "coo", "art", "finalize"]
    assert out["departments"] == ["art"]
    assert "art" in out["artifacts"]


def test_coo_each_department():
    for dept in ["research", "art", "librarian", "intake"]:
        out = _run(make_stub(ceo="coo", coo_pick=dept), "task", f"t-{dept}")
        assert dept in out["departments"]
        assert dept in out["artifacts"]
        assert out["routing_trace"][-2] == dept  # 부서가 finalize 직전


# ── 다부서 map-reduce fan-out (집합 멤버십 단언) ───────────────────────────────
def test_coo_multidept_fanout():
    out = _run(
        make_stub(ceo="coo", coo_pick="research, art"), "조사+에셋 동시", "t-multi"
    )
    assert set(out["departments"]) == {"research", "art"}  # 병렬 누적
    assert (
        "research" in out["artifacts"] and "art" in out["artifacts"]
    )  # operator.or_ 머지 2개
    trace = set(out["routing_trace"])
    assert {"ceo_route", "coo", "research", "art", "finalize"} <= trace
    assert out["routing_trace"][0] == "ceo_route"
    assert out["routing_trace"][-1] == "finalize"  # finalize 1회 조인
    assert out["bluf"]


def test_devlead_multidept_fanout():
    out = _run(make_stub(ceo="dev_lead", dev_pick="dev, qa"), "구현+검증", "t-dq")
    assert set(out["departments"]) == {"dev", "qa"}
    assert "dev" in out["artifacts"] and "qa" in out["artifacts"]


# ── CEO 전략(qwen) seam 격리 ──────────────────────────────────────────────────
def test_ceo_uses_strategy_seam_not_gemma():
    """ceo_route 는 _strategy_call(전략 seam) 경유 — gemma seam(use_caller)이 아님."""
    gemma_stub = make_stub(
        ceo="dev_lead", coo_pick="research"
    )  # gemma 라우팅하면 dev_lead

    def strategy_stub(prompt, t, m):
        if "[라우팅 작업]" in prompt:
            return "ROUTE: coo | strategy-decided"  # 전략 seam은 coo
        return "x"

    out = _run(gemma_stub, "ambiguous", "t-strat", strategy_stub=strategy_stub)
    assert out["route"] == "coo"  # ★전략 seam 사용 증명(gemma였다면 dev_lead)
    assert "research" in out["artifacts"]


# ── escalate goto 회귀가드 ────────────────────────────────────────────────────
def test_escalate_interrupt_and_resume():
    g = build_org_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t-esc"}}
    with (
        use_caller(make_stub(ceo="escalate")),
        use_strategy_caller(make_stub(ceo="escalate")),
    ):
        first = g.invoke({"directive": "spend real money"}, cfg)
        assert "__interrupt__" in first
        assert not first.get("bluf")
        assert g.get_state(cfg).interrupts
        final = g.invoke(Command(resume="approve"), cfg)
    assert final["decision"] == "approve"
    assert "escalate" in final["routing_trace"]
    assert final["routing_trace"][-1] == "finalize"
    assert final["bluf"]  # ★goto 없으면 무음종료


def test_default_route_when_unparseable():
    def stub(prompt: str, t: float, m: int) -> str:
        if "[라우팅 작업]" in prompt:
            return "uh i dunno"  # 파싱 불가 -> default 'coo'
        if "research(조사)" in prompt:
            return "pick: research"
        if "BLUF" in prompt:
            return "결론: x"
        return "art-out"

    out = _run(stub, "???", "t-def")
    assert out["route"] == "coo"
