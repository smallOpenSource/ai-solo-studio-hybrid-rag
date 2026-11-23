/**
 * playable.test.ts — ★헤드리스 플레이가능 결정론 단언 (G005 US-G5-6 AC3).
 *
 * playable_definition(.omc/prd.json) 4단언 — 베이스라인 game/data + createSim(seed 고정)으로
 * 600틱(dt=0.05 → 30초)을 실행해 검증한다(렌더 무의존·재현가능):
 *   1. 600틱 무예외(throw 0)
 *   2. 30초(=600틱) 후 player.hp>0 (생존·즉사 0)
 *   3. 런 중 레벨업 선택지 ≥1회 제시(upgrades 바인딩)
 *   4. 웨이브 ≥1 스폰 & 런 내내 동시 엔티티 수 ≤ ENTITY_CAP(풀링·저사양 가드)
 *
 * seeded RNG라 재현가능 — 같은 seed는 항상 같은 결과.
 */

import { describe, it, expect } from "vitest";

import { createSim, tick, ENTITY_CAP } from "../sim/sim";
import { ENTITY_CAP as POOL_CAP, makeEntityPools } from "../sim/pool";
import { loadBaselineData } from "../sim/data";
import type { SimState } from "../sim/types";

const DT = 0.05; // 20Hz
const TICKS = 600; // 600 * 0.05 = 30초
const SEED = 12345;

/** 베이스라인 + 고정 seed로 600틱 실행하고, 매 틱 예외/엔티티상한을 추적한다. */
function runHeadless(seed: number): {
  state: SimState;
  threwAt: number; // -1 = 예외 없음
  capViolated: boolean;
  maxEntities: number;
} {
  const data = loadBaselineData();
  const state = createSim(data, seed);
  let threwAt = -1;
  let capViolated = false;
  let maxEntities = 0;
  for (let i = 0; i < TICKS; i++) {
    try {
      tick(state, DT);
    } catch {
      threwAt = i;
      break;
    }
    let active = 0;
    for (const e of state.enemies) if (e.active) active++;
    for (const p of state.projectiles) if (p.active) active++;
    if (active > maxEntities) maxEntities = active;
    if (active > ENTITY_CAP) capViolated = true;
  }
  return { state, threwAt, capViolated, maxEntities };
}

describe("플레이가능 결정론 — 베이스라인 600틱(30초)", () => {
  it("1. 600틱 무예외(throw 0)", () => {
    const { threwAt } = runHeadless(SEED);
    expect(threwAt).toBe(-1);
  });

  it("2. 30초 후 플레이어 생존(hp>0·즉사 0)", () => {
    const { state } = runHeadless(SEED);
    expect(state.tick).toBe(TICKS);
    expect(state.gameOver).toBe(false);
    expect(state.player.hp).toBeGreaterThan(0);
  });

  it("3. 레벨업 선택지 ≥1회 제시(upgrades 바인딩)", () => {
    const { state } = runHeadless(SEED);
    expect(state.upgradeChoicesOffered.length).toBeGreaterThanOrEqual(1);
    // 제시된 선택지는 실제 업그레이드 인덱스를 가리킨다(빈 제시 금지).
    for (const choice of state.upgradeChoicesOffered) {
      expect(choice.optionIndices.length).toBeGreaterThanOrEqual(1);
    }
  });

  it("4. 웨이브 ≥1 스폰 & 동시 엔티티 ≤ ENTITY_CAP(풀링·저사양 가드)", () => {
    const { state, capViolated, maxEntities } = runHeadless(SEED);
    expect(state.spawned).toBeGreaterThanOrEqual(1);
    expect(capViolated).toBe(false);
    expect(maxEntities).toBeLessThanOrEqual(ENTITY_CAP);
    // 적이 실제로 스폰돼 전투가 일어났다(처치 ≥1 — 무기 자동공격 바인딩).
    expect(state.kills).toBeGreaterThanOrEqual(1);
  });

  it("5. ENTITY_CAP은 sim/pool 단일 출처(=300)", () => {
    expect(ENTITY_CAP).toBe(POOL_CAP);
    expect(ENTITY_CAP).toBe(300);
  });
});

describe("ENTITY_CAP 풀 상한 직접 실증 (US-G5-6 Minor-1)", () => {
  it("maxSlots=4 풀: 4개 acquire 성공 → 5번째 acquire는 null 반환(상한 강제)", () => {
    // makeEntityPools(cap=4) → enemyCap=2, projectileCap=2.
    // 두 풀 합산 상한=4 — 각 풀을 cap까지 채운 뒤 추가 acquire가 null임을 단언.
    const pools = makeEntityPools(4); // enemyCap=2, projectileCap=2

    // 적 풀 2개 acquire (한도 = 2)
    const e1 = pools.enemies.acquire();
    const e2 = pools.enemies.acquire();
    expect(e1).not.toBeNull();
    expect(e2).not.toBeNull();
    // 3번째 acquire → null(한도 초과)
    const e3 = pools.enemies.acquire();
    expect(e3).toBeNull();
    // activeCount는 cap에 고정
    expect(pools.enemies.activeCount()).toBe(2);

    // 투사체 풀 2개 acquire
    const p1 = pools.projectiles.acquire();
    const p2 = pools.projectiles.acquire();
    expect(p1).not.toBeNull();
    expect(p2).not.toBeNull();
    // 3번째 → null
    const p3 = pools.projectiles.acquire();
    expect(p3).toBeNull();
    expect(pools.projectiles.activeCount()).toBe(2);
  });

  it("release 후 재사용 — 슬롯 해제 시 activeCount 감소 & 재acquire 성공", () => {
    const pools = makeEntityPools(4); // enemyCap=2

    const e1 = pools.enemies.acquire()!;
    pools.enemies.acquire(); // 한도 채움
    expect(pools.enemies.acquire()).toBeNull(); // 추가 불가

    // e1 release → 슬롯 재사용 가능
    pools.enemies.release(e1);
    expect(pools.enemies.activeCount()).toBe(1);
    const reused = pools.enemies.acquire();
    expect(reused).not.toBeNull(); // 재사용 성공
    expect(pools.enemies.activeCount()).toBe(2);
  });

  it("ENTITY_CAP=300 풀에서 600틱 실행 후 peakEntities가 cap 초과 없음(sim 내장 최댓값 실측)", () => {
    // SimState.peakEntities는 sim.ts updatePeakEntities()가 매 틱 실측·기록하는 최댓값.
    // runHeadless의 capViolated와 달리, 풀 자체 동작을 거쳐 sim이 기록한 실측값을 단언.
    const data = loadBaselineData();
    const state = createSim(data, SEED);
    for (let i = 0; i < TICKS; i++) {
      tick(state, DT);
    }
    // peakEntities는 sim 내부가 매 틱 실측한 적+투사체 최대 동시 활성 수
    expect(state.peakEntities).toBeLessThanOrEqual(ENTITY_CAP);
    expect(state.peakEntities).toBeGreaterThanOrEqual(1); // 실제 전투 발생 확인
  });
});

describe("결정론 재현성 — 같은 seed = 같은 결과", () => {
  it("동일 seed 2회 실행 → kills·생존·레벨 동일", () => {
    const a = runHeadless(SEED);
    const b = runHeadless(SEED);
    expect(a.state.kills).toBe(b.state.kills);
    expect(a.state.player.hp).toBe(b.state.player.hp);
    expect(a.state.player.level).toBe(b.state.player.level);
    expect(a.state.spawned).toBe(b.state.spawned);
    expect(a.state.upgradeChoicesOffered.length).toBe(
      b.state.upgradeChoicesOffered.length
    );
  });
});
