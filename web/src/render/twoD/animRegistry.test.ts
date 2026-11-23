/**
 * animRegistry 단위 테스트 (Vitest) — 2D 비주얼 폴리시 생동감 §4.
 *
 * 순수 로직(pixi 무의존)이라 mock이 필요 없다. advanceAnim의 프레임 진행 결정론과
 * __resetAnim 격리, idle/walk 모드 전환을 검증한다.
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  advanceAnim,
  getAnim,
  __resetAnim,
  WALK_FRAME_COUNT,
  FRAME_INTERVAL_MS,
} from "./animRegistry";

describe("animRegistry — 프레임 진행 결정론·격리", () => {
  beforeEach(() => {
    __resetAnim(); // 모듈 스코프 격리 — 테스트 간 상태 누수 차단.
  });

  it("getAnim: 처음 조회 시 frame0·idle로 초기화", () => {
    const s = getAnim("worker-0");
    expect(s.frame).toBe(0);
    expect(s.mode).toBe("idle");
    expect(s.timer).toBe(0);
  });

  it("idle 모드는 항상 frame0 고정(경과 시간 무관)", () => {
    expect(advanceAnim("worker-0", "idle", 1000)).toBe(0);
    expect(advanceAnim("worker-0", "idle", 5000)).toBe(0);
    expect(getAnim("worker-0").frame).toBe(0);
  });

  it("walk: 임계 미만 deltaMs는 프레임 전진 0(누적만)", () => {
    // FRAME_INTERVAL_MS 미만이면 frame 유지.
    expect(advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS - 1)).toBe(0);
    const s = getAnim("worker-0");
    expect(s.frame).toBe(0);
    expect(s.timer).toBe(FRAME_INTERVAL_MS - 1);
  });

  it("walk: 임계 도달 시 frame 1칸 전진·timer 차감", () => {
    expect(advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS)).toBe(1);
    expect(getAnim("worker-0").frame).toBe(1);
    expect(getAnim("worker-0").timer).toBe(0);
  });

  it("walk: 누적 deltaMs로 다중 프레임 전진(while)", () => {
    // 3.5칸 분량 → 3프레임 전진, 잔여 timer 0.5칸.
    const frame = advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS * 3 + FRAME_INTERVAL_MS / 2);
    expect(frame).toBe(3);
    expect(getAnim("worker-0").timer).toBeCloseTo(FRAME_INTERVAL_MS / 2);
  });

  it("walk: WALK_FRAME_COUNT에서 0으로 래핑(모듈러)", () => {
    // 정확히 WALK_FRAME_COUNT칸 → 0으로 래핑(0→...→0).
    const frame = advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS * WALK_FRAME_COUNT);
    expect(frame).toBe(0);
  });

  it("walk: WALK_FRAME_COUNT+2칸 → frame=2(래핑 후 잔여)", () => {
    const frame = advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS * (WALK_FRAME_COUNT + 2));
    expect(frame).toBe(2);
  });

  it("walk→idle 전환 시 frame0·timer0으로 리셋", () => {
    advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS * 4); // frame=4.
    expect(getAnim("worker-0").frame).toBe(4);
    advanceAnim("worker-0", "idle", 50); // idle → 리셋.
    expect(getAnim("worker-0").frame).toBe(0);
    expect(getAnim("worker-0").timer).toBe(0);
  });

  it("음수 deltaMs는 0으로 클램프(역행·NaN 방지)", () => {
    expect(advanceAnim("worker-0", "walk", -100)).toBe(0);
    expect(getAnim("worker-0").timer).toBe(0);
  });

  it("아바타별 상태 독립(서로 다른 id는 별 위상)", () => {
    advanceAnim("worker-a", "walk", FRAME_INTERVAL_MS * 2); // a=2.
    advanceAnim("worker-b", "walk", FRAME_INTERVAL_MS * 5); // b=5.
    expect(getAnim("worker-a").frame).toBe(2);
    expect(getAnim("worker-b").frame).toBe(5);
  });

  it("__resetAnim: 모든 상태 초기화(다음 getAnim은 frame0)", () => {
    advanceAnim("worker-0", "walk", FRAME_INTERVAL_MS * 3);
    expect(getAnim("worker-0").frame).toBe(3);
    __resetAnim();
    expect(getAnim("worker-0").frame).toBe(0);
  });
});
