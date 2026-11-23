/**
 * Phase 3 office/events.py to_dict() 출력을 미러한 TypeScript 타입.
 * 백엔드 SSE 계약의 프론트 미러 — 필드명/타입은 to_dict 키와 1:1 대응.
 *
 * 5패밀리 10이벤트:
 *   Lifecycle : RUN_STARTED · RUN_FINISHED · RUN_ERROR
 *   Text      : TEXT_MESSAGE
 *   ToolCall  : TOOL_CALL_START · TOOL_CALL_RESULT
 *   State     : STATE_SNAPSHOT · STATE_DELTA
 *   Interrupt : INTERRUPT_REQUESTED · INTERRUPT_RESOLVED · NODE_FAILED
 */

// ── 공통 기반 필드 ────────────────────────────────────────────────────────────

/** 모든 이벤트가 공유하는 기반 필드 */
export interface BaseEvent {
  run_id: string;
  ts: string;      // ISO-8601 문자열
  schema_v: number; // 현재 1 고정
  family: string;
  type: string;    // discriminant
}

// ── Lifecycle 패밀리 ──────────────────────────────────────────────────────────

export interface RunStarted extends BaseEvent {
  family: "Lifecycle";
  type: "RUN_STARTED";
  workflow: string;
}

/** status: "success" | "rejected" | "failed" */
export interface RunFinished extends BaseEvent {
  family: "Lifecycle";
  type: "RUN_FINISHED";
  workflow: string;
  status: string;
}

export interface RunError extends BaseEvent {
  family: "Lifecycle";
  type: "RUN_ERROR";
  error: string;
}

// ── Text 패밀리 ───────────────────────────────────────────────────────────────

export interface TextMessage extends BaseEvent {
  family: "Text";
  type: "TEXT_MESSAGE";
  role: string;    // "assistant" | "user" | "system"
  content: string;
  /** Phase 4a 이미지 토글 예약 필드. 현재는 null 고정. */
  image_url: string | null;
}

// ── ToolCall 패밀리 ───────────────────────────────────────────────────────────

export interface ToolCallStart extends BaseEvent {
  family: "ToolCall";
  type: "TOOL_CALL_START";
  node_id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolCallResult extends BaseEvent {
  family: "ToolCall";
  type: "TOOL_CALL_RESULT";
  node_id: string;
  name: string;
  result: Record<string, unknown>;
}

// ── State 패밀리 ──────────────────────────────────────────────────────────────

export interface StateSnapshot extends BaseEvent {
  family: "State";
  type: "STATE_SNAPSHOT";
  /** 현재 누적된 캐리포워드 전체 */
  accumulated: Record<string, unknown>;
}

export interface StateDelta extends BaseEvent {
  family: "State";
  type: "STATE_DELTA";
  /** 변경된 키→값 쌍만 */
  accumulated: Record<string, unknown>;
}

// ── Interrupt 패밀리 ──────────────────────────────────────────────────────────

export interface InterruptRequested extends BaseEvent {
  family: "Interrupt";
  type: "INTERRUPT_REQUESTED";
  node_id: string;
  tool: string;
  risk: string;   // "needs-approval"
  payload: Record<string, unknown>;
}

export interface InterruptResolved extends BaseEvent {
  family: "Interrupt";
  type: "INTERRUPT_RESOLVED";
  node_id: string;
  tool: string;
  decision: string; // "approve" | "reject"
}

/**
 * FailReason — office/events.py FailReason enum 미러.
 * Phase 4a에서 "question" 값 추가 예정 (자유 문자열 금지).
 */
export type FailReason = "gating" | "escalation" | "question";

export interface NodeFailed extends BaseEvent {
  family: "Interrupt";
  type: "NODE_FAILED";
  node_id: string;
  /** FailReason enum 값만 허용 (자유 문자열 금지) */
  reason: FailReason;
  detail: string;
}

// ── Discriminated Union (타입 가드 기반) ─────────────────────────────────────

/**
 * 알려진 10개 이벤트 타입의 discriminated union.
 * SSE 구독 훅이 미지 타입을 무시하고 이 타입만 처리한다.
 */
export type OfficeEvent =
  | RunStarted
  | RunFinished
  | RunError
  | TextMessage
  | ToolCallStart
  | ToolCallResult
  | StateSnapshot
  | StateDelta
  | InterruptRequested
  | InterruptResolved
  | NodeFailed;

/** 알려진 이벤트 type 문자열 집합 */
export const KNOWN_EVENT_TYPES = new Set<string>([
  "RUN_STARTED",
  "RUN_FINISHED",
  "RUN_ERROR",
  "TEXT_MESSAGE",
  "TOOL_CALL_START",
  "TOOL_CALL_RESULT",
  "STATE_SNAPSHOT",
  "STATE_DELTA",
  "INTERRUPT_REQUESTED",
  "INTERRUPT_RESOLVED",
  "NODE_FAILED",
]);

/**
 * 타입 가드 — 수신 JSON이 알려진 OfficeEvent인지 검사.
 * 미지 타입(type 필드가 KNOWN_EVENT_TYPES에 없는 것)은 false를 반환해 무시.
 */
export function isOfficeEvent(obj: unknown): obj is OfficeEvent {
  if (typeof obj !== "object" || obj === null) return false;
  const ev = obj as Record<string, unknown>;
  return (
    typeof ev.type === "string" &&
    KNOWN_EVENT_TYPES.has(ev.type) &&
    typeof ev.run_id === "string" &&
    typeof ev.ts === "string"
  );
}
