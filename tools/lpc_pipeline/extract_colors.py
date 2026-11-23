"""역할별 3D 카툰풍 초상에서 ROI 기반 의상색·머리색 추출 (★핵심 신규 모듈).

절차(AC 2):
  (a) 배경 마스킹 — 초상은 이미 알파 채널 보유(corners α=0)이라 알파>임계로 직접
      마스킹. 알파 폴백 실패 시 코너색 기반 플러드필 보조(_flood_bg).
  (b) ROI 영역분할 — 알파 바운딩박스 기준 세로 비율로 머리(상단)·피부(얼굴)·
      의상(몸통/하단) 밴드 분리.
  (c) 영역별 k-means(k=5) — ★전체이미지 단일 k-means 금지. 의상 밴드/머리 밴드
      각각에서 주색 군집을 뽑아 피부·배경 오염을 배제하고 dominant을 고른다.
  (d) 추출 실패 → 수동 폴백 색 사전(FALLBACK) 사용.

★결정론: k-means 고정 시드(kmeans.SEED). 같은 초상 → 같은 색.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

from kmeans import dominant_colors, luminance, to_hex

ROLES_DIR = "/opt/studio/web/public/assets/characters/roles"

# 8 portrait-backed 역할(고정 순서 — 몽타주/출력 일관).
ROLES = [
    "ceo",
    "dev",
    "designer",
    "accountant",
    "marketer",
    "planner",
    "secretary",
    "strategist",
]

# (d) 수동 폴백 색 사전 — 추출 실패 시에만 사용(role → (shirt_hex, hair_hex)).
# 초상 육안 기반 보수값(검정정장/계열색).
FALLBACK: dict[str, tuple[str, str]] = {
    "ceo": ("#23222a", "#3a2a20"),  # 검정 정장 / 갈색 머리.
    "dev": ("#27416b", "#3a2a26"),  # 파란 셔츠 / 흑갈 머리.
    "designer": ("#1a2c5a", "#2a1830"),  # 짙은 파랑 / 어두운 머리.
    "accountant": ("#3a3a44", "#2a2018"),
    "marketer": ("#8a3a3a", "#3a2820"),
    "planner": ("#2a5a5a", "#2a2018"),
    "secretary": ("#3a6068", "#5a2a2a"),
    "strategist": ("#2a3a5a", "#2a2018"),
}

ALPHA_THRESH = 64  # 안티앨리어싱 가장자리 배제용(반투명 차단).
SKIN_DIST = 48.0  # 피부 기준색에서 이 거리 이내면 의상에서 제외.
OUTLINE_LUM = 24.0  # 이 휘도 미만은 검은 윤곽선/극단그림자로 보고 배제.
SHADE_MERGE_DIST = 70.0  # 의상 base와 색상 유사·더 밝은 군집을 병합(3톤 base 보정).
HAIR_SKIN_DIST = 120.0  # 머리 추출용 강한 피부배제(이마·얼굴 살색 광역 제거).

# 대표 피부 톤(LPC body_light 얼굴 평균 근방) — 의상 밴드에서 팔/손 살색 배제용.
_SKIN_REF = np.array([224, 170, 140], dtype=np.float64)


def load_portrait(role: str) -> Image.Image:
    """역할 초상 PNG를 RGBA로 로드(읽기 전용)."""
    return Image.open(os.path.join(ROLES_DIR, f"{role}.png")).convert("RGBA")


def alpha_mask(arr: np.ndarray, thresh: int = ALPHA_THRESH) -> np.ndarray:
    """알파>임계 불투명 마스크(H×W bool). 배경 제거 1차 수단."""
    return arr[..., 3] > thresh


def _flood_bg(arr: np.ndarray, tol: int = 24) -> np.ndarray:
    """코너색 기반 배경 플러드필 보조(알파가 없는 경우 폴백).

    네 코너의 색과 tol 이내인 외곽 연결 영역을 배경으로 본다(BFS).
    반환: 전경(=비배경) 마스크 H×W bool.
    """
    h, w = arr.shape[:2]
    rgb = arr[..., :3].astype(np.int32)
    bg_ref = rgb[0, 0]
    visited = np.zeros((h, w), dtype=bool)
    from collections import deque

    dq: deque[tuple[int, int]] = deque()
    for x in range(w):
        dq.append((0, x))
        dq.append((h - 1, x))
    for y in range(h):
        dq.append((y, 0))
        dq.append((y, w - 1))
    while dq:
        y, x = dq.popleft()
        if visited[y, x]:
            continue
        if int(np.abs(rgb[y, x] - bg_ref).sum()) > tol:
            continue
        visited[y, x] = True
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx]:
                dq.append((ny, nx))
    return ~visited


def foreground_mask(arr: np.ndarray) -> np.ndarray:
    """전경 마스크 산출: 알파 우선, 알파가 무의미하면 플러드필 폴백."""
    a_mask = alpha_mask(arr)
    if a_mask.sum() >= 16:  # 알파가 유효한 분리를 줌.
        return a_mask
    return _flood_bg(arr)


def _band(mask: np.ndarray, y0: int, span: int, lo: float, hi: float) -> np.ndarray:
    """세로 비율 [lo,hi) 밴드로 마스크를 제한(H×W bool)."""
    a = max(0, int(y0 + span * lo))
    b = int(y0 + span * hi)
    m = mask.copy()
    m[:a] = False
    m[b:] = False
    return m


def _clean_px(px: np.ndarray, skin_dist: float = SKIN_DIST) -> np.ndarray:
    """밴드 픽셀에서 검은 윤곽선(저휘도)·피부색을 배제(주색 추출 정확도↑).

    픽셀 아트 초상은 검정 outline이 면적상 dominant 군집을 형성하므로
    OUTLINE_LUM 미만을 제거해야 실제 의상/머리 색이 드러난다. 머리 추출은
    이마·얼굴 피부 오염이 크므로 더 큰 skin_dist(HAIR_SKIN_DIST)를 써 살색을
    넓게 배제한다(피부 skd<90 vs 모발 skd>200 실측 분리).
    """
    px = px.astype(np.float64)
    if len(px) == 0:
        return px
    keep = luminance(px) >= OUTLINE_LUM
    d = np.abs(px - _SKIN_REF).sum(axis=1)
    keep &= d > skin_dist
    return px[keep]


def extract_role_colors(role: str) -> dict:
    """한 역할 초상에서 의상색·머리색 추출.

    반환 dict: role, shirt_rgb, hair_rgb, shirt_hex, hair_hex, source
      source = 'roi'(추출 성공) 또는 'fallback'(폴백 사전).
    """
    arr = np.array(load_portrait(role)).astype(np.float64)
    arr_u8 = np.array(load_portrait(role))
    fg = foreground_mask(arr_u8)
    ys, xs = np.where(fg)
    if len(ys) < 32:
        return _fallback_result(role)
    y0, y1 = int(ys.min()), int(ys.max())
    span = y1 - y0 + 1

    hair_rgb = _extract_hair(arr, fg, y0, span)
    shirt_rgb = _extract_shirt(arr, fg, y0, span)
    if hair_rgb is None or shirt_rgb is None:
        return _fallback_result(role)
    return {
        "role": role,
        "shirt_rgb": shirt_rgb,
        "hair_rgb": hair_rgb,
        "shirt_hex": to_hex(shirt_rgb),
        "hair_hex": to_hex(hair_rgb),
        "source": "roi",
    }


def _extract_hair(arr, fg, y0, span) -> np.ndarray | None:
    """머리 밴드(상단 0–28%)에서 outline·피부 제거 후 최대 주색 = 머리색.

    윤곽선 제거 뒤엔 실제 모발색이 dominant(ceo 갈색·dev 흑갈 실증).
    """
    band = _band(fg, y0, span, 0.0, 0.22)
    px = _clean_px(arr[band][..., :3], skin_dist=HAIR_SKIN_DIST)
    doms = dominant_colors(px, k=5)
    if not doms:
        return None
    return doms[0][0]  # 면적 최대 군집 = 모발 본색(피부 광역배제 후).


def _extract_shirt(arr, fg, y0, span) -> np.ndarray | None:
    """의상 밴드(몸통 45–95%)에서 outline·피부 제거 후 의상 base색.

    셔츠는 shadow/base/highlight가 별개 군집으로 쪼개지므로, 최대 군집을 base
    후보로 잡고 색상이 유사하며 더 밝은 군집이 있으면 평균내 base를 살짝 끌어올린다
    (그림자 군집만 잡혀 과도하게 어두워지는 것 방지·3톤 램프 중앙 정렬).
    """
    band = _band(fg, y0, span, 0.45, 0.95)
    px = _clean_px(arr[band][..., :3])
    doms = dominant_colors(px, k=5)
    if not doms:
        return None
    base = doms[0][0]
    base_lum = float(luminance(base))
    # 같은 색상(거리 가까움)·더 밝은 큰 군집을 찾아 base와 가중 평균.
    for cen, n in doms[1:3]:
        if float(luminance(cen)) > base_lum and _hue_close(base, cen):
            return (base + cen) / 2.0
    return base


def _hue_close(a: np.ndarray, b: np.ndarray) -> bool:
    """두 색이 휘도차를 무시하고 같은 색상 계열인지(채도 정규화 거리)."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    na = a / (a.sum() + 1e-6)
    nb = b / (b.sum() + 1e-6)
    return (
        float(np.abs(na - nb).sum()) < 0.18
        or float(np.abs(a - b).sum()) < SHADE_MERGE_DIST
    )


def _fallback_result(role: str) -> dict:
    """폴백 사전에서 결과 구성."""
    shirt_hex, hair_hex = FALLBACK.get(role, ("#404040", "#2a2018"))
    return {
        "role": role,
        "shirt_rgb": _hex_to_rgb(shirt_hex),
        "hair_rgb": _hex_to_rgb(hair_hex),
        "shirt_hex": shirt_hex,
        "hair_hex": hair_hex,
        "source": "fallback",
    }


def _hex_to_rgb(h: str) -> np.ndarray:
    """'#rrggbb' → float64 RGB 배열."""
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)
