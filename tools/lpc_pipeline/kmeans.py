"""결정론 k-means 군집화 + 공용 색 유틸 (numpy 전용·외부 의존 0).

LPC 합성 파이프라인 US-1 전반에서 쓰는 순수 함수 모음.
★결정론 보장: 모든 무작위(초기 중심 선택)는 고정 시드 RNG로만 발생.
같은 입력 → 같은 출력. colormath/skimage 미사용(numpy 직접 구현).
"""

from __future__ import annotations

import numpy as np

# 고정 시드 — 동일 입력에서 동일 클러스터를 보장(결정론 경계).
SEED = 1234


def kmeans(
    points: np.ndarray, k: int, iters: int = 24, seed: int = SEED
) -> tuple[np.ndarray, np.ndarray]:
    """points(N×D)를 k개 군집으로 나눈다(k-means++ 시드·고정 RNG·결정론).

    반환: (centers k×D float64, labels N int). N<k이면 가용 점만 중심으로 쓴다.
    빈 군집은 가장 먼 점으로 재시드해 NaN을 피한다.
    """
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n == 0:
        return np.zeros((0, pts.shape[1] if pts.ndim == 2 else 3)), np.zeros(0, int)
    k = min(k, n)
    rng = np.random.default_rng(seed)
    centers = _kpp_init(pts, k, rng)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        d = _sqdist(pts, centers)  # N×k
        new_labels = d.argmin(axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            labels = new_labels
            break
        labels = new_labels
        for c in range(k):
            sel = pts[labels == c]
            if len(sel) == 0:
                centers[c] = pts[d.max(axis=1).argmax()]  # 최원점 재시드.
            else:
                centers[c] = sel.mean(axis=0)
    return centers, labels


def _kpp_init(pts: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ 초기 중심 선택(고정 RNG로 결정론)."""
    n = len(pts)
    centers = np.empty((k, pts.shape[1]), dtype=np.float64)
    centers[0] = pts[rng.integers(n)]
    closest = _sqdist(pts, centers[:1]).ravel()
    for c in range(1, k):
        total = closest.sum()
        probs = closest / total if total > 0 else np.full(n, 1.0 / n)
        centers[c] = pts[rng.choice(n, p=probs)]
        closest = np.minimum(closest, _sqdist(pts, centers[c : c + 1]).ravel())
    return centers


def _sqdist(pts: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """제곱 유클리드 거리 행렬 N×k."""
    diff = pts[:, None, :] - centers[None, :, :]
    return (diff * diff).sum(axis=2)


def dominant_colors(
    pixels: np.ndarray, k: int = 5, seed: int = SEED
) -> list[tuple[np.ndarray, int]]:
    """픽셀 집합의 주색을 (중심RGB, 멤버수) 리스트로, 멤버수 내림차순 반환."""
    if len(pixels) == 0:
        return []
    centers, labels = kmeans(pixels, k, seed=seed)
    counts = np.bincount(labels, minlength=len(centers))
    order = np.argsort(-counts)
    return [(centers[i], int(counts[i])) for i in order if counts[i] > 0]


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec.601 가중 휘도(…,3) → (…,). float64."""
    rgb = np.asarray(rgb, dtype=np.float64)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def to_hex(rgb) -> str:
    """RGB(길이3) → '#rrggbb' 소문자."""
    r, g, b = (int(round(float(v))) for v in rgb[:3])

    def clamp(v: int) -> int:
        return max(0, min(255, v))

    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def count_unique_colors(img_rgba: np.ndarray, alpha_thresh: int = 16) -> int:
    """불투명(알파>임계) 픽셀의 유니크 RGB 색 수(SNES 4bpp 검증용)."""
    arr = np.asarray(img_rgba)
    if arr.shape[-1] == 4:
        opaque = arr[arr[..., 3] > alpha_thresh][..., :3]
    else:
        opaque = arr.reshape(-1, 3)
    if len(opaque) == 0:
        return 0
    return len(np.unique(opaque.reshape(-1, 3), axis=0))
