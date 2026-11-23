/**
 * pool.ts — 오브젝트 풀 + 엔티티 상한 (G005 US-G5-6 A1).
 *
 * ★저사양 가드(playable_definition #4): 동시 활성 엔티티(적+투사체)가 ENTITY_CAP을
 * 절대 초과하지 못하게 한다. 풀은 객체를 재사용(acquire/release)해 GC 압박과 할당
 * 폭주를 막는다 — release는 슬롯을 active:false로 되돌리고, acquire는 비활성 슬롯을
 * 우선 재사용(없으면 cap 한도 내에서만 새로 생성, 초과 시 null).
 */

import type { Enemy, Projectile } from "./types";

/** 동시 엔티티(적+투사체) 전역 상한 — 저사양 가드·풀링 검증의 기준선. */
export const ENTITY_CAP = 300;

/** 풀에서 비활성 슬롯을 찾아 재사용하거나, 한도 내에서 새로 만든다. */
export interface Pool<T extends { active: boolean }> {
  readonly items: T[];
  /** 비활성 슬롯 재사용 → 없으면 maxSlots 한도 내 신규 → 한도 초과면 null */
  acquire(): T | null;
  /** 슬롯을 비활성으로 되돌린다(다음 acquire가 재사용) */
  release(item: T): void;
  /** 현재 활성 슬롯 수 */
  activeCount(): number;
}

function makePool<T extends { active: boolean }>(
  factory: () => T,
  maxSlots: number
): Pool<T> {
  const items: T[] = [];
  return {
    items,
    acquire(): T | null {
      for (const it of items) {
        if (!it.active) {
          it.active = true;
          return it;
        }
      }
      if (items.length >= maxSlots) {
        return null; // ★한도 초과 — 새 엔티티 생성 거부(상한 강제)
      }
      const created = factory();
      created.active = true;
      items.push(created);
      return created;
    },
    release(item: T): void {
      item.active = false;
    },
    activeCount(): number {
      let n = 0;
      for (const it of items) if (it.active) n++;
      return n;
    },
  };
}

function newEnemy(): Enemy {
  return {
    active: false,
    defIndex: 0,
    x: 0,
    y: 0,
    hp: 0,
    speed: 0,
    damage: 0,
    behavior: "chase",
    contactCooldown: 0,
  };
}

function newProjectile(): Projectile {
  return {
    active: false,
    x: 0,
    y: 0,
    vx: 0,
    vy: 0,
    damage: 0,
    pierce: 0,
    rangeLeft: 0,
  };
}

/**
 * 적·투사체 풀 묶음. 두 풀의 활성 합이 ENTITY_CAP을 넘지 않도록 maxSlots를 분배한다
 * (적·투사체 각각 절반씩 — 합산 ENTITY_CAP 보장). enemyCap/projectileCap의 합 ≤ ENTITY_CAP.
 */
export interface EntityPools {
  enemies: Pool<Enemy>;
  projectiles: Pool<Projectile>;
  readonly enemyCap: number;
  readonly projectileCap: number;
}

export function makeEntityPools(cap: number = ENTITY_CAP): EntityPools {
  // 적·투사체에 절반씩 — 두 풀 활성 합 ≤ cap(상한은 합산 기준으로도 안전).
  const enemyCap = Math.floor(cap / 2);
  const projectileCap = cap - enemyCap;
  return {
    enemies: makePool(newEnemy, enemyCap),
    projectiles: makePool(newProjectile, projectileCap),
    enemyCap,
    projectileCap,
  };
}
