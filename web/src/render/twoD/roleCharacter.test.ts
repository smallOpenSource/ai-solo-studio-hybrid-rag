/**
 * roleCharacter 단위 테스트 (Vitest) — 8역할 로스터 정의 + pickRole 결정론.
 *
 * 이 모듈은 순수 함수(pixi 무의존)라 mock 없이 직접 검증한다.
 * 검증 기준:
 *   - ROLES 길이 8·중복 없음.
 *   - ROLE_LAYOUT/ROLE_NAMES가 모든 역할 키를 빠짐없이 보유.
 *   - pickRole 결정론(같은 id → 같은 Role)·반환은 항상 ROLES 멤버.
 *   - worker-0(대기 직원)은 고정 역할(ceo).
 *   - 시드 node_id 고정 매핑.
 */

import { describe, it, expect } from "vitest";
import { ROLES, ROLE_LAYOUT, ROLE_NAMES, pickRole, type Role } from "./roleCharacter";

describe("roleCharacter — ROLES/ROLE_LAYOUT/ROLE_NAMES 계약", () => {
  it("ROLES는 8역할·중복 없음", () => {
    expect(ROLES).toHaveLength(8);
    expect(new Set(ROLES).size).toBe(8);
  });

  it("ROLE_LAYOUT은 모든 역할 키를 보유(누락 0)", () => {
    for (const role of ROLES) {
      expect(ROLE_LAYOUT[role]).toBeDefined();
      expect(typeof ROLE_LAYOUT[role].x).toBe("number");
      expect(typeof ROLE_LAYOUT[role].y).toBe("number");
    }
    // 좌표 키 = 역할 수(여분 키 없음).
    expect(Object.keys(ROLE_LAYOUT)).toHaveLength(8);
  });

  it("ROLE_NAMES는 모든 역할 키를 보유(누락 0)", () => {
    for (const role of ROLES) {
      expect(ROLE_NAMES[role]).toBeTruthy();
    }
    expect(Object.keys(ROLE_NAMES)).toHaveLength(8);
  });

  it("ROLE_LAYOUT 좌표는 스테이지(720x560) 안에 있다", () => {
    for (const role of ROLES) {
      const { x, y } = ROLE_LAYOUT[role];
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(720);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(560);
    }
  });
});

describe("roleCharacter — pickRole 결정론·매핑", () => {
  it("같은 avatar.id → 항상 같은 Role(결정론)", () => {
    const ids = ["worker-n1_crm_get", "worker-n2_order_get", "worker-xyz", "worker-0"];
    for (const id of ids) {
      const a = pickRole(id);
      const b = pickRole(id);
      expect(a).toBe(b);
    }
  });

  it("반환은 항상 ROLES 멤버(8역할 중 하나)", () => {
    const roleSet = new Set<Role>(ROLES);
    const ids = [
      "worker-0",
      "worker-n1_crm_get",
      "worker-n2_order_get",
      "worker-n2_5_summary",
      "worker-n3_email_send",
      "worker-some_dynamic_node",
      "worker-aaa",
      "worker-zzz",
    ];
    for (const id of ids) {
      expect(roleSet.has(pickRole(id))).toBe(true);
    }
  });

  it("worker-0(대기 직원)은 ceo로 고정", () => {
    expect(pickRole("worker-0")).toBe("ceo");
  });

  it("시드 node_id는 고정 역할로 매핑(가독성)", () => {
    expect(pickRole("worker-n1_crm_get")).toBe("secretary");
    expect(pickRole("worker-n2_order_get")).toBe("accountant");
    expect(pickRole("worker-n2_5_summary")).toBe("planner");
    expect(pickRole("worker-n3_email_send")).toBe("marketer");
  });

  it("worker- 접두 없는 id도 안전 처리(전체를 nodeId로)", () => {
    // 접두 없는 입력도 결정론·ROLES 멤버 반환(방어).
    const r = pickRole("n1_crm_get");
    expect(new Set<Role>(ROLES).has(r)).toBe(true);
    // node_id 부분이 같으므로 worker- 접두 버전과 동일 결과.
    expect(pickRole("n1_crm_get")).toBe(pickRole("worker-n1_crm_get"));
  });
});
