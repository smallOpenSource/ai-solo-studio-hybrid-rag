"""orch.image_ops — §17.1 이미지 캐스케이드 tier1/2/3 + falsifiable 품질 게이트 (P7·NET-NEW).

§17.1 캐스케이드(위에서부터): **tier1** Stitch/Figma 결과소비 → **tier2** Python(Pillow/rembg/
OpenCV·``conda -n art``) → **tier3** ComfyUI Qwen-Image(서버 B·last-resort). tier1/2 산출이
*명명된 품질 술어* 를 만족하지 못할 때만 tier3 로 escalate 한다.

★falsifiability (T4·anti-green-theater): "tier1/2 불충분"은 **결정론적 명명 술어**로 정의한다 —
X1 최소해상도 · X2 투명도 · X3 슬롯-정수비교 · X4 naming-정규식. 주관적 "불충분" 금지.
``escalate_to_tier3`` 은 ``meets_quality`` 의 부정이며, 실패-X 입력 주입 시 결정론적으로 발화한다
(unit-test 가 negative-mutation 으로 입증).

설계 분리: ``meets_quality``/``escalate_to_tier3``/``AssetMeta``/``AssetSpec`` 는 **순수 파이썬**
(PIL/cv2 불필요·orch env 에서 테스트). 실제 이미지 처리(``inspect_asset``·``tier2_process``·
``tier1_consume``)는 Pillow/rembg/OpenCV 를 **함수 내부 지연 import**(``conda -n art`` 전용)한다 —
모듈 import 자체는 art env 없이도 성공한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class AssetSpec:
    """요청 품질 사양 (결정론 술어의 입력)."""

    min_width: int = 0
    min_height: int = 0
    require_alpha: bool = False
    min_alpha_ratio: float = 0.0  # 투명 픽셀 비율 하한(0~1)
    required_slots: int = 1  # 요청 슬롯 수(정수)
    name_pattern: Optional[str] = None  # X4 정규식(None=미적용)


@dataclass(frozen=True)
class AssetMeta:
    """산출 에셋 메타 (tier1/2 결과를 술어가 평가하는 단위·순수 데이터)."""

    width: int = 0
    height: int = 0
    has_alpha: bool = False
    alpha_ratio: float = 0.0
    slot_count: int = 1
    name: str = ""


# 명명된 품질 술어 X1..X4 — 각각 (meta, spec) -> (ok, reason). 결정론·주관 0.


def _x1_resolution(meta: AssetMeta, spec: AssetSpec) -> tuple[bool, str]:
    ok = meta.width >= spec.min_width and meta.height >= spec.min_height
    return (
        ok,
        ""
        if ok
        else f"X1 min-resolution: {meta.width}x{meta.height} < {spec.min_width}x{spec.min_height}",
    )


def _x2_transparency(meta: AssetMeta, spec: AssetSpec) -> tuple[bool, str]:
    if not spec.require_alpha:
        return True, ""
    ok = meta.has_alpha and meta.alpha_ratio >= spec.min_alpha_ratio
    return (
        ok,
        ""
        if ok
        else f"X2 transparency: has_alpha={meta.has_alpha} ratio={meta.alpha_ratio:.3f} < {spec.min_alpha_ratio:.3f}",
    )


def _x3_slots(meta: AssetMeta, spec: AssetSpec) -> tuple[bool, str]:
    # 정수 비교(요청 슬롯 수 vs 산출 수)·주관 잔여 0.
    ok = meta.slot_count >= spec.required_slots
    return ok, "" if ok else f"X3 slots: {meta.slot_count} < {spec.required_slots}"


def _x4_naming(meta: AssetMeta, spec: AssetSpec) -> tuple[bool, str]:
    if not spec.name_pattern:
        return True, ""
    ok = re.match(spec.name_pattern, meta.name) is not None
    return ok, "" if ok else f"X4 naming: {meta.name!r} !~ /{spec.name_pattern}/"


_PREDICATES: tuple[Callable[[AssetMeta, AssetSpec], tuple[bool, str]], ...] = (
    _x1_resolution,
    _x2_transparency,
    _x3_slots,
    _x4_naming,
)


def meets_quality(meta: AssetMeta, spec: AssetSpec) -> tuple[bool, str]:
    """모든 명명 술어(X1..X4)를 만족하면 (True, "")·아니면 (False, 첫 실패 사유). 결정론."""
    for pred in _PREDICATES:
        ok, reason = pred(meta, spec)
        if not ok:
            return False, reason
    return True, ""


def escalate_to_tier3(meta: AssetMeta, spec: AssetSpec) -> bool:
    """tier1/2 산출이 품질 술어를 만족 못 하면 True(=tier3 발화). meets_quality 의 부정·결정론."""
    ok, _ = meets_quality(meta, spec)
    return not ok


# --------------------------------------------------------------------- art env 처리
# 아래 함수들은 Pillow/rembg/OpenCV 를 함수 내부 지연 import (conda -n art 전용).
# 모듈 import 자체는 art env 없이도 성공(순수 술어/캐스케이드 로직과 분리).


def inspect_asset(path: str | Path) -> AssetMeta:
    """이미지 파일에서 AssetMeta 추출(width/height/alpha). Pillow 필요(art env)."""
    from PIL import Image  # lazy (art env)

    p = Path(path)
    with Image.open(p) as im:
        w, h = im.size
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        alpha_ratio = 0.0
        if has_alpha:
            alpha = im.convert("RGBA").getchannel("A")
            transparent = sum(1 for px in alpha.getdata() if px < 255)
            alpha_ratio = transparent / float(w * h) if w * h else 0.0
    return AssetMeta(
        width=w,
        height=h,
        has_alpha=bool(has_alpha),
        alpha_ratio=alpha_ratio,
        name=p.name,
    )


def tier1_consume(
    src: str | Path, out: str | Path, *, remove_bg: bool = False
) -> AssetMeta:
    """tier1: Stitch/Figma 결과 에셋 소비(슬라이스/배경제거). Pillow/rembg(art env)."""
    return _tier_process(src, out, remove_bg=remove_bg, resize=None)


def tier2_process(
    src: str | Path,
    out: str | Path,
    *,
    resize: Optional[tuple[int, int]] = None,
    remove_bg: bool = False,
) -> AssetMeta:
    """tier2: Pillow/rembg/OpenCV crop/resize/composite/bg-remove. art env."""
    return _tier_process(src, out, remove_bg=remove_bg, resize=resize)


def _tier_process(
    src: str | Path,
    out: str | Path,
    *,
    remove_bg: bool,
    resize: Optional[tuple[int, int]],
) -> AssetMeta:
    from PIL import Image  # lazy (art env)

    src_p, out_p = Path(src), Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_p) as im:
        im = im.convert("RGBA")
        if resize:
            im = im.resize(resize, Image.LANCZOS)
        if remove_bg:
            im = _remove_bg(im)
        im.save(out_p)
    return inspect_asset(out_p)


def _remove_bg(im: Any) -> Any:
    """rembg 로 배경 제거(art env). rembg 불가 시 Pillow 엣지-플러드필 폴백(crawl env 선례)."""
    try:
        from rembg import remove  # lazy (art env)

        return remove(im)
    except Exception:
        return _floodfill_bg(im)


def _floodfill_bg(im: Any) -> Any:
    """Pillow 엣지 시드 플러드필 배경제거 폴백(rembg 불가 시·결정론·근사)."""
    from PIL import Image, ImageDraw  # lazy

    im = im.convert("RGBA")
    w, h = im.size
    mask = Image.new("L", (w, h), 0)
    seed = im.getpixel((0, 0))
    px = im.load()
    drw = ImageDraw.Draw(mask)
    stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    seen = set()
    tol = 24
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
            continue
        seen.add((x, y))
        r, g, b, _ = px[x, y]
        if abs(r - seed[0]) + abs(g - seed[1]) + abs(b - seed[2]) <= tol:
            drw.point((x, y), fill=255)
            stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
    out = im.copy()
    op = out.load()
    mp = mask.load()
    for yy in range(h):
        for xx in range(w):
            if mp[xx, yy]:
                r, g, b, _ = op[xx, yy]
                op[xx, yy] = (r, g, b, 0)
    return out
