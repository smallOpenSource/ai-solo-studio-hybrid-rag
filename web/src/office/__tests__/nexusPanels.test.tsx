/**
 * Nexus 우 컬럼 패널 + 시스템 로그 단위 테스트 (US-1c/US-1d · @testing-library/react).
 *
 * 검증 항목:
 *   - ActiveTasksPanel: 진행바 width 스타일·헤더·담당자.
 *   - AgentCard: <img> src가 /assets/nexus/portraits/{role}_{emotion}.png 패턴·thinking→focused alias·onError 체인·스탯 바 width.
 *   - SystemLogPanel: 샘플 events 라인 수==기대·빈 배열→idle 문구·요약 본문.
 */

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ActiveTasksPanel } from "../ActiveTasksPanel";
import { AgentCard } from "../AgentCard";
import { SystemLogPanel, IDLE_LINE } from "../SystemLogPanel";
import { PORTRAIT_FALLBACK } from "../../config/emotionImages";
import type { OfficeEvent, TextMessage } from "../../types/events";

afterEach(() => cleanup());

// ── 이벤트 목 헬퍼(events.ts 미러 — chatPanel.test 관용구) ──────────────────────

const BASE = { run_id: "r1", ts: "2026-06-13T09:05:00Z", schema_v: 1 } as const;

function textMsg(role: string, content: string): TextMessage {
  return { ...BASE, family: "Text", type: "TEXT_MESSAGE", role, content, image_url: null };
}

function runStarted(workflow = "crm_notify"): OfficeEvent {
  return { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow };
}

// ── ActiveTasksPanel ─────────────────────────────────────────────────────────────

describe("ActiveTasksPanel — 진행 작업 목록", () => {
  const tasks = [
    { title: "Q3 Report Gen", pct: 75, assignee: "Data_Analyst" },
    { title: "Social Campaign", pct: 40, assignee: "Marketer" },
  ];

  it("헤더 'Active Tasks'와 각 작업 제목/담당자를 렌더한다", () => {
    render(<ActiveTasksPanel tasks={tasks} />);
    expect(screen.getByText("Active Tasks")).toBeDefined();
    expect(screen.getByText("Q3 Report Gen")).toBeDefined();
    expect(screen.getByText("Assigned: Marketer")).toBeDefined();
  });

  it("진행바 채움의 width가 pct%로 설정된다", () => {
    render(<ActiveTasksPanel tasks={tasks} />);
    const fills = screen.getAllByTestId("task-progress-fill");
    expect(fills).toHaveLength(2);
    expect(fills[0].style.width).toBe("75%");
    expect(fills[1].style.width).toBe("40%");
  });

  it("빈 배열이면 작업 행이 없다", () => {
    render(<ActiveTasksPanel tasks={[]} />);
    expect(screen.queryAllByTestId("active-task")).toHaveLength(0);
  });

  it("★빈 배열이면 idle 안내를 표시한다(데모 제거 후 정직 표시)", () => {
    render(<ActiveTasksPanel tasks={[]} />);
    expect(screen.getByTestId("active-tasks-idle")).toBeDefined();
    // 작업이 있으면 idle 안내는 없다.
    cleanup();
    render(<ActiveTasksPanel tasks={[{ title: "T", pct: 10, assignee: "X" }]} />);
    expect(screen.queryByTestId("active-tasks-idle")).toBeNull();
  });

  it("★step3.5: onCancelAll + 활성 작업이 있으면 '전체 취소' 버튼을 렌더하고 클릭 시 호출한다", () => {
    const onCancelAll = vi.fn();
    render(<ActiveTasksPanel tasks={tasks} onCancelAll={onCancelAll} />);
    const btn = screen.getByTestId("cancel-all-btn");
    expect(btn).toBeDefined();
    fireEvent.click(btn);
    expect(onCancelAll).toHaveBeenCalledTimes(1);
  });

  it("★step3.5: 작업이 없으면(idle) '전체 취소' 버튼을 렌더하지 않는다", () => {
    render(<ActiveTasksPanel tasks={[]} onCancelAll={() => {}} />);
    expect(screen.queryByTestId("cancel-all-btn")).toBeNull();
  });

  it("★step3.5: onCancelAll 미지정이면 '전체 취소' 버튼 없음(기존 동작 보존)", () => {
    render(<ActiveTasksPanel tasks={tasks} />);
    expect(screen.queryByTestId("cancel-all-btn")).toBeNull();
  });
});

// ── AgentCard ────────────────────────────────────────────────────────────────────

describe("AgentCard — JRPG 초상 카드 (★P4 nexus 초상)", () => {
  it("초상 <img> src가 /assets/nexus/portraits/{role}_{emotion}.png 패턴이다", () => {
    render(<AgentCard role="ceo" name="Agent_CEO" cls="Executive" emotion="happy" />);
    const img = screen.getByTestId("agent-portrait") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/assets/nexus/portraits/ceo_happy.png");
  });

  it("emotion 미지정 시 nexus neutral 경로로 폴백한다", () => {
    render(<AgentCard role="dev" name="Agent_Dev" />);
    const img = screen.getByTestId("agent-portrait") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/assets/nexus/portraits/dev_neutral.png");
  });

  it("★P4 thinking → focused(6번째 표정) alias로 표시한다(재분류 아님·표시 레이어)", () => {
    render(<AgentCard role="cfo" name="Agent_CFO" emotion="thinking" />);
    const img = screen.getByTestId("agent-portrait") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/assets/nexus/portraits/cfo_focused.png");
  });

  it("onError 폴백 체인: role_emotion → role_neutral → PORTRAIT_FALLBACK → 고정(루프 차단)", () => {
    render(<AgentCard role="legal" name="Agent_Legal" emotion="happy" />);
    const img = screen.getByTestId("agent-portrait") as HTMLImageElement;
    fireEvent.error(img); // 1차 → nexus legal_neutral
    expect(img.src).toContain("/assets/nexus/portraits/legal_neutral.png");
    fireEvent.error(img); // 2차 → PORTRAIT_FALLBACK
    expect(img.src).toContain(PORTRAIT_FALLBACK);
    fireEvent.error(img); // 3차 → 더는 안 바뀜(neutral로 핑퐁 금지)
    expect(img.src).toContain(PORTRAIT_FALLBACK);
  });

  it("이름·Class·Morale/Load 스탯 바 width를 렌더한다", () => {
    render(
      <AgentCard role="ceo" name="Agent_CEO" cls="Executive" morale={95} load={60} />,
    );
    expect(screen.getByText("Agent_CEO")).toBeDefined();
    expect(screen.getByText("Class: Executive")).toBeDefined();
    const fills = screen.getAllByTestId("agent-stat-fill");
    expect(fills).toHaveLength(2);
    expect(fills[0].style.width).toBe("95%"); // Morale
    expect(fills[1].style.width).toBe("60%"); // Load
  });

  it("★status 배지·'작업 N건' 라벨을 렌더한다(활동 바인딩·US-N7)", () => {
    render(
      <AgentCard role="dev" name="Agent_Dev" status="working" activeCount={2} />,
    );
    expect(screen.getByTestId("agent-status").textContent).toBe("작업중");
    expect(screen.getByTestId("agent-active-count").textContent).toBe("작업 2건");
  });

  it("status/activeCount 미지정 시 배지·카운트 라벨은 숨김(기존 카드 호환)", () => {
    render(<AgentCard role="ceo" name="Agent_CEO" morale={50} load={0} />);
    expect(screen.queryByTestId("agent-status")).toBeNull();
    expect(screen.queryByTestId("agent-active-count")).toBeNull();
  });

  it("load 이진(working=100/idle=0)이 스탯 바 width로 반영된다", () => {
    const { rerender } = render(
      <AgentCard role="dev" name="D" status="idle" activeCount={0} morale={80} load={0} />,
    );
    let fills = screen.getAllByTestId("agent-stat-fill");
    expect(fills[1].style.width).toBe("0%"); // idle → 0.
    rerender(
      <AgentCard role="dev" name="D" status="working" activeCount={1} morale={80} load={100} />,
    );
    fills = screen.getAllByTestId("agent-stat-fill");
    expect(fills[1].style.width).toBe("100%"); // working → 100(이진·소수% 없음).
  });
});

// ── SystemLogPanel ───────────────────────────────────────────────────────────────

describe("SystemLogPanel — 터미널 로그", () => {
  // ★C2: 기본 접힘(defaultCollapsed=true)으로 바뀌어, 라인 검증 테스트는 펼친 상태에서 한다.
  it("헤더 'SYSTEM LOG // terminal.exe'를 렌더한다(접힘에서도 헤더는 보임)", () => {
    render(<SystemLogPanel events={[]} />);
    expect(screen.getByText("SYSTEM LOG // terminal.exe")).toBeDefined();
  });

  it("★기본 접힘: 본문(라인) 숨김·data-collapsed=true·토글 버튼 존재", () => {
    render(<SystemLogPanel events={[textMsg("assistant", "x")]} />);
    expect(screen.getByTestId("system-log").getAttribute("data-collapsed")).toBe("true");
    expect(screen.queryByTestId("system-log-line")).toBeNull();
    expect(screen.getByTestId("syslog-toggle")).toBeDefined();
  });

  it("★토글 클릭 → 펼침(라인 표시)·재클릭 → 접힘(라인 숨김)", () => {
    render(<SystemLogPanel events={[textMsg("assistant", "x")]} />);
    fireEvent.click(screen.getByTestId("syslog-toggle"));
    expect(screen.getByTestId("system-log").getAttribute("data-collapsed")).toBe("false");
    expect(screen.getAllByTestId("system-log-line").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("syslog-toggle"));
    expect(screen.queryByTestId("system-log-line")).toBeNull();
  });

  it("빈 배열이면 idle 문구 한 줄만 렌더한다(펼침)", () => {
    render(<SystemLogPanel events={[]} defaultCollapsed={false} />);
    const lines = screen.getAllByTestId("system-log-line");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toBe(IDLE_LINE);
  });

  it("이벤트 라인 수가 입력 건수와 일치하고 type/요약을 담는다(펼침)", () => {
    const events: OfficeEvent[] = [
      runStarted("research_summary"),
      textMsg("assistant", "분석 완료"),
    ];
    render(<SystemLogPanel events={events} defaultCollapsed={false} />);
    const lines = screen.getAllByTestId("system-log-line");
    expect(lines).toHaveLength(2);
    expect(lines[0].textContent).toContain("RUN_STARTED");
    expect(lines[0].textContent).toContain("research_summary");
    expect(lines[1].textContent).toContain("TEXT_MESSAGE");
    expect(lines[1].textContent).toContain("분석 완료");
  });

  it("max를 초과하면 최근 max건만 렌더한다(펼침)", () => {
    const events: OfficeEvent[] = [
      runStarted("a"),
      runStarted("b"),
      runStarted("c"),
    ];
    render(<SystemLogPanel events={events} max={2} defaultCollapsed={false} />);
    const lines = screen.getAllByTestId("system-log-line");
    expect(lines).toHaveLength(2);
    // 최근 2건(b, c)만 — 가장 오래된 a는 제외.
    expect(lines[0].textContent).toContain("b");
    expect(lines[1].textContent).toContain("c");
  });
});
