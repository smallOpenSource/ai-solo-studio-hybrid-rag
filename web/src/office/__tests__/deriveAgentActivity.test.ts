/**
 * deriveAgentActivity 단위 테스트 (US-N6/N7/N8·Vitest).
 *
 * ★실 crm_notify node_id 픽스처(n1_crm_get/n2_order_get/n2_5_summary/n3_email_send)와
 *   RUN_FINISHED(node_id 없음)로 검증한다 — 가짜 ceo/dev node_id 금지(계획 AC). node_id→역할은
 *   pickRoleKey 단일 경로이며 bubbleTargetRole과 동일 역할을 줘야 한다(단일 진실원천).
 *
 * 순수 함수라 phaser 무관(캔버스 mock 불요).
 */

import { describe, it, expect } from "vitest";
import {
  deriveAgentActivity,
  deriveActiveTasks,
  workingRoles,
  latestEmotionForRole,
} from "../deriveAgentActivity";
import { pickRoleKey, bubbleTargetRole, ROLE_KEY_NAMES, ROLE_KEYS } from "../sceneLogic";
import type { OfficeEvent } from "../../types/events";

// ── 이벤트 픽스처 헬퍼(events.ts 미러) ─────────────────────────────────────────

const BASE = { run_id: "r1", ts: "2026-06-14T00:00:00Z", schema_v: 1 } as const;

function toolStart(node_id: string): OfficeEvent {
  return { ...BASE, family: "ToolCall", type: "TOOL_CALL_START", node_id, name: "t", args: {} };
}
function toolResult(node_id: string): OfficeEvent {
  return { ...BASE, family: "ToolCall", type: "TOOL_CALL_RESULT", node_id, name: "t", result: {} };
}
function nodeFailed(node_id: string): OfficeEvent {
  return { ...BASE, family: "Interrupt", type: "NODE_FAILED", node_id, reason: "gating", detail: "" };
}
function runStarted(workflow = "crm_notify"): OfficeEvent {
  return { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow };
}
function runFinished(status: string, workflow = "crm_notify"): OfficeEvent {
  return { ...BASE, family: "Lifecycle", type: "RUN_FINISHED", workflow, status };
}
function runError(error = "boom"): OfficeEvent {
  return { ...BASE, family: "Lifecycle", type: "RUN_ERROR", error };
}
function textMsg(role: string, content: string): OfficeEvent {
  return { ...BASE, family: "Text", type: "TEXT_MESSAGE", role, content, image_url: null };
}

// 실 crm_notify 노드 → 시드 역할(SEED_ROLE_KEY_MAP).
const CRM_NODES = ["n1_crm_get", "n2_order_get", "n2_5_summary", "n3_email_send"] as const;

describe("deriveAgentActivity — 실 crm_notify node_id 활동 파생", () => {
  it("빈 입력: 전부 idle·activeCount 0·sessionScore 100(데이터 0)", () => {
    const a = deriveAgentActivity([]);
    for (const role of ROLE_KEYS) {
      expect(a.byRole[role]).toBe("idle");
      expect(a.activeCountByRole[role]).toBe(0);
    }
    expect(a.sessionScore).toBe(100);
  });

  it("TOOL_CALL_START(n1_crm_get) → 비서(secretary) working·activeCount 1", () => {
    const role = pickRoleKey("n1_crm_get"); // secretary.
    const a = deriveAgentActivity([toolStart("n1_crm_get")]);
    expect(role).toBe("secretary");
    expect(a.byRole.secretary).toBe("working");
    expect(a.activeCountByRole.secretary).toBe(1);
  });

  it("START→RESULT 짝이 맞으면 done·activeCount 0", () => {
    const a = deriveAgentActivity([toolStart("n2_5_summary"), toolResult("n2_5_summary")]);
    const role = pickRoleKey("n2_5_summary"); // planner.
    expect(a.byRole[role]).toBe("done");
    expect(a.activeCountByRole[role]).toBe(0);
  });

  it("중첩 START 2회·RESULT 1회 → 잔여 있으면 working 유지", () => {
    const a = deriveAgentActivity([
      toolStart("n3_email_send"),
      toolStart("n3_email_send"),
      toolResult("n3_email_send"),
    ]);
    const role = pickRoleKey("n3_email_send"); // marketer.
    expect(a.byRole[role]).toBe("working");
    expect(a.activeCountByRole[role]).toBe(1);
  });

  it("NODE_FAILED(n3_email_send) → 마케터 failed·count 0 하한", () => {
    const a = deriveAgentActivity([nodeFailed("n3_email_send")]);
    const role = pickRoleKey("n3_email_send");
    expect(a.byRole[role]).toBe("failed");
    expect(a.activeCountByRole[role]).toBe(0); // 0 하한(START 없어도 음수 금지).
  });

  it("RUN_FINISHED(node_id 없음) → 전역 sessionScore(success/total)", () => {
    expect(deriveAgentActivity([runFinished("success")]).sessionScore).toBe(100);
    expect(
      deriveAgentActivity([runFinished("success"), runFinished("failed")]).sessionScore,
    ).toBe(50);
    expect(
      deriveAgentActivity([
        runFinished("success"),
        runFinished("rejected"),
        runFinished("failed"),
        runFinished("success"),
      ]).sessionScore,
    ).toBe(50);
  });

  it("sessionScore는 항상 0~100 클램프", () => {
    const a = deriveAgentActivity([runFinished("failed"), runFinished("failed")]);
    expect(a.sessionScore).toBeGreaterThanOrEqual(0);
    expect(a.sessionScore).toBeLessThanOrEqual(100);
    expect(a.sessionScore).toBe(0);
  });

  it("★단일 진실원천: 동일 node_id가 bubble·activity·pickRoleKey에서 동일 역할", () => {
    for (const node of CRM_NODES) {
      const viaPick = pickRoleKey(node);
      const viaBubble = bubbleTargetRole({ type: "TOOL_CALL_START", node_id: node });
      const a = deriveAgentActivity([toolStart(node)]);
      expect(viaBubble).toBe(viaPick); // 말풍선과 활동이 같은 역할.
      expect(a.byRole[viaPick]).toBe("working"); // 활동도 같은 역할에 표기.
    }
  });
});

describe("workingRoles — working 역할 집합(D active-glow 대상)", () => {
  it("working 역할만 ROLE_KEYS 순서로 반환", () => {
    const a = deriveAgentActivity([toolStart("n1_crm_get"), toolStart("n3_email_send")]);
    const glow = workingRoles(a.byRole);
    expect(glow).toContain(pickRoleKey("n1_crm_get"));
    expect(glow).toContain(pickRoleKey("n3_email_send"));
    // idle 역할은 미포함.
    expect(glow).not.toContain("ceo");
  });

  it("전부 idle이면 빈 배열", () => {
    expect(workingRoles(deriveAgentActivity([]).byRole)).toEqual([]);
  });
});

describe("latestEmotionForRole — 선택 역할 최근 발화 감정(표시 전용)", () => {
  it("해당 역할의 최근 TEXT_MESSAGE를 classifyEmotion", () => {
    const speaker = pickRoleKey("assistant"); // assistant 발화 → 고정 역할.
    const events = [textMsg("assistant", "분석 중입니다"), textMsg("assistant", "성공 완료 감사합니다")];
    expect(latestEmotionForRole(events, speaker)).toBe("happy"); // 최근 메시지 기준.
  });

  it("해당 역할 발화가 없으면 neutral", () => {
    const speaker = pickRoleKey("assistant");
    const other = ROLE_KEYS.find((r) => r !== speaker)!;
    expect(latestEmotionForRole([textMsg("assistant", "성공")], other)).toBe("neutral");
    expect(latestEmotionForRole([], speaker)).toBe("neutral");
  });
});

describe("deriveActiveTasks — ★진행 중인 run만(완료/에러 제외)", () => {
  it("진행 중 run은 항목으로·pct<100·assignee=pickRoleKey(최근 노드)", () => {
    const events = [runStarted("crm_notify"), toolStart("n1_crm_get")];
    const tasks = deriveActiveTasks(events);
    expect(tasks).toHaveLength(1);
    expect(tasks[0].title).toBe("crm_notify");
    expect(tasks[0].pct).toBeLessThan(100);
    expect(tasks[0].pct).toBeLessThanOrEqual(90);
    expect(tasks[0].assignee).toBe(ROLE_KEY_NAMES[pickRoleKey("n1_crm_get")]); // 비서.
  });

  it("★RUN_FINISHED로 끝난 run은 진행 목록에서 제외(완료 100% 누적 금지)", () => {
    const events = [
      runStarted("crm_notify"),
      toolStart("n1_crm_get"),
      toolResult("n1_crm_get"),
      runFinished("success"),
    ];
    expect(deriveActiveTasks(events)).toEqual([]);
  });

  it("★RUN_ERROR로 끝난 run도 제외", () => {
    const events = [runStarted("crm_notify"), toolStart("n1_crm_get"), runError("boom")];
    expect(deriveActiveTasks(events)).toEqual([]);
  });

  it("진행 중 run과 완료 run 혼재 시 진행 중인 것만 남는다", () => {
    const events = [
      // 완료된 run(run_id=done) — 제외 대상.
      { ...BASE, run_id: "done", family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" } as OfficeEvent,
      { ...BASE, run_id: "done", family: "Lifecycle", type: "RUN_FINISHED", workflow: "crm_notify", status: "success" } as OfficeEvent,
      // 진행 중 run(run_id=live) — 표시 대상.
      { ...BASE, run_id: "live", family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" } as OfficeEvent,
      { ...BASE, run_id: "live", family: "ToolCall", type: "TOOL_CALL_START", node_id: "n2_5_summary", name: "t", args: {} } as OfficeEvent,
    ];
    const tasks = deriveActiveTasks(events);
    expect(tasks).toHaveLength(1);
    expect(tasks[0].runId).toBe("live");
  });

  it("빈 입력 → 빈 배열", () => {
    expect(deriveActiveTasks([])).toEqual([]);
  });

  it("결정론(같은 입력 → 같은 출력)", () => {
    const events = [runStarted(), toolStart("n1_crm_get")];
    expect(deriveActiveTasks(events)).toEqual(deriveActiveTasks(events));
  });
});
