/**
 * AgentStatusOverlay — §9.4 Agent Status Overlay (P6c·패널 2).
 *
 * ★배지 = **viewport 우측 도크 상태판**(아바타-추종 floating 아님).
 *   근거: SR/renewal/AGPL_COMPLIANCE.md 결정② — WA 스크립팅 API(WA.ui)에 per-avatar floating
 *   프리미티브가 없어 "아바타 위 배지"를 글자대로 구현하려면 play 브릭 fork(=AGPL §13 트리거)가
 *   필요하다. 도크 상태판은 §9.4 line405("스크립팅 API로 연동") 재량으로 play 브릭 비침투.
 *
 * 데이터: orch GET /agents/status (§27.4 4필드). 직원별 8-상태 배지(state→색/라벨)·current_task 표시.
 * ★데이터바인딩(렌더-트랩 아님): 배지 state/색/라벨은 **props.agents 값에서 파생**한다 —
 *   고정 placeholder 0. 응답값이 바뀌면 배지도 바뀐다(play_control_check.mjs 가 falsifiable 단언).
 */

import { type AgentStatus, STATE_META } from "./types";

export interface AgentStatusOverlayProps {
  /** GET /agents/status 의 agents 배열(orch RO 응답·§27.4). */
  agents: AgentStatus[];
  /** 로딩/에러 상태(컨테이너가 주입·선택적). */
  loading?: boolean;
  error?: string | null;
}

/**
 * 한 직원의 도크 행: 색 점(state 색) + 이름(agent) + state 라벨 + current_task.
 * data-* 속성으로 테스트가 바인딩을 결정론적으로 단언한다(DOM read·스크린샷 아님).
 */
function AgentRow({ a }: { a: AgentStatus }) {
  const meta = STATE_META[a.state];
  return (
    <li
      className="control-agent-row"
      data-agent={a.agent}
      data-state={a.state}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 8px",
      }}
    >
      <span
        className="control-state-dot"
        data-color={meta.color}
        aria-hidden="true"
        style={{
          width: 10,
          height: 10,
          borderRadius: "50%",
          background: meta.color,
          flex: "0 0 auto",
        }}
      />
      <span className="control-agent-name" style={{ fontWeight: 600 }}>
        {a.agent}
      </span>
      <span
        className="control-state-label"
        data-label={meta.label}
        style={{ color: meta.color, fontSize: 12 }}
      >
        {meta.label}
      </span>
      {a.current_task ? (
        <span
          className="control-current-task"
          title={a.current_task}
          style={{
            marginLeft: "auto",
            fontSize: 11,
            color: "#94a3b8",
            maxWidth: 160,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {a.current_task}
        </span>
      ) : null}
    </li>
  );
}

/**
 * Agent Status Overlay 도크 상태판(viewport 우측·아바타-추종 아님).
 * 직원별 8-상태 배지를 리스트로 렌더한다.
 */
export function AgentStatusOverlay({
  agents,
  loading = false,
  error = null,
}: AgentStatusOverlayProps) {
  return (
    <aside
      className="control-agent-overlay"
      data-testid="agent-status-overlay"
      data-count={agents.length}
      aria-label="Agent Status Overlay"
      style={{
        position: "relative",
        background: "#0f172a",
        color: "#e2e8f0",
        borderRadius: 8,
        padding: 8,
        minWidth: 240,
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
      }}
    >
      <header
        style={{
          fontSize: 12,
          fontWeight: 700,
          opacity: 0.8,
          padding: "2px 8px 6px",
        }}
      >
        직원 상태 (Agent Status)
      </header>
      {error ? (
        <div className="control-overlay-error" style={{ color: "#ef4444", padding: 8, fontSize: 12 }}>
          상태 조회 실패: {error}
        </div>
      ) : loading && agents.length === 0 ? (
        <div className="control-overlay-loading" style={{ padding: 8, fontSize: 12, opacity: 0.7 }}>
          로딩 중…
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {agents.map((a) => (
            <AgentRow key={a.agent} a={a} />
          ))}
        </ul>
      )}
    </aside>
  );
}
