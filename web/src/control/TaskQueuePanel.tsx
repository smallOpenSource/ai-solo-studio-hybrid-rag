/**
 * TaskQueuePanel — §9.4 Task Queue Panel (P6c·패널 3).
 *
 * 데이터: orch GET /tasks (§27.3 task 큐). 작업 큐/진행을 리스트로 표시.
 * orch peek_tasks 는 임의 payload dict 를 돌려주므로(state_store.peek_tasks) 표시 필드는
 * 방어적 optional(task_id/status/directive/title/agent). REDIS 미설정 시 빈 큐(graceful).
 *
 * ★데이터바인딩(렌더-트랩 아님): 행/상태/제목은 **props.tasks 값에서 파생** — 고정 0.
 *   응답이 바뀌면 행도 바뀐다(play_control_check.mjs falsifiable 단언).
 */

import { type TaskRecord } from "./types";

export interface TaskQueuePanelProps {
  /** GET /tasks 의 tasks 배열(orch RO 응답·§27.3). */
  tasks: TaskRecord[];
  loading?: boolean;
  error?: string | null;
}

/** task 행 표시 제목: directive → title → task_id 순 폴백. */
function taskTitle(t: TaskRecord): string {
  return String(t.directive ?? t.title ?? t.task_id ?? "(untitled)");
}

function TaskRow({ t }: { t: TaskRecord }) {
  const title = taskTitle(t);
  const status = t.status ?? "queued";
  return (
    <li
      className="control-task-row"
      data-task-id={t.task_id != null ? String(t.task_id) : undefined}
      data-status={status}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 8px",
        borderBottom: "1px solid #1e293b",
      }}
    >
      <span
        className="control-task-status"
        style={{
          fontSize: 10,
          textTransform: "uppercase",
          color: "#94a3b8",
          flex: "0 0 auto",
          minWidth: 64,
        }}
      >
        {status}
      </span>
      <span className="control-task-title" style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {title}
      </span>
      {t.agent ? (
        <span className="control-task-agent" style={{ marginLeft: "auto", fontSize: 11, color: "#64748b" }}>
          {t.agent}
        </span>
      ) : null}
    </li>
  );
}

/** Task Queue 패널 — task 큐를 리스트로 렌더(진행 상태 포함). */
export function TaskQueuePanel({ tasks, loading = false, error = null }: TaskQueuePanelProps) {
  return (
    <section
      className="control-task-queue"
      data-testid="task-queue-panel"
      data-count={tasks.length}
      aria-label="Task Queue"
      style={{
        background: "#0f172a",
        color: "#e2e8f0",
        borderRadius: 8,
        padding: 8,
        minWidth: 240,
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
      }}
    >
      <header style={{ fontSize: 12, fontWeight: 700, opacity: 0.8, padding: "2px 8px 6px" }}>
        작업 큐 (Task Queue)
      </header>
      {error ? (
        <div className="control-task-error" style={{ color: "#ef4444", padding: 8, fontSize: 12 }}>
          작업 큐 조회 실패: {error}
        </div>
      ) : loading && tasks.length === 0 ? (
        <div className="control-task-loading" style={{ padding: 8, fontSize: 12, opacity: 0.7 }}>
          로딩 중…
        </div>
      ) : tasks.length === 0 ? (
        <div className="control-task-empty" style={{ padding: 8, fontSize: 12, opacity: 0.6 }}>
          대기 중인 작업 없음
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {tasks.map((t, i) => (
            <TaskRow key={t.task_id != null ? String(t.task_id) : `task-${i}`} t={t} />
          ))}
        </ul>
      )}
    </section>
  );
}
