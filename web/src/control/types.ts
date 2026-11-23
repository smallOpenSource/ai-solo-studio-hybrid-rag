/**
 * control/types.ts — SR/renewal §9.4 4-패널(P6c) RO 도메인 타입.
 *
 * 백엔드 계약(P6a/P6b·orch RO API):
 *   GET /agents/status → {agents: [{agent, state, current_task, since}]}  (§27.4 4필드·8-상태)
 *   GET /tasks         → {tasks: [...], redis?: "disabled"|"ok"}          (§27.3 task 큐)
 *
 * ★web/events.ts(= web/src/types/events.ts)는 **동결** — 여기서는 import(읽기)만 하고
 *   수정하지 않는다. control 모듈은 events.ts 의 SSE 타입을 Company Chat 에서 *읽기*로만 쓴다.
 * ★타입/상수는 orch 응답 형상을 1:1 미러한다(orch/agent_status.py AgentState·app.py /agents/status).
 */

// ── §9.4 / §27.4 8-상태 enum (orch/agent_status.py AgentState 미러·리터럴 8값) ──

/**
 * 정확히 8값(스펙 §9.4 line417·§27.4 line1877·orch/agent_status.py AgentState).
 * 추가/오타 금지 — orch enum 과 1:1.
 */
export type AgentStateValue =
  | "Idle"
  | "Researching"
  | "Coding"
  | "Reviewing"
  | "Meeting"
  | "Waiting"
  | "Blocked"
  | "Sleeping";

/** 8-상태 리터럴 집합(런타임 검증·미지 값 격리용). */
export const AGENT_STATES: readonly AgentStateValue[] = [
  "Idle",
  "Researching",
  "Coding",
  "Reviewing",
  "Meeting",
  "Waiting",
  "Blocked",
  "Sleeping",
] as const;

/**
 * GET /agents/status 의 per-persona 레코드(§27.4 4필드).
 * orch/app.py agents_status() 가 반환하는 형상과 1:1.
 */
export interface AgentStatus {
  /** persona id (config/personas.yaml·secretary 제외) */
  agent: string;
  /** 8-상태 중 하나(미실재 직원은 "Idle"·날조 0) */
  state: AgentStateValue;
  /** 현재 작업(thread_id/directive 도출·미실재면 null) */
  current_task: string | null;
  /** 마지막 전이 시각(ISO-8601·미실재면 null) */
  since: string | null;
}

/** GET /agents/status 응답 봉투. */
export interface AgentStatusResponse {
  agents: AgentStatus[];
}

// ── §27.3 task 큐 ─────────────────────────────────────────────────────────────

/**
 * GET /tasks 의 task 레코드. orch peek_tasks 는 임의 payload dict 를 그대로 돌려주므로
 * (orch/state_store.py peek_tasks) 표시에 필요한 필드만 optional 로 둔다(방어적·미존재 허용).
 */
export interface TaskRecord {
  task_id?: string | number;
  /** 작업 상태(queued|in_progress|done 등·§27.3) */
  status?: string;
  /** 표시용 제목/지시 요약 */
  directive?: string;
  title?: string;
  /** 담당 직원(persona id) */
  agent?: string;
  /** 시각(ISO-8601) */
  since?: string;
  ts?: string;
}

/** GET /tasks 응답 봉투(REDIS 미설정 시 redis:"disabled"·tasks 빈 배열). */
export interface TasksResponse {
  tasks: TaskRecord[];
  redis?: string;
}

// ── 8-상태 표시 메타(색/라벨) — 도크 상태판 단일 SoT ───────────────────────────

/** 한 상태의 표시 메타. badge 색(CSS color)·라벨·짧은 설명. */
export interface AgentStateMeta {
  /** 도크 배지 배경색(8값 구별). */
  color: string;
  /** 사람이 읽는 라벨(영문 enum 그대로·스펙 리터럴). */
  label: AgentStateValue;
}

/**
 * 8-상태 → 표시 메타. **리터럴 8값 전부** 매핑(누락 금지·exhaustive).
 * Record<AgentStateValue, …> 라 8값 중 하나라도 빠지면 tsc 에러(컴파일 가드).
 */
export const STATE_META: Record<AgentStateValue, AgentStateMeta> = {
  Idle: { color: "#6b7280", label: "Idle" }, // gray
  Researching: { color: "#3b82f6", label: "Researching" }, // blue
  Coding: { color: "#22c55e", label: "Coding" }, // green
  Reviewing: { color: "#a855f7", label: "Reviewing" }, // purple
  Meeting: { color: "#f59e0b", label: "Meeting" }, // amber
  Waiting: { color: "#eab308", label: "Waiting" }, // yellow
  Blocked: { color: "#ef4444", label: "Blocked" }, // red
  Sleeping: { color: "#475569", label: "Sleeping" }, // slate
};

/** 미지/오타 state 를 안전한 "Idle" 로 정규화(날조 아님 — 표시 폴백). */
export function normalizeState(s: string): AgentStateValue {
  return (AGENT_STATES as readonly string[]).includes(s)
    ? (s as AgentStateValue)
    : "Idle";
}
