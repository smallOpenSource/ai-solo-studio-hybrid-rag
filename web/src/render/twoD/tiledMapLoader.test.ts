/**
 * tiledMapLoader 단위 테스트 (Vitest) — Phase 1 배경 타일 파싱.
 *
 * parseTiledMap/tilePixel은 순수 함수다(pixi 무의존). 작은 맵 리터럴로
 * 배치 정확성(0 skip·frame=v-1·col/row 계산)·픽셀 크기·결정론을 검증한다.
 */

import { describe, it, expect } from "vitest";
import { parseTiledMap, tilePixel, type RawTileMap } from "./tiledMapLoader";

// ── 헬퍼: 작은 2x2 맵 ──────────────────────────────────────────────────────────

/** 2x2 맵. floor=[1,2,3,4] 전부 채움, decor=[0,5,0,0] 한 칸만(0 skip 검증). */
function smallMap(): RawTileMap {
  return {
    tileWidth: 32,
    tileHeight: 32,
    cols: 2,
    rows: 2,
    tileset: {
      image: "/x.png",
      columns: 8,
      tileCount: 8,
      tileWidth: 32,
      tileHeight: 32,
    },
    layers: [
      { name: "floor", data: [1, 2, 3, 4] },
      { name: "decor", data: [0, 5, 0, 0] },
    ],
  };
}

describe("parseTiledMap — 배치·픽셀·아틀라스", () => {
  it("floor 4칸 + decor 1칸 = placements 5개(0 skip)", () => {
    const parsed = parseTiledMap(smallMap());
    expect(parsed.placements).toHaveLength(5);
  });

  it("frame = 값-1, col = i%cols, row = floor(i/cols) (floor 레이어)", () => {
    const parsed = parseTiledMap(smallMap());
    // floor가 먼저 순회되므로 처음 4개가 floor.
    const floor = parsed.placements.slice(0, 4);
    expect(floor).toEqual([
      { frame: 0, col: 0, row: 0 }, // i=0, v=1
      { frame: 1, col: 1, row: 0 }, // i=1, v=2
      { frame: 2, col: 0, row: 1 }, // i=2, v=3
      { frame: 3, col: 1, row: 1 }, // i=3, v=4
    ]);
  });

  it("decor의 0 칸은 skip, 값 칸만 placement(레이어 순서 floor→decor)", () => {
    const parsed = parseTiledMap(smallMap());
    // decor=[0,5,0,0] → i=1, v=5 한 개만. frame=4, col=1, row=0.
    const decor = parsed.placements.slice(4);
    expect(decor).toEqual([{ frame: 4, col: 1, row: 0 }]);
  });

  it("pixelWidth=cols*tileW, pixelHeight=rows*tileH, atlasColumns=tileset.columns", () => {
    const parsed = parseTiledMap(smallMap());
    expect(parsed.pixelWidth).toBe(64); // 2*32
    expect(parsed.pixelHeight).toBe(64); // 2*32
    expect(parsed.atlasColumns).toBe(8);
    expect(parsed.cols).toBe(2);
    expect(parsed.rows).toBe(2);
    expect(parsed.tileWidth).toBe(32);
    expect(parsed.tileHeight).toBe(32);
  });

  it("결정론: 같은 입력 2회 파싱 → 동일 placements", () => {
    expect(parseTiledMap(smallMap())).toEqual(parseTiledMap(smallMap()));
  });

  it("전부 빈칸(v=0) 레이어 → placements 0개", () => {
    const empty: RawTileMap = {
      ...smallMap(),
      layers: [{ name: "floor", data: [0, 0, 0, 0] }],
    };
    expect(parseTiledMap(empty).placements).toHaveLength(0);
  });
});

describe("tilePixel — 좌표 환산(순수·결정론)", () => {
  it("원점 (0,0) → (0,0)", () => {
    expect(tilePixel(0, 0, 32, 32)).toEqual({ x: 0, y: 0 });
  });

  it("(col,row)=(3,2), tile=32 → (96,64)", () => {
    expect(tilePixel(3, 2, 32, 32)).toEqual({ x: 96, y: 64 });
  });

  it("경계값: 마지막 셀 (19,13), tile=32 → (608,416)", () => {
    expect(tilePixel(19, 13, 32, 32)).toEqual({ x: 608, y: 416 });
  });

  it("비정방 타일도 정확: (2,1), tileW=16 tileH=24 → (32,24)", () => {
    expect(tilePixel(2, 1, 16, 24)).toEqual({ x: 32, y: 24 });
  });
});
