/**
 * 이벤트 타입 가드 단위 테스트 (Vitest).
 *
 * 검증 항목:
 *   - isOfficeEvent: 알려진 10타입 식별
 *   - 미지 타입 거부
 *   - 필드 누락 거부
 *   - FailReason 타입 값 확인
 *   - KNOWN_EVENT_TYPES 집합 크기
 */

import { describe, it, expect } from "vitest";
import {
  isOfficeEvent,
  KNOWN_EVENT_TYPES,
  type RunStarted,
  type TextMessage,
  type NodeFailed,
  type OfficeEvent,
} from "../types/events";

// ── 테스트용 이벤트 팩토리 ────────────────────────────────────────────────────

const BASE = {
  run_id: "test-run-1",
  ts: "2026-06-08T00:00:00Z",
  schema_v: 1,
};

function makeRunStarted(): RunStarted {
  return { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" };
}

function makeTextMessage(): TextMessage {
  return {
    ...BASE,
    family: "Text",
    type: "TEXT_MESSAGE",
    role: "assistant",
    content: "안녕하세요",
    image_url: null,
  };
}

function makeNodeFailed(): NodeFailed {
  return {
    ...BASE,
    family: "Interrupt",
    type: "NODE_FAILED",
    node_id: "N1",
    reason: "escalation",
    detail: "length 가드 소진",
  };
}

// ── KNOWN_EVENT_TYPES ─────────────────────────────────────────────────────────

describe("KNOWN_EVENT_TYPES", () => {
  it("10개 이벤트 타입을 포함해야 한다", () => {
    expect(KNOWN_EVENT_TYPES.size).toBe(11); // NODE_FAILED 포함 11종 (10이벤트+1)
  });

  it("알려진 모든 type 문자열이 집합에 있어야 한다", () => {
    const expected = [
      "RUN_STARTED", "RUN_FINISHED", "RUN_ERROR",
      "TEXT_MESSAGE",
      "TOOL_CALL_START", "TOOL_CALL_RESULT",
      "STATE_SNAPSHOT", "STATE_DELTA",
      "INTERRUPT_REQUESTED", "INTERRUPT_RESOLVED", "NODE_FAILED",
    ];
    for (const t of expected) {
      expect(KNOWN_EVENT_TYPES.has(t), `${t} 가 KNOWN_EVENT_TYPES에 있어야 함`).toBe(true);
    }
  });
});

// ── isOfficeEvent 타입 가드 ───────────────────────────────────────────────────

describe("isOfficeEvent 타입 가드", () => {
  it("유효한 RUN_STARTED 이벤트를 인식해야 한다", () => {
    expect(isOfficeEvent(makeRunStarted())).toBe(true);
  });

  it("유효한 TEXT_MESSAGE 이벤트를 인식해야 한다", () => {
    expect(isOfficeEvent(makeTextMessage())).toBe(true);
  });

  it("유효한 NODE_FAILED 이벤트를 인식해야 한다", () => {
    expect(isOfficeEvent(makeNodeFailed())).toBe(true);
  });

  it("미지 type은 false를 반환해야 한다", () => {
    const unknown = { ...BASE, family: "Unknown", type: "UNKNOWN_TYPE" };
    expect(isOfficeEvent(unknown)).toBe(false);
  });

  it("type 필드가 없으면 false를 반환해야 한다", () => {
    const noType = { run_id: "x", ts: "2026-06-08T00:00:00Z", schema_v: 1 };
    expect(isOfficeEvent(noType)).toBe(false);
  });

  it("run_id 필드가 없으면 false를 반환해야 한다", () => {
    const noRunId = { ts: "2026-06-08T00:00:00Z", schema_v: 1, type: "RUN_STARTED" };
    expect(isOfficeEvent(noRunId)).toBe(false);
  });

  it("ts 필드가 없으면 false를 반환해야 한다", () => {
    const noTs = { run_id: "x", schema_v: 1, type: "RUN_STARTED" };
    expect(isOfficeEvent(noTs)).toBe(false);
  });

  it("null은 false를 반환해야 한다", () => {
    expect(isOfficeEvent(null)).toBe(false);
  });

  it("undefined는 false를 반환해야 한다", () => {
    expect(isOfficeEvent(undefined)).toBe(false);
  });

  it("문자열은 false를 반환해야 한다", () => {
    expect(isOfficeEvent("RUN_STARTED")).toBe(false);
  });

  it("숫자는 false를 반환해야 한다", () => {
    expect(isOfficeEvent(42)).toBe(false);
  });
});

// ── 각 이벤트 타입 인식 ───────────────────────────────────────────────────────

describe("모든 10이벤트 타입 인식", () => {
  const allEvents: OfficeEvent[] = [
    { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" },
    { ...BASE, family: "Lifecycle", type: "RUN_FINISHED", workflow: "crm_notify", status: "success" },
    { ...BASE, family: "Lifecycle", type: "RUN_ERROR", error: "오류 메시지" },
    { ...BASE, family: "Text", type: "TEXT_MESSAGE", role: "assistant", content: "텍스트", image_url: null },
    { ...BASE, family: "ToolCall", type: "TOOL_CALL_START", node_id: "N1", name: "crm_get", args: {} },
    { ...BASE, family: "ToolCall", type: "TOOL_CALL_RESULT", node_id: "N1", name: "crm_get", result: {} },
    { ...BASE, family: "State", type: "STATE_SNAPSHOT", accumulated: {} },
    { ...BASE, family: "State", type: "STATE_DELTA", accumulated: {} },
    { ...BASE, family: "Interrupt", type: "INTERRUPT_REQUESTED", node_id: "N3", tool: "email_send", risk: "needs-approval", payload: {} },
    { ...BASE, family: "Interrupt", type: "INTERRUPT_RESOLVED", node_id: "N3", tool: "email_send", decision: "approve" },
    { ...BASE, family: "Interrupt", type: "NODE_FAILED", node_id: "N1", reason: "escalation", detail: "가드 소진" },
  ];

  for (const ev of allEvents) {
    it(`${ev.type} 이벤트를 올바르게 인식해야 한다`, () => {
      expect(isOfficeEvent(ev)).toBe(true);
    });
  }
});

// ── TextMessage.image_url 필드 ────────────────────────────────────────────────

describe("TextMessage.image_url 필드", () => {
  it("image_url이 null인 TextMessage를 인식해야 한다", () => {
    const ev: TextMessage = {
      ...BASE,
      family: "Text",
      type: "TEXT_MESSAGE",
      role: "assistant",
      content: "텍스트",
      image_url: null,
    };
    expect(isOfficeEvent(ev)).toBe(true);
    expect(ev.image_url).toBeNull();
  });

  it("image_url이 문자열인 TextMessage를 인식해야 한다", () => {
    const ev: TextMessage = {
      ...BASE,
      family: "Text",
      type: "TEXT_MESSAGE",
      role: "assistant",
      content: "텍스트",
      image_url: "/work/1.png",
    };
    expect(isOfficeEvent(ev)).toBe(true);
    expect(ev.image_url).toBe("/work/1.png");
  });
});

// ── NodeFailed.reason FailReason 타입 ────────────────────────────────────────

describe("NodeFailed.reason FailReason 타입", () => {
  it("gating reason을 인식해야 한다", () => {
    const ev: NodeFailed = { ...makeNodeFailed(), reason: "gating" };
    expect(isOfficeEvent(ev)).toBe(true);
    expect(ev.reason).toBe("gating");
  });

  it("escalation reason을 인식해야 한다", () => {
    const ev: NodeFailed = { ...makeNodeFailed(), reason: "escalation" };
    expect(isOfficeEvent(ev)).toBe(true);
    expect(ev.reason).toBe("escalation");
  });

  it("question reason을 인식해야 한다 (Phase 4a 추가값)", () => {
    const ev: NodeFailed = { ...makeNodeFailed(), reason: "question" };
    expect(isOfficeEvent(ev)).toBe(true);
    expect(ev.reason).toBe("question");
  });
});
