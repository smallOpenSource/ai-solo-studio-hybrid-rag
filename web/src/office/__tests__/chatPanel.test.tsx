/**
 * ChatPanel 단위 테스트 (office-v2 US-0e·@testing-library/react).
 *
 * 검증 항목:
 *   - 좌/우 도킹 토글·접기/펼치기 상태 전환(data-side·data-collapsed).
 *   - TEXT_MESSAGE만 필터 렌더(다른 이벤트 무시)·role별 정렬.
 *   - 순수 헬퍼(selectTextMessages·roleLabel·formatTimestamp).
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import {
  ChatPanel,
  selectTextMessages,
  roleLabel,
  roleInitial,
  speakerLabel,
  formatTimestamp,
} from "../ChatPanel";
import { pickRoleKey, ROLE_KEY_NAMES } from "../sceneLogic";
import type { OfficeEvent, TextMessage } from "../../types/events";

const BASE = { run_id: "r1", ts: "2026-06-13T09:05:00Z", schema_v: 1 } as const;

function textMsg(role: string, content: string, ts = BASE.ts): TextMessage {
  return { ...BASE, ts, family: "Text", type: "TEXT_MESSAGE", role, content, image_url: null };
}

function runStarted(): OfficeEvent {
  return { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" };
}

afterEach(() => cleanup());

/**
 * ChatPanel을 렌더하고 펼친다(C2로 기본 접힘이 되어, 메시지/초상 검증 전 펼침이 필요).
 */
function renderExpanded(events: OfficeEvent[]) {
  const result = render(<ChatPanel events={events} />);
  fireEvent.click(screen.getByTestId("chat-toggle-collapse"));
  return result;
}

// ── 순수 헬퍼 ─────────────────────────────────────────────────────────────────

describe("ChatPanel 헬퍼 — 순수 함수", () => {
  it("selectTextMessages는 TEXT_MESSAGE만 추린다", () => {
    const events: OfficeEvent[] = [runStarted(), textMsg("assistant", "안녕"), runStarted()];
    const msgs = selectTextMessages(events);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toBe("안녕");
  });

  it("roleLabel/roleInitial 매핑(user→나·system→시스템·그외→직원)", () => {
    expect(roleLabel("user")).toBe("나");
    expect(roleLabel("system")).toBe("시스템");
    expect(roleLabel("assistant")).toBe("직원");
    expect(roleInitial("user")).toBe("나");
  });

  it("formatTimestamp는 HH:MM·잘못된 ts는 원문 폴백", () => {
    expect(formatTimestamp("2026-06-13T09:05:00Z")).toMatch(/^\d{2}:\d{2}$/);
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});

// ── 토글 상태 전환 ────────────────────────────────────────────────────────────

describe("ChatPanel — 좌/우 도킹·접기/펼치기 토글", () => {
  // ★C2: 기본 접힘(collapsed=true)으로 바뀌었다 — 펼침이 필요한 검증은 토글 후 한다.
  it("기본은 우측 도킹·접힘", () => {
    render(<ChatPanel events={[]} />);
    const panel = screen.getByTestId("chat-panel");
    expect(panel.getAttribute("data-side")).toBe("right");
    expect(panel.getAttribute("data-collapsed")).toBe("true");
  });

  it("좌우 토글 버튼이 side를 right↔left 전환한다", () => {
    render(<ChatPanel events={[]} />);
    const panel = screen.getByTestId("chat-panel");
    expect(panel.getAttribute("data-side")).toBe("right");

    fireEvent.click(screen.getByTestId("chat-toggle-side"));
    expect(panel.getAttribute("data-side")).toBe("left");

    fireEvent.click(screen.getByTestId("chat-toggle-side"));
    expect(panel.getAttribute("data-side")).toBe("right");
  });

  it("펼침 토글이 collapsed를 전환하고 메시지 목록을 표면화/숨김한다", () => {
    render(<ChatPanel events={[textMsg("assistant", "안녕")]} />);
    const panel = screen.getByTestId("chat-panel");

    // 기본 접힘: 메시지 목록 숨김.
    expect(panel.getAttribute("data-collapsed")).toBe("true");
    expect(screen.queryByTestId("chat-messages")).toBeNull();

    // 펼치기 클릭 → collapsed false·목록 등장.
    fireEvent.click(screen.getByTestId("chat-toggle-collapse"));
    expect(panel.getAttribute("data-collapsed")).toBe("false");
    expect(screen.getByTestId("chat-messages")).toBeDefined();

    // 다시 접기 → 목록 사라짐.
    fireEvent.click(screen.getByTestId("chat-toggle-collapse"));
    expect(panel.getAttribute("data-collapsed")).toBe("true");
    expect(screen.queryByTestId("chat-messages")).toBeNull();
  });
});

// ── TEXT_MESSAGE 렌더 ─────────────────────────────────────────────────────────

describe("ChatPanel — TEXT_MESSAGE 렌더(Discord류)", () => {
  it("TEXT_MESSAGE만 메시지 행으로 렌더한다(다른 이벤트 무시)", () => {
    const events: OfficeEvent[] = [
      runStarted(),
      textMsg("assistant", "첫 메시지"),
      textMsg("user", "내 답장"),
    ];
    renderExpanded(events);

    const rows = screen.getAllByTestId("chat-message");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("첫 메시지")).toBeDefined();
    expect(screen.getByText("내 답장")).toBeDefined();
  });

  it("role별 data-role이 행에 부여된다(정렬 분기 근거)", () => {
    renderExpanded([textMsg("user", "나")]);
    const row = screen.getByTestId("chat-message");
    expect(row.getAttribute("data-role")).toBe("user");
  });

  it("메시지 없으면 빈 안내를 표시한다", () => {
    renderExpanded([runStarted()]);
    expect(screen.queryByTestId("chat-message")).toBeNull();
    expect(screen.getByText(/아직 메시지가 없습니다/)).toBeDefined();
  });
});

// ── 발화자 역할 초상 (assistant — 16 역할) ──────────────────────────────────────

describe("ChatPanel — assistant 역할 초상 표시", () => {
  it("speakerLabel: assistant는 역할명·user/system은 기존 라벨", () => {
    // assistant는 16 역할명(node_id 없으면 role 'assistant' 해시 → 결정론 역할).
    const role = pickRoleKey("assistant");
    expect(speakerLabel(textMsg("assistant", "안녕"))).toBe(ROLE_KEY_NAMES[role]);
    expect(speakerLabel(textMsg("user", "나"))).toBe("나");
    expect(speakerLabel(textMsg("system", "공지"))).toBe("시스템");
  });

  it("assistant 메시지는 초상 <img>를 렌더(이니셜 대신)·data-role-key 부여", () => {
    renderExpanded([textMsg("assistant", "처리 완료했습니다")]);
    const img = screen.getByTestId("chat-portrait") as HTMLImageElement;
    expect(img).toBeDefined();
    // src는 /assets/portraits/{role}_{emotion}.png 규약.
    const role = pickRoleKey("assistant");
    expect(img.getAttribute("data-role-key")).toBe(role);
    expect(img.src).toContain(`/assets/portraits/${role}_`);
    // '완료' → happy 감정 분류 반영.
    expect(img.src).toContain(`${role}_happy.png`);
  });

  it("user/system 메시지는 초상 없이 이니셜 아바타 유지", () => {
    renderExpanded([textMsg("user", "내 말"), textMsg("system", "시스템 공지")]);
    // 초상 img는 없어야(generic 동작 보존).
    expect(screen.queryByTestId("chat-portrait")).toBeNull();
    // 두 행 모두 렌더(user·system)·기존 이니셜 아바타 경로 유지.
    const rows = screen.getAllByTestId("chat-message");
    expect(rows.map((r) => r.getAttribute("data-role"))).toEqual(["user", "system"]);
    // 본문 내용은 그대로 표시.
    expect(screen.getByText("내 말")).toBeDefined();
    expect(screen.getByText("시스템 공지")).toBeDefined();
  });

  it("초상 onError는 role_neutral → PORTRAIT_FALLBACK으로 단계적 폴백(루프 차단)", () => {
    renderExpanded([textMsg("assistant", "분석 중")]);
    const img = screen.getByTestId("chat-portrait") as HTMLImageElement;
    const role = pickRoleKey("assistant");

    // 1차(role_thinking 등) 실패 → role_neutral.
    fireEvent.error(img);
    expect(img.src).toContain(`${role}_neutral.png`);

    // role_neutral 실패 → PORTRAIT_FALLBACK(/work/1.png).
    fireEvent.error(img);
    expect(img.src).toContain("/work/1.png");

    // 최종 폴백도 실패 → onerror 제거(무한 루프 차단).
    fireEvent.error(img);
    expect(img.onerror).toBeNull();
  });
});
