/**
 * G005 게임 스키마 검증 — office/game_schema.py의 TS 포팅 (US-G5-2 AC4).
 *
 * 모든 검증 규칙은 Python 원본과 동치:
 *   - 수치 범위(>0 vs >=0), enum, 개수 하한, 비감소, tStart 엄격 증가.
 *   - bool을 number로 오인 금지(typeof 검사).
 *   - validate_all → validateAll (cross-file 참조 무결성 포함).
 */

export interface ValidationResult {
  ok: boolean;
  errors: string[];
}

// ── 공통 헬퍼 ────────────────────────────────────────────────────────────────

/** JSON number 여부 — bool은 number로 보지 않음 */
function isNumber(v: unknown): v is number {
  return typeof v === "number" && !Number.isNaN(v);
}

/** JSON integer 여부 — bool 제외, 소수점 없음 */
function isInt(v: unknown): v is number {
  return isNumber(v) && Number.isFinite(v) && Math.floor(v) === v;
}

function checkStr(
  obj: Record<string, unknown>,
  key: string,
  where: string,
  errors: string[]
): void {
  if (!(key in obj)) {
    errors.push(`${where}: 필수 필드 누락 '${key}'`);
  } else if (typeof obj[key] !== "string" || !(obj[key] as string)) {
    errors.push(`${where}: '${key}'는 비어있지 않은 문자열이어야 함`);
  }
}

interface CheckNumberOpts {
  minimum?: number;
  exclusive?: boolean;
  integer?: boolean;
}

function checkNumber(
  obj: Record<string, unknown>,
  key: string,
  where: string,
  errors: string[],
  opts: CheckNumberOpts = {}
): void {
  if (!(key in obj)) {
    errors.push(`${where}: 필수 필드 누락 '${key}'`);
    return;
  }
  const val = obj[key];
  const { minimum, exclusive = false, integer = false } = opts;

  if (integer) {
    if (!isInt(val)) {
      errors.push(`${where}: '${key}'는 정수여야 함`);
      return;
    }
  } else if (!isNumber(val)) {
    errors.push(`${where}: '${key}'는 숫자여야 함`);
    return;
  }

  const num = val as number;
  if (minimum !== undefined) {
    if (exclusive && !(num > minimum)) {
      errors.push(`${where}: '${key}'는 ${minimum}보다 커야 함 (받음: ${num})`);
    } else if (!exclusive && !(num >= minimum)) {
      errors.push(`${where}: '${key}'는 ${minimum} 이상이어야 함 (받음: ${num})`);
    }
  }
}

interface CheckNumberListOpts {
  nonDecreasing?: boolean;
}

function checkNumberList(
  obj: Record<string, unknown>,
  key: string,
  where: string,
  errors: string[],
  opts: CheckNumberListOpts = {}
): void {
  if (!(key in obj)) {
    errors.push(`${where}: 필수 필드 누락 '${key}'`);
    return;
  }
  const arr = obj[key];
  if (!Array.isArray(arr)) {
    errors.push(`${where}: '${key}'는 배열이어야 함`);
    return;
  }
  if (!arr.every(isNumber)) {
    errors.push(`${where}: '${key}' 원소는 모두 숫자여야 함`);
    return;
  }
  if (opts.nonDecreasing) {
    for (let i = 1; i < arr.length; i++) {
      if (arr[i] < arr[i - 1]) {
        errors.push(
          `${where}: '${key}'는 비감소여야 함 (index ${i}: ${arr[i]} < ${arr[i - 1]})`
        );
        break;
      }
    }
  }
}

function itemsList(
  obj: unknown,
  key: string,
  minCount: number,
  label: string,
  errors: string[]
): unknown[] {
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    errors.push(`${label}: 루트는 객체여야 함`);
    return [];
  }
  const rec = obj as Record<string, unknown>;
  const items = rec[key];
  if (!Array.isArray(items)) {
    errors.push(`${label}: '${key}'는 배열이어야 함`);
    return [];
  }
  if (items.length < minCount) {
    errors.push(`${label}: '${key}'는 최소 ${minCount}개여야 함 (받음: ${items.length})`);
  }
  return items;
}

function result(errors: string[]): ValidationResult {
  return { ok: errors.length === 0, errors };
}

// ── 단계별 순수 검증 함수 ────────────────────────────────────────────────────

const ENEMY_BEHAVIORS = new Set(["chase", "swarm", "ranged", "charger"]);

/** meta.json 검증 */
export function validateMeta(obj: unknown): ValidationResult {
  const errors: string[] = [];
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    return result([`meta: 루트는 객체여야 함 (받음: ${typeof obj})`]);
  }
  const rec = obj as Record<string, unknown>;

  for (const key of ["theme", "title", "coreLoop", "win", "lose"]) {
    checkStr(rec, key, "meta", errors);
  }

  const cont = rec["continue"];
  if (typeof cont !== "object" || cont === null || Array.isArray(cont)) {
    errors.push("meta: 'continue'는 객체여야 함");
  } else {
    const c = cont as Record<string, unknown>;
    checkNumber(c, "freeRetries", "meta.continue", errors, { minimum: 0, integer: true });
    checkNumberList(c, "costCurve", "meta.continue", errors, { nonDecreasing: true });
    checkNumber(c, "sessionCap", "meta.continue", errors, { minimum: 1, integer: true });
  }

  return result(errors);
}

/** enemies.json 검증 */
export function validateEnemies(obj: unknown): ValidationResult {
  const errors: string[] = [];
  const items = itemsList(obj, "enemies", 3, "enemies", errors);

  const seenIds = new Set<string>();
  for (let i = 0; i < items.length; i++) {
    const e = items[i];
    const where = `enemies[${i}]`;
    if (typeof e !== "object" || e === null || Array.isArray(e)) {
      errors.push(`${where}: 객체여야 함`);
      continue;
    }
    const rec = e as Record<string, unknown>;
    checkStr(rec, "id", where, errors);
    checkStr(rec, "name", where, errors);
    checkNumber(rec, "hp", where, errors, { minimum: 0, exclusive: true });
    checkNumber(rec, "speed", where, errors, { minimum: 0, exclusive: true });
    checkNumber(rec, "damage", where, errors, { minimum: 0 });
    checkNumber(rec, "spawnWeight", where, errors, { minimum: 0, exclusive: true });

    const behavior = rec["behavior"];
    if (!ENEMY_BEHAVIORS.has(behavior as string)) {
      errors.push(
        `${where}: 'behavior'는 ${[...ENEMY_BEHAVIORS].sort().join("/")} 중 하나여야 함 (받음: ${JSON.stringify(behavior)})`
      );
    }

    const eid = rec["id"];
    if (typeof eid === "string" && eid) {
      if (seenIds.has(eid)) {
        errors.push(`${where}: 'id' 중복 (${JSON.stringify(eid)})`);
      }
      seenIds.add(eid);
    }
  }

  return result(errors);
}

/** weapons.json 검증 */
export function validateWeapons(obj: unknown): ValidationResult {
  const errors: string[] = [];
  const items = itemsList(obj, "weapons", 2, "weapons", errors);

  for (let i = 0; i < items.length; i++) {
    const w = items[i];
    const where = `weapons[${i}]`;
    if (typeof w !== "object" || w === null || Array.isArray(w)) {
      errors.push(`${where}: 객체여야 함`);
      continue;
    }
    const rec = w as Record<string, unknown>;
    checkStr(rec, "id", where, errors);
    checkStr(rec, "name", where, errors);
    checkNumber(rec, "fireRate", where, errors, { minimum: 0, exclusive: true });
    checkNumber(rec, "projectiles", where, errors, { minimum: 1, integer: true });
    checkNumber(rec, "damage", where, errors, { minimum: 0, exclusive: true });
    checkNumber(rec, "range", where, errors, { minimum: 0, exclusive: true });
    checkNumber(rec, "pierce", where, errors, { minimum: 0, integer: true });
  }

  return result(errors);
}

/** waves.json 검증 — tStart 엄격 오름차순(strict >) */
export function validateWaves(obj: unknown): ValidationResult {
  const errors: string[] = [];
  const items = itemsList(obj, "waves", 2, "waves", errors);

  let prevTstart: number | null = null;
  for (let i = 0; i < items.length; i++) {
    const w = items[i];
    const where = `waves[${i}]`;
    if (typeof w !== "object" || w === null || Array.isArray(w)) {
      errors.push(`${where}: 객체여야 함`);
      continue;
    }
    const rec = w as Record<string, unknown>;
    checkNumber(rec, "tStart", where, errors, { minimum: 0 });
    checkNumber(rec, "difficulty", where, errors, { minimum: 0, exclusive: true });

    const tStart = rec["tStart"];
    const tEnd = rec["tEnd"];

    if (!isNumber(tEnd)) {
      errors.push(`${where}: 'tEnd'는 숫자여야 함`);
    } else if (isNumber(tStart) && !(tEnd > tStart)) {
      errors.push(`${where}: 'tEnd'(${tEnd})는 'tStart'(${tStart})보다 커야 함`);
    }

    // tStart 엄격 오름차순(strict >)
    if (isNumber(tStart)) {
      if (prevTstart !== null && !(tStart > prevTstart)) {
        errors.push(
          `${where}: 'tStart'는 오름차순이어야 함 (${tStart} <= 이전 ${prevTstart})`
        );
      }
      prevTstart = tStart;
    }

    const spawn = rec["spawn"];
    if (!Array.isArray(spawn) || spawn.length === 0) {
      errors.push(`${where}: 'spawn'은 비어있지 않은 배열이어야 함`);
    } else {
      for (let j = 0; j < spawn.length; j++) {
        const s = spawn[j];
        const swhere = `${where}.spawn[${j}]`;
        if (typeof s !== "object" || s === null || Array.isArray(s)) {
          errors.push(`${swhere}: 객체여야 함`);
          continue;
        }
        const srec = s as Record<string, unknown>;
        checkStr(srec, "enemyId", swhere, errors);
        checkNumber(srec, "rate", swhere, errors, { minimum: 0, exclusive: true });
      }
    }
  }

  return result(errors);
}

/** upgrades.json 검증 */
export function validateUpgrades(obj: unknown): ValidationResult {
  const errors: string[] = [];
  const items = itemsList(obj, "upgrades", 3, "upgrades", errors);

  for (let i = 0; i < items.length; i++) {
    const u = items[i];
    const where = `upgrades[${i}]`;
    if (typeof u !== "object" || u === null || Array.isArray(u)) {
      errors.push(`${where}: 객체여야 함`);
      continue;
    }
    const rec = u as Record<string, unknown>;
    checkStr(rec, "id", where, errors);
    checkStr(rec, "name", where, errors);
    checkStr(rec, "stat", where, errors);
    checkNumber(rec, "delta", where, errors);
    checkNumber(rec, "cap", where, errors);
  }

  return result(errors);
}

/** balance.json 검증 */
export function validateBalance(obj: unknown): ValidationResult {
  const errors: string[] = [];
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    return result([`balance: 루트는 객체여야 함 (받음: ${typeof obj})`]);
  }
  const rec = obj as Record<string, unknown>;

  checkNumber(rec, "survivalTargetSec", "balance", errors, { minimum: 0, exclusive: true });
  checkNumberList(rec, "dpsCurve", "balance", errors, { nonDecreasing: true });

  if (!("inRange" in rec)) {
    errors.push("balance: 필수 필드 누락 'inRange'");
  } else if (typeof rec["inRange"] !== "boolean") {
    errors.push("balance: 'inRange'는 불리언이어야 함");
  }

  const report = rec["report"];
  if (typeof report !== "object" || report === null || Array.isArray(report)) {
    errors.push("balance: 'report'는 객체여야 함");
  } else {
    for (const [k, v] of Object.entries(report)) {
      if (typeof v !== "boolean") {
        errors.push(`balance.report['${k}']: 불리언이어야 함`);
      }
    }
  }

  return result(errors);
}

// ── 통합 검증 (데이터 객체 직접 수신 + cross-file 참조 무결성) ─────────────

export interface GameDataBundle {
  meta: unknown;
  enemies: unknown;
  weapons: unknown;
  waves: unknown;
  upgrades: unknown;
  balance: unknown;
}

/**
 * 6파일 데이터 번들 전체 검증 + cross-file 참조 무결성.
 * TS 환경에서는 파일 로드 없이 import된 JSON 객체를 직접 수신.
 */
export function validateAll(data: GameDataBundle): ValidationResult {
  const errors: string[] = [];

  const checks: Array<[string, unknown, (o: unknown) => ValidationResult]> = [
    ["meta.json", data.meta, validateMeta],
    ["enemies.json", data.enemies, validateEnemies],
    ["weapons.json", data.weapons, validateWeapons],
    ["waves.json", data.waves, validateWaves],
    ["upgrades.json", data.upgrades, validateUpgrades],
    ["balance.json", data.balance, validateBalance],
  ];

  for (const [fname, obj, validator] of checks) {
    const res = validator(obj);
    if (!res.ok) {
      for (const e of res.errors) {
        errors.push(`${fname} → ${e}`);
      }
    }
  }

  // cross-file 참조 무결성: waves.spawn.enemyId ∈ enemies.id
  const enemiesData = data.enemies;
  const wavesData = data.waves;
  if (
    typeof enemiesData === "object" &&
    enemiesData !== null &&
    typeof wavesData === "object" &&
    wavesData !== null
  ) {
    const eRec = enemiesData as Record<string, unknown>;
    const wRec = wavesData as Record<string, unknown>;
    const enemyIds = new Set<string>(
      Array.isArray(eRec["enemies"])
        ? (eRec["enemies"] as unknown[])
            .filter((e) => typeof e === "object" && e !== null && typeof (e as Record<string, unknown>)["id"] === "string")
            .map((e) => (e as Record<string, unknown>)["id"] as string)
        : []
    );
    if (Array.isArray(wRec["waves"])) {
      (wRec["waves"] as unknown[]).forEach((w, i) => {
        if (typeof w !== "object" || w === null) return;
        const wObj = w as Record<string, unknown>;
        if (!Array.isArray(wObj["spawn"])) return;
        (wObj["spawn"] as unknown[]).forEach((s, j) => {
          if (typeof s !== "object" || s === null) return;
          const sObj = s as Record<string, unknown>;
          const ref = sObj["enemyId"];
          if (typeof ref === "string" && !enemyIds.has(ref)) {
            errors.push(
              `참조 무결성: waves[${i}].spawn[${j}].enemyId=${JSON.stringify(ref)}가 enemies.id에 없음`
            );
          }
        });
      });
    }
  }

  return result(errors);
}
