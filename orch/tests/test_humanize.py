"""humanize_korean 단위 — 한국어 가드·의미보존(stub)·과윤문 롤백·실패 비차단·결정론(caller 주입).

모델 미호출(stub caller) — 라이브 모델 검증은 verify 스크립트가 별도 수행.
"""

from orch.humanize import _change_rate, _korean_ratio, humanize_korean


def test_empty_returns_original():
    r = humanize_korean("")
    assert r["text"] == "" and r["applied"] is False and r["note"] == "empty"


def test_non_korean_skipped_without_model_call():
    calls = []

    def caller(p, t, m):
        calls.append(p)
        return "SHOULD NOT BE USED"

    src = "ClickQuest: upgrade1 cost 10, upgrade2 cost 20"
    r = humanize_korean(src, caller=caller)
    assert r["applied"] is False and r["note"] == "not-korean"
    assert r["text"] == src and not calls  # 비한국어 → 모델 미호출


def test_korean_applied_uses_caller_output():
    src = "이 기술은 매우 혁신적이며, 효율성을 높일 수 있다. 결론적으로 주목할 만하다."
    cleaned = "이 기술은 효율을 높인다. 쓸 만하다."

    def caller(p, t, m):
        assert "룰북" in p and src in p  # 룰북 + 원문 주입
        return cleaned

    r = humanize_korean(src, caller=caller)
    assert r["applied"] is True and r["text"] == cleaned
    assert 0.0 < r["change_rate"] <= 1.0 and r["note"] == "ok"


def test_over_edit_rollback_preserves_original():
    src = "이 기술은 효율을 높인다. 사용자 경험을 개선한다. 비용을 절감한다."

    def caller(p, t, m):
        return "완전히 다른 내용 전혀 무관한 텍스트입니다 한국어 롤백 대상"

    r = humanize_korean(src, caller=caller)
    assert r["applied"] is False and r["note"] == "over-edit-rollback"
    assert r["text"] == src


def test_model_failure_returns_original():
    src = "이 기술은 매우 혁신적이며 효율성을 높일 수 있다."

    def caller(p, t, m):
        raise RuntimeError("boom")

    r = humanize_korean(src, caller=caller)
    assert r["applied"] is False and r["text"] == src and r["note"] == "call-failed"


def test_code_fence_stripped():
    src = "이 기술은 매우 혁신적이며 효율성을 높일 수 있다. 결론적으로 좋다."

    def caller(p, t, m):
        return "```\n이 기술은 효율을 높인다. 좋다.\n```"

    r = humanize_korean(src, caller=caller)
    assert r["applied"] is True and "```" not in r["text"]


def test_helpers():
    assert _korean_ratio("한국어 abc") > 0.3
    assert _korean_ratio("english only 123") == 0.0
    assert _change_rate("abc", "abc") == 0.0
    assert _change_rate("abc", "xyz") > 0.5
