/**
 * schema.test.ts — game/src/schema.ts 검증 (US-G5-2 AC4)
 *
 * 1. 베이스라인 6파일 → validate* + validateAll 전부 ok:true
 * 2. 고의 invalid 케이스 → 거부 단언
 */

import { describe, it, expect } from "vitest";
import {
  validateMeta,
  validateEnemies,
  validateWeapons,
  validateWaves,
  validateUpgrades,
  validateBalance,
  validateAll,
  type GameDataBundle,
} from "../schema";

// ── 베이스라인 데이터 import ──────────────────────────────────────────────────
import metaJson from "../../data/meta.json";
import enemiesJson from "../../data/enemies.json";
import weaponsJson from "../../data/weapons.json";
import wavesJson from "../../data/waves.json";
import upgradesJson from "../../data/upgrades.json";
import balanceJson from "../../data/balance.json";

const baseline: GameDataBundle = {
  meta: metaJson,
  enemies: enemiesJson,
  weapons: weaponsJson,
  waves: wavesJson,
  upgrades: upgradesJson,
  balance: balanceJson,
};

// ── 1. 베이스라인 전체 유효성 ─────────────────────────────────────────────────

describe("베이스라인 — 각 파일 개별 검증", () => {
  it("meta.json → ok:true", () => {
    const res = validateMeta(metaJson);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });

  it("enemies.json → ok:true", () => {
    const res = validateEnemies(enemiesJson);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });

  it("weapons.json → ok:true", () => {
    const res = validateWeapons(weaponsJson);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });

  it("waves.json → ok:true", () => {
    const res = validateWaves(wavesJson);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });

  it("upgrades.json → ok:true", () => {
    const res = validateUpgrades(upgradesJson);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });

  it("balance.json → ok:true", () => {
    const res = validateBalance(balanceJson);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });
});

describe("베이스라인 — validateAll (cross-file 참조 무결성 포함)", () => {
  it("validateAll ok:true, errors:[]", () => {
    const res = validateAll(baseline);
    expect(res.errors).toEqual([]);
    expect(res.ok).toBe(true);
  });
});

// ── 2. validateMeta 거부 케이스 ───────────────────────────────────────────────

describe("validateMeta — invalid 거부", () => {
  it("루트가 객체가 아님 → ok:false", () => {
    expect(validateMeta(null).ok).toBe(false);
    expect(validateMeta(42).ok).toBe(false);
    expect(validateMeta([]).ok).toBe(false);
  });

  it("필수 문자열 누락 → 에러 포함", () => {
    const res = validateMeta({ title: "x", coreLoop: "x", win: "x", lose: "x", continue: { freeRetries: 0, costCurve: [], sessionCap: 1 } });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("theme"))).toBe(true);
  });

  it("continue 누락 → ok:false", () => {
    const res = validateMeta({ theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x" });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("continue"))).toBe(true);
  });

  it("freeRetries 음수 → ok:false", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: -1, costCurve: [], sessionCap: 1 },
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("freeRetries"))).toBe(true);
  });

  it("costCurve 감소 → ok:false", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: 0, costCurve: [100, 50, 200], sessionCap: 1 },
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("costCurve"))).toBe(true);
  });

  it("sessionCap=0 → ok:false", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: 0, costCurve: [], sessionCap: 0 },
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("sessionCap"))).toBe(true);
  });

  it("bool을 number로 오인 금지 — freeRetries=true → ok:false", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: true, costCurve: [], sessionCap: 1 },
    });
    expect(res.ok).toBe(false);
  });
});

// ── 3. validateEnemies 거부 케이스 ───────────────────────────────────────────

describe("validateEnemies — invalid 거부", () => {
  it("enemies 2개 미만 → ok:false", () => {
    const res = validateEnemies({
      enemies: [
        { id: "a", name: "A", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "chase" },
        { id: "b", name: "B", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "swarm" },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("3"))).toBe(true);
  });

  it("hp=0 (>0 위반) → ok:false", () => {
    const res = validateEnemies({
      enemies: [
        { id: "a", name: "A", hp: 0, speed: 5, damage: 1, spawnWeight: 1, behavior: "chase" },
        { id: "b", name: "B", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "swarm" },
        { id: "c", name: "C", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "ranged" },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("hp"))).toBe(true);
  });

  it("behavior 잘못된 값 → ok:false", () => {
    const res = validateEnemies({
      enemies: [
        { id: "a", name: "A", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "flying" },
        { id: "b", name: "B", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "swarm" },
        { id: "c", name: "C", hp: 10, speed: 5, damage: 1, spawnWeight: 1, behavior: "ranged" },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("behavior"))).toBe(true);
  });

  it("damage=true (bool, number 아님) → ok:false", () => {
    const res = validateEnemies({
      enemies: [
        { id: "a", name: "A", hp: 10, speed: 5, damage: true, spawnWeight: 1, behavior: "chase" },
        { id: "b", name: "B", hp: 10, speed: 5, damage: 0, spawnWeight: 1, behavior: "swarm" },
        { id: "c", name: "C", hp: 10, speed: 5, damage: 0, spawnWeight: 1, behavior: "ranged" },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("damage"))).toBe(true);
  });
});

// ── 4. validateWaves 거부 케이스 ─────────────────────────────────────────────

describe("validateWaves — invalid 거부", () => {
  it("waves 1개 미만 → ok:false", () => {
    const res = validateWaves({
      waves: [{ tStart: 0, tEnd: 60, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 }],
    });
    expect(res.ok).toBe(false);
  });

  it("tEnd <= tStart → ok:false", () => {
    const res = validateWaves({
      waves: [
        { tStart: 0, tEnd: 0, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 },
        { tStart: 10, tEnd: 60, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("tEnd"))).toBe(true);
  });

  it("tStart 동률(비엄격) → ok:false", () => {
    const res = validateWaves({
      waves: [
        { tStart: 0, tEnd: 60, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 },
        { tStart: 0, tEnd: 120, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 2 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("tStart") && e.includes("오름차순"))).toBe(true);
  });

  it("tStart 역순 → ok:false", () => {
    const res = validateWaves({
      waves: [
        { tStart: 60, tEnd: 120, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 },
        { tStart: 0, tEnd: 30, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 2 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("tStart"))).toBe(true);
  });

  it("spawn 빈 배열 → ok:false", () => {
    const res = validateWaves({
      waves: [
        { tStart: 0, tEnd: 60, spawn: [], difficulty: 1 },
        { tStart: 60, tEnd: 120, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("spawn"))).toBe(true);
  });

  it("rate=0 (>0 위반) → ok:false", () => {
    const res = validateWaves({
      waves: [
        { tStart: 0, tEnd: 60, spawn: [{ enemyId: "a", rate: 0 }], difficulty: 1 },
        { tStart: 60, tEnd: 120, spawn: [{ enemyId: "a", rate: 1 }], difficulty: 1 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("rate"))).toBe(true);
  });
});

// ── 5. validateBalance 거부 케이스 ───────────────────────────────────────────

describe("validateBalance — invalid 거부", () => {
  it("survivalTargetSec=0 (>0 위반) → ok:false", () => {
    const res = validateBalance({
      survivalTargetSec: 0,
      dpsCurve: [10, 20],
      inRange: true,
      report: {},
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("survivalTargetSec"))).toBe(true);
  });

  it("dpsCurve 감소 → ok:false", () => {
    const res = validateBalance({
      survivalTargetSec: 300,
      dpsCurve: [20, 10, 30],
      inRange: true,
      report: {},
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("dpsCurve"))).toBe(true);
  });

  it("inRange 숫자(1) → ok:false (bool이어야 함)", () => {
    const res = validateBalance({
      survivalTargetSec: 300,
      dpsCurve: [10, 20],
      inRange: 1,
      report: {},
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("inRange"))).toBe(true);
  });

  it("report 값이 string → ok:false", () => {
    const res = validateBalance({
      survivalTargetSec: 300,
      dpsCurve: [10, 20],
      inRange: true,
      report: { check: "yes" },
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("report"))).toBe(true);
  });
});

// ── 6. validateUpgrades 거부 케이스 ──────────────────────────────────────────

describe("validateUpgrades — invalid 거부", () => {
  it("upgrades 2개 (>=3 미만) → ok:false", () => {
    const res = validateUpgrades({
      upgrades: [
        { id: "a", name: "A", stat: "dmg", delta: 5, cap: 50 },
        { id: "b", name: "B", stat: "spd", delta: 10, cap: 100 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("3"))).toBe(true);
  });

  it("stat 누락 → ok:false", () => {
    const res = validateUpgrades({
      upgrades: [
        { id: "a", name: "A", delta: 5, cap: 50 },
        { id: "b", name: "B", stat: "spd", delta: 10, cap: 100 },
        { id: "c", name: "C", stat: "hp", delta: 2, cap: 20 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("stat"))).toBe(true);
  });
});

// ── 7. validateAll — cross-file 참조 무결성 거부 ─────────────────────────────

describe("validateAll — 참조 무결성 거부", () => {
  it("waves.spawn.enemyId가 enemies.id에 없음 → ok:false", () => {
    const brokenBundle: GameDataBundle = {
      ...baseline,
      waves: {
        waves: [
          {
            tStart: 0,
            tEnd: 60,
            spawn: [{ enemyId: "nonexistent_enemy", rate: 1 }],
            difficulty: 1,
          },
          {
            tStart: 60,
            tEnd: 120,
            spawn: [{ enemyId: "zombie", rate: 1 }],
            difficulty: 2,
          },
        ],
      },
    };
    const res = validateAll(brokenBundle);
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("참조 무결성"))).toBe(true);
    expect(res.errors.some((e) => e.includes("nonexistent_enemy"))).toBe(true);
  });

  it("meta 필수 누락 → ok:false", () => {
    const res = validateAll({ ...baseline, meta: {} });
    expect(res.ok).toBe(false);
  });
});

// ── 8. isInt 언어간 수렴 — float 정수값 허용·비정수 거부 (US-G5-2 M-1) ─────────

describe("isInt 언어간 수렴 — float 정수값 허용·비정수·bool 거부", () => {
  it("freeRetries=1.0 (float 정수값) → ok:true — TS Number.isInteger(1.0)===true와 동치", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: 1.0, costCurve: [], sessionCap: 1 },
    });
    expect(res.ok).toBe(true);
    expect(res.errors).toEqual([]);
  });

  it("freeRetries=1.5 (비정수 float) → ok:false", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: 1.5, costCurve: [], sessionCap: 1 },
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("freeRetries"))).toBe(true);
  });

  it("freeRetries=true (bool) → ok:false", () => {
    const res = validateMeta({
      theme: "t", title: "x", coreLoop: "x", win: "x", lose: "x",
      continue: { freeRetries: true, costCurve: [], sessionCap: 1 },
    });
    expect(res.ok).toBe(false);
  });

  it("projectiles=2.0 (float 정수값) → ok:true", () => {
    const res = validateWeapons({
      weapons: [
        { id: "a", name: "A", fireRate: 1, projectiles: 2.0, damage: 5, range: 100, pierce: 0 },
        { id: "b", name: "B", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: 0 },
      ],
    });
    expect(res.ok).toBe(true);
  });

  it("projectiles=1.5 (비정수) → ok:false", () => {
    const res = validateWeapons({
      weapons: [
        { id: "a", name: "A", fireRate: 1, projectiles: 1.5, damage: 5, range: 100, pierce: 0 },
        { id: "b", name: "B", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: 0 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("projectiles"))).toBe(true);
  });
});

// ── 9. validateWeapons 거부 케이스 ───────────────────────────────────────────

describe("validateWeapons — invalid 거부", () => {
  it("weapons 1개 (>=2 미만) → ok:false", () => {
    const res = validateWeapons({
      weapons: [
        { id: "a", name: "A", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: 0 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("2"))).toBe(true);
  });

  it("projectiles=0 (>=1 위반) → ok:false", () => {
    const res = validateWeapons({
      weapons: [
        { id: "a", name: "A", fireRate: 1, projectiles: 0, damage: 5, range: 100, pierce: 0 },
        { id: "b", name: "B", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: 0 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("projectiles"))).toBe(true);
  });

  it("damage=0 (>0 위반) → ok:false", () => {
    const res = validateWeapons({
      weapons: [
        { id: "a", name: "A", fireRate: 1, projectiles: 1, damage: 0, range: 100, pierce: 0 },
        { id: "b", name: "B", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: 0 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("damage"))).toBe(true);
  });

  it("pierce 음수 → ok:false", () => {
    const res = validateWeapons({
      weapons: [
        { id: "a", name: "A", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: -1 },
        { id: "b", name: "B", fireRate: 1, projectiles: 1, damage: 5, range: 100, pierce: 0 },
      ],
    });
    expect(res.ok).toBe(false);
    expect(res.errors.some((e) => e.includes("pierce"))).toBe(true);
  });
});
