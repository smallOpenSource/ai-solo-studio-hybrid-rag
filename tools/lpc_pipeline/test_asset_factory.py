"""US-AF4 에셋공장 비파괴 검증(독립 스크립트·bare pytest 금지).

add_role(deploy=False·검증만)·add_zone(rebuild=False·맵 미변경)으로 팩토리 4-에이전트
오케스트레이션을 입증한다. 실파일(office.tmj/roles_v2) 0변경. 실행: crawl env python.
"""

from __future__ import annotations

import sys

import asset_factory as af
import build_us3 as b3
import build_us4 as b4

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS: " if cond else "  FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def main() -> int:
    print("[ArtDirector] 스타일 스펙")
    spec = af.ArtDirector().style_spec()
    check(spec["cell"] == 32 and spec["max_colors"] == 15, f"스펙 32px·≤15색: {spec}")

    print("[ArtDirector] 신규 의상색 배정(기존 16과 farthest)")
    cands = ["#7a4a2a", "#2a6a4a", "#6a2a7a", "#a05020", "#3a6a8a"]
    picked = af.ArtDirector().assign_costume(cands, list(b3.ROLES))
    check(picked in cands, f"farthest 후보 선택: {picked}")

    print("[add_role] 신규 직원 'qa_sample' 생성→검증(deploy=False·비파괴)")
    res = af.add_role("qa_sample", picked, "auburn", "glasses", deploy=False)
    check(res["colors_ok"], f"색수 {res['n_colors']} ≤15")
    check(res["deployed"] is False, "deploy=False → 배치 안 함(roles_v2 미변경)")
    print(
        f"    → 의상색 {res['costume_hex']} · 기존과 min-ΔE {res['min_delta_e']} (식별 {res['identity_ok']})"
    )

    print("[add_zone] ★단일룸 전환으로 후속 보류 → NotImplementedError(재설계 §12 M1)")
    # build_us4가 세로밴드 누적→단일 개방형 룸(고정 MAP_H·리터럴 SEATS)으로 재구성되어
    # 밴드 append 전제가 소멸 → add_zone은 NotImplementedError를 던진다(전역 BANDS 미변경).
    try:
        af.add_zone(
            "meeting",
            "Meeting Room",
            ["ceo", "strategist"],
            b4.RUG_KEYS[0],
            height=5,
            rebuild=False,
        )
        check(False, "add_zone이 NotImplementedError를 던져야 함")
    except NotImplementedError:
        check(True, "add_zone = 단일룸 전환 후속 보류(NotImplementedError) ✓")

    print("[self-diffusion] R&D 스텁은 NotImplementedError")
    try:
        af.PixelArtist().generate_self_diffusion()
        check(False, "스텁이 예외를 던져야 함")
    except NotImplementedError:
        check(True, "SD-piXL 자체생성 = R&D 스텁(NotImplementedError) ✓")

    print(
        "\n"
        + (
            "GREEN: 에셋공장 4-에이전트 동작 입증(비파괴)"
            if not FAILS
            else f"RED: {FAILS}"
        )
    )
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
