/**
 * ActiveTasksPanel — 우 컬럼 상단 진행 작업 목록 (US-1c · "Bit-Driven Logic").
 *
 * 각 작업=제목 + pct% + 진행바(pixelInset 트랙·green 채움) + 담당자(우측정렬).
 * pixelPanel 래퍼·헤더 "Active Tasks". 색/폰트/베벨은 nexusTheme 토큰 전용.
 *
 * 정본: work/stitch/pixel_office/dashboard_nexus_os.html(Active Tasks 섹션, 다크 번역).
 */

import type { CSSProperties } from "react";
import {
  NEXUS_COLORS,
  NEXUS_FONTS,
  NEXUS_SPACING,
  BORDER_RADIUS,
  pixelPanel,
  pixelInset,
} from "./nexusTheme";

// ── Props ─────────────────────────────────────────────────────────────────────

/** 단일 진행 작업. */
export interface ActiveTask {
  /** 작업 제목. */
  title: string;
  /** 진행률(0~100). 진행바 width로 직접 사용. */
  pct: number;
  /** 담당자(우측 정렬 표시). */
  assignee: string;
}

export interface ActiveTasksPanelProps {
  /** 진행 중 작업 목록. */
  tasks: ActiveTask[];
  /**
   * ★P1 step3.5: 전체 활성 일괄 취소 핸들러(선택). 주어지고 tasks가 있으면 헤더에
   * "전체 취소" 버튼을 노출한다. 멀티 워커풀(concurrency:N)에서 동시 다중 활성 task를
   * 한 번에 취소(POST /api/run/cancel-all). 미지정이면 버튼 없음(기존 동작 보존).
   */
  onCancelAll?: () => void;
}

// ── ActiveTasksPanel ─────────────────────────────────────────────────────────────

/**
 * 진행 중 작업 목록 패널. 빈 배열이면 헤더 + idle 안내 한 줄(데모 제거·정직 표시).
 */
export function ActiveTasksPanel({ tasks, onCancelAll }: ActiveTasksPanelProps) {
  return (
    <div style={panelStyle}>
      <div style={headerStyle}>
        <span>Active Tasks</span>
        {onCancelAll && tasks.length > 0 && (
          <button
            type="button"
            data-testid="cancel-all-btn"
            onClick={onCancelAll}
            style={cancelBtnStyle}
            title="진행 중인 모든 워크플로를 일괄 취소"
          >
            전체 취소
          </button>
        )}
      </div>
      <div style={bodyStyle}>
        {tasks.length === 0 ? (
          <div data-testid="active-tasks-idle" style={idleStyle}>
            진행 중인 작업이 없습니다 (idle)
          </div>
        ) : (
          tasks.map((task, i) => <TaskRow key={i} task={task} />)
        )}
      </div>
    </div>
  );
}

// ── 작업 1행 ────────────────────────────────────────────────────────────────────

/** 제목 + pct% + 진행바 + 담당자. */
function TaskRow({ task }: { task: ActiveTask }) {
  return (
    <div data-testid="active-task" style={taskRowStyle}>
      {/* 상단: 제목 + pct%. */}
      <div style={taskTopStyle}>
        <span style={taskTitleStyle}>{task.title}</span>
        <span style={taskPctStyle}>{task.pct}%</span>
      </div>
      {/* 진행바: pixelInset 트랙·green 채움. */}
      <div style={trackStyle}>
        <div
          data-testid="task-progress-fill"
          style={{ ...fillStyle, width: `${task.pct}%` }}
        />
      </div>
      {/* 담당자(우측 정렬). */}
      <div style={assigneeStyle}>Assigned: {task.assignee}</div>
    </div>
  );
}

// ── 스타일(인라인·nexusTheme 토큰 전용) ─────────────────────────────────────────

const panelStyle: CSSProperties = {
  ...pixelPanel(),
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
};

const headerStyle: CSSProperties = {
  padding: "8px 16px",
  backgroundColor: NEXUS_COLORS.indigo,
  color: NEXUS_COLORS.onSurface,
  fontFamily: NEXUS_FONTS.headline,
  fontWeight: 700,
  fontSize: 16,
  borderBottomStyle: "solid",
  borderBottomWidth: 2,
  borderBottomColor: NEXUS_COLORS.bevelDark,
  // ★step3.5: 제목 + 선택적 "전체 취소" 버튼을 양끝 정렬.
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 8,
};

// ★step3.5 "전체 취소" 버튼(danger·error 토큰·pixel 외곽선). 활성 task가 있고
// onCancelAll이 주어질 때만 렌더(파괴적 동작이라 절제된 노출).
const cancelBtnStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 11,
  fontWeight: 700,
  color: NEXUS_COLORS.error,
  backgroundColor: "transparent",
  borderStyle: "solid",
  borderWidth: 2,
  borderColor: NEXUS_COLORS.error,
  borderRadius: BORDER_RADIUS,
  padding: "2px 8px",
  cursor: "pointer",
  flexShrink: 0,
};

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.gutter,
  padding: NEXUS_SPACING.gutter,
  overflowY: "auto",
  minHeight: 0,
};

const taskRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.unit,
};

const taskTopStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
};

const taskTitleStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
  fontWeight: 700,
  color: NEXUS_COLORS.onSurface,
};

const taskPctStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
  fontWeight: 700,
  color: NEXUS_COLORS.green,
};

// 진행바 트랙: pixelInset(함몰) + 검정 바닥.
const trackStyle: CSSProperties = {
  ...pixelInset(NEXUS_COLORS.black),
  height: 18,
  padding: 2,
  width: "100%",
};

// 채움: green.
const fillStyle: CSSProperties = {
  height: "100%",
  backgroundColor: NEXUS_COLORS.green,
  borderRadius: BORDER_RADIUS,
};

const assigneeStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 10,
  color: NEXUS_COLORS.muted,
  textAlign: "right",
};

// 빈 상태 idle 안내(데모 제거 후 실 이벤트 없을 때 정직 표시).
const idleStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 12,
  color: NEXUS_COLORS.muted,
};

export default ActiveTasksPanel;
