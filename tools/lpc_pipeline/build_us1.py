"""US-1 메인 엔트리 — 8역할 LPC 합성·리컬러·마스터팔레트 양자화·출력·몽타주.

파이프라인(AC 1–6):
  1) 각 역할 초상에서 의상색·머리색 추출(extract_colors).
  2) 중립 LPC 레이어를 추출색 3톤 램프로 리컬러(recolor) → body 위 합성.
  3) idle(row10/frame0) 추출 → 바운딩박스 기반 정수배 NEAREST 업스케일(맵 가독).
  4) 8역할 합성물을 단일 마스터 팔레트(≤MASTER_COLORS)로 양자화(응집).
     캐릭터당 색수 ≤MAX_PER_SPRITE(SNES 4bpp) 보장.
  5) 출력: roles_v2_preview/{role}.png + us1_montage.png(라벨 포함).

★결정론: 추출·리컬러·양자화 모두 고정 시드/순수. 동일 입력 → 동일 산출.
실행: /opt/conda/envs/one_man_corp-crawl/bin/python build_us1.py
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw

import compose
import extract_colors as ec
import recolor
from kmeans import count_unique_colors, kmeans, to_hex

OUT_DIR = "/opt/studio/work/lpc/roles_v2_preview"
MONTAGE_PATH = "/opt/studio/work/lpc/us1_montage.png"

# 양자화/사이징 파라미터.
MASTER_COLORS = 24  # 마스터 팔레트 색수(16~32 범위·응집).
MAX_PER_SPRITE = 15  # 캐릭터당 색수 상한(SNES 4bpp 수용기준).
TARGET_H_MIN = 32  # 맵 가독 캐릭터 높이 하한(¾탑다운).
TARGET_H_MAX = 44  # 맵 가독 캐릭터 높이 상한.
TARGET_H_TYPICAL = 40  # 업스케일 시 목표 높이.
PANTS_DARKEN = 0.62  # 바지 = 의상색 어둡게(정장 하의 통일감).

# 리컬러에 쓸 중립 베이스 레이어(휘도 램프 풍부한 흰색 계열).
NEUTRAL_SHIRT = "white_longsleeve"
NEUTRAL_PANTS = "white_pants_male"
NEUTRAL_HAIR = "hair_brown"


def build_role_idle(colors: dict) -> Image.Image:
    """추출색으로 한 역할의 idle 스프라이트(원해상도 64×64 RGBA)를 합성한다.

    body_light(피부 원본) 위에 리컬러한 shirt→pants→hair를 알파 합성.
    """
    shirt = recolor.recolor_layer(NEUTRAL_SHIRT, colors["shirt_rgb"])
    pants = recolor.recolor_layer(NEUTRAL_PANTS, colors["shirt_rgb"] * PANTS_DARKEN)
    hair = recolor.recolor_layer(NEUTRAL_HAIR, colors["hair_rgb"])
    sheet = compose.load_layer("body_light")
    for layer in (shirt, pants, hair):
        sheet = compose.alpha_over(sheet, layer)
    return compose.extract_idle(sheet)


def size_for_map(idle: Image.Image) -> Image.Image:
    """idle을 콘텐츠 bbox로 타이트 크롭 후 정수배 NEAREST로 맵 가독 크기에 맞춘다.

    절차(바운딩박스 기반·¾탑다운 맵 가독):
      1) 콘텐츠 bbox로 크롭(투명 패딩 제거 → 맵 배치 효율·정렬 일관).
      2) 크롭 콘텐츠 높이 h가 [TARGET_H_MIN,TARGET_H_MAX]면 1배(이미 가독·픽셀 보존).
         h<min이면 정수배 업스케일(round(target/h), NEAREST로 또렷). h>max여도
         정수배 다운스케일은 비정수배라 품질저하 → 1배 유지(LPC 원픽셀 보존).
    LPC 64px 프레임의 캐릭터 콘텐츠(~49px)는 크롭 시 타깃 상한 근방이라 1배가 적정.
    """
    x0, y0, x1, y1 = compose.content_bbox(idle)
    cropped = idle.crop((x0, y0, x1, y1))
    h = max(1, y1 - y0)
    if h < TARGET_H_MIN:
        scale = max(1, round(TARGET_H_TYPICAL / h))
    else:
        scale = 1  # 이미 가독 범위 이상 → 원픽셀 보존(정수배 축소 불가).
    if scale == 1:
        return cropped
    return cropped.resize(
        (cropped.width * scale, cropped.height * scale), Image.NEAREST
    )


def build_master_palette(idles: list[Image.Image]) -> np.ndarray:
    """8역할 합성물 전체 불투명 픽셀로 단일 마스터 팔레트(K×3 uint8) 산출.

    전 캐릭터 색을 모아 k-means(고정시드)로 MASTER_COLORS개 대표색 추출 → 응집.
    """
    pool = []
    for im in idles:
        arr = np.array(im)
        pool.append(arr[arr[..., 3] > 16][..., :3])
    allpx = np.concatenate(pool, axis=0).astype(np.float64)
    centers, _ = kmeans(allpx, MASTER_COLORS)
    return np.clip(np.round(centers), 0, 255).astype(np.uint8)


def quantize_to_palette(idle: Image.Image, palette: np.ndarray) -> Image.Image:
    """idle 불투명 픽셀을 마스터 팔레트 최근접색으로 스냅(알파 보존).

    각 픽셀 → 팔레트 중 L2 최근접. 투명 픽셀은 (0,0,0,0)로 유지.
    """
    arr = np.array(idle).astype(np.int64)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    mask = alpha > 16
    pts = rgb[mask].astype(np.float64)
    pal = palette.astype(np.float64)
    d = ((pts[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
    nearest = pal[d.argmin(axis=1)]
    out = arr.copy()
    out[..., :3][mask] = nearest.astype(np.int64)
    out[~mask] = 0  # 투명 정규화.
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def enforce_color_cap(idle: Image.Image, cap: int = MAX_PER_SPRITE) -> Image.Image:
    """스프라이트 색수가 cap 초과면 그 스프라이트 자체 색으로 재양자화해 ≤cap 보장.

    마스터 팔레트 스냅 후에도 한 캐릭터가 cap을 넘으면(드묾), 해당 픽셀들만으로
    k-means(k=cap) 재양자화. 응집은 유지(이미 마스터색 근방).
    """
    if count_unique_colors(np.array(idle)) <= cap:
        return idle
    arr = np.array(idle).astype(np.int64)
    rgb = arr[..., :3]
    mask = arr[..., 3] > 16
    pts = rgb[mask].astype(np.float64)
    centers, labels = kmeans(pts, cap)
    snapped = np.clip(np.round(centers[labels]), 0, 255)
    out = arr.copy()
    out[..., :3][mask] = snapped.astype(np.int64)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def make_montage(
    sprites: list[tuple[str, Image.Image]], view_scale: int = 3
) -> Image.Image:
    """8역할 idle을 가로 배열 + 역할 라벨한 몽타주(RGBA) 생성(육안 검증용).

    검수 편의로 스프라이트를 view_scale배 NEAREST 확대(픽셀 또렷·출력 파일과 무관).
    셀 폭은 확대 스프라이트와 라벨 텍스트 중 넓은 쪽에 맞춰 라벨 겹침을 방지.
    """
    pad = 10
    label_h = 16
    big = [
        (role, s.resize((s.width * view_scale, s.height * view_scale), Image.NEAREST))
        for role, s in sprites
    ]
    label_w = [len(role) * 6 + 6 for role, _ in big]  # 기본폰트 ~6px/char.
    cell_w = max(max(s.width for _, s in big) + pad, max(label_w) + pad)
    cell_h = max(s.height for _, s in big) + pad + label_h
    total_w = cell_w * len(big)
    canvas = Image.new("RGBA", (total_w, cell_h), (40, 44, 52, 255))
    draw = ImageDraw.Draw(canvas)
    for i, (role, sp) in enumerate(big):
        x = i * cell_w + (cell_w - sp.width) // 2
        canvas.alpha_composite(sp, (x, pad))
        draw.text((i * cell_w + 4, cell_h - label_h), role, fill=(230, 230, 230, 255))
    return canvas


def run() -> dict:
    """전체 US-1 파이프라인 실행. 반환: 역할별 결과·검증 요약 dict."""
    os.makedirs(OUT_DIR, exist_ok=True)
    colors = {r: ec.extract_role_colors(r) for r in ec.ROLES}
    idles = {r: build_role_idle(colors[r]) for r in ec.ROLES}
    palette = build_master_palette([idles[r] for r in ec.ROLES])

    results = {}
    sprites_for_montage = []
    for r in ec.ROLES:
        quant = quantize_to_palette(idles[r], palette)
        capped = enforce_color_cap(quant)
        scaled = size_for_map(capped)
        scaled.save(os.path.join(OUT_DIR, f"{r}.png"))
        ncol = count_unique_colors(np.array(capped))
        x0, y0, x1, y1 = compose.content_bbox(capped)
        results[r] = {
            "shirt_hex": colors[r]["shirt_hex"],
            "hair_hex": colors[r]["hair_hex"],
            "source": colors[r]["source"],
            "colors": ncol,
            "content_h": y1 - y0,
            "out_w": scaled.width,
            "out_h": scaled.height,
        }
        sprites_for_montage.append((r, scaled))

    montage = make_montage(sprites_for_montage)
    montage.save(MONTAGE_PATH)
    return {
        "results": results,
        "master_palette": [to_hex(c) for c in palette],
        "montage": MONTAGE_PATH,
    }


def _print_report(summary: dict) -> None:
    """검증 리포트 표준출력(색수 단언 포함)."""
    print("=== US-1 LPC 합성 결과 ===")
    print(
        f"마스터 팔레트({len(summary['master_palette'])}색): {summary['master_palette']}"
    )
    print(
        f"\n{'역할':12s} {'의상색':9s} {'머리색':9s} {'색수':>4s} {'≤15':>4s} {'추출':>8s} {'출력px':>8s}"
    )
    all_ok = True
    for role, d in summary["results"].items():
        ok = d["colors"] <= MAX_PER_SPRITE
        all_ok &= ok
        print(
            f"{role:12s} {d['shirt_hex']:9s} {d['hair_hex']:9s} "
            f"{d['colors']:>4d} {'OK' if ok else 'X':>4s} {d['source']:>8s} "
            f"{d['out_w']}x{d['out_h']}"
        )
    print(f"\n색수≤{MAX_PER_SPRITE} 전역 단언: {'PASS' if all_ok else 'FAIL'}")
    print(f"몽타주: {summary['montage']}")
    assert all_ok, "색수 상한 위반 — 양자화 재확인 필요"
    print("\n모든 단언 통과.")


if __name__ == "__main__":
    _print_report(run())
