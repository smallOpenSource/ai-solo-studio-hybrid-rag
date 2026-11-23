"""org_graph.finalize 의 humanize 배선 단위 — D(발행 텍스트 윤문 확장).

finalize 의 BLUF 보고(Discord 등 외부 발행되는 두 번째 발행 지점)가 humanize_korean 을
실제로 통과하는지 검증. graph.stage_content 와 동형. **non-vacuous**: humanize 배선을
제거하면 test_finalize_humanizes_korean_bluf 가 깨진다(bluf==원문이 됨).

결정성: org_graph 가 재노출하는 use_caller(graph.py ContextVar) 로 stub 주입 — finalize 의
_call(BLUF 합성)과 humanize 의 caller=_active_caller() 둘 다 동일 stub 이 받는다.
프롬프트에 "룰북" 포함 여부로 두 호출을 분기한다(humanize 프롬프트만 룰북 주입).
"""

from orch.org_graph import finalize, use_caller


def _make_stub(bluf_text: str, humanized_text: str, seen: list[str]):
    def stub(prompt: str, temperature: float, max_tokens: int) -> str:
        if "룰북" in prompt:  # humanize 윤문 호출
            seen.append("humanize")
            return humanized_text
        seen.append("finalize")  # finalize BLUF 합성 호출
        return bluf_text

    return stub


def test_finalize_humanizes_korean_bluf():
    seen: list[str] = []
    bluf = "결론: 슬라이드 퍼즐 게임을 완성했다. 필요행동: Owner 검토 바랍니다. 근거: 디자인과 QA 통과."
    humanized = "결론: 슬라이드 퍼즐을 완성했어요. 필요행동: 검토 부탁드려요. 근거: 디자인·QA 통과."
    state = {
        "artifacts": {"dev": "puzzle done"},
        "routing_trace": ["ceo", "dev_lead"],
        "departments": ["dev"],
        "decision": "",
    }
    with use_caller(_make_stub(bluf, humanized, seen)):
        out = finalize(state)

    assert "humanize" in seen  # 윤문 caller 실제 호출(배선 제거 시 깨짐)
    assert out["bluf"] == humanized  # 윤문본이 BLUF 로 발행
    assert out["shared_facts"]["_finalize"]["bluf"] == humanized  # blackboard 도 윤문본
    assert "humanized" in out["messages"][0]["content"]  # Δ 메시지


def test_finalize_skips_non_korean_bluf():
    seen: list[str] = []
    bluf = "Conclusion: puzzle shipped. Action: please review. Basis: design and QA passed."
    state = {
        "artifacts": {"dev": "puzzle"},
        "routing_trace": ["ceo"],
        "departments": ["dev"],
        "decision": "",
    }
    with use_caller(_make_stub(bluf, "SHOULD NOT BE USED", seen)):
        out = finalize(state)

    assert "humanize" not in seen  # 비한국어 → korean-guard 가 윤문 caller 미호출
    assert out["bluf"] == bluf  # 원문 보존(의미 불변)
    assert out["messages"][0]["content"] == "[finalize] BLUF ready"  # Δ 없음
