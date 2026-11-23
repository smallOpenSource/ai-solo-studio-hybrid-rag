"""US-2 게이트 독립 단언 스크립트 (★bare pytest 금지 — 직접 실행).

실행: /opt/conda/envs/one_man_corp-crawl/bin/python test_gate.py
  - 순수 numpy 단언만(루프/async/네트워크 0 → 엔진 테스트 hang 무관·안전).
  - assert 기반. 전건 통과 → sys.exit(0), 실패 → sys.exit(1).

검증 항목:
  1) ★CIEDE2000 정확성 — Sharma et al.(2005) 공개 레퍼런스 쌍.
  2) 색수 게이트 합성픽스처(15색 PASS·16색 FAIL).
  3) ΔE 매트릭스 대칭성·대각 0.
  4) 멤버십 경계(팔레트 내/외).
  5) sRGB→Lab 기지값(흰/검/원색) 정합.
"""

from __future__ import annotations

import sys

import numpy as np

import gate


def _check(cond: bool, msg: str) -> None:
    """단언 헬퍼 — 실패 메시지를 명확히."""
    assert cond, msg
    print(f"  PASS: {msg}")


def test_ciede2000_reference() -> None:
    """★Sharma et al. 테스트데이터 1쌍으로 CIEDE2000 정확성 검증(tol 0.01)."""
    lab1 = np.array([50.0, 2.6772, -79.7751])
    lab2 = np.array([50.0, 0.0, -82.7485])
    got = gate.delta_e_ciede2000(lab1, lab2)
    expected = 2.0425
    _check(
        abs(got - expected) < 0.01,
        f"CIEDE2000 Sharma 레퍼런스: got={got:.4f} expected={expected} (Δ={abs(got - expected):.5f})",
    )


def test_ciede2000_extra_references() -> None:
    """Sharma 추가 레퍼런스 2쌍(회전항 RT 경계·고채도)으로 강건성 보강."""
    cases = [
        # (lab1, lab2, expected ΔE2000) — Sharma et al. Table 1 발췌.
        ((50.0, 2.5, 0.0), (50.0, 0.0, -2.5), 4.3065),  # 색상차 + RT 영역.
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),  # 광역 색차.
    ]
    for lab1, lab2, exp in cases:
        got = gate.delta_e_ciede2000(np.array(lab1), np.array(lab2))
        _check(
            abs(got - exp) < 0.01,
            f"CIEDE2000 추가: {lab1}↔{lab2} got={got:.4f} exp={exp} (Δ={abs(got - exp):.5f})",
        )


def test_ciede2000_identity() -> None:
    """동일 색 ΔE2000 = 0(대각 보장)."""
    lab = np.array([54.3, -12.0, 33.0])
    _check(abs(gate.delta_e_ciede2000(lab, lab)) < 1e-9, "동일색 ΔE2000=0")


def test_srgb_to_lab_known() -> None:
    """sRGB→Lab 기지값: 흰색 L≈100·검정 L≈0·중성회색 a,b≈0."""
    white = gate.srgb_to_lab(np.array([255.0, 255.0, 255.0]))
    black = gate.srgb_to_lab(np.array([0.0, 0.0, 0.0]))
    gray = gate.srgb_to_lab(np.array([128.0, 128.0, 128.0]))
    _check(
        abs(white[0] - 100.0) < 0.1 and abs(white[1]) < 0.1 and abs(white[2]) < 0.1,
        f"흰색 Lab≈(100,0,0): {white.round(3).tolist()}",
    )
    _check(abs(black[0]) < 1e-6, f"검정 L≈0: {black.round(3).tolist()}")
    _check(
        abs(gray[1]) < 0.1 and abs(gray[2]) < 0.1,
        f"중성회색 a,b≈0: {gray.round(3).tolist()}",
    )


def test_color_count_gate() -> None:
    """색수 게이트 합성픽스처: 15색 PASS·16색 FAIL(비투명 고유색 카운트)."""
    fix15 = _synth_sprite(15)
    fix16 = _synth_sprite(16)
    n15 = len(gate.unique_opaque_colors(fix15))
    n16 = len(gate.unique_opaque_colors(fix16))
    _check(
        n15 == 15 and n15 <= gate.MAX_COLORS,
        f"15색 픽스처 색수={n15} ≤{gate.MAX_COLORS} PASS",
    )
    _check(
        n16 == 16 and n16 > gate.MAX_COLORS,
        f"16색 픽스처 색수={n16} >{gate.MAX_COLORS} FAIL감지",
    )


def test_matrix_symmetry() -> None:
    """ΔE 매트릭스 대칭·대각 0(합성 Lab 4색)."""
    labs = [
        gate.srgb_to_lab(np.array(c, dtype=np.float64))
        for c in [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]
    ]
    mat = gate.delta_e_matrix(labs)
    _check(np.allclose(mat, mat.T), "ΔE 매트릭스 대칭")
    _check(np.allclose(np.diag(mat), 0.0), "ΔE 매트릭스 대각=0")
    _check((mat[np.triu_indices(4, 1)] > 0).all(), "상이색 ΔE>0")


def test_membership_boundary() -> None:
    """멤버십 경계: 팔레트 내 색=1.0·팔레트 외 색=0.0."""
    palette = np.array([[10, 20, 30], [200, 100, 50]], dtype=np.uint8)
    inside = [np.array([10.0, 20.0, 30.0]), np.array([201.0, 101.0, 49.0])]  # tol 8 내.
    outside = [np.array([120.0, 120.0, 120.0])]  # 어느 팔레트색과도 멀다.
    _check(gate.membership_ratio(inside, palette) == 1.0, "팔레트 내 원시색 멤버십=1.0")
    _check(
        gate.membership_ratio(outside, palette) == 0.0, "팔레트 외 원시색 멤버십=0.0"
    )
    _check(gate.membership_ratio([], palette) == 0.0, "빈 입력 멤버십=0.0")


def test_distinct_count() -> None:
    """distinct 카운트: 동일 3색 → 1, 광역 상이 3색 → 3."""
    same = [gate.srgb_to_lab(np.array([100.0, 100.0, 100.0]))] * 3
    diff = [
        gate.srgb_to_lab(np.array(c, dtype=np.float64))
        for c in [(220, 20, 20), (20, 220, 20), (20, 20, 220)]
    ]
    _check(gate.distinct_count(same, gate.DELTA_E_IDENTIFY) == 1, "동일3색 distinct=1")
    _check(gate.distinct_count(diff, gate.DELTA_E_IDENTIFY) == 3, "상이3색 distinct=3")


def _synth_sprite(n_colors: int) -> np.ndarray:
    """n_colors개 고유 비투명색을 가진 합성 RGBA 스프라이트(결정론)."""
    rng = np.random.default_rng(7)
    palette = rng.integers(0, 256, size=(n_colors, 3), dtype=np.int64)
    # 각 색을 충분히 분리(고유색 보장) — 인덱스*간격으로 결정.
    palette = (np.arange(n_colors)[:, None] * np.array([13, 7, 5]) + 3) % 256
    side = 8
    arr = np.zeros((side, side, 4), dtype=np.uint8)
    flat = arr.reshape(-1, 4)
    for i in range(n_colors):  # 각 고유색 1픽셀 이상 배치.
        flat[i, :3] = palette[i]
        flat[i, 3] = 255
    return arr


def main() -> None:
    """전체 단언 실행. 실패 시 AssertionError → sys.exit(1)."""
    tests = [
        ("CIEDE2000 Sharma 레퍼런스", test_ciede2000_reference),
        ("CIEDE2000 추가 레퍼런스", test_ciede2000_extra_references),
        ("CIEDE2000 동일색=0", test_ciede2000_identity),
        ("sRGB→Lab 기지값", test_srgb_to_lab_known),
        ("색수 게이트(15/16)", test_color_count_gate),
        ("ΔE 매트릭스 대칭성", test_matrix_symmetry),
        ("멤버십 경계", test_membership_boundary),
        ("distinct 카운트", test_distinct_count),
    ]
    failed = 0
    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
    print()
    if failed:
        print(f"FAIL: {failed}개 테스트 실패")
        sys.exit(1)
    print("GREEN: 모든 단언 통과")
    sys.exit(0)


if __name__ == "__main__":
    main()
