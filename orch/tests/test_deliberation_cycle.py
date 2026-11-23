"""orch P3b-2 통합 테스트 — cycle authority_gate 의 §19 재진입 숙의 분기(MAJOR-1).

P3b-1(orch/deliberation.py·needs_deliberation·StateStore.incr)은 완료(31 tests).
여기서는 **build_cycle_graph 전체를 통과시켜** authority_gate 가:
  ① (가역+저확신+고위험신호) → deliberate 우회 → authority_gate **재진입** → stage_qa → finalize
  ② ★one-shot 종료성(HIGH-2·P0): judge 가 *항상* 저confidence 여도 deliberate 정확히 1회·무한루프 0
  ③ (신호 없는 일상 가역) → deliberate 미경유·직접 stage_qa→finalize(레거시 무손상)
  ④ ★ContextVar 전파: stub_judge 의 고유 텍스트가 최종 adr_payload 에 반영(서브그래프 내부 stub 실증)

LLM 0(stub)·결정론·not slow. graph use_caller·graph use_searcher·deliberation use_judge_caller
세 ContextVar 를 함께 바인딩(서브그래프 노드에도 전파). MemorySaver checkpointer·thread_id config.
orch env 은 async 를 ``asyncio.run`` 으로 구동(test_graph_unit 관례·pytest-asyncio 비의존).
"""

from __future__ import annotations

import asyncio
import contextlib

from langgraph.checkpoint.memory import MemorySaver

from orch import graph as graph_mod
from orch.deliberation import use_judge_caller
from orch.graph import build_cycle_graph, deliberate, use_caller, use_searcher
from orch.tests.conftest import stub_searcher

# ── 픽스처 directive ────────────────────────────────────────────────────────────
# 가역(one-way 키워드 無)·범위내(owner_reserved 키워드 無)·고위험신호("전략","방향")
# 동반 → needs_deliberation 트리거 후보(단 conf<0.60 필요 → stub 가 낮은 conf emit).
_DELIBERATE = "전략 방향 대전환 검토"
# 일상 가역·신호 없음(canonical) → needs_deliberation False(저확신이어도 신호 부재).
_PLAIN = "make a clicker game"

# stub_judge 가 ADR 에 심는 고유 마커(ContextVar 전파 falsify 용).
_JUDGE_MARKER = "ZZ-judge-marker-7f3a"


# ── stub callers ────────────────────────────────────────────────────────────────
def low_conf_gemma(prompt: str, temperature: float, max_tokens: int) -> str:
    """cycle 스테이지 + 숙의 서브그래프(proposer/debate/premortem) 공용 gemma stub.

    ceo_route 는 'conf 0.40' 을 emit → decision_confidence=0.40(<TAU_NOTIFY=0.60)
    이라 _DELIBERATE 가 숙의 트리거 후보가 된다. 숙의 서브그래프 gemma 콜(독립제안/
    Proponent/Challenger/Premortem)은 비어있지 않은 텍스트만 필요(파싱은 judge 가 함).
    """
    p = prompt.lower()
    if "ceo router" in p:
        return "ROUTE: build | conf 0.40 | strategic pivot under review"
    if "design stage" in p:
        return "core loop: click. reward: +1 coin. upgrade: auto-clicker."
    if "content stage" in p:
        return "Name: Clicky Quest. items: Cursor(10) Grandma(20) Factory(30)"
    if "qa stage" in p:
        return "check1: loop ok. check2: costs ok. risk: balance. VERDICT: PASS"
    if "bluf" in p:
        return (
            "결론: 전략 검토 결과 진행 가능.\n"
            "필요행동: 없음.\n"
            "근거:\n- research: F2P (https://example.test/wiki/F2P)\n"
            "- design: ok\n- content: ok\n- qa: PASS"
        )
    # 숙의 서브그래프 gemma 콜(독립 제안/Proponent/Challenger/Premortem) — 비빈 텍스트.
    if "독립 제안" in prompt:
        return "제안: 단계적 전환(독립)."
    if "proponent" in p:
        return "찬성: 전환이 성장 기회."
    if "challenger" in p:
        return "반론: 전환 비용·리스크 큼(이의)."
    if "premortem" in p:
        return "실패원인: 전환 혼선으로 사용자 이탈."
    return "stub-artifact"


def high_conf_gemma(prompt: str, temperature: float, max_tokens: int) -> str:
    """ceo_route 가 'conf 0.90' emit(가역+고확신) → AUTONOMOUS·숙의 미진입(직진 경로)."""
    p = prompt.lower()
    if "ceo router" in p:
        return "ROUTE: build | conf 0.90 | confident build"
    return low_conf_gemma(prompt, temperature, max_tokens)


def low_conf_judge(prompt: str, temperature: float, max_tokens: int) -> str:
    """Judge(qwen) stub — *항상* 저confidence(0.40) 반환(one-shot 종료성 falsify).

    DECISION 라인에 고유 마커를 심어 ContextVar 전파(서브그래프 내부 LLM 이 이 stub 를
    실제로 받았는지)를 adr_payload 에서 검증한다. 종료가 합의/고conf 에 의존하면
    이 stub 로 무한루프가 나야 하지만, one-shot 가드(deliberated=True)가 차단한다.
    """
    if "synthesizer" in prompt.lower() or "Synthesizer" in prompt:
        return f"DECISION: 단계적 전환 채택 {_JUDGE_MARKER} | conf 0.40 | MINORITY: 즉시 전환 반대"
    return "stub"


# ── helpers ───────────────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.run(coro)


@contextlib.contextmanager
def _ctx(gemma, judge=low_conf_judge, searcher=stub_searcher):
    """graph(use_caller·use_searcher) + deliberation(use_judge_caller) 세 seam 바인딩.

    서브그래프 propose/debate/premortem 는 graph use_caller(gemma)·synthesize 는
    deliberation use_judge_caller(judge) 로 전파(ContextVar copy across executor).
    """
    with use_caller(gemma), use_judge_caller(judge), use_searcher(searcher):
        yield


def _graph():
    """MemorySaver 체크포인터 기반 cycle 그래프(thread_id 로 resume·재진입 토폴로지)."""
    return build_cycle_graph(MemorySaver())


# ── ① 숙의 진입 + 재진입 + N1 수렴 ──────────────────────────────────────────────
def test_deliberation_entry_reentry_and_n1_convergence():
    """가역+저확신+고위험신호 → deliberate 경유 → authority_gate 재진입 → stage_qa → finalize.

    최종 state: deliberation_path==True · adr_payload non-empty · deliberated==True ·
    finalize(bluf '결론' 시작) 도달(N1 수렴). 재진입 authority_gate 가 갱신 conf(0.40·
    가역)로 NOTIFY → 무차단 stage_qa(interrupt 0).
    """

    async def go():
        g = _graph()
        cfg = {"configurable": {"thread_id": "delib-entry-1"}}
        with _ctx(low_conf_gemma):
            state = await g.ainvoke({"directive": _DELIBERATE}, cfg)
            snap = await g.aget_state(cfg)
        # 숙의 경유 표식 + 산출 ADR + one-shot 플래그.
        assert state.get("deliberation_path") is True
        assert state.get("deliberated") is True
        assert state.get("adr_payload")  # 강제 합성(빈 폴백 아님)
        # 재진입 후 가역(NOTIFY) → interrupt 없이 finalize 까지 완주(N1 수렴).
        assert not snap.interrupts
        assert state.get("authority_tier") == "notify"
        assert "qa" in (state.get("artifacts") or {})
        assert state["bluf"].startswith("결론")

    _run(go())


# ── ② one-shot 종료성(HIGH-2·P0·★시스템 다운 차단) ──────────────────────────────
def test_deliberation_one_shot_terminates_even_when_judge_always_low(monkeypatch):
    """judge 가 *항상* 저conf 여도 그래프 종료(무한루프 0)·deliberate **정확히 1회**.

    ★게이트레벨 재진입 무한루프 차단 실증: deliberate 노드를 spy 로 감싸 호출 횟수를
    센다. 재진입 후 deliberated=True 라 needs_deliberation False → 2회차 디스패치 0.
    recursion_limit/GraphRecursionError 안 남(자연 종료).
    """
    calls = {"n": 0}
    real_deliberate = deliberate

    def spy_deliberate(state):
        calls["n"] += 1
        return real_deliberate(state)

    # build_cycle_graph 가 모듈 전역 deliberate 를 add_node 하므로 모듈 심볼을 패치.
    monkeypatch.setattr(graph_mod, "deliberate", spy_deliberate)

    async def go():
        g = _graph()  # 패치된 deliberate 로 컴파일
        cfg = {"configurable": {"thread_id": "delib-oneshot-1"}}
        with _ctx(low_conf_gemma, judge=low_conf_judge):
            state = await g.ainvoke({"directive": _DELIBERATE}, cfg)
            snap = await g.aget_state(cfg)
        # 자연 종료(finalize 도달)·deliberate 정확히 1회.
        assert state["bluf"].startswith("결론")
        assert list(snap.next) == []  # 그래프 종료(대기 노드 0)
        assert calls["n"] == 1, f"deliberate ran {calls['n']}x (expected exactly 1)"
        # 재진입 후 needs_deliberation False(one-shot 가드) — 직접 재단언.
        assert (
            graph_mod.needs_deliberation(
                _DELIBERATE, {"decision_confidence": 0.40, "deliberated": True}
            )
            is False
        )

    _run(go())


# ── ③ 비-숙의 직진(레거시 무손상) ───────────────────────────────────────────────
def test_plain_directive_skips_deliberation():
    """신호 없는 일상 가역(canonical clicker) → deliberate 미경유·직접 stage_qa→finalize.

    저확신(conf 0.40)이어도 _DELIBERATE_SIGNAL 키워드 부재 → needs_deliberation False.
    deliberation_path 미set(falsy)·레거시 권한 경로(NOTIFY 무차단) 그대로.
    """

    async def go():
        g = _graph()
        cfg = {"configurable": {"thread_id": "plain-1"}}
        with _ctx(low_conf_gemma):
            state = await g.ainvoke({"directive": _PLAIN}, cfg)
            snap = await g.aget_state(cfg)
        assert not state.get("deliberation_path")  # 미경유(False/미set)
        assert not state.get("deliberated")
        assert not snap.interrupts
        assert state["bluf"].startswith("결론")
        assert "qa" in (state.get("artifacts") or {})

    _run(go())


def test_high_confidence_directive_skips_deliberation():
    """고위험신호 directive 라도 고확신(conf 0.90) → AUTONOMOUS·숙의 미진입(저확신 트리거 부재).

    needs_deliberation 은 conf<TAU_NOTIFY 필수 — 고확신이면 신호가 있어도 직진.
    """

    async def go():
        g = _graph()
        cfg = {"configurable": {"thread_id": "highconf-1"}}
        with _ctx(high_conf_gemma):
            state = await g.ainvoke({"directive": _DELIBERATE}, cfg)
            snap = await g.aget_state(cfg)
        assert not state.get("deliberation_path")
        assert state.get("authority_tier") == "autonomous"
        assert not snap.interrupts
        assert state["bluf"].startswith("결론")

    _run(go())


# ── ④ ContextVar 전파(서브그래프 내부 LLM 이 stub 를 실제로 받음) ──────────────────
def test_contextvar_judge_propagates_into_adr_payload():
    """stub_judge 가 낸 고유 마커가 최종 adr_payload(decision)에 반영 → 서브그래프 내부
    synthesize 가 use_judge_caller(stub)를 실제로 사용했음을 falsify."""

    async def go():
        g = _graph()
        cfg = {"configurable": {"thread_id": "ctxvar-1"}}
        with _ctx(low_conf_gemma, judge=low_conf_judge):
            state = await g.ainvoke({"directive": _DELIBERATE}, cfg)
        adr = state.get("adr_payload") or {}
        # 마커가 ADR 직렬화(decision 필드)에 존재 → ContextVar 가 nested 노드에 전파됨.
        assert _JUDGE_MARKER in str(adr), (
            f"judge marker missing from adr_payload: {adr}"
        )
        # 갱신 conf 도 judge 값(0.40)으로 cycle 키에 반영(재진입 입력).
        assert state.get("adr_payload", {}).get("confidence") == 0.40

    _run(go())
