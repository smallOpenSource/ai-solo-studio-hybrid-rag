/**
 * data.ts — 베이스라인 game/data/*.json을 GameData로 묶는 로더 (G005 US-G5-6).
 *
 * vite/vitest가 JSON을 정적 import(번들에 포함)한다. 렌더(main.ts)·테스트(playable.test.ts)
 * 공통 진입 — game/dist 빌드에 데이터가 포함되도록 import로 묶는다(US-G5-2 AC5).
 */

import metaJson from "../../data/meta.json";
import enemiesJson from "../../data/enemies.json";
import weaponsJson from "../../data/weapons.json";
import wavesJson from "../../data/waves.json";
import upgradesJson from "../../data/upgrades.json";
import balanceJson from "../../data/balance.json";

import type { GameData } from "./types";

/** 베이스라인 6파일을 GameData 번들로 묶어 반환한다. */
export function loadBaselineData(): GameData {
  return {
    meta: metaJson as GameData["meta"],
    enemies: enemiesJson as GameData["enemies"],
    weapons: weaponsJson as GameData["weapons"],
    waves: wavesJson as GameData["waves"],
    upgrades: upgradesJson as GameData["upgrades"],
    balance: balanceJson,
  };
}
