/**
 * tiledMapLoader — 타일맵 JSON을 파싱해 결정론적 배치 목록으로 변환 (Phase 1 배경 타일).
 *
 * office.json(자체 스키마)을 읽어 각 레이어(floor→decor 순)의 비어있지 않은 셀을
 * TilePlacement(frame·col·row)로 펼친다. 파싱·좌표 환산은 전부 순수 함수다(pixi 무의존).
 *
 * 텍스처(BaseTexture)는 TwoDStage가 비동기 로드 후 setTileAtlas로 주입한다(주입 시임).
 * applyScene은 getTileAtlas()로 받아 배경 스프라이트를 만든다 — 미주입 시 배경 생략(기존 동작).
 */

// ── 파싱 입출력 타입 ──────────────────────────────────────────────────────────

/** office.json 원본 스키마. data는 행우선(row-major) 1차원·길이 cols*rows. */
export interface RawTileMap {
  tileWidth: number;
  tileHeight: number;
  cols: number;
  rows: number;
  tileset: {
    image: string;
    columns: number;
    tileCount: number;
    tileWidth: number;
    tileHeight: number;
  };
  layers: { name: string; data: number[] }[];
}

/** 한 칸의 배치 — 아틀라스 frame(0-based)과 격자 좌표. */
export interface TilePlacement {
  frame: number;
  col: number;
  row: number;
}

/** 파싱 결과 — 렌더에 필요한 모든 결정론 값. */
export interface ParsedTileMap {
  tileWidth: number;
  tileHeight: number;
  cols: number;
  rows: number;
  atlasColumns: number;
  pixelWidth: number;
  pixelHeight: number;
  placements: TilePlacement[];
}

// ── 순수 파싱 ────────────────────────────────────────────────────────────────

/**
 * 원본 타일맵을 파싱해 배치 목록을 만든다(레이어 순서대로 — floor 먼저).
 *
 * 각 레이어 data를 셀 인덱스 i로 순회: 값 v>=1이면 placement
 * `{ frame: v-1, col: i%cols, row: floor(i/cols) }` 추가, v=0이면 skip(빈칸).
 * 값→frame 매핑: 1..tileCount → 아틀라스 frame 0..(tileCount-1).
 *
 * @param raw office.json 원본.
 * @returns 결정론적 ParsedTileMap(같은 입력 → 같은 출력).
 */
export function parseTiledMap(raw: RawTileMap): ParsedTileMap {
  const { cols, rows, tileWidth, tileHeight } = raw;
  const placements: TilePlacement[] = [];

  for (const layer of raw.layers) {
    for (let i = 0; i < layer.data.length; i += 1) {
      const v = layer.data[i];
      if (v >= 1) {
        placements.push({
          frame: v - 1,
          col: i % cols,
          row: Math.floor(i / cols),
        });
      }
    }
  }

  return {
    tileWidth,
    tileHeight,
    cols,
    rows,
    atlasColumns: raw.tileset.columns,
    pixelWidth: cols * tileWidth,
    pixelHeight: rows * tileHeight,
    placements,
  };
}

/**
 * 격자 좌표(col,row)를 좌상단 픽셀로 환산한다(순수·결정론).
 * @returns `{ x: col*tileW, y: row*tileH }`.
 */
export function tilePixel(
  col: number,
  row: number,
  tileW: number,
  tileH: number
): { x: number; y: number } {
  return { x: col * tileW, y: row * tileH };
}

// ── 텍스처 주입 시임 (모듈 스코프 — TwoDStage가 비동기 로드 후 주입) ───────────

/** 주입되는 아틀라스 — BaseTexture(pixi 타입 회피 위해 unknown)와 파싱 맵. */
export interface TileAtlas {
  baseTexture: unknown;
  map: ParsedTileMap;
}

/** 주입된 아틀라스(없으면 null — 배경 미생성). 모듈 스코프. */
let _atlas: TileAtlas | null = null;

/** 아틀라스를 주입한다(TwoDStage가 office.png·office.json 로드 후 호출). null로 해제 가능. */
export function setTileAtlas(a: TileAtlas | null): void {
  _atlas = a;
}

/** 주입된 아틀라스를 반환한다(없으면 null). applyScene이 배경 생성 전에 조회. */
export function getTileAtlas(): TileAtlas | null {
  return _atlas;
}

/** 테스트 격리용 — 주입된 아틀라스를 초기화한다. */
export function __resetTileAtlas(): void {
  _atlas = null;
}

// ── 오피스 에셋 번들은 officeAssets.ts로 이관 (qwen 단일 스타일 로스터 전환) ─────
//
// 기존 OfficeAssets(LPC 레이어 + 타일시트 sub) 모델은 폐기되고, qwen 캐릭터/바닥/
// 가구 텍스처 번들이 officeAssets.ts의 getOfficeAssets/setOfficeAssets/OfficeAssets로
// 옮겨졌다. tiledMapLoader는 office.json 파싱(parseTiledMap)과 TileAtlas 시임만 유지한다.
