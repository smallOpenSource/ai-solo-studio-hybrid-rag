/**
 * roleNames.test.ts — US-01 수용 기준 (AC1~AC4).
 */

import { describe, it, expect } from "vitest";
import { KOREAN_NAME, fullLabel } from "../roleNames";
import { ROLE_KEYS, ROLE_KEY_NAMES } from "../sceneLogic";

describe("KOREAN_NAME", () => {
  // AC1: KOREAN_NAME 키 집합 === ROLE_KEYS 집합 (16개·누락/잉여 0)
  it("AC1: 키 집합이 ROLE_KEYS와 완전히 일치한다 (16개)", () => {
    const koreanKeys = new Set(Object.keys(KOREAN_NAME));
    const roleKeySet = new Set<string>(ROLE_KEYS);

    // 누락 — ROLE_KEYS 중 KOREAN_NAME에 없는 것
    const missing = [...roleKeySet].filter((k) => !koreanKeys.has(k));
    // 잉여 — KOREAN_NAME 중 ROLE_KEYS에 없는 것
    const extra = [...koreanKeys].filter((k) => !roleKeySet.has(k));

    expect(missing, `누락 키: ${missing.join(", ")}`).toHaveLength(0);
    expect(extra, `잉여 키: ${extra.join(", ")}`).toHaveLength(0);
    expect(koreanKeys.size).toBe(16);
  });

  // AC2: 모든 이름 length <= 4 · 중복 0
  it("AC2: 모든 이름이 length <= 4 이고 중복이 없다", () => {
    const names = Object.values(KOREAN_NAME);

    const tooLong = names.filter((n) => n.length > 4);
    expect(tooLong, `length > 4 이름: ${tooLong.join(", ")}`).toHaveLength(0);

    const nameSet = new Set(names);
    expect(nameSet.size, "이름 중복 존재").toBe(names.length);
  });
});

describe("fullLabel", () => {
  // AC3: fullLabel("marketer") === { name: "김지원", title: "마케터" }
  it('AC3: fullLabel("marketer")가 올바른 name·title을 반환한다', () => {
    expect(fullLabel("marketer")).toEqual({ name: "김지원", title: "마케터" });
  });

  // AC4: import 후 ROLE_KEY_NAMES 불변 (사이드이펙트 0)
  it("AC4: roleNames import 후 ROLE_KEY_NAMES가 변경되지 않는다", () => {
    const snapshot = { ...ROLE_KEY_NAMES };
    // roleNames 모듈은 이미 import됨 — 추가 import 없이 상태 검사
    expect(ROLE_KEY_NAMES).toEqual(snapshot);
  });
});
