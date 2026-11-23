"""orch P3b-5 통합 테스트 — org_graph ceo_route 의 §19 재진입 숙의(cycle 미러).

C3a(cycle 숙의)는 완전 GREEN. 여기서는 **build_org_graph 전체를 통과시켜** org ceo_route 가:
  ① (가역+저확신+고위험신호) → deliberate_org 우회 → ceo_route **재진입**(qwen 1콜·2회차 재호출0)
     → 부서 라우팅 → finalize(가역 commit&proceed·interrupt 0)
  ② 숙의→escalate(judge ESCALATE:yes·recommend_escalate) → 재진입 route=escalate → escalate
     노드 interrupt + ★goto=finalize 가드 보존(무음종료 0)·adr_payload 보존(app if-interrupt sink)
  ③ ★one-shot 종료성(P0·★시스템 다운 차단): judge 항상 저conf 여도 deliberate_org 정확히 1회
  ④ (신호 없는 일상 가역) → deliberate_org 미경유·기존 부서 라우팅(레거시 무손상)
  ⑤ ★MINOR-2: ceo stub conf 미파싱 → proxy 2값(citation 無=0.55)→<0.60·고위험신호 → 진입.

org 모델 seam: ceo=qwen `_strategy_call`(use_strategy_caller)·부서/숙의 proposer=gemma
(use_caller)·judge(use_judge_caller). LLM 0(stub)·결정론·not slow. MemorySaver·thread_id.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from orch import org_graph as org_mod
from orch.deliberation import use_judge_caller
from orch.graph import use_caller
from orch.org_graph import build_org_graph, deliberate_org, use_strategy_caller

# ── 픽스처 directive ────────────────────────────────────────────────────────────
# 가역(one-way 키워드 無)·범위내(owner_reserved 키워드 無)·고위험신호("전략","방향")
# 동반 → needs_deliberation 트리거 후보(단 conf<0.60 → ceo stub 가 낮은 conf emit).
_DELIBERATE = "전략 방향 대전환 — 시장 재편 대응 조사"
# 일상 가역·신호 없음 → needs_deliberation False(저확신이어도 신호 부재).
_PLAIN = "make concept art"

# judge 가 ADR 에 심는 고유 마커(ContextVar 전파·adr_payload 보존 falsify 용).
_JUDGE_MARKER = "ZZ-org-judge-9c4b"


# ── stub callers ────────────────────────────────────────────────────────────────
def ceo_low_conf(route: str = "coo"):
    """org ceo(전략 seam) stub — conf<0.60 emit(<TAU_NOTIFY) → 숙의 트리거 후보.

    [라우팅 작업] 프롬프트에만 응답(나머지는 'x'). route 는 1회차 qwen 라우팅 결과로
    route_reason 에 들어가 2회차 재라우팅의 입력이 된다.
    """

    def stub(prompt: str, temperature: float, max_tokens: int) -> str:
        if "[라우팅 작업]" in prompt:
            return f"ROUTE: {route} | conf 0.40 | strategic pivot under review"
        return "x"

    return stub


def ceo_no_conf(route: str = "coo"):
    """★MINOR-2: ceo stub 가 conf 미표기 → _parse_confidence None → assess proxy(2값).

    citation 없으면 proxy=0.55(<0.60) → 고위험신호 동반 시 숙의 진입(해상도 한계 falsify).
    """

    def stub(prompt: str, temperature: float, max_tokens: int) -> str:
        if "[라우팅 작업]" in prompt:
            return f"ROUTE: {route} | strategic pivot under review"  # conf 라인 없음
        return "x"

    return stub


def org_gemma(prompt: str, temperature: float, max_tokens: int) -> str:
    """부서 + 숙의 서브그래프(proposer/debate/premortem) 공용 gemma stub(비빈 텍스트)."""
    p = prompt.lower()
    if "research(조사)" in prompt:  # coo dispatch
        return "pick: research"
    if "dev(구현 스펙)" in prompt:  # dev_lead dispatch
        return "pick: dev"
    if "BLUF" in prompt:  # finalize
        return "결론: ok\n필요행동: none\n근거:\n- test"
    # 숙의 서브그래프 gemma 콜(독립 제안/Proponent/Challenger/Premortem) — 비빈 텍스트.
    if "독립 제안" in prompt:
        return "제안: 단계적 전환(독립)."
    if "proponent" in p:
        return "찬성: 전환이 성장 기회."
    if "challenger" in p:
        return "반론: 전환 비용·리스크 큼(이의)."
    if "premortem" in p:
        return "실패원인: 전환 혼선으로 사용자 이탈."
    return "stub-artifact"  # department work


def judge_commit(prompt: str, temperature: float, max_tokens: int) -> str:
    """Judge(qwen) stub — ESCALATE:no(가역 commit&proceed)·저conf(one-shot falsify).

    DECISION 라인에 고유 마커 → adr_payload 보존/ContextVar 전파 검증.
    """
    if "synthesizer" in prompt.lower() or "Synthesizer" in prompt:
        return (
            f"DECISION: 단계적 전환 채택 {_JUDGE_MARKER} | conf 0.40 "
            "| MINORITY: 즉시 전환 반대 | ESCALATE: no"
        )
    return "stub"


def judge_escalate(prompt: str, temperature: float, max_tokens: int) -> str:
    """Judge(qwen) stub — ESCALATE:yes → recommend_escalate → 재진입 route=escalate."""
    if "synthesizer" in prompt.lower() or "Synthesizer" in prompt:
        return (
            f"DECISION: Owner 승인 필요 {_JUDGE_MARKER} | conf 0.40 "
            "| MINORITY: 보류 | ESCALATE: yes"
        )
    return "stub"


# ── helpers ───────────────────────────────────────────────────────────────────
def _graph():
    return build_org_graph(MemorySaver())


# ── ① 숙의 진입 + 재진입(qwen 2회차 재호출0) + 가역 commit ────────────────────────
def test_org_deliberation_entry_reentry_reversible_commit():
    """가역+저확신+고위험신호 → deliberate_org → ceo_route 재진입 → 부서 → finalize.

    deliberation_path True·adr_payload non-empty·가역 무차단(interrupt 0)·★qwen(ceo)는
    1회만 호출(2회차 분기는 재호출 0·gemma-affinity)·judge 마커가 adr_payload 에 전파.
    """
    g = _graph()
    cfg = {"configurable": {"thread_id": "org-delib-1"}}
    ceo_calls = {"n": 0}
    base_ceo = ceo_low_conf("coo")

    def ceo_spy(prompt, t, m):
        if "[라우팅 작업]" in prompt:
            ceo_calls["n"] += 1
        return base_ceo(prompt, t, m)

    with (
        use_caller(org_gemma),
        use_strategy_caller(ceo_spy),
        use_judge_caller(judge_commit),
    ):
        out = g.invoke({"directive": _DELIBERATE}, cfg)
        snap = g.get_state(cfg)

    assert out.get("deliberation_path") is True
    assert out.get("deliberated") is True
    assert out.get("adr_payload")  # 강제 합성(빈 폴백 아님)
    assert _JUDGE_MARKER in str(
        out.get("adr_payload")
    )  # ContextVar 전파(judge stub 사용)
    # 재진입 후 가역 → interrupt 없이 부서 라우팅·finalize 완주.
    assert "__interrupt__" not in out
    assert not snap.interrupts
    assert out["route"] == "coo"  # route_reason(1회차 qwen content)로 재라우팅
    assert "research" in (out.get("departments") or [])
    assert out["routing_trace"][-1] == "finalize"
    assert "deliberate_org" in out["routing_trace"]
    # ★gemma-affinity: ceo(qwen) 1콜만(2회차 재진입은 재호출 0).
    assert ceo_calls["n"] == 1, (
        f"ceo(qwen) called {ceo_calls['n']}x (expected exactly 1)"
    )


# ── ② 숙의→escalate(recommend_escalate)·goto=finalize 가드 보존 ───────────────────
def test_org_deliberation_escalate_keeps_finalize_guard():
    """judge ESCALATE:yes → recommend_escalate → 재진입 route=escalate → escalate interrupt.

    ★goto=finalize 가드 보존(무음종료 0)·adr_payload 보존(app if-interrupt sink 대상).
    """
    g = _graph()
    cfg = {"configurable": {"thread_id": "org-delib-esc"}}
    with (
        use_caller(org_gemma),
        use_strategy_caller(ceo_low_conf("coo")),
        use_judge_caller(judge_escalate),
    ):
        first = g.invoke({"directive": _DELIBERATE}, cfg)
        assert "__interrupt__" in first  # 숙의→escalate → interrupt
        snap = g.get_state(cfg)
        assert snap.interrupts
        val = snap.interrupts[0].value
        assert val["tier"] == "escalate" and "brief" in val  # §20.4a 페이로드
        # adr_payload 가 정지 시점 state 에 존재(app if-interrupt _adr_sink 대상·무음유실 0).
        assert snap.values.get("adr_payload")
        assert snap.values.get("deliberation_path") is True
        final = g.invoke(Command(resume="approve"), cfg)
    assert final["route"] == "escalate"
    assert final["decision"] == "approve"
    assert final["routing_trace"][-1] == "finalize"  # ★goto=finalize 무음종료 가드 보존
    assert final["bluf"]


# ── ③ one-shot 종료성(P0·★시스템 다운 차단) ───────────────────────────────────────
def test_org_deliberation_one_shot_terminates_even_when_judge_always_low(monkeypatch):
    """judge 항상 저conf 여도 그래프 종료(무한루프 0)·deliberate_org **정확히 1회**.

    deliberate_org 를 spy 로 감싸 호출 횟수를 센다. 재진입 후 deliberated=True 라
    needs_deliberation False → 2회차 디스패치 0. GraphRecursionError 안 남(자연 종료).
    """
    calls = {"n": 0}
    real = deliberate_org

    def spy(state):
        calls["n"] += 1
        return real(state)

    monkeypatch.setattr(org_mod, "deliberate_org", spy)

    g = _graph()  # 패치된 deliberate_org 로 컴파일
    cfg = {"configurable": {"thread_id": "org-delib-oneshot"}}
    with (
        use_caller(org_gemma),
        use_strategy_caller(ceo_low_conf("coo")),
        use_judge_caller(judge_commit),
    ):
        out = g.invoke({"directive": _DELIBERATE}, cfg)
        snap = g.get_state(cfg)
    assert out["routing_trace"][-1] == "finalize"  # 자연 종료(finalize 도달)
    assert list(snap.next) == []  # 그래프 종료(대기 노드 0)
    assert calls["n"] == 1, f"deliberate_org ran {calls['n']}x (expected exactly 1)"
    # 재진입 후 needs_deliberation False(one-shot 가드) — 직접 재단언.
    assert (
        org_mod.needs_deliberation(
            _DELIBERATE, {"confidence": 0.40, "deliberated": True}
        )
        is False
    )


# ── ④ 비-숙의 직진(레거시 무손상) ───────────────────────────────────────────────
def test_org_plain_directive_skips_deliberation():
    """신호 없는 일상 가역 → deliberate_org 미경유·기존 부서 라우팅(레거시 무손상).

    저확신(conf 0.40)이어도 _DELIBERATE_SIGNAL 키워드 부재 → needs_deliberation False.
    """
    g = _graph()
    cfg = {"configurable": {"thread_id": "org-plain"}}
    with (
        use_caller(org_gemma),
        use_strategy_caller(ceo_low_conf("coo")),
        use_judge_caller(judge_commit),
    ):
        out = g.invoke({"directive": _PLAIN}, cfg)
        snap = g.get_state(cfg)
    assert not out.get("deliberation_path")  # 미경유(False/미set)
    assert not out.get("deliberated")
    assert "deliberate_org" not in out["routing_trace"]
    assert not snap.interrupts
    assert out["route"] == "coo"
    assert "research" in (out.get("departments") or [])
    assert out["routing_trace"][-1] == "finalize"


# ── ⑤ MINOR-2: ceo conf 미파싱 → proxy 해상도 한계(정직 표기) ──────────────────────
def test_org_minor2_unparsed_conf_proxy_resolution():
    """ceo stub conf 미표기(_parse_confidence None) → assess proxy(citation 無=0.55<0.60).

    고위험신호 동반이므로 숙의 진입(proxy 가 <τ_notify 라 트리거 도달). org 트리거 해상도가
    proxy 2값(0.55/0.75)에 의존함을 falsify(해상도 한계·정직).
    """
    g = _graph()
    cfg = {"configurable": {"thread_id": "org-minor2"}}
    with (
        use_caller(org_gemma),
        use_strategy_caller(ceo_no_conf("coo")),
        use_judge_caller(judge_commit),
    ):
        out = g.invoke({"directive": _DELIBERATE}, cfg)
    # proxy=0.55(citation 없음·directive 에 research 미반영)·고위험신호 → 진입.
    assert out.get("deliberation_path") is True
    assert "deliberate_org" in out["routing_trace"]
    assert out["routing_trace"][-1] == "finalize"
