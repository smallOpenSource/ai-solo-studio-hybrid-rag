/**
 * control/api.ts — orch RO(읽기 전용) API 소비 (SR/renewal §6.8·§9.4).
 *
 * 백엔드 계약(P6a/P6b·orch RO API·읽기 전용·write 0):
 *   GET /agents/status → {agents: [{agent, state, current_task, since}]}  (§27.4)
 *   GET /tasks         → {tasks: [...], redis?}                           (§27.3)
 *
 * 모든 호출은 fetch() 기반(web/src/api/rest.ts 패턴 계승: AbortSignal.timeout + Error throw).
 * 테스트는 vi.stubGlobal('fetch', …) 또는 playwright window.fetch 스텁으로 mock 한다(네트워크 0).
 * ★base URL 은 same-origin 상대경로(WA 셸/별도 origin 합성은 P6c 인터페이스 주석 참조).
 */

import {
  type AgentStatus,
  type AgentStatusResponse,
  type TaskRecord,
  type TasksResponse,
  normalizeState,
} from "./types";

const TIMEOUT_MS = 15000;

/**
 * GET /agents/status — per-persona 8-상태(§27.4 4필드)를 가져온다(RO).
 * state 는 normalizeState 로 8값 중 하나로 정규화(미지 값 → Idle·표시 안전).
 * 실패 시 Error throw(rest.ts 동형).
 */
export async function fetchAgentStatus(base = ""): Promise<AgentStatus[]> {
  const res = await fetch(`${base}/agents/status`, {
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) {
    throw new Error(`fetchAgentStatus 실패: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as Partial<AgentStatusResponse>;
  return (data.agents ?? []).map((a) => ({
    agent: String(a.agent ?? ""),
    state: normalizeState(String(a.state ?? "Idle")),
    current_task: a.current_task ?? null,
    since: a.since ?? null,
  }));
}

/**
 * GET /tasks — task 큐(§27.3)를 가져온다(RO).
 * orch 가 REDIS 미설정이면 {tasks:[], redis:"disabled"} 를 200 으로 돌려준다(graceful·503 아님).
 * 실패 시 Error throw.
 */
export async function fetchTasks(base = ""): Promise<TaskRecord[]> {
  const res = await fetch(`${base}/tasks`, {
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) {
    throw new Error(`fetchTasks 실패: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as Partial<TasksResponse>;
  return data.tasks ?? [];
}
