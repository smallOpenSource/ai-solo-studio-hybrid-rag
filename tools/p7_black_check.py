"""P7 검정(black) 이미지 판별 — VAE 수리 검증 (art env·PIL·NET-NEW).

과거 Server B qwen-image 는 VAE decode(fp16 NaN) 로 **전 픽셀 (0,0,0) 검정·black_ratio=1.0**
을 냈다(SR/renewal/SERVER_B_검정이미지_점검요청.md). Owner VAE 수리 후 실 생성 이미지가
*컬러*인지 판별한다. critic 이 과거 ad-hoc 으로 쓰던 black_ratio/max_channel/non-black
계산을 재사용 가능한 결정론 도구로 코드화(repo 에 헬퍼 부재였음).

사용:
    python tools/p7_black_check.py <png_path>
판정(falsifiable):
    · 전 채널 max==0 또는 non-black 픽셀==0 → 전검정 → exit 1 (VAE 여전 degraded).
    · 컬러 존재(max_channel>0·non-black>0)            → exit 0 (정상·VAE 수리 확인).
STDOUT: ``BLACK_CHECK ... verdict=BLACK|COLOR`` (집계 가능 한 줄).

비-black 픽셀 카운트는 채널 per-pixel max(ImageChops.lighter) 히스토그램으로 계산한다
(deprecated getdata() 회피·"임의 채널 > 임계" 의미 보존).
"""

from __future__ import annotations

import sys

from PIL import Image, ImageChops

_THRESHOLD = 8  # 채널값 ≤8 은 사실상 검정(코덱 노이즈/디더 여유).


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: p7_black_check.py <png_path>")
        return 2
    path = sys.argv[1]
    im = Image.open(path).convert("RGB")
    w, h = im.size
    total = w * h
    extrema = im.getextrema()  # ((rmin,rmax),(gmin,gmax),(bmin,bmax))
    max_channel = max(ch[1] for ch in extrema)
    # 픽셀별 채널 최댓값 이미지 → 히스토그램. bin>임계 합 = "임의 채널 > 임계" 픽셀 수.
    r, g, b = im.split()
    per_pixel_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
    hist = per_pixel_max.histogram()  # 256 bins (0..255)
    non_black = sum(hist[_THRESHOLD + 1 :])
    black_ratio = 1.0 - (non_black / total) if total else 1.0
    is_black = (max_channel == 0) or (non_black == 0)
    print(
        f"BLACK_CHECK path={path} size={w}x{h} max_channel={max_channel} "
        f"non_black_px={non_black}/{total} black_ratio={black_ratio:.6f} "
        f"verdict={'BLACK' if is_black else 'COLOR'}"
    )
    return 1 if is_black else 0


if __name__ == "__main__":
    raise SystemExit(main())
