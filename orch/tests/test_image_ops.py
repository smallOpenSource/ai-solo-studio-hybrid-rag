"""P7b — orch/image_ops.py 캐스케이드 falsifiable unit (순수 로직·orch env·PIL 불필요).

★anti-green-theater(T4): "tier1/2 불충분"을 명명 술어 X1..X4 로 결정론 정의하고, **실패-X 입력을
주입하면 escalate_to_tier3 가 결정론적으로 발화**함을 입증(negative-mutation·각 술어 양/음·정수 경계).
실제 이미지 처리(art env)는 별도(test_image_ops_artenv 가 있다면 거기·여기선 순수 로직만).
"""

from __future__ import annotations

from orch.image_ops import AssetMeta, AssetSpec, escalate_to_tier3, meets_quality

# 모든 술어를 만족하는 기준 메타/사양.
_OK_META = AssetMeta(
    width=1024,
    height=1024,
    has_alpha=True,
    alpha_ratio=0.3,
    slot_count=4,
    name="hero_01.png",
)
_SPEC = AssetSpec(
    min_width=512,
    min_height=512,
    require_alpha=True,
    min_alpha_ratio=0.1,
    required_slots=4,
    name_pattern=r"hero_\d+\.png$",
)


def test_meets_quality_all_pass():
    ok, reason = meets_quality(_OK_META, _SPEC)
    assert ok and reason == ""
    assert escalate_to_tier3(_OK_META, _SPEC) is False  # 만족 → tier3 미발화


# --- X1 min-resolution: 양/음 경계 -------------------------------------------


def test_x1_fail_low_resolution_escalates():
    low = AssetMeta(
        width=256,
        height=256,
        has_alpha=True,
        alpha_ratio=0.3,
        slot_count=4,
        name="hero_01.png",
    )
    ok, reason = meets_quality(low, _SPEC)
    assert not ok and reason.startswith("X1")
    assert escalate_to_tier3(low, _SPEC) is True  # 결정론 발화


def test_x1_boundary_equal_passes():
    edge = AssetMeta(
        width=512,
        height=512,
        has_alpha=True,
        alpha_ratio=0.3,
        slot_count=4,
        name="hero_01.png",
    )
    assert meets_quality(edge, _SPEC)[0] is True  # >= 경계 통과


# --- X2 transparency ----------------------------------------------------------


def test_x2_fail_no_alpha_escalates():
    opaque = AssetMeta(
        width=1024,
        height=1024,
        has_alpha=False,
        alpha_ratio=0.0,
        slot_count=4,
        name="hero_01.png",
    )
    ok, reason = meets_quality(opaque, _SPEC)
    assert not ok and reason.startswith("X2")
    assert escalate_to_tier3(opaque, _SPEC) is True


def test_x2_fail_low_alpha_ratio_escalates():
    thin = AssetMeta(
        width=1024,
        height=1024,
        has_alpha=True,
        alpha_ratio=0.01,
        slot_count=4,
        name="hero_01.png",
    )
    ok, reason = meets_quality(thin, _SPEC)
    assert not ok and reason.startswith("X2")


def test_x2_not_required_ignores_alpha():
    spec = AssetSpec(
        min_width=512, min_height=512, require_alpha=False, required_slots=1
    )
    opaque = AssetMeta(width=1024, height=1024, has_alpha=False, slot_count=1, name="x")
    assert meets_quality(opaque, spec)[0] is True


# --- X3 슬롯 정수 경계(N -> N-1 -> escalate) ----------------------------------


def test_x3_fail_slot_shortfall_escalates():
    short = AssetMeta(
        width=1024,
        height=1024,
        has_alpha=True,
        alpha_ratio=0.3,
        slot_count=3,
        name="hero_01.png",
    )  # N-1
    ok, reason = meets_quality(short, _SPEC)
    assert not ok and reason.startswith("X3")
    assert escalate_to_tier3(short, _SPEC) is True


def test_x3_exact_slots_pass():
    exact = AssetMeta(
        width=1024,
        height=1024,
        has_alpha=True,
        alpha_ratio=0.3,
        slot_count=4,
        name="hero_01.png",
    )
    assert meets_quality(exact, _SPEC)[0] is True


# --- X4 naming 정규식 ---------------------------------------------------------


def test_x4_fail_naming_escalates():
    misnamed = AssetMeta(
        width=1024,
        height=1024,
        has_alpha=True,
        alpha_ratio=0.3,
        slot_count=4,
        name="villain_01.png",
    )
    ok, reason = meets_quality(misnamed, _SPEC)
    assert not ok and reason.startswith("X4")
    assert escalate_to_tier3(misnamed, _SPEC) is True


def test_x4_no_pattern_ignores_name():
    spec = AssetSpec(min_width=1, min_height=1, required_slots=1, name_pattern=None)
    assert (
        meets_quality(
            AssetMeta(width=10, height=10, slot_count=1, name="anything"), spec
        )[0]
        is True
    )


# --- negative-mutation: 술어 순서·첫 실패 우선 --------------------------------


def test_first_failure_reported_deterministic():
    # X1·X3 둘 다 실패 → 술어 순서상 X1 이 먼저 보고(결정론).
    bad = AssetMeta(
        width=10,
        height=10,
        has_alpha=True,
        alpha_ratio=0.3,
        slot_count=1,
        name="hero_01.png",
    )
    ok, reason = meets_quality(bad, _SPEC)
    assert not ok and reason.startswith("X1")


def test_escalate_is_strict_negation_of_meets():
    # 무작위가 아닌 결정론: escalate == not meets_quality (각 케이스).
    for meta in (
        _OK_META,
        AssetMeta(
            width=1,
            height=1,
            slot_count=4,
            has_alpha=True,
            alpha_ratio=0.3,
            name="hero_1.png",
        ),
        AssetMeta(
            width=1024,
            height=1024,
            slot_count=2,
            has_alpha=True,
            alpha_ratio=0.3,
            name="hero_1.png",
        ),
    ):
        ok, _ = meets_quality(meta, _SPEC)
        assert escalate_to_tier3(meta, _SPEC) is (not ok)
