/**
 * types.ts — 시뮬레이션 데이터/런타임 타입 (G005 US-G5-6 A1).
 *
 * - 데이터 타입(EnemyDef/WeaponDef/...)은 schema_contract(game/src/schema.ts 검증규칙)와
 *   동일한 형태의 game/data/*.json 모양이다(런타임이 소비하는 읽기 전용 정의).
 * - 런타임 타입(Player/Enemy/Projectile/SimState)은 tick이 변형하는 시뮬 상태다.
 *   ★풀링을 위해 엔티티는 `active` 플래그를 가진다(release=active:false, acquire=재사용).
 */

// ── 데이터 정의(game/data/*.json 형태) ───────────────────────────────────────

export type EnemyBehavior = "chase" | "swarm" | "ranged" | "charger";

export interface EnemyDef {
  id: string;
  name: string;
  hp: number;
  speed: number;
  damage: number;
  spawnWeight: number;
  behavior: EnemyBehavior;
}

export interface WeaponDef {
  id: string;
  name: string;
  fireRate: number;
  projectiles: number;
  damage: number;
  range: number;
  pierce: number;
}

export interface SpawnDef {
  enemyId: string;
  rate: number;
}

export interface WaveDef {
  tStart: number;
  tEnd: number;
  spawn: SpawnDef[];
  difficulty: number;
}

export interface UpgradeDef {
  id: string;
  name: string;
  stat: string;
  delta: number;
  cap: number;
}

export interface MetaContinue {
  freeRetries: number;
  costCurve: number[];
  sessionCap: number;
}

export interface MetaDef {
  theme: string;
  title: string;
  coreLoop: string;
  win: string;
  lose: string;
  continue: MetaContinue;
}

/** 런타임이 소비하는 6파일 데이터 번들(game/data/*.json import 결과). */
export interface GameData {
  meta: MetaDef;
  enemies: { enemies: EnemyDef[] };
  weapons: { weapons: WeaponDef[] };
  waves: { waves: WaveDef[] };
  upgrades: { upgrades: UpgradeDef[] };
  balance: unknown;
}

// ── 런타임 엔티티(풀링 — active 플래그로 acquire/release) ──────────────────────

export interface Player {
  x: number;
  y: number;
  hp: number;
  maxHp: number;
  /** 누적 경험치 / 다음 레벨까지 필요 경험치 */
  xp: number;
  level: number;
  xpToNext: number;
  /** 업그레이드로 누적된 데미지 보너스(weapon.damage에 가산) */
  damageBonus: number;
  /** 업그레이드로 누적된 fireRate 배수(>=1) */
  fireRateMult: number;
}

export interface Enemy {
  active: boolean;
  /** 정의 인덱스(enemies 배열) — 풀 슬롯이 어떤 적인지 */
  defIndex: number;
  x: number;
  y: number;
  hp: number;
  speed: number;
  damage: number;
  behavior: EnemyBehavior;
  /** 적-플레이어 접촉 데미지 쿨다운(초) — 매 틱 데미지 폭주 방지 */
  contactCooldown: number;
}

export interface Projectile {
  active: boolean;
  x: number;
  y: number;
  vx: number;
  vy: number;
  damage: number;
  pierce: number;
  /** 남은 사거리(픽셀) — 0 이하가 되면 release */
  rangeLeft: number;
}

/** 레벨업 시 제시되는 업그레이드 선택지(인덱스 묶음). */
export interface UpgradeChoice {
  level: number;
  optionIndices: number[];
}

export interface SimState {
  /** 경과 시뮬 시간(초) */
  time: number;
  /** 진행 틱 수 */
  tick: number;
  player: Player;
  /** 적 풀(active만 살아있음) */
  enemies: Enemy[];
  /** 투사체 풀(active만 살아있음) */
  projectiles: Projectile[];
  /** 무기별 다음 발사까지 남은 쿨다운(초) — weapons 인덱스 정렬 */
  weaponCooldowns: number[];
  /** 적별 스폰 누적기(소수 spawn rate 적산) — waves.spawn 평탄화 인덱스 정렬 */
  spawnAccumulators: number[];
  gameOver: boolean;
  /** 누적 처치 수 */
  kills: number;
  /** 누적 스폰 수(웨이브 스폰 검증용) */
  spawned: number;
  /** 런 중 제시된 레벨업 선택지(★업그레이드 바인딩 단언용) */
  upgradeChoicesOffered: UpgradeChoice[];
  /** 동시 활성 엔티티(적+투사체) 역대 최댓값(★ENTITY_CAP 가드 검증용) */
  peakEntities: number;
}
