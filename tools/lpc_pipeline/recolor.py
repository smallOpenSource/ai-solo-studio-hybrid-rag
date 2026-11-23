"""LPC 레이어 리컬러 — 추출 주색으로 의상/머리색 교체(★3톤 셰이딩 보존).

방법(AC 3):
  중립 베이스 레이어(white_longsleeve 등)는 휘도 램프(shadow→base→highlight)로
  셰이딩을 표현한다. 이 휘도 램프를 타깃색 기반 3톤 램프로 매핑하면 LPC 특유의
  픽셀 셰이딩을 보존한 채 색상만 바꿀 수 있다.

  구현: 타깃 base색에서 shadow(어둡게)·highlight(밝게) 2색을 HSV-유사 명도
  스케일로 파생 → 3색 앵커. 원본 픽셀 휘도를 [lo,hi]에서 정규화한 t로
  shadow→base→highlight 선형보간. 색상(hue)은 타깃, 명암 구조는 원본 유지.

★결정론: 순수 함수(랜덤 0). 같은 (레이어,색) → 같은 출력.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from compose import load_layer
from kmeans import luminance

# 3톤 명도 계수(base=1.0 기준 shadow/highlight 배율).
SHADOW_MUL = 0.55
HIGHLIGHT_MUL = 1.45


def make_ramp(base_rgb: np.ndarray) -> np.ndarray:
    """타깃 base색에서 (shadow, base, highlight) 3색 앵커 생성 (3×3 float).

    명도만 스케일(색상·채도 비율 유지). 255 클램프.
    """
    base = np.asarray(base_rgb, dtype=np.float64)
    shadow = np.clip(base * SHADOW_MUL, 0, 255)
    highlight = np.clip(base * HIGHLIGHT_MUL, 0, 255)
    return np.stack([shadow, base, highlight])  # 3×3.


def _ramp_lookup(t: np.ndarray, anchors: np.ndarray) -> np.ndarray:
    """t∈[0,1] (N,)을 3색 앵커(3×3)로 구간선형보간 → (N×3).

    t<0.5: shadow→base, t>=0.5: base→highlight. 셰이딩 비선형 보존.
    """
    shadow, base, high = anchors
    out = np.empty((len(t), 3), dtype=np.float64)
    lo = t < 0.5
    f_lo = (t[lo] / 0.5)[:, None]
    out[lo] = shadow * (1 - f_lo) + base * f_lo
    f_hi = ((t[~lo] - 0.5) / 0.5)[:, None]
    out[~lo] = base * (1 - f_hi) + high * f_hi
    return out


def recolor_layer(neutral_name: str, target_rgb: np.ndarray) -> Image.Image:
    """중립 레이어를 타깃색 3톤 램프로 리컬러(RGBA 832×1344·셰이딩 보존).

    불투명 픽셀의 휘도를 [p5,p95]로 정규화해 t를 만들고 램프로 색을 입힌다.
    알파는 원본 보존. 투명 픽셀은 손대지 않는다.
    """
    src = load_layer(neutral_name)
    arr = np.array(src).astype(np.float64)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    mask = alpha > 16
    if not mask.any():
        return src
    lum = luminance(rgb[mask])
    lo, hi = np.percentile(lum, 5), np.percentile(lum, 95)
    if hi - lo < 1e-3:
        hi = lo + 1.0
    t = np.clip((lum - lo) / (hi - lo), 0.0, 1.0)
    anchors = make_ramp(target_rgb)
    rgb_new = _ramp_lookup(t, anchors)
    out = arr.copy()
    out[..., :3][mask] = rgb_new
    return Image.fromarray(out.astype(np.uint8), "RGBA")
