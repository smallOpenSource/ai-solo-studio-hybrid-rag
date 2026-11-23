"""slice_stitch.py — Stitch 확산 시트(JPEG·불투명)를 그리드 셀로 분할 + 배경제거(알파 PNG).

standalone 모듈(crawl env: PIL+numpy). 결정론(동일 입력 → 동일 출력 바이트).
US-2a. ★실제 시트 적용·미학/세탁 채택은 HITL(US-2b) — 이 모듈은 "도구 + 픽스처 테스트"다.

배경제거는 extract_colors._flood_bg 패턴 차용: 4코너 시드 → BFS 플러드필(코너색 ±tol)로
연결된 배경 영역을 알파 0으로. 확산 시트의 평탄한 단색 배경(tan/grey)에 적합.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Optional

import numpy as np
from PIL import Image


def slice_grid(img: Image.Image, rows: int, cols: int) -> list[Image.Image]:
    """이미지를 rows×cols 균등 그리드로 분할(좌상→우하·행 우선). RGBA 셀 리스트 반환.

    결정론: PIL crop은 결정론적·셀 순서 고정. 나머지(여백) 픽셀은 버린다(// 몫 기준).
    """
    if rows < 1 or cols < 1:
        raise ValueError("rows/cols must be >= 1")
    w, h = img.size
    cw, ch = w // cols, h // rows
    if cw == 0 or ch == 0:
        raise ValueError("image smaller than requested grid")
    rgba = img.convert("RGBA")
    cells: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            box = (c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)
            cells.append(rgba.crop(box))
    return cells


def remove_bg(img: Image.Image, tol: int = 24) -> Image.Image:
    """4코너 시드 플러드필로 배경을 투명화(코너색 ±tol·max-채널 거리). 알파 RGBA 반환.

    결정론: 코너 처리 순서 고정·BFS 도달집합은 순서 무관(고정 술어). 평탄 배경 가정.
    """
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    h, w = arr.shape[:2]
    rgb = arr[..., :3].astype(np.int16)
    visited = np.zeros((h, w), dtype=bool)

    corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    for sy, sx in corners:
        if visited[sy, sx]:
            continue
        seed = rgb[sy, sx].copy()
        dq: deque[tuple[int, int]] = deque([(sy, sx)])
        visited[sy, sx] = True
        while dq:
            y, x = dq.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if (
                    0 <= ny < h
                    and 0 <= nx < w
                    and not visited[ny, nx]
                    and int(np.abs(rgb[ny, nx] - seed).max()) <= tol
                ):
                    visited[ny, nx] = True
                    dq.append((ny, nx))

    arr[visited, 3] = 0
    return Image.fromarray(arr, "RGBA")


def slice_sheet(
    path: str,
    rows: int,
    cols: int,
    out_dir: str,
    prefix: str,
    names: Optional[list[str]] = None,
    strip_bg: bool = True,
    tol: int = 24,
) -> list[dict]:
    """시트 파일을 분할(+선택적 배경제거)해 RGBA PNG로 저장. 매니페스트(dict 리스트) 반환.

    결정론: PIL PNG 기본 저장은 타임스탬프 청크 없음 → 동일 입력 재실행 시 byte-diff 0.
    """
    img = Image.open(path)
    cells = slice_grid(img, rows, cols)
    os.makedirs(out_dir, exist_ok=True)
    manifest: list[dict] = []
    for i, cell in enumerate(cells):
        if strip_bg:
            cell = remove_bg(cell, tol=tol)
        name = names[i] if names and i < len(names) else f"{prefix}_{i:02d}"
        out_path = os.path.join(out_dir, f"{name}.png")
        cell.save(out_path, format="PNG")
        manifest.append(
            {
                "index": i,
                "name": name,
                "path": out_path,
                "size": list(cell.size),
                "mode": cell.mode,
            }
        )
    return manifest
