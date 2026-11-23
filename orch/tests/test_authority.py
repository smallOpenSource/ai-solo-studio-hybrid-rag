"""P3 §20 authority — classify 진리표(C1) + assess + decide demote + §20.4a 브리프.

결정론·LLM 0·orch env. τ 진리표는 *규칙 메커닉(배관)* 증명(라이브 gradient 아님·OBSERVE-ONLY).
"""

from __future__ import annotations

from orch.authority import (
    TAU_AUTO,
    TAU_NOTIFY,
    Tier,
    assess,
    classify,
    decide,
    escalate_payload,
    render_escalate_brief,
)


# ── classify 진리표 (≥10 케이스·reversal_cost 축 포함·경계) ──────────────────────
def test_classify_truth_table():
    # 가역·저비용·범위내 → confidence 분기
    assert classify("two_way", "low", 0.80, "in_scope") is Tier.AUTONOMOUS
    assert classify("two_way", "low", 0.70, "in_scope") is Tier.NOTIFY
    assert classify("two_way", "low", 0.50, "in_scope") is Tier.NOTIFY  # 가역이라 진행
    # one-way → 항상 Escalate (confidence 무관)
    assert classify("one_way", "low", 0.95, "in_scope") is Tier.ESCALATE
    assert classify("one_way", "low", 0.50, "in_scope") is Tier.ESCALATE
    # ★reversal_cost=high → Escalate(가역이어도·Architect#2 co-equal)
    assert classify("two_way", "high", 0.90, "in_scope") is Tier.ESCALATE
    # owner_reserved → 항상 Escalate
    assert classify("two_way", "low", 0.99, "owner_reserved") is Tier.ESCALATE
    assert classify("one_way", "high", 0.85, "owner_reserved") is Tier.ESCALATE


def test_classify_tau_boundaries():
    # tie-break: >= 가 이긴다(MINOR#1). 정확히 0.75 → AUTO·0.60 → NOTIFY.
    assert classify("two_way", "low", TAU_AUTO, "in_scope") is Tier.AUTONOMOUS
    assert classify("two_way", "low", TAU_AUTO - 0.001, "in_scope") is Tier.NOTIFY
    assert classify("two_way", "low", TAU_NOTIFY, "in_scope") is Tier.NOTIFY
    assert classify("two_way", "low", TAU_NOTIFY - 0.001, "in_scope") is Tier.NOTIFY


# ── assess derivation (키워드·confidence emit/proxy) ────────────────────────────
def test_assess_owner_reserved_keyword():
    rev, cost, conf, scope = assess("스팀에 출시하자", {})
    assert scope == "owner_reserved"


def test_assess_one_way_keyword():
    rev, _c, _conf, _s = assess("프로덕션 DB 삭제해", {})
    assert rev == "one_way"


def test_assess_high_cost_keyword():
    _r, cost, _conf, _s = assess("전체 마이그레이션 진행", {})
    assert cost == "high"


def test_assess_plain_directive_two_way_in_scope():
    rev, cost, conf, scope = assess("클리커 게임 컨셉 초안 작성", {})
    assert (rev, cost, scope) == ("two_way", "low", "in_scope")


def test_assess_confidence_emit_precedence():
    # state 의 decision_confidence(ceo_route emit) 가 proxy 보다 우선.
    _r, _c, conf, _s = assess("초안", {"decision_confidence": 0.9})
    assert conf == 0.9


def test_assess_confidence_proxy_citation():
    _r, _c, conf_cite, _s = assess("초안", {"knowledge_source": "https://x/y"})
    _r2, _c2, conf_none, _s2 = assess("초안", {})
    assert conf_cite == 0.75 and conf_none == 0.55  # 2값 proxy(OBSERVE-ONLY)


# ── decide: RPI demote + 보수 demote ───────────────────────────────────────────
def test_decide_autonomous_with_citation():
    tier, inputs, _reason = decide(
        "클리커 게임 초안",
        {"decision_confidence": 0.85, "knowledge_source": "https://x"},
    )
    assert tier is Tier.AUTONOMOUS and inputs["confidence"] == 0.85


def test_decide_rpi_demote_no_citation():
    # 가역+고확신이나 citation 없음 → §20.2 RPI demote → NOTIFY.
    tier, _i, reason = decide("클리커 게임 초안", {"decision_confidence": 0.85})
    assert tier is Tier.NOTIFY and "RPI-demote" in reason


def test_decide_conservative_demote_high_impact_verb():
    # one_way 키워드엔 안 걸리지만 고위험 동사 + 고확신 + citation → AUTO였을 것 → 보수 NOTIFY.
    # ('broadcast' 는 _HIGH_IMPACT_VERB 이나 one_way/owner_reserved 키워드는 아님)
    tier, _i, reason = decide(
        "broadcast the update",
        {"decision_confidence": 0.9, "knowledge_source": "https://x"},
    )
    assert tier is Tier.NOTIFY and "conservative-demote" in reason


def test_decide_escalate_owner_reserved():
    tier, _i, _r = decide(
        "스팀에 출시", {"decision_confidence": 0.95, "knowledge_source": "https://x"}
    )
    assert tier is Tier.ESCALATE


# ── §20.4a 브리프 + 페이로드 ────────────────────────────────────────────────────
def test_render_escalate_brief_shape():
    out = render_escalate_brief(
        "광고비 집행",
        "월 100만원 광고 승인 필요.",
        ["진행", "보류"],
        "ROI 추정 양호",
        ["예산 초과 위험"],
        0.82,
        "one_way",
        deadline="06/30",
    )
    assert out.startswith("🔴 [광고비 집행]")
    assert "BLUF:" in out and "확신 0.82" in out and "옵션:" in out and "리스크:" in out


def test_escalate_payload_computed_confidence_replaces_literal():
    _t, inputs, _r = decide("스팀에 출시", {"decision_confidence": 0.83})
    payload = escalate_payload(
        "스팀에 출시",
        {"decision_confidence": 0.83, "artifacts": {"design": "d"}},
        inputs,
    )
    assert payload["confidence"] == 0.83  # ★0.55/0.5 리터럴 아님
    assert (
        payload["tier"] == "escalate"
        and "brief" in payload
        and payload["options"] == ["approve", "reject"]
    )
