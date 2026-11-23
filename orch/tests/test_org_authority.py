"""P3 C2b §20 — org ceo_route escalate 가 키워드(authority.decide) 구동(qwen 판단 override).

org_graph 의 escalate 발화조건을 키워드→τ 로 격상했음을 단언: owner-reserved 지시는 qwen 이 다른
부서로 라우팅해도 ESCALATE, 가역 지시는 qwen 이 escalate 라 답해도 무차단. 결정론·stub·MemorySaver.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from orch.graph import use_caller
from orch.org_graph import build_org_graph, use_strategy_caller
from orch.tests.test_org_graph import make_stub


def test_org_escalate_keyword_driven_overrides_qwen():
    # qwen(stub)=coo 라우팅이어도, owner-reserved 키워드(출시·실금전 지출)는 decide() 가 ESCALATE 강제.
    g = build_org_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t-org-esc"}}
    stub = make_stub(ceo="coo")
    with use_caller(stub), use_strategy_caller(stub):
        first = g.invoke({"directive": "스팀에 출시하고 광고비 실금전 지출"}, cfg)
        assert "__interrupt__" in first  # qwen=coo 무시·escalate→interrupt
        snap = g.get_state(cfg)
        assert snap.interrupts
        val = snap.interrupts[0].value
        # §20.4a 페이로드(브리프 + 계산 confidence·하드코딩 0.5 아님).
        assert val["tier"] == "escalate" and "brief" in val
        final = g.invoke(Command(resume="approve"), cfg)
    assert final["route"] == "escalate"
    assert final["decision"] == "approve"
    assert final["routing_trace"][-1] == "finalize"  # ★goto=finalize 무음종료 가드 보존


def test_org_reversible_no_escalate_despite_qwen():
    # 가역 지시는 qwen(stub)=escalate 라 답해도 decide() 가 escalate 아님 → 부서 라우팅(무차단).
    g = build_org_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t-org-rev"}}
    stub = make_stub(ceo="escalate", coo_pick="research")
    with use_caller(stub), use_strategy_caller(stub):
        out = g.invoke({"directive": "fix the build"}, cfg)
    assert (
        out["route"] != "escalate"
    )  # 키워드 없음 → 결정론 override(qwen escalate 무시)
    assert "__interrupt__" not in out  # 무차단(escalate 미발화)
    assert out["routing_trace"][-1] == "finalize"
