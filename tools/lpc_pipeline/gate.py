"""US-2 응집/식별 자동 품질게이트 — 순수 numpy CIEDE2000 (★신규·비파괴).

보증 범위(승인본 ralplan-office-v2-assets.md §6-2 게이트 2분리):
  본 자동게이트는 **응집·식별·저색만** 정량 보증한다. 세탁(런더링)·미학 판별은
  HITL US-5a 소관이며 여기서 다루지 않는다(과대주장 금지).

측정(AC 1):
  1) CIEDE2000: sRGB→Lab(D65) + delta_e_ciede2000(완전 구현·RT 회전항 포함).
  2) 역할별 의상 주색: 렌더 스프라이트에서 피부/머리/투명 제외 후 최빈 의상색.
  3) 색수: 스프라이트당 고유 비투명색 ≤15 단언.
  4) 역할쌍 의상 ΔE2000 8×8 매트릭스 → ΔE<25 식별실패쌍 목록.
  5) 팔레트 멤버십: 각 역할 **원시(스냅前) 추출색**이 마스터 팔레트 내 비율.
     ★스냅後 측정은 자명 100%라 무의미 → 반드시 원시색 기준(extract_colors).
  6) 식별축 카운트: {의상 주색·머리색·소품유무} 근사 → 역할쌍 상이축 ≥3 여부.
  7) 프린지(semiAA)/selout = ★경고 메트릭만(차단 아님·신호역전 실측). 값만 리포트.

★결정론: 마스터 팔레트 k-means 고정 시드(kmeans.SEED). 같은 입력 → 같은 산출.
실행: /opt/conda/envs/one_man_corp-crawl/bin/python gate.py
"""

from __future__ import annotations

import json
import os

import numpy as np
from PIL import Image

import extract_colors as ec
from kmeans import kmeans, luminance, to_hex

ROLES = ec.ROLES
SPRITE_DIR = "/opt/studio/work/lpc/roles_v2_preview"
REPORT_PATH = "/opt/studio/work/lpc/gate_report.json"

# 게이트 임계.
MAX_COLORS = 15  # 스프라이트당 고유 비투명색 상한(SNES 4bpp).
DELTA_E_IDENTIFY = 25.0  # 의상 ΔE2000 식별 하한(미만=식별실패쌍).
MASTER_COLORS = 24  # 마스터 팔레트 색수(US-1 build_us1.MASTER_COLORS와 정합).
ALPHA_THRESH = 16  # 비투명 임계(count_unique_colors와 동일).
# 세로 밴드(콘텐츠 bbox 기준 비율) — extract_colors 의상/머리 ROI 관례와 정합.
TORSO_LO, TORSO_HI = 0.45, 0.95  # 의상(몸통) 밴드 — 얼굴 살색 자체 배제.
HAIR_LO, HAIR_HI = 0.0, 0.22  # 머리 밴드(상단) — extract_colors와 동일.
HAIR_SKIN_DIST = ec.HAIR_SKIN_DIST  # 머리 추출용 강한 피부배제(이마/얼굴 광역).
SKIN_DIST = ec.HAIR_SKIN_DIST  # ★의상도 동일 강배제(살색 #fdd5b7 skin_dist≈115 제거).
OUTLINE_LUM = 36.0  # 검은 윤곽선/극단그림자 휘도 컷(suit outline #271820 lum29 배제).
_SKIN_REF = ec._SKIN_REF  # 대표 피부톤(extract_colors와 단일 출처).

# 식별축: 머리색 distinct 판정용 ΔE 하한(의상과 동일 기준 적용).
HAIR_DELTA_E = 25.0


# ── CIEDE2000 (순수 numpy·완전 구현) ──────────────────────────────────


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """sRGB [0,1] → 선형 RGB(역감마). 표준 IEC 61966-2-1."""
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB(0–255, …×3) → CIE Lab(D65). 순수 numpy."""
    rgb = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = _srgb_to_linear(rgb)
    # 선형 sRGB → XYZ (D65, sRGB 매트릭스).
    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = lin @ m.T
    # D65 백색점 정규화.
    white = np.array([0.95047, 1.00000, 1.08883])
    xyz = xyz / white
    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16.0) / 116.0)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)
    return lab


def delta_e_ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """두 Lab 색의 CIEDE2000 색차(스칼라). L'·C'·h'·ΔL'·ΔC'·ΔH'·RT 회전항 완전 구현.

    구현은 Sharma et al.(2005) 정식을 따른다(참조데이터 검증은 test_gate.py).
    """
    l1, a1, b1 = float(lab1[0]), float(lab1[1]), float(lab1[2])
    l2, a2, b2 = float(lab2[0]), float(lab2[1]), float(lab2[2])
    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    g = 0.5 * (1.0 - np.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    a1p, a2p = (1.0 + g) * a1, (1.0 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p, h2p = _atan2_deg(b1, a1p), _atan2_deg(b2, a2p)
    dlp = l2 - l1
    dcp = c2p - c1p
    dhp = _dhp(h1p, h2p, c1p, c2p)
    dHp = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp) / 2.0)
    lbar = (l1 + l2) / 2.0
    cbarp = (c1p + c2p) / 2.0
    hbarp = _hbarp(h1p, h2p, c1p, c2p)
    t = (
        1.0
        - 0.17 * np.cos(np.radians(hbarp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hbarp))
        + 0.32 * np.cos(np.radians(3.0 * hbarp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hbarp - 63.0))
    )
    d_theta = 30.0 * np.exp(-(((hbarp - 275.0) / 25.0) ** 2))
    rc = 2.0 * np.sqrt(cbarp**7 / (cbarp**7 + 25.0**7))
    sl = 1.0 + (0.015 * (lbar - 50.0) ** 2) / np.sqrt(20.0 + (lbar - 50.0) ** 2)
    sc = 1.0 + 0.045 * cbarp
    sh = 1.0 + 0.015 * cbarp * t
    rt = -np.sin(np.radians(2.0 * d_theta)) * rc
    return float(
        np.sqrt(
            (dlp / sl) ** 2
            + (dcp / sc) ** 2
            + (dHp / sh) ** 2
            + rt * (dcp / sc) * (dHp / sh)
        )
    )


def _atan2_deg(y: float, x: float) -> float:
    """atan2 → [0,360) 도(CIEDE2000 h' 규약)."""
    deg = np.degrees(np.arctan2(y, x))
    return deg + 360.0 if deg < 0 else deg


def _dhp(h1p: float, h2p: float, c1p: float, c2p: float) -> float:
    """Δh'(부호 포함, [-180,180]). C'곱=0이면 0(Sharma 규약)."""
    if c1p * c2p == 0.0:
        return 0.0
    diff = h2p - h1p
    if diff > 180.0:
        return diff - 360.0
    if diff < -180.0:
        return diff + 360.0
    return diff


def _hbarp(h1p: float, h2p: float, c1p: float, c2p: float) -> float:
    """H̄'(평균 색상각). C'곱=0이면 합, |차|>180이면 360 보정(Sharma 규약)."""
    if c1p * c2p == 0.0:
        return h1p + h2p
    s = h1p + h2p
    if abs(h1p - h2p) > 180.0:
        return (s + 360.0) / 2.0 if s < 360.0 else (s - 360.0) / 2.0
    return s / 2.0


# ── 색 추출/측정 ──────────────────────────────────────────────────────


def load_sprite(role: str) -> np.ndarray:
    """렌더된 역할 스프라이트를 RGBA 배열로 로드(읽기 전용)."""
    return np.array(Image.open(os.path.join(SPRITE_DIR, f"{role}.png")).convert("RGBA"))


def unique_opaque_colors(arr: np.ndarray) -> np.ndarray:
    """비투명(알파>임계) 고유 RGB 색(M×3 uint8)."""
    opaque = arr[arr[..., 3] > ALPHA_THRESH][..., :3]
    if len(opaque) == 0:
        return np.zeros((0, 3), dtype=np.uint8)
    return np.unique(opaque.reshape(-1, 3), axis=0)


def _content_y_bounds(arr: np.ndarray) -> tuple[int, int]:
    """비투명 콘텐츠의 세로 [y0, y1+1] 경계(밴드 비율 기준점). 빈 경우 전체."""
    ys = np.where((arr[..., 3] > ALPHA_THRESH).any(axis=1))[0]
    if len(ys) == 0:
        return 0, arr.shape[0]
    return int(ys.min()), int(ys.max()) + 1


def _band_dominant(arr, lo, hi, skin_dist) -> np.ndarray:
    """콘텐츠 bbox 세로 [lo,hi) 밴드에서 윤곽선/피부 배제 후 최빈색(float64 RGB).

    밴드 비율은 비투명 콘텐츠 높이 기준(extract_colors._band과 동일 의미). 저휘도
    윤곽선·그림자와 살색(skin_dist 이내)을 배제해 면적 dominant를 식별색으로 본다.
    """
    y0, y1 = _content_y_bounds(arr)
    span = y1 - y0
    a = max(0, int(y0 + span * lo))
    b = int(y0 + span * hi)
    px = arr[a:b][arr[a:b, :, 3] > ALPHA_THRESH][..., :3].astype(np.float64)
    if len(px) == 0:
        return np.zeros(3, dtype=np.float64)
    keep = luminance(px) >= OUTLINE_LUM
    keep &= np.abs(px - _SKIN_REF).sum(axis=1) > skin_dist
    px = px[keep]
    if len(px) == 0:
        return np.zeros(3, dtype=np.float64)
    cols, counts = np.unique(
        px.astype(np.uint8).reshape(-1, 3), axis=0, return_counts=True
    )
    return cols[counts.argmax()].astype(np.float64)


def costume_dominant(arr: np.ndarray) -> np.ndarray:
    """스프라이트 의상 주색(몸통 밴드 45–95%·피부/윤곽선 제외 최빈, float64 RGB).

    얼굴 살색을 ROI 자체로 배제(머리 아래 몸통만)하고, 잔여 팔·손 살색은 강한
    skin_dist로 제거한다. extract_colors._extract_shirt 밴드와 동일 영역.
    """
    return _band_dominant(arr, TORSO_LO, TORSO_HI, SKIN_DIST)


def hair_dominant(arr: np.ndarray) -> np.ndarray:
    """스프라이트 머리 주색(상단 밴드 0–22%·피부 광역배제 최빈, float64 RGB).

    extract_colors._extract_hair 밴드와 동일(이마/얼굴 살색을 HAIR_SKIN_DIST로 배제).
    """
    return _band_dominant(arr, HAIR_LO, HAIR_HI, HAIR_SKIN_DIST)


def build_master_palette() -> np.ndarray:
    """마스터 팔레트(K×3 uint8): US-1 build_us1.py 산출이 없으므로 8역할 비투명색
    합집합을 k-means(MASTER_COLORS·고정시드)로 양자화해 구성(US-1과 동일 절차).
    ★멤버십은 이 팔레트 대비 **원시 추출색**(스냅前)으로 측정(아래 membership).
    """
    pool = [load_sprite(r) for r in ROLES]
    px = np.concatenate(
        [a[a[..., 3] > ALPHA_THRESH][..., :3] for a in pool], axis=0
    ).astype(np.float64)
    centers, _ = kmeans(px, MASTER_COLORS)
    return np.clip(np.round(centers), 0, 255).astype(np.uint8)


def membership_ratio(
    raw_rgb: list[np.ndarray], palette: np.ndarray, tol: float = 8.0
) -> float:
    """원시색 리스트가 마스터 팔레트 내(L2≤tol)에 드는 비율 [0,1].

    ★측정은 팔레트 **스냅前 원시색** 기준(스냅後는 정의상 100%라 무의미).
    tol은 양자화 반경 근사(라운딩 오차 수용). 응집(원시색이 이미 팔레트 근방)
    여부를 정량화한다.
    """
    if not raw_rgb:
        return 0.0
    pal = palette.astype(np.float64)
    inside = 0
    for c in raw_rgb:
        c = np.asarray(c, dtype=np.float64)
        d = np.sqrt(((pal - c) ** 2).sum(axis=1)).min()
        if d <= tol:
            inside += 1
    return inside / len(raw_rgb)


def fringe_metrics(arr: np.ndarray) -> dict:
    """프린지(반투명 AA)·selout 근사 경고 메트릭(★차단 아님·값만).

    semi_aa = 0<알파<255 반투명 픽셀 수(외곽 AA 신호). selout_like = 비투명 중
    저휘도(<OUTLINE_LUM) 픽셀 수(셀렉티브 아웃라인 근사). 신호역전 실측이라
    게이트 차단에 쓰지 않고 리포트에 값만 남긴다.
    """
    alpha = arr[..., 3]
    semi = int(((alpha > 0) & (alpha < 255)).sum())
    opaque = arr[alpha > ALPHA_THRESH][..., :3].astype(np.float64)
    selout = int((luminance(opaque) < OUTLINE_LUM).sum()) if len(opaque) else 0
    return {"semi_aa_px": semi, "selout_like_px": selout}


# ── 매트릭스/식별축 ────────────────────────────────────────────────────


def delta_e_matrix(labs: list[np.ndarray]) -> np.ndarray:
    """역할 Lab 리스트 → 대칭 N×N ΔE2000 매트릭스(대각=0)."""
    n = len(labs)
    mat = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            d = delta_e_ciede2000(labs[i], labs[j])
            mat[i, j] = d
            mat[j, i] = d
    return mat


def failing_pairs(mat: np.ndarray, names: list[str], thresh: float) -> list[dict]:
    """ΔE<thresh인 상삼각 쌍을 ΔE 오름차순으로(식별실패쌍 = US-3 입력)."""
    out = []
    n = len(names)
    for i in range(n):
        for j in range(i + 1, n):
            if mat[i, j] < thresh:
                out.append(
                    {
                        "pair": [names[i], names[j]],
                        "delta_e": round(float(mat[i, j]), 2),
                    }
                )
    out.sort(key=lambda d: d["delta_e"])
    return out


def distinct_count(labs: list[np.ndarray], thresh: float) -> int:
    """ΔE≥thresh로 서로 구분되는 군집 수(탐욕 군집·근사 distinct).

    첫 색을 대표로 시작, 이후 색이 모든 기존 대표와 ΔE≥thresh면 새 군집 대표로
    추가. 식별축(의상/머리 색 다양성) 근사 카운트.
    """
    reps: list[np.ndarray] = []
    for c in labs:
        if all(delta_e_ciede2000(c, r) >= thresh for r in reps):
            reps.append(c)
    return len(reps)


def identity_axes(costume_labs, hair_labs, props) -> dict:
    """식별축 카운트: 의상색 distinct·머리색 distinct·소품 distinct(유무 종류수).

    역할쌍 상이축 ≥3 충족 여부의 전역 근사(축별 다양성이 클수록 식별 견고).
    소품은 현재 미구현 표식이라 단일값(distinct=1) → 의상/머리에 의존함을 명시.
    """
    cd = distinct_count(costume_labs, DELTA_E_IDENTIFY)
    hd = distinct_count(hair_labs, HAIR_DELTA_E)
    pd = len(set(props))
    return {
        "costume_distinct": cd,
        "hair_distinct": hd,
        "prop_distinct": pd,
        "axes_active": int(cd > 1) + int(hd > 1) + int(pd > 1),
    }


# ── 실행/리포트 ────────────────────────────────────────────────────────


def run() -> dict:
    """전체 게이트 실행 → gate_report.json 작성 후 요약 dict 반환(결정론)."""
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    sprites = {r: load_sprite(r) for r in ROLES}
    palette = build_master_palette()
    raw = {r: ec.extract_role_colors(r) for r in ROLES}  # 스냅前 원시 추출색.

    per_role, costume_labs, hair_labs, props = {}, [], [], []
    for r in ROLES:
        arr = sprites[r]
        cdom = costume_dominant(arr)
        hdom = hair_dominant(arr)
        uniq = unique_opaque_colors(arr)
        clab = srgb_to_lab(cdom)
        hlab = srgb_to_lab(hdom)
        costume_labs.append(clab)
        hair_labs.append(hlab)
        props.append("none")  # 소품 미구현 — 식별축은 의상/머리에 의존.
        raw_colors = [raw[r]["shirt_rgb"], raw[r]["hair_rgb"]]
        per_role[r] = {
            "costume_hex": to_hex(cdom),
            "hair_hex": to_hex(hdom),
            "raw_shirt_hex": raw[r]["shirt_hex"],
            "raw_hair_hex": raw[r]["hair_hex"],
            "extract_source": raw[r]["source"],
            "n_colors": int(len(uniq)),
            "colors_ok": bool(len(uniq) <= MAX_COLORS),
            "membership_ratio": round(membership_ratio(raw_colors, palette), 3),
            **fringe_metrics(arr),
        }

    mat = delta_e_matrix(costume_labs)
    fails = failing_pairs(mat, ROLES, DELTA_E_IDENTIFY)
    axes = identity_axes(costume_labs, hair_labs, props)
    iu = np.triu_indices(len(ROLES), k=1)
    report = {
        "roles": ROLES,
        "master_palette": [to_hex(c) for c in palette],
        "thresholds": {
            "max_colors": MAX_COLORS,
            "delta_e_identify": DELTA_E_IDENTIFY,
        },
        "per_role": per_role,
        "costume_delta_e_matrix": [[round(float(v), 2) for v in row] for row in mat],
        "delta_e_min": round(float(mat[iu].min()), 2),
        "delta_e_max": round(float(mat[iu].max()), 2),
        "failing_pairs": fails,
        "n_failing_pairs": len(fails),
        "colors_cap_all_ok": all(per_role[r]["colors_ok"] for r in ROLES),
        "identity_axes": axes,
        "warnings_note": "fringe/selout = 경고 메트릭(차단 아님). 신호역전 실측.",
        "scope_note": "응집·식별·저색만 보증. 세탁/미학은 HITL US-5a 소관.",
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def _print_report(rep: dict) -> None:
    """게이트 결과 표준출력(한국어 요약)."""
    print("=== US-2 응집/식별 품질게이트 ===")
    print(f"마스터 팔레트({len(rep['master_palette'])}색): {rep['master_palette']}")
    print(
        f"\n{'역할':12s} {'의상색':9s} {'머리색':9s} {'색수':>4s} {'≤15':>4s} {'멤버십':>6s} {'semiAA':>7s} {'selout':>7s}"
    )
    for r in rep["roles"]:
        d = rep["per_role"][r]
        print(
            f"{r:12s} {d['costume_hex']:9s} {d['hair_hex']:9s} {d['n_colors']:>4d} "
            f"{'OK' if d['colors_ok'] else 'X':>4s} {d['membership_ratio']:>6.2f} "
            f"{d['semi_aa_px']:>7d} {d['selout_like_px']:>7d}"
        )
    print(
        f"\n색수≤{rep['thresholds']['max_colors']} 전건: {'PASS' if rep['colors_cap_all_ok'] else 'FAIL'}"
    )
    print(
        f"의상 ΔE2000 min={rep['delta_e_min']} max={rep['delta_e_max']} (식별하한 {rep['thresholds']['delta_e_identify']})"
    )
    print(
        f"식별실패쌍(ΔE<{rep['thresholds']['delta_e_identify']}): {rep['n_failing_pairs']}개"
    )
    for fp in rep["failing_pairs"]:
        print(f"  {fp['pair'][0]:11s} ↔ {fp['pair'][1]:11s} ΔE={fp['delta_e']}")
    ax = rep["identity_axes"]
    print(
        f"식별축: 의상distinct={ax['costume_distinct']} 머리distinct={ax['hair_distinct']} "
        f"소품distinct={ax['prop_distinct']} → 활성축={ax['axes_active']} (목표≥3)"
    )
    print(f"리포트: {REPORT_PATH}")


if __name__ == "__main__":
    _print_report(run())
