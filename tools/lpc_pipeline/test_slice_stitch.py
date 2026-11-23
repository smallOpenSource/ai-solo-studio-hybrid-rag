"""test_slice_stitch.py — slice_stitch 픽스처 테스트 (US-2a·standalone·crawl env).

★pytest 의존성 없이 실행(crawl env에 pytest 미설치·의존성 추가 금지):
    safe_run + crawl env python tools/lpc_pipeline/test_slice_stitch.py
(pytest가 있으면 `python -m pytest`로도 수집 가능 — test_* 함수 + 평범한 assert.)

검증: 셀 개수==기대 · RGBA · 결정론(byte-diff 0) · 코너 배경 투명화.
"""

import io
import os
import tempfile

import numpy as np
from PIL import Image

from slice_stitch import remove_bg, slice_grid, slice_sheet


def make_fixture() -> Image.Image:
    """200×200 합성 시트: 평탄 tan 배경 + 2×2 셀 각 중앙 40×40 색square."""
    bg = (200, 180, 150)
    arr = np.full((200, 200, 3), bg, dtype=np.uint8)
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    k = 0
    for r in range(2):
        for c in range(2):
            cy, cx = r * 100 + 50, c * 100 + 50
            arr[cy - 20 : cy + 20, cx - 20 : cx + 20] = colors[k]
            k += 1
    return Image.fromarray(arr, "RGB")


def test_slice_grid_count_mode_size():
    cells = slice_grid(make_fixture(), 2, 2)
    assert len(cells) == 4  # 개수 == 기대(rows×cols)
    assert all(c.mode == "RGBA" for c in cells)
    assert all(c.size == (100, 100) for c in cells)


def test_remove_bg_corner_transparent_center_opaque():
    cell = slice_grid(make_fixture(), 2, 2)[0]
    out = remove_bg(cell)
    a = np.array(out)
    assert a[0, 0, 3] == 0  # 코너=배경 → 투명
    assert a[50, 50, 3] == 255  # 중앙 색square → 불투명


def test_deterministic_bytes():
    cell = slice_grid(make_fixture(), 2, 2)[0]

    def to_png_bytes(im: Image.Image) -> bytes:
        b = io.BytesIO()
        remove_bg(im).save(b, format="PNG")
        return b.getvalue()

    assert to_png_bytes(cell) == to_png_bytes(cell)  # 동일 입력 2회 → 동일 바이트


def test_slice_sheet_writes_and_reproducible():
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "fix.png")
        make_fixture().save(f)
        out_dir = os.path.join(td, "out")

        man = slice_sheet(f, 2, 2, out_dir, "cell")
        assert len(man) == 4
        assert all(m["mode"] == "RGBA" for m in man)
        assert all(m["size"] == [100, 100] for m in man)

        # 재실행 byte-diff 0(결정론)
        with open(man[0]["path"], "rb") as fh:
            b1 = fh.read()
        slice_sheet(f, 2, 2, out_dir, "cell")
        with open(man[0]["path"], "rb") as fh:
            b2 = fh.read()
        assert b1 == b2


def _main() -> int:
    """pytest 없는 standalone 러너 — 모든 test_* 함수 실행·PASS/FAIL 집계."""
    tests = [
        v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001 — 러너는 전 테스트 결과 집계
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
