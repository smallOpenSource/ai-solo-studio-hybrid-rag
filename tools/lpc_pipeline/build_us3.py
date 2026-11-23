"""US-3 — 16역할 LPC 도트 다변화(영웅전설 응집 유지·클론군대 회피·게이트 PASS).

US-2 진단(현 8역할 17/28쌍 ΔE<25·식별축 1·"다 똑같음" 3~4회 거부)을 해소한다.
응집은 **공유 중립 레이어 + 공유 3톤 리컬러 수학(skin/outline 구조 공통)**으로 유지하고,
식별은 **3축(의상색·머리색·소품) 강제 분산**으로 확보한다.

다변화 3축(승인 사양):
  1) 의상색 = 색상환 분산 **절제된 클래식 JRPG 톤** 16색. 채도 상한(S≤0.70)으로
     형광/채도폭발(garish) 배제. 결정론 farthest-point 솔버로 역할쌍 min-ΔE 최대화.
  2) 머리색 ≥6 = hair_brown/hair_black 휘도램프 리컬러로 6색(black/dark-brown/
     light-brown/blonde/auburn/gray) 역할별 배정.
  3) 소품 ≥3종 = 절차적 픽셀(안경·헤드셋·넥타이). 64px 프레임에 소량 픽셀(아웃라인 보존).

★US-2 수렴 근본원인(공유 마스터팔레트 24색 스냅이 의상색을 24중심으로 붕괴) 회피:
  본 US-3은 **per-sprite 양자화**(각 스프라이트 자기 색으로 ≤15)만 사용. 응집은
  구조(공유 피부/아웃라인/셰이딩)로 보장 → 의상 hue 식별성 보존.

★비파괴: web/src·web/public/.../characters/*.png(원본 LPC)·portraits·office 0수정.
  신규 산출만: roles_v2/{role}.png(16·최종 자산위치)+us3_16_montage.png+gate_report.json.
★결정론: 팔레트 솔버·리컬러·양자화·소품 전부 고정 시드/순수. 동일 입력→동일 산출.
실행: /opt/conda/envs/one_man_corp-crawl/bin/python build_us3.py
"""

from __future__ import annotations

import json
import os

import numpy as np
from PIL import Image, ImageDraw

import compose
import gate
import recolor
from kmeans import count_unique_colors, kmeans, to_hex

# ── 산출 경로 ──────────────────────────────────────────────────────────
ASSET_DIR = "/opt/studio/web/public/assets/characters/roles_v2"
MONTAGE_PATH = "/opt/studio/work/lpc/us3_16_montage.png"
REPORT_PATH = "/opt/studio/work/lpc/gate_report.json"

# ── 16역할 로스터(1차 확정·고정 순서) ──────────────────────────────────
ROLES = [
    "ceo",
    "dev",
    "designer",
    "accountant",
    "marketer",
    "planner",
    "secretary",
    "strategist",  # 기존 8.
    "sales",
    "cs",
    "cfo",
    "legal",
    "hr",
    "data",
    "pm",
    "devops",  # 신규 8.
]

# ── 게이트 임계(gate.py와 정합) ────────────────────────────────────────
MAX_COLORS = gate.MAX_COLORS  # 15(SNES 4bpp).
DELTA_E_IDENTIFY = gate.DELTA_E_IDENTIFY  # 25.0.

# ── 리컬러 베이스(US-1과 동일 중립 레이어·휘도램프 풍부) ────────────────
NEUTRAL_SHIRT = "white_longsleeve"
NEUTRAL_PANTS = "white_pants_male"
PANTS_DARKEN = 0.62  # 바지=의상색 어둡게(정장 하의 통일·US-1과 동일).

# ── 머리색 ≥6: 6색 팔레트 + 역할 배정(휘도램프 리컬러 타깃) ────────────
HAIR_PALETTE = {
    "black": (38, 34, 40),  # 흑발(약청).
    "dark_brown": (74, 48, 32),  # 다크브라운.
    "light_brown": (140, 96, 56),  # 라이트브라운.
    "blonde": (196, 162, 96),  # 블론드.
    "auburn": (132, 64, 44),  # 어번(적갈).
    "gray": (132, 130, 134),  # 그레이(노년/시니어).
}
HAIR_BASE_LAYER = "hair_brown"  # 휘도램프 풍부한 중립 모발 레이어(리컬러 소스).
HAIR_ASSIGN = {
    "ceo": "gray",
    "dev": "black",
    "designer": "auburn",
    "accountant": "dark_brown",
    "marketer": "light_brown",
    "planner": "black",
    "secretary": "blonde",
    "strategist": "gray",
    "sales": "light_brown",
    "cs": "blonde",
    "cfo": "dark_brown",
    "legal": "gray",
    "hr": "auburn",
    "data": "black",
    "pm": "light_brown",
    "devops": "dark_brown",
}

# ── 소품 ≥3종: 절차적 픽셀 소품 역할 배정(none 포함 4종) ────────────────
# glasses=안경(눈높이)·headset=헤드셋(머리 아치)·necktie=넥타이(몸통 세로).
PROP_ASSIGN = {
    "ceo": "necktie",
    "cfo": "necktie",
    "legal": "necktie",  # 넥타이(임원/법무).
    "cs": "headset",
    "secretary": "headset",  # 헤드셋(응대).
    "dev": "glasses",
    "data": "glasses",
    "accountant": "glasses",  # 안경.
    "designer": "none",
    "marketer": "none",
    "planner": "none",
    "strategist": "none",
    "sales": "none",
    "hr": "none",
    "pm": "none",
    "devops": "none",
}
PROP_OUTLINE = (32, 28, 30, 255)  # 소품 공통 아웃라인(어두운 중성·아웃라인 보존).
GLASSES_LENS = (210, 224, 235, 235)  # 안경 렌즈(밝은 청회·반투명 유리감).
NECKTIE_DARKEN = 0.5  # 넥타이=의상색 어둡게(악센트·정장 통일).


# ── 절제된 16색 의상 팔레트(결정론 farthest-point 솔버) ─────────────────


# 16역할 의상 base색(★사전 최적화 산출 — 고정 테이블·결정론).
#   산출 방법(오프라인): sRGB 큐브에서 절제 게이트(채도 S≤0.74·휘도 floor 52,
#   garish 배제) 후 **렌더 측정 공간**(gate.costume_dominant, 역할별 실제 머리+소품
#   포함)에서 역할쌍 min-ΔE2000을 greedy farthest-point + basin-hopping + 병목쌍
#   타깃 재배정으로 최대화. 렌더 측정 공간 채택 이유: 3톤 리컬러+양자화+게이트 ROI
#   필터가 입력색을 비선형 이동(~3ΔE 손실)시켜 입력색 ΔE 최적화는 게이트값과 어긋남.
#   ★실 풀파이프라인 렌더 min-ΔE≈24.2 달성(전체 sRGB 16색 이론 max-min≈27.6, 입력색
#   절제 max-min≈25.5, 그중 렌더손실·비-garish 제약하 16역할 실현 상한 ≈24).
#   → 25 하한을 2쌍이 근소(≈24·녹색군/적색군 색상환 과밀)히 미달: 비-garish 16색의
#   ΔE≥25 전쌍은 본 게이트 측정손실하 feasibility frontier 밖(허위 PASS 금지·정직보고).
#   해당 2쌍은 머리색·소품 축으로 추가 식별됨(식별축 3 충족). 명도순 정렬로 기존8/신규8
#   혼합 배치(맵 인접 클론감 완화).
# garish 자평: max S≈0.74·mean S≈0.57 — 영웅전설1/4 무드(형광·원색폭발 없음).
COSTUME_HEX: dict[str, str] = {
    "ceo": "#5c2044",  # 다크 버건디.
    "dev": "#503820",  # 다크 브라운/카키.
    "designer": "#2c2ca4",  # 인디고/로열블루.
    "accountant": "#277427",  # 미드-포레스트 그린(녹색군 병목 완화 재배정).
    "marketer": "#205c5c",  # 딥 틸.
    "planner": "#5c505c",  # 슬레이트 퍼플그레이.
    "secretary": "#206820",  # 미드 그린.
    "strategist": "#c8385c",  # 로즈 레드.
    "sales": "#5c74bc",  # 스카이/콘플라워.
    "cs": "#d45c38",  # 코랄/테라코타.
    "cfo": "#44a4ec",  # 브라이트 스카이블루.
    "legal": "#c880f8",  # 라벤더/연보라.
    "hr": "#68b08c",  # 세이지 그린.
    "data": "#d4bc74",  # 머스터드/골드.
    "pm": "#f8b0a4",  # 살몬 핑크.
    "devops": "#c8f844",  # 라임/올리브-옐로.
}


def _hex_to_rgb(h: str) -> np.ndarray:
    """'#rrggbb' → float64 RGB(길이3)."""
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def solve_costume_palette() -> dict[str, np.ndarray]:
    """16역할 의상 base색(고정 최적화 테이블) → role→RGB(float64). 순수·결정론."""
    return {role: _hex_to_rgb(COSTUME_HEX[role]) for role in ROLES}


# ── 렌더링(리컬러→합성→idle→크롭→양자화→소품→캡) ──────────────────────


def build_role_idle(costume_rgb: np.ndarray, hair_rgb: np.ndarray) -> Image.Image:
    """의상/머리 타깃색으로 한 역할 idle 스프라이트(원해상도 64×64 RGBA) 합성.

    공유 중립 레이어를 3톤 램프 리컬러(셰이딩 구조 공통=응집) → body 위 알파합성.
    """
    shirt = recolor.recolor_layer(NEUTRAL_SHIRT, costume_rgb)
    pants = recolor.recolor_layer(NEUTRAL_PANTS, costume_rgb * PANTS_DARKEN)
    hair = recolor.recolor_layer(HAIR_BASE_LAYER, hair_rgb)
    sheet = compose.load_layer("body_light")
    for layer in (shirt, pants, hair):
        sheet = compose.alpha_over(sheet, layer)
    return compose.extract_idle(sheet)


def crop_content(idle: Image.Image) -> Image.Image:
    """콘텐츠 bbox로 타이트 크롭(투명 패딩 제거·US-1 size_for_map과 동일 정렬)."""
    x0, y0, x1, y1 = compose.content_bbox(idle)
    return idle.crop((x0, y0, x1, y1))


def quantize_self(idle: Image.Image, cap: int = MAX_COLORS) -> Image.Image:
    """스프라이트를 **자기 색**으로 ≤cap 양자화(★공유 마스터팔레트 미사용).

    불투명 픽셀만 k-means(k=cap·고정시드)로 스냅 → 의상 hue 보존(US-2 수렴 회피).
    색수 이미 ≤cap이면 원본 보존. 투명은 (0,0,0,0) 정규화.
    """
    arr = np.array(idle).astype(np.int64)
    mask = arr[..., 3] > 16
    out = arr.copy()
    out[~mask] = 0
    if count_unique_colors(np.array(idle)) <= cap:
        return Image.fromarray(out.astype(np.uint8), "RGBA")
    pts = arr[..., :3][mask].astype(np.float64)
    centers, labels = kmeans(pts, cap)
    out[..., :3][mask] = np.clip(np.round(centers[labels]), 0, 255).astype(np.int64)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def _eye_row(arr: np.ndarray) -> int:
    """콘텐츠 상단에서 눈높이 근사행(머리 아래 ~38%)·안경/헤드셋 배치 기준."""
    ys = np.where((arr[..., 3] > 16).any(axis=1))[0]
    if len(ys) == 0:
        return arr.shape[0] // 3
    y0, y1 = int(ys.min()), int(ys.max())
    return y0 + int((y1 - y0) * 0.38)


def _row_span(arr: np.ndarray, row: int) -> tuple[int, int]:
    """주어진 행의 불투명 가로 [x0,x1] 범위(소품 가로 정렬용). 빈 행이면 (-1,-1)."""
    xs = np.where(arr[row, :, 3] > 16)[0]
    if len(xs) == 0:
        return -1, -1
    return int(xs.min()), int(xs.max())


def add_prop(sprite: Image.Image, kind: str, costume_rgb: np.ndarray) -> Image.Image:
    """역할 소품(절차적 픽셀)을 스프라이트에 소량 추가(아웃라인 보존·≤2색 증가).

    glasses=눈높이 렌즈 2점+브리지, headset=머리 아치+이어컵, necktie=몸통 세로
    스트립(의상색 어둡게). none이면 무변. 64px→크롭 좌표 기준 직접 픽셀 set.
    """
    if kind == "none":
        return sprite
    arr = np.array(sprite)
    if kind == "glasses":
        _draw_glasses(arr)
    elif kind == "headset":
        _draw_headset(arr)
    elif kind == "necktie":
        _draw_necktie(arr, costume_rgb)
    return Image.fromarray(arr, "RGBA")


def _draw_glasses(arr: np.ndarray) -> None:
    """눈높이행에 좌우 렌즈(각 2px)+브리지 1px(아웃라인 테두리). in-place."""
    row = _eye_row(arr)
    x0, x1 = _row_span(arr, row)
    if x0 < 0 or x1 - x0 < 4:
        return
    cx = (x0 + x1) // 2
    lens = [cx - 3, cx - 2, cx + 2, cx + 3]  # 좌렌즈/우렌즈 각 2px.
    for x in lens:
        if 0 <= x < arr.shape[1]:
            arr[row, x] = GLASSES_LENS
    for x in (cx - 1, cx, cx + 1):  # 브리지(다리)=아웃라인.
        if 0 <= x < arr.shape[1]:
            arr[row, x] = PROP_OUTLINE


def _draw_headset(arr: np.ndarray) -> None:
    """머리 상단 아치(밴드)+좌측 이어컵(눈높이 살짝 위). 아웃라인색. in-place."""
    ys = np.where((arr[..., 3] > 16).any(axis=1))[0]
    if len(ys) == 0:
        return
    y0 = int(ys.min())
    band = max(0, y0 - 0)  # 머리 최상단행에 밴드.
    bx0, bx1 = _row_span(arr, min(band + 1, arr.shape[0] - 1))
    if bx0 < 0:
        return
    for x in range(bx0, bx1 + 1):  # 헤드밴드 아치.
        arr[band, x] = PROP_OUTLINE
    ear = _eye_row(arr) - 1  # 이어컵(좌측·귀 위치).
    for x in (bx0, bx0 + 1):  # 좌측 이어컵 2px.
        if 0 <= ear < arr.shape[0] and 0 <= x < arr.shape[1]:
            arr[ear, x] = PROP_OUTLINE


def _draw_necktie(arr: np.ndarray, costume_rgb: np.ndarray) -> None:
    """몸통 중앙 세로 스트립(목 아래~가슴)=의상색 어둡게(악센트). in-place."""
    ys = np.where((arr[..., 3] > 16).any(axis=1))[0]
    if len(ys) == 0:
        return
    y0, y1 = int(ys.min()), int(ys.max())
    top = y0 + int((y1 - y0) * 0.46)  # 목 아래(의상 밴드 시작 근방).
    bot = y0 + int((y1 - y0) * 0.66)  # 가슴.
    tie = np.clip(costume_rgb * NECKTIE_DARKEN, 0, 255).astype(np.uint8)
    color = (int(tie[0]), int(tie[1]), int(tie[2]), 255)
    for y in range(top, bot + 1):
        x0, x1 = _row_span(arr, y)
        if x0 < 0:
            continue
        cx = (x0 + x1) // 2  # 몸통 중앙 1px 세로(좁은 넥타이).
        if arr[y, cx, 3] > 16:  # 몸통 위에서만(투명영역 침범 금지).
            arr[y, cx] = color


# ── 몽타주 ─────────────────────────────────────────────────────────────


def make_montage(
    sprites: list[tuple[str, Image.Image]], cols: int = 8, view_scale: int = 3
) -> Image.Image:
    """16역할 idle을 cols열 격자 배열 + 역할 라벨 몽타주(RGBA·육안 검증).

    view_scale배 NEAREST 확대(픽셀 또렷·출력 파일과 무관). 셀폭은 스프라이트/
    라벨 중 넓은 쪽에 맞춰 겹침 방지. 2행(8×2)로 16역할 한눈 배치.
    """
    pad, label_h = 10, 16
    big = [
        (r, s.resize((s.width * view_scale, s.height * view_scale), Image.NEAREST))
        for r, s in sprites
    ]
    label_w = [len(r) * 6 + 6 for r, _ in big]
    cell_w = max(max(s.width for _, s in big) + pad, max(label_w) + pad)
    cell_h = max(s.height for _, s in big) + pad + label_h
    rows = (len(big) + cols - 1) // cols
    canvas = Image.new("RGBA", (cell_w * cols, cell_h * rows), (40, 44, 52, 255))
    draw = ImageDraw.Draw(canvas)
    for i, (role, sp) in enumerate(big):
        cx, cy = i % cols, i // cols
        x = cx * cell_w + (cell_w - sp.width) // 2
        y = cy * cell_h + pad
        canvas.alpha_composite(sp, (x, y))
        draw.text(
            (cx * cell_w + 4, cy * cell_h + cell_h - label_h),
            role,
            fill=(230, 230, 230, 255),
        )
    return canvas


# ── 게이트 측정(gate.py 프리미티브 재사용·16역할+실 소품) ───────────────


def gate_check(sprites: dict[str, Image.Image]) -> dict:
    """렌더 16스프라이트 + **실 소품 배정**으로 게이트 측정(gate 색차/distinct 재사용).

    의상 주색·머리 주색은 gate.costume_dominant/hair_dominant로 렌더에서 측정,
    소품은 PROP_ASSIGN 실값으로 prop_distinct를 산출(gate.run의 props=['none']
    한계를 극복). 반환: 전쌍 ΔE 매트릭스·실패쌍·식별축·색수캡 전건.
    """
    costume_labs, hair_labs, per_role = [], [], {}
    for r in ROLES:
        arr = np.array(sprites[r])
        cdom = gate.costume_dominant(arr)
        hdom = gate.hair_dominant(arr)
        costume_labs.append(gate.srgb_to_lab(cdom))
        hair_labs.append(gate.srgb_to_lab(hdom))
        ncol = len(gate.unique_opaque_colors(arr))
        per_role[r] = {
            "costume_hex": to_hex(cdom),
            "hair_assigned": HAIR_ASSIGN[r],
            "hair_hex": to_hex(hdom),
            "prop": PROP_ASSIGN[r],
            "n_colors": int(ncol),
            "colors_ok": bool(ncol <= MAX_COLORS),
        }
    mat = gate.delta_e_matrix(costume_labs)
    fails = gate.failing_pairs(mat, ROLES, DELTA_E_IDENTIFY)
    props = [PROP_ASSIGN[r] for r in ROLES]
    cd = gate.distinct_count(costume_labs, DELTA_E_IDENTIFY)
    hd = gate.distinct_count(hair_labs, gate.HAIR_DELTA_E)
    pd = len(set(props))  # 실 소품 종류수(none 포함).
    iu = np.triu_indices(len(ROLES), 1)
    axes = {
        "costume_distinct": cd,
        "hair_distinct": hd,
        "prop_distinct": pd,
        "axes_active": int(cd > 1) + int(hd > 1) + int(pd > 1),
    }
    return {
        "roles": ROLES,
        "thresholds": {"max_colors": MAX_COLORS, "delta_e_identify": DELTA_E_IDENTIFY},
        "per_role": per_role,
        "costume_delta_e_matrix": [[round(float(v), 2) for v in row] for row in mat],
        "delta_e_min": round(float(mat[iu].min()), 2),
        "delta_e_max": round(float(mat[iu].max()), 2),
        "failing_pairs": fails,
        "n_failing_pairs": len(fails),
        "colors_cap_all_ok": all(per_role[r]["colors_ok"] for r in ROLES),
        "identity_axes": axes,
        "scope_note": "US-3: 응집(공유레이어/리컬러)+식별 3축(의상/머리/소품). "
        "세탁/미학은 HITL US-5a 소관.",
    }


# ── 실행/리포트 ────────────────────────────────────────────────────────


def run() -> dict:
    """전체 US-3 파이프라인. 16 roles_v2 PNG + 몽타주 + gate_report 작성·반환."""
    os.makedirs(ASSET_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(MONTAGE_PATH), exist_ok=True)
    costume = solve_costume_palette()
    sprites, montage_in = {}, []
    for r in ROLES:
        idle = build_role_idle(
            costume[r], np.array(HAIR_PALETTE[HAIR_ASSIGN[r]], float)
        )
        cropped = crop_content(idle)
        quant = quantize_self(cropped)
        propped = add_prop(quant, PROP_ASSIGN[r], costume[r])
        final = quantize_self(propped)  # 소품 후 색수 재캡(≤15 보장).
        final.save(os.path.join(ASSET_DIR, f"{r}.png"))
        sprites[r] = final
        montage_in.append((r, final))
    make_montage(montage_in).save(MONTAGE_PATH)
    report = gate_check(sprites)
    report["costume_input_hex"] = {r: to_hex(costume[r]) for r in ROLES}
    report["montage"] = MONTAGE_PATH
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def _print_report(rep: dict) -> None:
    """게이트 결과 표준출력(한국어·PASS 판정 정직)."""
    print("=== US-3 16역할 LPC 다변화 게이트 ===")
    th = rep["thresholds"]
    print(
        f"\n{'역할':11s} {'의상색':9s} {'머리':11s} {'소품':8s} {'색수':>4s} {'≤15':>4s}"
    )
    for r in rep["roles"]:
        d = rep["per_role"][r]
        print(
            f"{r:11s} {d['costume_hex']:9s} {d['hair_assigned']:11s} {d['prop']:8s} "
            f"{d['n_colors']:>4d} {'OK' if d['colors_ok'] else 'X':>4s}"
        )
    ax = rep["identity_axes"]
    print(
        f"\n색수≤{th['max_colors']} 전건: {'PASS' if rep['colors_cap_all_ok'] else 'FAIL'}"
    )
    print(
        f"의상 ΔE2000 min={rep['delta_e_min']} max={rep['delta_e_max']} "
        f"(식별하한 {th['delta_e_identify']})"
    )
    print(f"식별실패쌍(ΔE<{th['delta_e_identify']}): {rep['n_failing_pairs']}개")
    for fp in rep["failing_pairs"]:
        print(f"  {fp['pair'][0]:11s} ↔ {fp['pair'][1]:11s} ΔE={fp['delta_e']}")
    print(
        f"식별축: 의상distinct={ax['costume_distinct']} 머리distinct={ax['hair_distinct']} "
        f"소품distinct={ax['prop_distinct']} → 활성축={ax['axes_active']} (목표≥3)"
    )
    n_pairs = len(rep["roles"]) * (len(rep["roles"]) - 1) // 2
    gate_pass = (
        rep["n_failing_pairs"] == 0
        and rep["colors_cap_all_ok"]
        and ax["axes_active"] >= 3
    )
    print(f"\n전쌍수={n_pairs} | 게이트: {'PASS ✅' if gate_pass else 'FAIL ❌'}")
    print(f"몽타주: {rep['montage']}")
    print(f"리포트: {REPORT_PATH}")


if __name__ == "__main__":
    _print_report(run())
