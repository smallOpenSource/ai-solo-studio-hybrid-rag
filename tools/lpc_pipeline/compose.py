"""LPC 레이어 알파 합성 → idle 프레임(row10/frame0) 추출 (비파괴·읽기전용 입력).

확정 사실(재측정 불요):
  - LPC 시트 = 832×1344, 64px 프레임, 13열×21행.
  - idle-down = row 10, frame 0 → crop (x=0, y=640, 64, 64).
  - 합성 순서(알파 over, 아래→위): body_light → longsleeve → pants_male → hair.
    (web/src lpcCharacter.ts LAYER_ORDER=body→pants→shirt→hair와 시각 동일.)
★preview_3roles.png가 이 합성+추출이 동작함을 입증한 참조 PoC.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

# 입력 LPC 레이어 디렉터리(읽기 전용 — 절대 수정 금지).
CHARS_DIR = "/opt/studio/web/public/assets/characters"

FRAME = 64
SHEET_W, SHEET_H = 832, 1344
IDLE_ROW = 10  # WALK_ROW.down (정면).
IDLE_COL = 0  # frame0 = 정지 포즈.
IDLE_BOX = (
    IDLE_COL * FRAME,
    IDLE_ROW * FRAME,
    (IDLE_COL + 1) * FRAME,
    (IDLE_ROW + 1) * FRAME,
)


def load_layer(name: str) -> Image.Image:
    """레이어 PNG를 RGBA 832×1344로 로드(LA/P 모드 통일·읽기 전용)."""
    path = os.path.join(CHARS_DIR, f"{name}.png")
    img = Image.open(path).convert("RGBA")
    if img.size != (SHEET_W, SHEET_H):
        raise ValueError(f"{name}: 예상 {SHEET_W}×{SHEET_H}, 실제 {img.size}")
    return img


def alpha_over(base: Image.Image, top: Image.Image) -> Image.Image:
    """top을 base 위에 알파 합성(둘 다 RGBA 동일크기·정렬 동일)."""
    return Image.alpha_composite(base, top)


def compose_sheet(layer_names: list[str]) -> Image.Image:
    """레이어 이름들을 아래→위 순서로 알파 합성한 전체 시트(RGBA) 반환.

    layer_names[0]이 최하단(body). 정렬은 모두 동일(0,0 스택)이라 어긋남 0.
    """
    if not layer_names:
        raise ValueError("layer_names 비어 있음")
    out = load_layer(layer_names[0])
    for name in layer_names[1:]:
        out = alpha_over(out, load_layer(name))
    return out


def extract_idle(sheet: Image.Image) -> Image.Image:
    """합성 시트에서 idle(row10/frame0) 64×64 프레임을 잘라낸다(RGBA)."""
    return sheet.crop(IDLE_BOX)


def content_bbox(
    frame_rgba: Image.Image, alpha_thresh: int = 16
) -> tuple[int, int, int, int]:
    """프레임 내 불투명 픽셀의 바운딩박스 (x0,y0,x1,y1) 반파(half-open).

    캐릭터 높이/폭 산출용. 빈 프레임이면 전체 박스 반환(방어).
    """
    a = np.array(frame_rgba)[..., 3]
    ys, xs = np.where(a > alpha_thresh)
    if len(xs) == 0:
        return (0, 0, frame_rgba.width, frame_rgba.height)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def compose_idle(layer_names: list[str]) -> Image.Image:
    """레이어 합성→idle 추출을 한 번에(편의 함수). RGBA 64×64."""
    return extract_idle(compose_sheet(layer_names))
