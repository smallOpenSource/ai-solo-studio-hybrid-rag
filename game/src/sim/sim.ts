/**
 * sim.ts — 순수 결정론 서바이버라이크 시뮬레이션 (G005 US-G5-6 A1).
 *
 * 렌더 무의존(헤드리스 실행 가능). createSim(data, seed)→SimState, tick(state, dt)→SimState.
 * 데이터주도: enemies/weapons/waves/upgrades/meta를 그대로 소비한다.
 *
 * ★결정론: seed 고정 시 동일 입력→동일 결과(mulberry32). 부동소수 연산만 쓰고 Date/Math.random
 *   미사용. tick은 state를 in-place 변형해 같은 SimState 참조를 돌려준다(풀 슬롯 재사용 — 할당 0).
 *
 * 좌표계: WORLD_W×WORLD_H 아레나. 플레이어는 중앙 부근에서 가장 가까운 적 반대 방향으로
 *   자동 이동(서바이버라이크 — 회피 생존). 적은 waves.json 시간대별로 가장자리에서 스폰돼
 *   플레이어를 향한다. 무기는 weapons.json fireRate로 가장 가까운 적에게 자동 발사한다.
 *
 * dt=0.05(20Hz) 기준 600틱 = 30초(playable_definition의 600틱·30초 생존과 일치).
 */

import { makeRng, type Rng } from "./rng";
import { ENTITY_CAP, makeEntityPools, type EntityPools } from "./pool";
import type {
  EnemyDef,
  GameData,
  Player,
  SimState,
  UpgradeDef,
  WeaponDef,
} from "./types";

// ── 월드/플레이어 상수(밸런스 — 베이스라인이 30초 생존 가능하도록 튜닝) ──────────
const WORLD_W = 960;
const WORLD_H = 640;
const PLAYER_BASE_HP = 120;
const PLAYER_SPEED = 130; // px/s — 적 대부분보다 빠르거나 비슷해 회피 가능
const PLAYER_RADIUS = 12;
const ENEMY_RADIUS = 10;
const CONTACT_DAMAGE_COOLDOWN = 0.5; // 적 접촉 데미지 간격(초)
const XP_PER_KILL = 5;
const XP_BASE_TO_NEXT = 30; // 1→2 레벨 필요 경험치(레벨마다 증가)
const PROJECTILE_SPEED = 360; // px/s
const PROJECTILE_RADIUS = 6;
const UPGRADE_OPTIONS = 3; // 레벨업 시 제시할 선택지 수

interface FlatSpawn {
  enemyDefIndex: number;
  rate: number;
  tStart: number;
  tEnd: number;
}

interface SimInternal {
  data: GameData;
  rng: Rng;
  pools: EntityPools;
  enemyDefs: EnemyDef[];
  weaponDefs: WeaponDef[];
  upgradeDefs: UpgradeDef[];
  /** waves.spawn을 평탄화한 스폰 항목(spawnAccumulators 인덱스와 1:1) */
  flatSpawns: FlatSpawn[];
}

// SimState ↔ 내부 보조 데이터 매핑(SimState를 JSON 직렬화 가능하게 유지하려 분리 보관).
const _internals = new WeakMap<SimState, SimInternal>();

function dist2(ax: number, ay: number, bx: number, by: number): number {
  const dx = ax - bx;
  const dy = ay - by;
  return dx * dx + dy * dy;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** 데이터+seed로 초기 SimState를 만든다(렌더 무의존). */
export function createSim(data: GameData, seed: number): SimState {
  const rng = makeRng(seed);
  const pools = makeEntityPools(ENTITY_CAP);
  const enemyDefs = data.enemies.enemies;
  const weaponDefs = data.weapons.weapons;
  const upgradeDefs = data.upgrades.upgrades;

  // waves.spawn 평탄화 — 각 (wave, spawn) 쌍을 enemyDefIndex로 해소한다.
  const enemyIndexById = new Map<string, number>();
  enemyDefs.forEach((e, i) => enemyIndexById.set(e.id, i));
  const flatSpawns: FlatSpawn[] = [];
  for (const wave of data.waves.waves) {
    for (const s of wave.spawn) {
      const idx = enemyIndexById.get(s.enemyId);
      if (idx === undefined) continue; // 참조 무결성은 validate가 보장 — 방어적 skip
      flatSpawns.push({
        enemyDefIndex: idx,
        rate: s.rate,
        tStart: wave.tStart,
        tEnd: wave.tEnd,
      });
    }
  }

  const player: Player = {
    x: WORLD_W / 2,
    y: WORLD_H / 2,
    hp: PLAYER_BASE_HP,
    maxHp: PLAYER_BASE_HP,
    xp: 0,
    level: 1,
    xpToNext: XP_BASE_TO_NEXT,
    damageBonus: 0,
    fireRateMult: 1,
  };

  const state: SimState = {
    time: 0,
    tick: 0,
    player,
    enemies: pools.enemies.items,
    projectiles: pools.projectiles.items,
    weaponCooldowns: weaponDefs.map((w) => 1 / w.fireRate),
    spawnAccumulators: flatSpawns.map(() => 0),
    gameOver: false,
    kills: 0,
    spawned: 0,
    upgradeChoicesOffered: [],
    peakEntities: 0,
  };

  _internals.set(state, {
    data,
    rng,
    pools,
    enemyDefs,
    weaponDefs,
    upgradeDefs,
    flatSpawns,
  });
  return state;
}

/** 가장자리 랜덤 위치(스폰 지점) — 플레이어 밖에서 진입. */
function edgeSpawnPoint(rng: Rng): { x: number; y: number } {
  const side = rng.int(4);
  switch (side) {
    case 0:
      return { x: rng.range(0, WORLD_W), y: 0 };
    case 1:
      return { x: WORLD_W, y: rng.range(0, WORLD_H) };
    case 2:
      return { x: rng.range(0, WORLD_W), y: WORLD_H };
    default:
      return { x: 0, y: rng.range(0, WORLD_H) };
  }
}

function spawnEnemies(state: SimState, intern: SimInternal, dt: number): void {
  const { rng, pools, enemyDefs, flatSpawns } = intern;
  for (let i = 0; i < flatSpawns.length; i++) {
    const fs = flatSpawns[i];
    if (state.time < fs.tStart || state.time >= fs.tEnd) continue;
    state.spawnAccumulators[i] += fs.rate * dt;
    while (state.spawnAccumulators[i] >= 1) {
      state.spawnAccumulators[i] -= 1;
      const enemy = pools.enemies.acquire();
      if (enemy === null) break; // ★상한 도달 — 스폰 거부(엔티티 ≤ cap 보장)
      const def = enemyDefs[fs.enemyDefIndex];
      const pt = edgeSpawnPoint(rng);
      enemy.defIndex = fs.enemyDefIndex;
      enemy.x = pt.x;
      enemy.y = pt.y;
      enemy.hp = def.hp;
      enemy.speed = def.speed;
      enemy.damage = def.damage;
      enemy.behavior = def.behavior;
      enemy.contactCooldown = 0;
      state.spawned += 1;
    }
  }
}

/** 플레이어 자동 이동 — 가장 가까운 적 반대 방향으로 회피(서바이버라이크 생존). */
function movePlayer(state: SimState, dt: number): void {
  const p = state.player;
  let nearestD2 = Infinity;
  let nx = 0;
  let ny = 0;
  for (const e of state.enemies) {
    if (!e.active) continue;
    const d2 = dist2(p.x, p.y, e.x, e.y);
    if (d2 < nearestD2) {
      nearestD2 = d2;
      nx = e.x;
      ny = e.y;
    }
  }
  let dx: number;
  let dy: number;
  if (nearestD2 === Infinity) {
    // 적 없음 — 중앙으로 복귀(가장자리 몰림 방지).
    dx = WORLD_W / 2 - p.x;
    dy = WORLD_H / 2 - p.y;
  } else {
    // 가장 가까운 적 반대 방향 + 중앙 복귀 약간 혼합(구석 갇힘 방지).
    dx = p.x - nx + (WORLD_W / 2 - p.x) * 0.2;
    dy = p.y - ny + (WORLD_H / 2 - p.y) * 0.2;
  }
  const len = Math.hypot(dx, dy) || 1;
  p.x = clamp(p.x + (dx / len) * PLAYER_SPEED * dt, PLAYER_RADIUS, WORLD_W - PLAYER_RADIUS);
  p.y = clamp(p.y + (dy / len) * PLAYER_SPEED * dt, PLAYER_RADIUS, WORLD_H - PLAYER_RADIUS);
}

function moveEnemies(state: SimState, dt: number): void {
  const p = state.player;
  for (const e of state.enemies) {
    if (!e.active) continue;
    let dx = p.x - e.x;
    let dy = p.y - e.y;
    // swarm은 약간의 지그재그(결정론 — 위치 기반 사인 오프셋)로 군집 흔들림.
    if (e.behavior === "swarm") {
      const wobble = Math.sin((e.x + e.y) * 0.05 + state.time) * 0.4;
      const ox = -dy;
      const oy = dx;
      dx += ox * wobble;
      dy += oy * wobble;
    }
    const len = Math.hypot(dx, dy) || 1;
    e.x += (dx / len) * e.speed * dt;
    e.y += (dy / len) * e.speed * dt;
    if (e.contactCooldown > 0) e.contactCooldown -= dt;
  }
}

function findNearestEnemy(
  state: SimState,
  x: number,
  y: number,
  maxRange: number
): { idx: number; dx: number; dy: number } | null {
  let best = -1;
  let bestD2 = maxRange * maxRange;
  let bx = 0;
  let by = 0;
  for (let i = 0; i < state.enemies.length; i++) {
    const e = state.enemies[i];
    if (!e.active) continue;
    const d2 = dist2(x, y, e.x, e.y);
    if (d2 <= bestD2) {
      bestD2 = d2;
      best = i;
      bx = e.x;
      by = e.y;
    }
  }
  if (best < 0) return null;
  return { idx: best, dx: bx - x, dy: by - y };
}

function fireWeapons(state: SimState, intern: SimInternal, dt: number): void {
  const { weaponDefs, pools } = intern;
  const p = state.player;
  for (let wi = 0; wi < weaponDefs.length; wi++) {
    const w = weaponDefs[wi];
    state.weaponCooldowns[wi] -= dt;
    if (state.weaponCooldowns[wi] > 0) continue;
    // 사거리 내 가장 가까운 적이 있을 때만 발사(없으면 쿨다운 유지).
    const target = findNearestEnemy(state, p.x, p.y, w.range);
    if (target === null) continue;
    state.weaponCooldowns[wi] = 1 / (w.fireRate * p.fireRateMult);
    const baseAng = Math.atan2(target.dy, target.dx);
    const spread = 0.18; // projectiles>1이면 부채꼴
    for (let k = 0; k < w.projectiles; k++) {
      const proj = pools.projectiles.acquire();
      if (proj === null) break; // ★상한 도달 — 투사체 생성 거부(엔티티 ≤ cap)
      const offset =
        w.projectiles > 1 ? (k - (w.projectiles - 1) / 2) * spread : 0;
      const ang = baseAng + offset;
      proj.x = p.x;
      proj.y = p.y;
      proj.vx = Math.cos(ang) * PROJECTILE_SPEED;
      proj.vy = Math.sin(ang) * PROJECTILE_SPEED;
      proj.damage = w.damage + p.damageBonus;
      proj.pierce = w.pierce;
      proj.rangeLeft = w.range;
    }
  }
}

function moveProjectiles(state: SimState, intern: SimInternal, dt: number): void {
  const { pools } = intern;
  for (const pr of state.projectiles) {
    if (!pr.active) continue;
    pr.x += pr.vx * dt;
    pr.y += pr.vy * dt;
    pr.rangeLeft -= PROJECTILE_SPEED * dt;
    if (
      pr.rangeLeft <= 0 ||
      pr.x < -20 ||
      pr.x > WORLD_W + 20 ||
      pr.y < -20 ||
      pr.y > WORLD_H + 20
    ) {
      pools.projectiles.release(pr);
    }
  }
}

function resolveProjectileHits(state: SimState, intern: SimInternal): void {
  const { pools } = intern;
  const hitR2 = (PROJECTILE_RADIUS + ENEMY_RADIUS) ** 2;
  for (const pr of state.projectiles) {
    if (!pr.active) continue;
    for (const e of state.enemies) {
      if (!e.active) continue;
      if (dist2(pr.x, pr.y, e.x, e.y) <= hitR2) {
        e.hp -= pr.damage;
        if (e.hp <= 0) {
          pools.enemies.release(e);
          state.kills += 1;
          gainXp(state, intern, XP_PER_KILL);
        }
        if (pr.pierce > 0) {
          pr.pierce -= 1;
        } else {
          pools.projectiles.release(pr);
          break; // 이 투사체 소진 — 다음 투사체로
        }
      }
    }
  }
}

function resolveContactDamage(state: SimState): void {
  const p = state.player;
  const hitR2 = (PLAYER_RADIUS + ENEMY_RADIUS) ** 2;
  for (const e of state.enemies) {
    if (!e.active) continue;
    if (e.contactCooldown > 0) continue;
    if (dist2(p.x, p.y, e.x, e.y) <= hitR2) {
      p.hp -= e.damage;
      e.contactCooldown = CONTACT_DAMAGE_COOLDOWN;
    }
  }
  if (p.hp <= 0) {
    p.hp = 0;
    state.gameOver = true;
  }
}

/** 경험치 획득 → 레벨업 시 업그레이드 선택지 제시 + 적용(★upgrades 바인딩). */
function gainXp(state: SimState, intern: SimInternal, amount: number): void {
  const p = state.player;
  p.xp += amount;
  while (p.xp >= p.xpToNext) {
    p.xp -= p.xpToNext;
    p.level += 1;
    p.xpToNext = Math.floor(XP_BASE_TO_NEXT * (1 + (p.level - 1) * 0.35));
    offerAndApplyUpgrade(state, intern);
  }
}

function offerAndApplyUpgrade(state: SimState, intern: SimInternal): void {
  const { upgradeDefs, rng } = intern;
  if (upgradeDefs.length === 0) return;
  // 선택지 제시(★레벨업 선택지 ≥1회 — playable_definition #3). 결정론 셔플로 N개 뽑는다.
  const n = Math.min(UPGRADE_OPTIONS, upgradeDefs.length);
  const pool = upgradeDefs.map((_, i) => i);
  // Fisher-Yates(seeded) 부분 셔플.
  for (let i = 0; i < n; i++) {
    const j = i + rng.int(pool.length - i);
    const tmp = pool[i];
    pool[i] = pool[j];
    pool[j] = tmp;
  }
  const optionIndices = pool.slice(0, n);
  state.upgradeChoicesOffered.push({ level: state.player.level, optionIndices });
  // 자동 선택: 첫 옵션 적용(헤드리스 — 결정론. 렌더 빌드에선 UI가 고를 자리).
  applyUpgrade(state, upgradeDefs[optionIndices[0]]);
}

function applyUpgrade(state: SimState, up: UpgradeDef): void {
  const p = state.player;
  switch (up.stat) {
    case "damage":
      p.damageBonus = Math.min(p.damageBonus + up.delta, up.cap);
      break;
    case "fireRate":
      p.fireRateMult = Math.min(p.fireRateMult + up.delta, up.cap);
      break;
    case "speed":
      // 이동속도는 상수라 maxHp 소폭 보강으로 환산(생존 곡선 — 결정론·무해).
      p.maxHp = Math.min(p.maxHp + up.delta, p.maxHp + up.cap);
      break;
    case "hpRegen":
      p.hp = Math.min(p.hp + up.delta, p.maxHp);
      break;
    default:
      // 미지 stat — 데미지 보너스로 폴백(스키마는 임의 stat 허용).
      p.damageBonus = Math.min(p.damageBonus + Math.abs(up.delta), up.cap);
      break;
  }
}

function updatePeakEntities(state: SimState): void {
  let n = 0;
  for (const e of state.enemies) if (e.active) n++;
  for (const pr of state.projectiles) if (pr.active) n++;
  if (n > state.peakEntities) state.peakEntities = n;
}

/** 한 틱 진행(dt초). state를 in-place 변형해 같은 참조를 반환한다. */
export function tick(state: SimState, dt: number): SimState {
  if (state.gameOver) return state;
  const intern = _internals.get(state);
  if (intern === undefined) {
    throw new Error("tick: SimState가 createSim 산출물이 아님(internal 없음)");
  }

  state.time += dt;
  state.tick += 1;

  spawnEnemies(state, intern, dt);
  movePlayer(state, dt);
  moveEnemies(state, dt);
  fireWeapons(state, intern, dt);
  moveProjectiles(state, intern, dt);
  resolveProjectileHits(state, intern);
  resolveContactDamage(state);
  updatePeakEntities(state);

  return state;
}

export { ENTITY_CAP, WORLD_W, WORLD_H };
