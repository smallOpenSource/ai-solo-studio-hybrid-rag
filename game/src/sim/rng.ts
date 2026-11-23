/**
 * rng.ts — mulberry32 seeded RNG (G005 US-G5-6 A1).
 *
 * web/src/office/sceneLogic.ts의 makeRng(mulberry32) 선례와 동일한 알고리즘.
 * 같은 seed면 동일한 난수열을 재현한다(★시뮬레이션 결정론의 토대 — playable.test.ts).
 */

export interface Rng {
  /** [0, 1) 부동소수 난수 */
  next(): number;
  /** [min, max) 범위 부동소수 */
  range(min: number, max: number): number;
  /** [0, n) 정수 (n>0) */
  int(n: number): number;
}

/** mulberry32 — 32비트 상태 seeded PRNG. 결정론·고속·의존성 0. */
export function makeRng(seed: number): Rng {
  let a = seed >>> 0;
  const next = (): number => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    range: (min: number, max: number): number => min + next() * (max - min),
    int: (n: number): number => Math.floor(next() * n),
  };
}
