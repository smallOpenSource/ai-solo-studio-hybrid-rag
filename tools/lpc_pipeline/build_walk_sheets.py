"""build_walk_sheets.py — 16역할 LPC 4방향 walk 스프라이트시트 생성(gather.town급).

목적: 맵 캐릭터를 정적 qwen PNG → LPC 4방향 walk 애니로 전환(이미지 생성 0·in-repo LPC 레이어
알파 합성만). LPC Universal Spritesheet(832×1344·13열×21행·64px)에서 **walk 행 8-11**
(up/left/down/right·각 9프레임: col0=정지, col1-8=보행)을 잘라 역할별 832×256 시트로 저장.

입력: web/public/assets/characters/{body_light, *_longsleeve, *_pants_male, hair_*}.png (LPC 파생·CC-BY-SA/GPL).
출력: web/public/assets/characters/roles_walk/{role}.png (16개·832×256).

라이선스: LPC 계승(CC-BY-SA 3.0 / GPL 3.0·CREDITS-LPC.md). 내부 PoC 사용 충족.
식별성: 라벨(이름/역할)이 1차 식별. 스프라이트는 결정론 distinct 조합(NO PURPLE — magenta 제외).

실행: python tools/lpc_pipeline/build_walk_sheets.py  (repo 루트에서)
"""

from __future__ import annotations

import os

from PIL import Image

BASE = "web/public/assets/characters/"
OUT = "web/public/assets/characters/roles_walk/"
FR = 64  # LPC 프레임 크기.
WALK_ROW_START = 8  # 행 8=up, 9=left, 10=down, 11=right.
WALK_ROW_END = 12  # exclusive(8~11 = 4행).

# 16역할(ROLE_KEYS 순서와 동일) — sceneLogic.ROLE_KEYS와 1:1.
ROLES = [
    "ceo",
    "strategist",
    "cfo",
    "legal",
    "pm",
    "planner",
    "secretary",
    "hr",
    "dev",
    "devops",
    "data",
    "accountant",
    "designer",
    "marketer",
    "sales",
    "cs",
]

# 가용 레이어(in-repo) — ★NO PURPLE: magenta_pants 제외.
SHIRTS = ["teal", "white", "brown", "maroon"]
PANTS = ["red", "teal", "white"]
HAIR = ["brown", "black"]


def _combos() -> list[tuple[str, str, str]]:
    """결정론 distinct (shirt, pants, hair) 조합 목록(hair→shirt→pants 순·24개)."""
    out: list[tuple[str, str, str]] = []
    for h in HAIR:
        for s in SHIRTS:
            for p in PANTS:
                out.append((s, p, h))
    return out


def _compose(layers: list[str]) -> Image.Image:
    """LPC 레이어들을 알파 합성해 full 832×1344 시트를 만든다(비파괴·읽기전용 입력)."""
    sheet: Image.Image | None = None
    for name in layers:
        im = Image.open(os.path.join(BASE, f"{name}.png")).convert("RGBA")
        sheet = im if sheet is None else Image.alpha_composite(sheet, im)
    assert sheet is not None
    return sheet


def build() -> None:
    os.makedirs(OUT, exist_ok=True)
    recipe = dict(zip(ROLES, _combos()[: len(ROLES)]))
    for role, (shirt, pants, hair) in recipe.items():
        layers = [
            "body_light",
            f"{shirt}_longsleeve",
            f"{pants}_pants_male",
            f"hair_{hair}",
        ]
        sheet = _compose(layers)
        # walk 4행(8~11) crop → 832×256.
        walk = sheet.crop((0, WALK_ROW_START * FR, sheet.width, WALK_ROW_END * FR))
        walk.save(os.path.join(OUT, f"{role}.png"))
        print(f"{role:11s} {layers} -> {walk.size}")
    print(f"\n생성 완료: {len(recipe)}개 → {OUT}")


if __name__ == "__main__":
    build()
