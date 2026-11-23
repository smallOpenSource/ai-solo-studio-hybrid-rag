/**
 * SystemLogPanel — 터미널 시스템 로그 (US-1d · "Bit-Driven Logic").
 *
 * terminalWindow() 섀시·헤더 "SYSTEM LOG // terminal.exe"·최근 max건 이벤트를
 * `> {type}: {요약}` 그린 라인으로(읽기 전용). 빈 배열이면 idle 라인.
 * 색/폰트/베벨은 nexusTheme 토큰 전용. events.ts 필드만 안전 접근(읽기·옵셔널 체이닝).
 *
 * 정본: work/stitch/pixel_office/dashboard_nexus_os.html(System Log 패널).
 */

import { useState, type CSSProperties } from "react";
import type { OfficeEvent } from "../types/events";
import { NEXUS_COLORS, NEXUS_FONTS, NEXUS_SPACING, terminalWindow } from "./nexusTheme";

// ── 이벤트 요약 (순수 — events.ts 실제 필드만, 옵셔널 접근) ──────────────────────

/** idle(빈 로그) 라인 문구. */
export const IDLE_LINE = "> Waiting for user input...";

/**
 * OfficeEvent를 `{type}: {요약}` 본문으로 — type 판별 후 존재 필드만 안전 접근.
 * events.ts를 수정하지 않고 union 멤버의 실제 필드를 읽는다(없으면 type만).
 */
export function summarizeEvent(ev: OfficeEvent): string {
  switch (ev.type) {
    case "RUN_STARTED":
    case "RUN_FINISHED":
      // RunStarted/RunFinished: workflow(+RunFinished는 status).
      return ev.type === "RUN_FINISHED"
        ? `${ev.type}: ${ev.workflow} (${ev.status})`
        : `${ev.type}: ${ev.workflow}`;
    case "RUN_ERROR":
      return `${ev.type}: ${ev.error}`;
    case "TEXT_MESSAGE":
      return `${ev.type}: [${ev.role}] ${ev.content}`;
    case "TOOL_CALL_START":
    case "TOOL_CALL_RESULT":
      return `${ev.type}: ${ev.name}`;
    case "STATE_SNAPSHOT":
    case "STATE_DELTA":
      // accumulated 키 수만 요약(값 덤프 회피).
      return `${ev.type}: ${Object.keys(ev.accumulated).length} keys`;
    case "INTERRUPT_REQUESTED":
      return `${ev.type}: ${ev.tool} (${ev.risk})`;
    case "INTERRUPT_RESOLVED":
      return `${ev.type}: ${ev.tool} -> ${ev.decision}`;
    case "NODE_FAILED":
      return `${ev.type}: ${ev.reason} - ${ev.detail}`;
    default:
      // 미지 type 안전망(union 외 — 런타임 방어).
      return `${(ev as { type: string }).type}`;
  }
}

// ── Props ─────────────────────────────────────────────────────────────────────

export interface SystemLogPanelProps {
  /** 표시할 이벤트(최근 것이 배열 끝이라고 가정). */
  events: OfficeEvent[];
  /** 표시 최대 건수(기본 8). */
  max?: number;
  /** ★C2: 초기 접힘 여부(기본 true — 기본 접힘 시작). */
  defaultCollapsed?: boolean;
}

// ── SystemLogPanel ───────────────────────────────────────────────────────────────

/**
 * 읽기 전용 시스템 로그. 최근 max건만 그린 라인으로·빈 배열이면 idle 라인.
 * ★C2: 기본 접힘(defaultCollapsed=true) + 토글 버튼(syslog-toggle)으로 펼침.
 *   접힘 시 본문(라인 목록)을 숨겨 화면을 차지하지 않는다.
 */
export function SystemLogPanel({ events, max = 8, defaultCollapsed = true }: SystemLogPanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const recent = events.slice(-max);
  return (
    <div data-testid="system-log" data-collapsed={collapsed} style={windowStyle}>
      <div style={titleBarStyle}>
        <span style={{ flex: 1 }}>SYSTEM LOG // terminal.exe</span>
        <button
          type="button"
          data-testid="syslog-toggle"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "시스템 로그 펼치기" : "시스템 로그 접기"}
          aria-label={collapsed ? "시스템 로그 펼치기" : "시스템 로그 접기"}
          style={toggleBtnStyle}
        >
          {collapsed ? "▼" : "▲"}
        </button>
      </div>
      {!collapsed && (
        <div style={bodyStyle}>
          {recent.length === 0 ? (
            <div data-testid="system-log-line" style={lineStyle}>
              {IDLE_LINE}
            </div>
          ) : (
            recent.map((ev, i) => (
              <div key={i} data-testid="system-log-line" style={lineStyle}>
                &gt; {summarizeEvent(ev)}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── 스타일(인라인·nexusTheme 토큰 전용) ─────────────────────────────────────────

// 접힘 시 헤더만 남도록 height는 고정하지 않고 본문에 maxHeight를 둔다(접힘=헤더 높이).
const windowStyle: CSSProperties = {
  ...terminalWindow(),
  display: "flex",
  flexDirection: "column",
};

const titleBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: NEXUS_SPACING.unit,
  padding: "4px 12px",
  backgroundColor: NEXUS_COLORS.surfaceHigh,
  color: NEXUS_COLORS.onSurfaceVariant,
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 12,
  fontWeight: 700,
  borderBottomStyle: "solid",
  borderBottomWidth: 1,
  borderBottomColor: NEXUS_COLORS.outlineVariant,
};

// 헤더 토글 버튼(작은 아이콘).
const toggleBtnStyle: CSSProperties = {
  background: "transparent",
  borderStyle: "none",
  color: NEXUS_COLORS.green,
  cursor: "pointer",
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 12,
  padding: "0 4px",
};

// 펼침 시 본문: 고정 최대 높이 내에서 스크롤(헤더는 항상 보임). 접힘 시엔 렌더 0.
const bodyStyle: CSSProperties = {
  height: 132,
  minHeight: 0,
  overflowY: "auto",
  padding: NEXUS_SPACING.gutter - 4,
  display: "flex",
  flexDirection: "column",
  gap: NEXUS_SPACING.unit,
};

const lineStyle: CSSProperties = {
  fontFamily: NEXUS_FONTS.terminal,
  fontSize: 13,
  color: NEXUS_COLORS.green,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

export default SystemLogPanel;
