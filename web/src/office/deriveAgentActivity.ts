/**
 * deriveAgentActivity — wsEvents → 역할 활동 상태 파생 (★rev2 활동 기반 정직 바인딩).
 *
 * ★설계 현실(Architect 실증): TOOL_CALL_START/RESULT·NODE_FAILED 의 node_id = engine 노드
 *   id(예 n1_crm_get)이며 16역할 키가 아니다. RUN_FINISHED는 node_id가 없다(workflow·status만).
 *   → 역할별 정량 morale/load는 데이터가 없으므로 위조 금지. 이 모듈은 "활동(activity)"만
 *   정직하게 파생한다:
 *     - node_id → 역할: ★pickRoleKey 단일 경로(시드맵+해시·sceneLogic SSOT·ChatPanel 선례).
 *     - load: 선택 역할 활성 노드 수(이진 working/idle은 표시 레이어에서).
 *     - morale: 전역 세션 성공률(RUN_FINISHED success/total·역할별 아님).
 *
 * 순수 함수 — 캔버스·React·네트워크·Date.now 무의존(결정론 단위 테스트). 컴포넌트/씬은
 * 표시만 한다. 백엔드/이벤트(events.ts 11종)는 0수정(read-only 파생).
 */

import type { OfficeEvent } from "../types/events";
import { ROLE_KEYS, ROLE_KEY_NAMES, pickRoleKey, type RoleKey } from "./sceneLogic";
import { classifyEmotion } from "../render/emotion";
import type { EmotionLabel } from "../config/emotionImages";

// ── 활동 상태 타입 ─────────────────────────────────────────────────────────────

/** 한 역할의 활동 상태(표시 배지·글로우 대상). */
export type AgentActivityStatus = "idle" | "working" | "done" | "failed";

/** wsEvents에서 파생한 활동 스냅샷(역할별 상태·활성수 + 전역 세션 성공률). */
export interface AgentActivity {
  /** 역할별 활동 상태(기본 idle). */
  byRole: Record<RoleKey, AgentActivityStatus>;
  /** 역할별 진행 중 노드 수(START - RESULT/FAILED, 0 하한). */
  activeCountByRole: Record<RoleKey, number>;
  /** 전역 세션 성공률(0~100·RUN_FINISHED success/total·데이터 0 시 100). */
  sessionScore: number;
}

/** 16 역할 키 전부를 같은 초기값으로 채운 레코드를 만든다(누락 0). */
function filledByRole<T>(value: T): Record<RoleKey, T> {
  const out = {} as Record<RoleKey, T>;
  for (const role of ROLE_KEYS) {
    out[role] = value;
  }
  return out;
}

/**
 * wsEvents를 역할 활동 스냅샷으로 파생한다(순수·결정론).
 *
 * - TOOL_CALL_START → pickRoleKey(node_id) 역할 working·activeCount+1.
 * - TOOL_CALL_RESULT → activeCount-1(0 하한)·잔여 있으면 working, 없으면 done.
 * - NODE_FAILED → activeCount-1(0 하한)·failed.
 * - RUN_FINISHED(node_id 없음) → 전역 sessionScore 갱신(success/total).
 * 기본 idle·sessionScore 기본 100(RUN_FINISHED 0건).
 *
 * @param events 누적 OfficeEvent 배열(append-only).
 */
export function deriveAgentActivity(events: readonly OfficeEvent[]): AgentActivity {
  const byRole = filledByRole<AgentActivityStatus>("idle");
  const activeCountByRole = filledByRole<number>(0);
  let success = 0;
  let finishedTotal = 0;

  for (const ev of events) {
    switch (ev.type) {
      case "TOOL_CALL_START": {
        const role = pickRoleKey(ev.node_id);
        activeCountByRole[role] += 1;
        byRole[role] = "working";
        break;
      }
      case "TOOL_CALL_RESULT": {
        const role = pickRoleKey(ev.node_id);
        activeCountByRole[role] = Math.max(0, activeCountByRole[role] - 1);
        byRole[role] = activeCountByRole[role] > 0 ? "working" : "done";
        break;
      }
      case "NODE_FAILED": {
        const role = pickRoleKey(ev.node_id);
        activeCountByRole[role] = Math.max(0, activeCountByRole[role] - 1);
        byRole[role] = "failed";
        break;
      }
      case "RUN_FINISHED": {
        finishedTotal += 1;
        if (ev.status === "success") {
          success += 1;
        }
        break;
      }
      default:
        break; // 그 외 이벤트는 활동에 무관(read-only).
    }
  }

  const rawScore = finishedTotal === 0 ? 100 : Math.round((success / finishedTotal) * 100);
  const sessionScore = Math.min(100, Math.max(0, rawScore)); // 0~100 클램프.
  return { byRole, activeCountByRole, sessionScore };
}

/**
 * 활동 상태에서 "지금 일하는"(working) 역할 키 집합을 고른다(순수·D active-glow 대상).
 *
 * @param byRole deriveAgentActivity().byRole.
 * @returns ROLE_KEYS 순서를 보존한 working 역할 배열(글로우 대상).
 */
export function workingRoles(byRole: Record<RoleKey, AgentActivityStatus>): RoleKey[] {
  return ROLE_KEYS.filter((role) => byRole[role] === "working");
}

// ── 카드 표정 바인딩 (선택 역할 최근 발화 감정) ─────────────────────────────────

/**
 * 한 역할의 가장 최근 TEXT_MESSAGE 감정을 분류해 반환한다(순수·표시 전용).
 *
 * ★TextMessage는 node_id가 없으므로 ChatPanel 선례와 동일하게 식별자=node_id(있으면)
 *   또는 role 문자열을 pickRoleKey에 넘겨 발화자 역할을 고른다. 해당 역할 메시지가 없으면
 *   neutral. classifyEmotion은 0수정(thinking→focused는 카드 표시 alias가 담당).
 *
 * @param events 누적 OfficeEvent 배열.
 * @param role 대상 16 역할 키(선택 역할).
 * @returns EmotionLabel(매칭 없으면 "neutral").
 */
export function latestEmotionForRole(
  events: readonly OfficeEvent[],
  role: RoleKey,
): EmotionLabel {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (ev.type !== "TEXT_MESSAGE") {
      continue;
    }
    const nodeId = (ev as { node_id?: string }).node_id;
    const speaker = pickRoleKey(nodeId && nodeId.length > 0 ? nodeId : ev.role);
    if (speaker === role) {
      return classifyEmotion(ev.content);
    }
  }
  return "neutral";
}

// ── Active Tasks 파생 (워크플로 run → 진행 작업) ──────────────────────────────

/** 파생된 진행 작업 1건(ActiveTasksPanel 입력으로 매핑). ★진행 중인 run만 — 완료/에러 제외. */
export interface DerivedTask {
  /** run_id(키·결정론 정렬용). */
  runId: string;
  /** 작업 제목(=workflow 이름). */
  title: string;
  /** 진행률 0~100(미완 근사·관측 tool 완료 비율·상한 90). */
  pct: number;
  /** 담당자 표시명(최근 활성 노드 → pickRoleKey 역할명·없으면 "—"). */
  assignee: string;
}

/** run별 누적 상태(파생 중간값). */
interface RunAccum {
  title: string;
  started: Set<string>;
  completed: Set<string>;
  /** 종료 여부(RUN_FINISHED 또는 RUN_ERROR) — 진행 목록에서 제외 기준. */
  terminal: boolean;
  lastNode?: string;
}

/**
 * wsEvents를 ★진행 중인 워크플로 run만의 작업 목록으로 파생한다(순수·결정론).
 *
 * RUN_FINISHED(success/rejected/failed)나 RUN_ERROR로 끝난 run은 제외한다 — "Active Tasks"는
 * 진행 중인 것만 보여준다(완료 100% 누적 표시 금지). 시작됐고 아직 안 끝난 run만 남는다.
 *
 * ★정직: 워크플로 총 노드 수를 모르므로(비침투) 진행률은 "관측한 tool 호출의 완료 비율"
 *   근사다 — min(90, 완료/시작·%)로 90% 상한(완료 전 100% 위조 금지·진행 중이므로 100 불가).
 *   assignee는 최근 TOOL_CALL_START node_id의 pickRoleKey 역할명. Map은 삽입 순서 보존이라
 *   run 등장 순서 결정론.
 *
 * @param events 누적 OfficeEvent 배열.
 */
export function deriveActiveTasks(events: readonly OfficeEvent[]): DerivedTask[] {
  const runs = new Map<string, RunAccum>();
  const ensure = (runId: string): RunAccum => {
    let accum = runs.get(runId);
    if (!accum) {
      accum = { title: "workflow", started: new Set(), completed: new Set(), terminal: false };
      runs.set(runId, accum);
    }
    return accum;
  };

  for (const ev of events) {
    switch (ev.type) {
      case "RUN_STARTED":
        ensure(ev.run_id).title = ev.workflow;
        break;
      case "TOOL_CALL_START": {
        const accum = ensure(ev.run_id);
        accum.started.add(ev.node_id);
        accum.lastNode = ev.node_id;
        break;
      }
      case "TOOL_CALL_RESULT":
        ensure(ev.run_id).completed.add(ev.node_id);
        break;
      case "NODE_FAILED":
        ensure(ev.run_id).completed.add(ev.node_id);
        break;
      case "RUN_FINISHED":
      case "RUN_ERROR":
        ensure(ev.run_id).terminal = true; // 종료 — 진행 목록에서 제외.
        break;
      default:
        break;
    }
  }

  const out: DerivedTask[] = [];
  for (const [runId, accum] of runs) {
    if (accum.terminal) {
      continue; // ★진행 중인 run만(완료/에러 제외).
    }
    const pct =
      accum.started.size > 0
        ? Math.min(90, Math.round((accum.completed.size / accum.started.size) * 100))
        : 5; // 시작만 됨 — 진행 슬리버.
    const assignee = accum.lastNode ? ROLE_KEY_NAMES[pickRoleKey(accum.lastNode)] : "—";
    out.push({ runId, title: accum.title, pct, assignee });
  }
  return out;
}
