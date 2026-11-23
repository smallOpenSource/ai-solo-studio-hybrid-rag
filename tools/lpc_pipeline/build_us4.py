"""US-4 — 오피스 9존 타일: 절차적 32px 인테리어 타일셋 + Tiled JSON 9존 레이아웃.

LPC 16역할 도트 캐릭터(`web/public/assets/characters/roles_v2/{16}.png`·30×49·¾탑다운·
≤15색·selout)와 **응집**하는 인테리어 타일셋과, 표준 Tiled orthogonal 타일맵(9존)을
**결정론적 단일패스 PIL 드로잉**으로 생성한다.

응집 설계(사용자 3~4회 "다 제각각" 거부 해소):
  - **동일 32px 그리드**·**¾탑다운 시점**(가구는 위/앞면 분리·상단 어둡게)·
  - **절제된 제한 팔레트**(JRPG 무드·형광 금지·전체 ≤24색)·**selout 클린 아웃라인**
    (어두운 중성 외곽선·캐릭터와 동일)·**동일 픽셀 밀도**(거친 디더 없이 평면+1px 셰이드).
  - 톤=중립/따뜻한 오피스(우드 바닥·뉴트럴 벽·우드 책상·녹색 화분·존 러그 색구분).

산출(★신규 파일만·기존 0수정):
  - web/public/assets/office_v2/tileset.png  : 격자 타일셋 PNG(각 타일 인덱스 고정).
  - web/public/assets/office_v2/office.tmj    : 표준 Tiled orthogonal JSON(레이어+spawns).
  - work/lpc/us4_office_preview.png           : 전체 맵 PIL 합성 프리뷰(육안 검증·스프라이트 오버레이).

★안전: 검색/최적화/반복 루프 **전무**. 전부 고정 좌표/색의 결정론 단일패스. 동일입력→동일산출.
★실행(safe_run 경유 필수):
  bash .omc/guards/safe_run.sh 120 4000000 -- \
    /opt/conda/envs/one_man_corp-crawl/bin/python tools/lpc_pipeline/build_us4.py
"""

from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw

# ── 산출 경로 ──────────────────────────────────────────────────────────
ROOT = "/opt/studio"
ASSET_DIR = os.path.join(ROOT, "web/public/assets/office_v2")
TILESET_PATH = os.path.join(ASSET_DIR, "tileset.png")
TMJ_PATH = os.path.join(ASSET_DIR, "office.tmj")
PREVIEW_PATH = os.path.join(ROOT, "work/lpc/us4_office_preview.png")
CHAR_DIR = os.path.join(ROOT, "web/public/assets/characters/roles_v2")

TILE = 32  # 타일 한 변(캐릭터 그리드와 동일 픽셀 밀도).

# ── 제한 팔레트(전체 ≤24색·JRPG 무드·형광 금지·캐릭터 selout과 정합) ──────
# 어두운 중성 아웃라인 1색을 모든 타일이 공유(=selout 응집). 따뜻한 오피스 톤.
PAL = {
    "outline": (40, 35, 46),  # 공통 selout 아웃라인(캐릭터 PROP_OUTLINE 계열).
    "shadow": (58, 52, 64),  # 어두운 셰이드(가구 상단/접지 그림자).
    # 우드 바닥(따뜻한 우드 2톤 — 보드 줄눈).
    "floor_wood": (150, 112, 72),
    "floor_wood_d": (128, 94, 58),
    # 뉴트럴 바닥(복도/공용 — 차분한 그레이 베이지 2톤).
    "floor_neut": (176, 168, 156),
    "floor_neut_d": (156, 148, 136),
    # 벽(뉴트럴 웜그레이 — 상단 면/하단 굽도리).
    "wall": (198, 190, 180),
    "wall_d": (168, 160, 150),
    "wall_base": (120, 112, 104),  # 벽 하단 굽도리(접지 어둡게).
    # 우드 책상(중간 우드 — 천판/측면/상단 하이라이트).
    "desk_top": (164, 120, 74),
    "desk_side": (132, 94, 56),
    "desk_hi": (208, 168, 116),
    # 의자(차분한 슬레이트 블루 — 좌면/등받이).
    "chair": (96, 110, 132),
    "chair_d": (74, 86, 106),
    # 화분(녹색 잎 2톤 + 우드 화분).
    "plant_lf": (96, 146, 84),
    "plant_lf_d": (72, 116, 64),
    "plant_pot": (150, 96, 62),
    # 파티션(반투 유리 칸막이 — 옅은 청회 + 프레임).
    "part_glass": (188, 204, 210),
    "part_frame": (120, 128, 134),
    # 문(우드 도어 + 손잡이).
    "door": (118, 86, 54),
    "door_h": (208, 196, 120),
    # 존 러그 9색(채도 절제·서로 식별·바닥 위 부드러운 악센트).
    "rug_ceo": (150, 92, 96),  # 버건디(임원).
    "rug_coo": (104, 120, 150),  # 슬레이트 블루(운영).
    "rug_cfo": (108, 144, 132),  # 세이지 틸(재무).
    "rug_legal": (140, 124, 156),  # 라벤더 그레이(법무).
    "rug_pm": (168, 144, 100),  # 머스터드 베이지(기획).
    "rug_research": (120, 138, 110),  # 올리브 그린(리서치).
    "rug_dev": (110, 130, 146),  # 인디고 그레이(개발).
    "rug_design": (158, 116, 132),  # 로즈(디자인).
    "rug_market": (160, 138, 108),  # 탠(마케팅).
    "rug_customer": (118, 146, 140),  # 민트(고객).
}

# 러그 키 순서(타일 인덱스/존 매핑 고정) — 9존 식별색.
RUG_KEYS = [
    "rug_ceo",
    "rug_coo",
    "rug_cfo",
    "rug_legal",
    "rug_pm",
    "rug_research",
    "rug_dev",
    "rug_design",
    "rug_market",
    "rug_customer",
]


def _img() -> Image.Image:
    """투명 32×32 RGBA 타일 캔버스(신규)."""
    return Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))


def _selout(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    """사각 영역 1px 아웃라인(공통 selout·캐릭터와 동일한 어두운 외곽선)."""
    draw.rectangle(box, outline=PAL["outline"], width=1)


# ── 개별 타일 드로잉(전부 결정론·고정 좌표) ─────────────────────────────


def tile_floor(wood: bool) -> Image.Image:
    """바닥 타일 — 우드/뉴트럴 2톤 보드 줄눈(¾탑다운·평면+1px 줄눈). 아웃라인 없음(연속)."""
    im = _img()
    d = ImageDraw.Draw(im)
    base = PAL["floor_wood"] if wood else PAL["floor_neut"]
    dark = PAL["floor_wood_d"] if wood else PAL["floor_neut_d"]
    d.rectangle((0, 0, TILE - 1, TILE - 1), fill=base)
    # 보드 줄눈(세로 2줄·우드는 판자감·뉴트럴은 타일감). 결정론 고정열.
    for x in (10, 21):
        d.line((x, 0, x, TILE - 1), fill=dark)
    d.line((0, TILE - 1, TILE - 1, TILE - 1), fill=dark)  # 하단 줄눈(접지).
    return im


def tile_wall_top() -> Image.Image:
    """벽 상단 면(룸 뒤벽 윗부분·밝은 면+상단 음영). ¾탑다운 벽 윗단."""
    im = _img()
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, TILE - 1, TILE - 1), fill=PAL["wall"])
    d.rectangle((0, 0, TILE - 1, 5), fill=PAL["wall_d"])  # 상단 음영 밴드.
    d.line((0, 0, TILE - 1, 0), fill=PAL["outline"])  # 최상단 selout.
    return im


def tile_wall_side() -> Image.Image:
    """벽 하단(굽도리 포함·바닥 접지 어둡게). 룸 경계 세로/하단벽 본체."""
    im = _img()
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, TILE - 1, TILE - 1), fill=PAL["wall"])
    d.rectangle((0, TILE - 7, TILE - 1, TILE - 1), fill=PAL["wall_base"])  # 굽도리.
    d.line((0, TILE - 1, TILE - 1, TILE - 1), fill=PAL["outline"])  # 접지 selout.
    return im


def tile_desk() -> Image.Image:
    """책상 — ¾탑다운 카운터(천판 윗면 + 앞 전면·우드 2톤·selout). 가로 edge-to-edge.

    캐릭터 발밑 아래 행에 깔려 **앞쪽 카운터**로 읽힘. 가로로 2칸 이어붙여도
    한 책상으로 보이도록 좌우 끝까지 채우고 상/하단만 selout(세로 외곽선 생략).
    """
    im = _img()
    d = ImageDraw.Draw(im)
    d.rectangle((0, 4, TILE - 1, TILE - 3), fill=PAL["desk_top"])  # 천판(가로 풀폭).
    d.rectangle((0, TILE - 10, TILE - 1, TILE - 3), fill=PAL["desk_side"])  # 앞 전면.
    d.line((0, 6, TILE - 1, 6), fill=PAL["desk_hi"])  # 천판 상단 하이라이트(우드결).
    d.line((0, 4, TILE - 1, 4), fill=PAL["outline"])  # 상단 selout.
    d.line((0, TILE - 3, TILE - 1, TILE - 3), fill=PAL["outline"])  # 하단 selout(접지).
    return im


def tile_chair() -> Image.Image:
    """의자 — ¾탑다운(좌면 + 등받이 윗부분·슬레이트 블루·selout)."""
    im = _img()
    d = ImageDraw.Draw(im)
    d.rectangle((9, 12, TILE - 10, TILE - 6), fill=PAL["chair"])  # 좌면.
    d.rectangle((9, 7, TILE - 10, 12), fill=PAL["chair_d"])  # 등받이.
    _selout(d, (9, 7, TILE - 10, TILE - 6))
    return im


def tile_plant() -> Image.Image:
    """화분 — 우드 화분 + 둥근 잎 덩어리 2톤(녹색·selout). 코너/존 데코."""
    im = _img()
    d = ImageDraw.Draw(im)
    d.rectangle((11, TILE - 11, TILE - 12, TILE - 5), fill=PAL["plant_pot"])  # 화분.
    _selout(d, (11, TILE - 11, TILE - 12, TILE - 5))
    d.ellipse((8, 4, TILE - 9, TILE - 11), fill=PAL["plant_lf"])  # 잎 덩어리.
    d.ellipse((11, 6, TILE - 14, 15), fill=PAL["plant_lf_d"])  # 잎 음영.
    d.ellipse((8, 4, TILE - 9, TILE - 11), outline=PAL["outline"], width=1)
    return im


def tile_partition(vertical: bool) -> Image.Image:
    """파티션 칸막이 — 반투 유리 + 프레임(존 구분·세로/가로). selout 프레임."""
    im = _img()
    d = ImageDraw.Draw(im)
    if vertical:
        d.rectangle((12, 0, TILE - 13, TILE - 1), fill=PAL["part_glass"])
        _selout(d, (12, 0, TILE - 13, TILE - 1))
    else:
        d.rectangle((0, 12, TILE - 1, TILE - 13), fill=PAL["part_glass"])
        _selout(d, (0, 12, TILE - 1, TILE - 13))
    return im


def tile_door() -> Image.Image:
    """문 — 우드 도어 패널 + 손잡이(룸 출입구·selout)."""
    im = _img()
    d = ImageDraw.Draw(im)
    d.rectangle((6, 2, TILE - 7, TILE - 1), fill=PAL["door"])
    d.rectangle(
        (9, 6, TILE - 10, TILE - 5), outline=PAL["desk_side"], width=1
    )  # 패널 결.
    d.rectangle((TILE - 11, 15, TILE - 9, 17), fill=PAL["door_h"])  # 손잡이.
    _selout(d, (6, 2, TILE - 7, TILE - 1))
    return im


def tile_rug(color_key: str) -> Image.Image:
    """존 러그 — 둥근 모서리 사각 + 1px 안쪽 테두리(존 식별색·바닥 위 악센트)."""
    im = _img()
    d = ImageDraw.Draw(im)
    c = PAL[color_key]
    inner = tuple(max(0, v - 28) for v in c)  # 러그 안쪽 테두리(같은 색 어둡게).
    d.rectangle((2, 2, TILE - 3, TILE - 3), fill=c)
    d.rectangle((5, 5, TILE - 6, TILE - 6), outline=inner, width=1)
    return im


# ── 타일셋 조립(인덱스 고정·firstgid=1 기준 frame=index) ─────────────────


def build_tile_list() -> list[tuple[str, Image.Image]]:
    """(name, image) 타일 목록 — 인덱스 = Tiled gid-1. 고정 순서(결정론)."""
    tiles: list[tuple[str, Image.Image]] = [
        ("floor_wood", tile_floor(True)),  # 0
        ("floor_neutral", tile_floor(False)),  # 1
        ("wall_top", tile_wall_top()),  # 2
        ("wall_side", tile_wall_side()),  # 3
        ("desk", tile_desk()),  # 4
        ("chair", tile_chair()),  # 5
        ("plant", tile_plant()),  # 6
        ("partition_v", tile_partition(True)),  # 7
        ("partition_h", tile_partition(False)),  # 8
        ("door", tile_door()),  # 9
    ]
    for key in RUG_KEYS:  # 10..19 — 9존 러그 식별색(10종).
        tiles.append((key, tile_rug(key)))
    return tiles


def compose_tileset(tiles: list[tuple[str, Image.Image]], columns: int) -> Image.Image:
    """타일을 columns열 격자 PNG로 배치(인덱스=행우선 위치·frame 고정)."""
    n = len(tiles)
    rows = (n + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * TILE, rows * TILE), (0, 0, 0, 0))
    for i, (_name, im) in enumerate(tiles):
        x, y = (i % columns) * TILE, (i // columns) * TILE
        sheet.alpha_composite(im, (x, y))
    return sheet


def count_colors(sheet: Image.Image) -> int:
    """불투명 픽셀 고유색 수(팔레트 검증·아웃라인 제외 의미는 보고에서)."""
    cols = sheet.getcolors(1 << 20) or []
    return len({rgba[:3] for _, rgba in cols if rgba[3] > 8})


# ── 9존 맵 레이아웃(고정 격자·결정론) ───────────────────────────────────
# gid 상수(firstgid=1 → gid = index+1).
G_FLOOR_W = 1
G_FLOOR_N = 2
G_WALL_T = 3
G_WALL_S = 4
G_DESK = 5
G_CHAIR = 6
G_PLANT = 7
G_PART_V = 8
G_PART_H = 9
G_DOOR = 10
G_RUG = {k: 11 + i for i, k in enumerate(RUG_KEYS)}  # 러그 gid(11..20).

MAP_W = 40  # 가로 타일수(단일 개방형 룸·와이드).
MAP_H = 26  # 세로 타일수(단일 룸 고정 — 밴드누적 폐기).

# ── 단일 개방형 룸 zone 정의(재설계 §4.2·§4.5) ─────────────────────────────
# 세로밴드 누적 폐기 → 칸막이 없는 단일 룸 + 7 팀구역(경영진+6팀)을 러그 색으로 식별.
# ZONES = (zone_name, 러그키[기존 RUG_KEYS 재사용], [배정역할]).
#   Executive(ceo,strategist,cfo,legal) / Dev(dev,devops) / Product(pm,planner) /
#   DataFin(data,accountant) / Design(designer) / GtM(marketer,secretary,sales) /
#   PeopleCS(cs,hr). 16역할 1:1 분배.
ZONES = [
    ("Executive", "rug_ceo", ["ceo", "strategist", "cfo", "legal"]),
    ("Dev", "rug_dev", ["dev", "devops"]),
    ("Product", "rug_pm", ["pm", "planner"]),
    ("DataFin", "rug_research", ["data", "accountant"]),
    ("Design", "rug_design", ["designer"]),
    ("GtM", "rug_market", ["marketer", "secretary", "sales"]),
    ("PeopleCS", "rug_customer", ["cs", "hr"]),
]

# ── 좌석 좌표(§4.4 리터럴 SEATS · role→(col,row) 타일 인덱스 · 모호함0) ──────
# 의자=(col,row)·책상=(col,row+1)+(col+1,row+1) 2칸·spawn px=col*TILE+TILE//2,
# py=(row+1)*TILE(발밑·책상행 하단). object 좌상단=(px-TILE//2, py-TILE).
# col 간격≥4(=128px) 가로 비겹침·row 군집(경영진4·팀13/19) 위계.
SEATS = {
    "ceo": (19, 3),
    "strategist": (12, 4),
    "cfo": (24, 4),
    "legal": (30, 4),
    "dev": (5, 13),
    "devops": (9, 13),
    "pm": (15, 13),
    "planner": (19, 13),
    "data": (25, 13),
    "accountant": (29, 13),
    "designer": (35, 13),
    "marketer": (5, 19),
    "secretary": (9, 19),
    "sales": (13, 19),
    "cs": (25, 19),
    "hr": (29, 19),
}

# ── 팀 구역 러그 직사각 범위(§12 M2 · col0..col1·row0..row1 inclusive · 인접 비겹침) ──
# 각 구역 col×row 범위에 팀 러그 gid 채움. zone_name → (col0, col1, row0, row1).
ZONE_RUG_RANGE = {
    "Executive": (10, 32, 2, 6),
    "Dev": (4, 11, 12, 14),
    "Product": (14, 21, 12, 14),
    "DataFin": (24, 31, 12, 14),
    "Design": (33, 38, 12, 14),
    "GtM": (4, 15, 18, 20),
    "PeopleCS": (24, 31, 18, 20),
}


def _band_rows() -> tuple[int, list[tuple[int, int]]]:
    """단일 룸 — 고정 높이 반환(밴드누적 폐기). 호출부 시그니처 호환을 위해 유지.

    반환: (map_h=MAP_H, [(0, MAP_H)]) — 단일 룸 1개·시작행0·전체높이.
    """
    return MAP_H, [(0, MAP_H)]


def _new_grid(map_h: int) -> list[list[int]]:
    """gid 0(빈칸) 채운 MAP_W×map_h 격자."""
    return [[0] * MAP_W for _ in range(map_h)]


def _fill_floor_walls(
    floor: list[list[int]], walls: list[list[int]], brow: list[tuple[int, int]]
) -> None:
    """단일 개방형 룸: 전면 우드 바닥 + 외벽 테두리만(내부 칸막이/파티션 0) + 상단 중앙 문 1개.

    brow는 시그니처 호환용(단일 룸이라 미사용). 룸 전체(0..MAP_H-1)에 바닥을 깔고
    상/하/좌/우 외벽만 두른다 — 개방형 핵심(내부 세로파티션·3룸분할 삭제).
    """
    y1 = MAP_H - 1
    for ry in range(MAP_H):  # 바닥 전면 우드(응집 통일).
        for rx in range(MAP_W):
            floor[ry][rx] = G_FLOOR_W
    for rx in range(MAP_W):  # 상/하 외벽.
        walls[0][rx] = G_WALL_T
        walls[y1][rx] = G_WALL_S
    for ry in range(MAP_H):  # 좌/우 외벽.
        walls[ry][0] = G_WALL_S
        walls[ry][MAP_W - 1] = G_WALL_S
    walls[0][MAP_W // 2] = G_DOOR  # 상단벽 중앙 문 1개(접근감).


def _place_furniture(
    furn: list[list[int]],
    brow: list[tuple[int, int]],
    spawns: list[dict],
) -> None:
    """단일 룸 좌석 배치: 팀 러그(구역 직사각) + 역할별 의자/책상(§4.4 SEATS) + spawn object.

    brow는 시그니처 호환용(단일 룸·SEATS가 직접 좌표 지정·미사용).
      - 러그: 각 zone 구역 col×row 범위(ZONE_RUG_RANGE)에 팀색 러그 gid 채움(furniture 깔개).
      - 좌석: SEATS[role]=(col,row) → 의자(col,row)·책상(col,row+1)+(col+1,row+1) 2칸.
      - spawn: px=col*TILE+TILE//2·py=(row+1)*TILE(발밑·책상행 하단). object=(px-16,py-32).
    """
    # 1) 팀 러그 — 구역 직사각 범위(좌석/책상보다 먼저 깔아 러그를 아래에).
    for zname, rug_key, _roles in ZONES:
        col0, col1, row0, row1 = ZONE_RUG_RANGE[zname]
        for ry in range(row0, row1 + 1):
            for rx in range(col0, col1 + 1):
                furn[ry][rx] = G_RUG[rug_key]
    # 2) 좌석(의자/책상)·spawn — role→zone 매핑으로 zone property 부여.
    role_zone = {role: zname for zname, _rk, roles in ZONES for role in roles}
    for role, (col, row) in SEATS.items():
        zname = role_zone[role]
        furn[row][col] = G_CHAIR  # 의자(캐릭터 상체 뒤·발밑 위).
        furn[row + 1][col] = G_DESK  # 책상(발밑 아래 행·앞면 노출).
        furn[row + 1][col + 1] = G_DESK  # 2칸 책상(넓은 워크스테이션 가독).
        spawns.append(
            {
                "role": role,
                "zone": zname,
                "px": col * TILE + TILE // 2,  # 스프라이트 중심 X.
                "py": (row + 1) * TILE,  # 발밑(책상행 하단 경계).
                "col": col,
                "row": row,
            }
        )


def grid_to_data(grid: list[list[int]]) -> list[int]:
    """격자→행우선 1차원 gid 배열(Tiled data·CSV 인코딩용)."""
    return [grid[r][c] for r in range(len(grid)) for c in range(MAP_W)]


# ── 표준 Tiled orthogonal JSON(.tmj) 작성 ───────────────────────────────


def _spawn_objects(spawns: list[dict]) -> list[dict]:
    """spawn 목록 → Tiled object 리스트(name=역할명·zone property·id 1..N). 순수."""
    objects = []
    for oid, sp in enumerate(spawns, start=1):
        objects.append(
            {
                "id": oid,
                "name": sp["role"],  # ★Phaser createFromObjects 키(역할명).
                "type": "spawn",
                "x": sp["px"] - TILE // 2,  # object 좌상단(타일 칸 좌상).
                "y": sp["py"] - TILE,
                "width": TILE,
                "height": TILE,
                "visible": True,
                "rotation": 0,
                "properties": [{"name": "zone", "type": "string", "value": sp["zone"]}],
            }
        )
    return objects


def _tileset_def(tile_count: int, columns: int) -> dict:
    """외부 image(tileset.png) 참조 Tiled tileset 정의(firstgid=1). 순수."""
    rows = (tile_count + columns - 1) // columns
    return {
        "firstgid": 1,
        "name": "office_v2",
        "image": "tileset.png",
        "imagewidth": columns * TILE,
        "imageheight": rows * TILE,
        "tilewidth": TILE,
        "tileheight": TILE,
        "tilecount": tile_count,
        "columns": columns,
        "margin": 0,
        "spacing": 0,
    }


def _map_layers(
    map_h: int, floor: list, walls: list, furn: list, objects: list[dict]
) -> list[dict]:
    """3 타일레이어(floor/walls/furniture) + spawns objectgroup 리스트. 순수."""
    base = {
        "type": "tilelayer",
        "width": MAP_W,
        "height": map_h,
        "x": 0,
        "y": 0,
        "opacity": 1,
        "visible": True,
    }
    return [
        {**base, "id": 1, "name": "floor", "data": grid_to_data(floor)},
        {**base, "id": 2, "name": "walls", "data": grid_to_data(walls)},
        {**base, "id": 3, "name": "furniture", "data": grid_to_data(furn)},
        {
            "type": "objectgroup",
            "id": 4,
            "name": "spawns",
            "x": 0,
            "y": 0,
            "opacity": 1,
            "visible": True,
            "draworder": "topdown",
            "objects": objects,
        },
    ]


def build_tiled_json(
    map_h: int,
    floor: list[list[int]],
    walls: list[list[int]],
    furn: list[list[int]],
    spawns: list[dict],
    tile_count: int,
    columns: int,
) -> dict:
    """표준 Tiled orthogonal 맵 dict(타일레이어 3 + spawns objectgroup·16 object).

    Phaser `load.tilemapTiledJSON` + `map.createLayer`/`createFromObjects` 직접 호환.
    tileset은 외부 image(tileset.png) 참조·firstgid=1. object는 name=역할명.
    """
    objects = _spawn_objects(spawns)
    return {
        "type": "map",
        "version": "1.10",
        "tiledversion": "1.10.2",
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "infinite": False,
        "width": MAP_W,
        "height": map_h,
        "tilewidth": TILE,
        "tileheight": TILE,
        "nextlayerid": 5,
        "nextobjectid": len(objects) + 1,
        "tilesets": [_tileset_def(tile_count, columns)],
        "layers": _map_layers(map_h, floor, walls, furn, objects),
    }


# ── PIL 프리뷰 합성(맵 렌더 + 16역할 스프라이트 오버레이) ────────────────


def render_preview(
    tiles: list[tuple[str, Image.Image]],
    map_h: int,
    layers: list[list[list[int]]],
    spawns: list[dict],
) -> Image.Image:
    """타일맵을 PIL로 합성한 전체 오피스 1장 + spawn 위치에 역할 스프라이트 오버레이.

    레이어 floor→walls→furniture 순 알파합성 → 캐릭터 스프라이트(30×49)를 발밑정렬.
    존 라벨 텍스트도 상단에 그려 육안 검증. NEAREST(픽셀 또렷)는 캔버스 자체가 32px.
    """
    by_index = [im for _n, im in tiles]
    canvas = Image.new("RGBA", (MAP_W * TILE, map_h * TILE), (44, 40, 52, 255))
    for grid in layers:  # floor→walls→furniture.
        for r in range(map_h):
            for c in range(MAP_W):
                gid = grid[r][c]
                if gid >= 1:
                    canvas.alpha_composite(by_index[gid - 1], (c * TILE, r * TILE))
    # 캐릭터 스프라이트 오버레이(spawn 발밑정렬·존당 역할).
    for sp in spawns:
        path = os.path.join(CHAR_DIR, f"{sp['role']}.png")
        if not os.path.exists(path):
            continue
        spr = Image.open(path).convert("RGBA")
        sx = sp["px"] - spr.width // 2
        sy = sp["py"] - spr.height  # 발밑 = py(칸 하단).
        canvas.alpha_composite(spr, (sx, sy))
        spr.close()
    # 존 라벨(각 팀구역 러그 좌상단·뉴트럴 흰색·육안 검증).
    draw = ImageDraw.Draw(canvas)
    for zname, _rug_key, _roles in ZONES:
        col0, _col1, row0, _row1 = ZONE_RUG_RANGE[zname]
        draw.text(
            (col0 * TILE + 2, row0 * TILE + 3),
            zname,
            fill=(245, 242, 235, 255),
        )
    return canvas


# ── 실행/보고 ──────────────────────────────────────────────────────────


def _zone_summary(brow: list[tuple[int, int]]) -> list[dict]:
    """단일 룸 팀구역별 요약(이름·러그행범위·가로타일·역할) 리스트. 순수·결정론.

    brow는 시그니처 호환용(단일 룸·미사용). 각 zone의 ZONE_RUG_RANGE 직사각 기준.
    run() 반환 dict 계약(zone/rows/width_tiles/roles 키) 보존.
    """
    out = []
    for zname, _rug_key, roles in ZONES:
        col0, col1, row0, row1 = ZONE_RUG_RANGE[zname]
        out.append(
            {
                "zone": zname,
                "rows": [row0, row1],
                "width_tiles": (col1 - col0 + 1),
                "roles": roles,
            }
        )
    return out


def _build_maps() -> tuple[int, list, list, list, list[dict]]:
    """floor/walls/furniture 격자 + spawns 생성(레이아웃 단일패스). → (map_h,F,W,Fu,sp)."""
    map_h, brow = _band_rows()
    floor, walls, furn = _new_grid(map_h), _new_grid(map_h), _new_grid(map_h)
    spawns: list[dict] = []
    _fill_floor_walls(floor, walls, brow)
    _place_furniture(furn, brow, spawns)
    return map_h, floor, walls, furn, spawns


def run() -> dict:
    """전체 US-4 파이프라인: 타일셋 PNG + office.tmj + 프리뷰 작성·요약 dict 반환."""
    os.makedirs(ASSET_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(PREVIEW_PATH), exist_ok=True)

    tiles = build_tile_list()
    columns = 5  # 타일셋 격자 열수(20타일 → 5×4).
    sheet = compose_tileset(tiles, columns)
    sheet.save(TILESET_PATH)
    n_colors = count_colors(sheet)

    map_h, floor, walls, furn, spawns = _build_maps()
    tmj = build_tiled_json(map_h, floor, walls, furn, spawns, len(tiles), columns)
    with open(TMJ_PATH, "w", encoding="utf-8") as f:
        json.dump(tmj, f, ensure_ascii=False, indent=1)
    render_preview(tiles, map_h, [floor, walls, furn], spawns).save(PREVIEW_PATH)

    _, brow = _band_rows()
    zones_summary = _zone_summary(brow)
    return {
        "tileset_png": TILESET_PATH,
        "tmj": TMJ_PATH,
        "preview": PREVIEW_PATH,
        "n_tile_types": len(tiles),
        "tile_names": [n for n, _ in tiles],
        "tileset_colors": n_colors,
        "map_tiles": [MAP_W, map_h],
        "map_pixels": [MAP_W * TILE, map_h * TILE],
        "n_zones": len(zones_summary),
        "zones": zones_summary,
        "n_spawn_objects": len(spawns),
        "spawn_roles": [s["role"] for s in spawns],
        "layers": ["floor", "walls", "furniture", "spawns(objectgroup)"],
    }


def _print_report(rep: dict) -> None:
    """US-4 산출 요약 표준출력(한국어·육안 검증 경로 강조)."""
    print("=== US-4 오피스 타일 (절차적 32px + Tiled JSON) ===")
    print(
        "  레이아웃: 단일 개방형 룸(외벽만·내부 칸막이0) "
        f"+ {len(rep['zones'])}팀구역(러그 색구분)."
    )
    print(f"\n타일셋 PNG : {rep['tileset_png']}")
    print(f"Tiled JSON : {rep['tmj']}")
    print(f"프리뷰     : {rep['preview']}  ★육안 검증")
    print(
        f"\n타일 종류 {rep['n_tile_types']}개 (색수 {rep['tileset_colors']}) "
        f"| 맵 {rep['map_tiles'][0]}×{rep['map_tiles'][1]} 타일 "
        f"= {rep['map_pixels'][0]}×{rep['map_pixels'][1]}px"
    )
    print(f"타일: {', '.join(rep['tile_names'])}")
    print(f"\n레이어: {', '.join(rep['layers'])}")
    print(f"\n단일 개방형 룸 · {rep['n_zones']}팀구역:")
    for z in rep["zones"]:
        print(
            f"  {z['zone']:20s} rows{z['rows']} w={z['width_tiles']:>2d} "
            f"역할={','.join(z['roles'])}"
        )
    print(
        f"\nspawn object {rep['n_spawn_objects']}개 (16역할): "
        f"{', '.join(rep['spawn_roles'])}"
    )
    n_set = len(set(rep["spawn_roles"]))
    print(f"고유 역할 {n_set}/16 {'✅ 전역할 배치' if n_set == 16 else '⚠ 누락/중복'}")


if __name__ == "__main__":
    _print_report(run())
