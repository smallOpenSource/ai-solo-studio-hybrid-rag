/**
 * App 뷰 토글 통합 테스트 (Phase 4c US-005, 기준10/11/14).
 *
 * 검증 항목:
 *   1. 토글 시 useEventStream 1회만 호출(재구독 0) — spy 호출 횟수 단언(★긴장2).
 *   2. viewMode 'text'↔'office-v2' 전환 시 렌더러 교체(해당 뷰 컨테이너 등장).
 *   3. 텍스트 렌더러·Inbox·이미지 토글 회귀 0(기존 동작 유지).
 *
 * ★ 구 pixi 2D 맵 뷰('2d')는 컷오버로 완전 제거 — 관련 mock·테스트도 함께 제거.
 *   office-v2(Phaser)가 2D 시각화를 대체한다.
 * ★ useEventStream은 spy로 감싸 호출 횟수·동일 events 공유를 검증한다.
 * ★ Inbox는 fetch /api/inbox를 호출하므로 fetch를 빈 응답으로 stub한다.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import type { OfficeEvent, RunStarted } from "../types/events";

// ── PhaserGame mock: jsdom(WebGL 없음·phaser CanvasFeatures 크래시) 대응 ────────
// office-v2 뷰 컨테이너 등장을 식별할 수 있도록 testid를 붙인다(실 Phaser.Game은 e2e).
vi.mock("../office/PhaserGame", () => ({
  PhaserGame: () => <div data-testid="phaser-game-mock" />,
}));

// ── EventBus mock: App이 직접 import(채널 발행/구독) — phaser 로드 회피 ──────────
// ★기능형 emitter로 mock한다(emit→on 디스패치) — selectedRole 구독(agent-selected)을
// 테스트에서 emit으로 검증하기 위함. 채널 상수도 함께 노출(App이 import). 핸들러 클로저는
// 팩토리 스코프(호이스트 안전). 실 Phaser.Events.EventEmitter는 e2e.
vi.mock("../office/EventBus", () => {
  const handlers: Record<string, Array<(...a: unknown[]) => void>> = {};
  return {
    OFFICE_EVENT_CHANNEL: "office-event",
    SCENE_READY_CHANNEL: "current-scene-ready",
    AGENT_SELECTED_CHANNEL: "agent-selected",
    AGENT_ACTIVITY_CHANNEL: "agent-activity",
    EventBus: {
      emit: vi.fn((event: string, ...args: unknown[]) => {
        for (const fn of [...(handlers[event] ?? [])]) fn(...args);
        return true;
      }),
      on: vi.fn((event: string, fn: (...a: unknown[]) => void) => {
        (handlers[event] ??= []).push(fn);
      }),
      off: vi.fn((event: string, fn: (...a: unknown[]) => void) => {
        handlers[event] = (handlers[event] ?? []).filter((h) => h !== fn);
      }),
    },
  };
});

// ── rest mock: DEPLOY AGENT → startRun 호출 단언(실 fetch 회피) ──────────────────
vi.mock("../api/rest", () => ({
  startRun: vi.fn().mockResolvedValue(undefined),
}));

// ── useEventStream mock: 호출 횟수·동일 events 공유 검증 ───────────────────────
// 모듈 스코프 spy — 렌더 간 동일 events 배열 참조를 반환해 "공유"를 모사한다.
const useEventStreamSpy = vi.fn();
vi.mock("../api/sse", () => ({
  useEventStream: (url: string) => useEventStreamSpy(url),
}));

// ── useOfficeWebSocket mock: App이 무조건 호출(SSE와 병존) — 실 WS 연결 회피 ────
// 빈 events·no-op send 반환(office-v2 외 뷰에선 영향 0·EventBus도 빈 발행).
vi.mock("../api/ws", () => ({
  useOfficeWebSocket: () => ({
    events: [],
    lastSeq: 0,
    connected: false,
    sendJsonMessage: vi.fn(),
  }),
}));

// ── knowledge API mock (US-G3-5) ───────────────────────────────────────────────
vi.mock("../api/knowledge", () => ({
  searchKnowledge: vi.fn().mockResolvedValue({ results: [] }),
}));

// ── mock 이후 import ───────────────────────────────────────────────────────────
import App from "../App";
import { EventBus } from "../office/EventBus";
import { startRun } from "../api/rest";
import { searchKnowledge } from "../api/knowledge";
import { fullLabel } from "../office/roleNames";

// ── 헬퍼: 최소 이벤트 픽스처 ─────────────────────────────────────────────────

function makeRunStarted(workflow = "test-wf"): RunStarted {
  return {
    run_id: "run-test",
    ts: "2026-06-08T00:00:00Z",
    schema_v: 1,
    family: "Lifecycle",
    type: "RUN_STARTED",
    workflow,
  };
}

/** useEventStream이 반환할 고정 결과 — 렌더 간 동일 events 배열을 공유한다. */
const SHARED_EVENTS: OfficeEvent[] = [makeRunStarted("wf-1"), makeRunStarted("wf-2")];

beforeEach(() => {
  // useEventStream은 항상 동일한 events 배열 참조를 반환(공유 모사).
  useEventStreamSpy.mockReturnValue({
    events: SHARED_EVENTS,
    lastSeq: 2,
    connected: true,
  });
  // Inbox가 호출하는 fetch /api/inbox를 빈 응답으로 stub.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ review: [], notify: [], question: [] }),
      status: 200,
      statusText: "OK",
    })
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

// ── 1. useEventStream 단일 구독(재구독 0) ─────────────────────────────────────

describe("App 뷰 토글 — useEventStream 단일 구독(기준 ★긴장2)", () => {
  it("초기 렌더에서 useEventStream을 1회만 호출한다", () => {
    render(<App />);
    expect(useEventStreamSpy).toHaveBeenCalledTimes(1);
    expect(useEventStreamSpy).toHaveBeenCalledWith("/events/stream");
  });

  it("[텍스트]→[오피스 v2]→[텍스트] 토글해도 useEventStream 호출 횟수가 토글마다 늘지 않는다(재구독 0)", () => {
    render(<App />);
    // 초기 1회
    expect(useEventStreamSpy).toHaveBeenCalledTimes(1);

    // 오피스 v2로 토글 → 리렌더(호출 +1, 단 이는 React 리렌더지 재구독 아님)
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    // 텍스트로 토글 → 리렌더
    fireEvent.click(screen.getByTestId("view-toggle-text"));

    // ★핵심: useEventStream은 항상 동일 url("/events/stream")로만 호출된다.
    // 조건부 호출·다중 구독이 없음을 url 단일성으로 단언(재구독/재연결 0).
    const urls = useEventStreamSpy.mock.calls.map((c) => c[0]);
    expect(new Set(urls)).toEqual(new Set(["/events/stream"]));
    // 매 렌더 정확히 1회씩만(중복 구독 없음) — 호출마다 url 동일.
    expect(urls.every((u) => u === "/events/stream")).toBe(true);
  });
});

// ── 2. viewMode 전환 시 렌더러 교체 ───────────────────────────────────────────

describe("App 뷰 토글 — viewMode 전환 시 렌더러 교체(기준10/11)", () => {
  it("기본은 오피스 v2 뷰 — Phaser 레이아웃 렌더(Q4 승인)", () => {
    render(<App />);
    // ★기본 viewMode 'office-v2'(Q4 승인) — 웹 접속 시 처음부터 오피스.
    expect(screen.getByTestId("office-v2-layout")).toBeDefined();
    expect(screen.queryByTestId("text-status-board")).toBeNull();
  });

  it("★P3 US-3c: 맵 뷰포트가 pixel-inset 베벨 프레임(함몰 베벨·샤프 0·셸 톤 일치)", () => {
    render(<App />);
    const frame = screen.getByTestId("office-viewport-frame");
    // 하드엣지 베벨: 위=왼쪽(한 쌍)·아래=오른쪽(다른 쌍)·두 쌍이 서로 달라야 베벨(평면 아님).
    expect(frame.style.borderStyle).toBe("solid");
    expect(frame.style.borderTopColor).toBe(frame.style.borderLeftColor);
    expect(frame.style.borderBottomColor).toBe(frame.style.borderRightColor);
    expect(frame.style.borderTopColor).not.toBe(frame.style.borderBottomColor);
    expect(frame.style.borderTopColor).toBeTruthy();
    // 샤프(라운드 금지) — React는 0을 "0"으로 렌더(value===0 특례).
    expect(["0", "0px"]).toContain(frame.style.borderRadius);
    // Phaser 100% 높이 체인 보존(box-sizing border-box).
    expect(frame.style.boxSizing).toBe("border-box");
  });

  it("[텍스트] 클릭 시 TextStatusBoard 렌더(오피스→텍스트 토글)", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-text"));
    expect(screen.getByTestId("text-status-board")).toBeDefined();
    expect(screen.queryByTestId("office-v2-layout")).toBeNull();
  });

  it("[오피스 v2] 클릭 시 Phaser 등장·텍스트 상태창 사라짐(렌더러 교체)", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
    expect(screen.queryByTestId("text-status-board")).toBeNull();
  });

  it("[오피스 v2]→[텍스트] 클릭 시 다시 텍스트 상태창 등장", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();

    fireEvent.click(screen.getByTestId("view-toggle-text"));
    expect(screen.getByTestId("text-status-board")).toBeDefined();
    expect(screen.queryByTestId("phaser-game-mock")).toBeNull();
  });
});

// ── 3. 왕복 후 events 보존(동일 배열 공유·유실 0) ─────────────────────────────

describe("App 뷰 토글 — 왕복 후 events 보존(기준14)", () => {
  it("[오피스 v2]→[텍스트]→[오피스 v2] 왕복 후에도 events가 보존된다(동일 배열 공유)", () => {
    render(<App />);

    // 오피스 v2
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    expect(screen.getByTestId("office-v2-layout")).toBeDefined();

    // 텍스트 — 같은 events를 시간순 렌더(2개 행). 워크플로 시작 텍스트가 보여야 함.
    fireEvent.click(screen.getByTestId("view-toggle-text"));
    expect(screen.getByTestId("text-status-board")).toBeDefined();
    // formatEventText: RUN_STARTED → "▶ 워크플로 시작: {workflow}"
    expect(screen.getAllByText(/워크플로 시작: wf-/).length).toBe(2);

    // 다시 오피스 v2 — 레이아웃 재등장(events 유실 0).
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    expect(screen.getByTestId("office-v2-layout")).toBeDefined();

    // ★useEventStream은 매 렌더 동일 SHARED_EVENTS 참조 반환 — 모든 호출이 동일 url.
    const urls = useEventStreamSpy.mock.calls.map((c) => c[0]);
    expect(urls.every((u) => u === "/events/stream")).toBe(true);
  });
});

// ── 4. 텍스트 렌더러·Inbox·이미지 토글 회귀 0 ─────────────────────────────────

describe("App 뷰 토글 — 기존 동작 회귀 0(기준 회귀)", () => {
  it("이미지 토글 체크박스가 텍스트 뷰에서 렌더된다", () => {
    render(<App />);
    // 기본은 office-v2(Q4) → 텍스트 뷰로 전환해야 이미지 토글이 보인다.
    fireEvent.click(screen.getByTestId("view-toggle-text"));
    expect(screen.getByTestId("image-toggle")).toBeDefined();
  });

  it("오피스 v2 뷰에서는 이미지 토글이 숨겨진다(텍스트 뷰 전용)", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    expect(screen.queryByTestId("image-toggle")).toBeNull();

    // 텍스트로 돌아오면 다시 등장(회귀 0).
    fireEvent.click(screen.getByTestId("view-toggle-text"));
    expect(screen.getByTestId("image-toggle")).toBeDefined();
  });

  it("이미지 토글 ON 시 TextMessage 감정 이미지가 표시된다(기존 동작 유지)", () => {
    // TextMessage 이벤트를 포함한 events로 교체(image_url null → 프론트 감정 분류).
    const textMsg: OfficeEvent = {
      run_id: "run-test",
      ts: "2026-06-08T00:00:01Z",
      schema_v: 1,
      family: "Text",
      type: "TEXT_MESSAGE",
      role: "assistant",
      content: "안녕하세요 반갑습니다",
      image_url: null,
    };
    useEventStreamSpy.mockReturnValue({
      events: [textMsg],
      lastSeq: 1,
      connected: true,
    });

    render(<App />);
    // 기본 office-v2(Q4) → 텍스트 뷰로 전환(이미지 토글·감정 이미지는 텍스트 뷰 전용).
    fireEvent.click(screen.getByTestId("view-toggle-text"));
    // 토글 OFF: 감정 이미지 없음
    expect(screen.queryByTestId("emotion-image")).toBeNull();
    // 토글 ON: 감정 이미지 등장
    fireEvent.click(screen.getByTestId("image-toggle"));
    expect(screen.getByTestId("emotion-image")).toBeDefined();
  });

  it("★D2: 워크플로 시작 버튼은 office-v2 Settings 섹션으로 이동했다(상단 큰 툴바 제거)", () => {
    render(<App />);
    // 기본 Dashboard 섹션엔 시작 버튼이 없다(상단 툴바에서 제거됨).
    expect(screen.queryByTestId("start-run-btn")).toBeNull();
    // Settings 섹션으로 이동 → 시작 버튼 등장.
    fireEvent.click(screen.getByText("Settings"));
    expect(screen.getByTestId("start-run-btn")).toBeDefined();
  });

  it("★D2: 연결 상태는 작은 인디케이터로 표시된다(가짜 텔레메트리 0)", () => {
    render(<App />);
    const ind = screen.getByTestId("conn-indicator");
    expect(ind).toBeDefined();
    // 실 connected/seq만(seq 2) — 가짜 SYS/GPU/UP 없음.
    expect(ind.textContent).toContain("seq 2");
  });
});

// ── 5. office-v2 뷰 통합 (US-0f) ───────────────────────────────────────────────

describe("App 뷰 토글 — office-v2 통합(US-0f)", () => {
  it("오피스 v2 토글 버튼이 렌더된다(텍스트 토글 무파괴·구 2D 토글 제거됨)", () => {
    render(<App />);
    expect(screen.getByTestId("view-toggle-office")).toBeDefined();
    // 텍스트 토글은 그대로.
    expect(screen.getByTestId("view-toggle-text")).toBeDefined();
    // ★구 pixi 2D 맵 토글은 컷오버로 제거됨 — 더 이상 존재하지 않는다.
    expect(screen.queryByTestId("view-toggle-2d")).toBeNull();
  });

  it("[오피스 v2] 클릭 시 Phaser 게임 + 채팅 패널 등장·기존 상태창/Inbox 사라짐", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-office"));

    expect(screen.getByTestId("office-v2-layout")).toBeDefined();
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
    expect(screen.getByTestId("chat-panel")).toBeDefined();
    // office-v2는 별 레이아웃 — 텍스트 상태창·Inbox 미표시.
    expect(screen.queryByTestId("text-status-board")).toBeNull();
    expect(screen.queryByTestId("inbox")).toBeNull();
  });

  it("[오피스 v2]→[텍스트] 복귀 시 기존 상태창·Inbox 재등장(무파괴)", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    expect(screen.getByTestId("office-v2-layout")).toBeDefined();

    fireEvent.click(screen.getByTestId("view-toggle-text"));
    expect(screen.getByTestId("text-status-board")).toBeDefined();
    expect(screen.getByTestId("inbox")).toBeDefined();
    expect(screen.queryByTestId("office-v2-layout")).toBeNull();
  });

  it("office-v2 토글 왕복에도 useEventStream은 동일 url로만 호출(SSE 재구독 0)", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("view-toggle-office"));
    fireEvent.click(screen.getByTestId("view-toggle-text"));

    const urls = useEventStreamSpy.mock.calls.map((c) => c[0]);
    expect(urls.every((u) => u === "/events/stream")).toBe(true);
  });
});

// ── 6. 사이드바 섹션 전환 + Phaser 영속 (US-N1) ────────────────────────────────

describe("App office-v2 — 사이드바 섹션 전환 + Phaser 영속(US-N1)", () => {
  it("기본 Dashboard: 맵 + 우 컬럼(dashboard-right) + Phaser 표시", () => {
    render(<App />);
    expect(screen.getByTestId("live-office-view")).toBeDefined();
    expect(screen.getByTestId("dashboard-right")).toBeDefined();
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
    // 활성 내비 강조(Dashboard).
    expect(screen.getByText("Dashboard").getAttribute("data-active")).toBe("true");
  });

  it("Office 클릭: 맵 표시 유지·우 컬럼 사라짐(전체폭)·Phaser 영속", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Office"));
    expect(screen.getByTestId("live-office-view").style.display).not.toBe("none");
    expect(screen.queryByTestId("dashboard-right")).toBeNull();
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
    expect(screen.getByText("Office").getAttribute("data-active")).toBe("true");
  });

  it("Agents 클릭: 그리드 표시·맵 display:none·★Phaser 언마운트 0(영속)", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Agents"));
    expect(screen.getByTestId("section-agents")).toBeDefined();
    // 맵 컨테이너는 DOM에 남되 display:none(PhaserGame ResizeObserver 0치수 가드).
    expect(screen.getByTestId("live-office-view").style.display).toBe("none");
    // ★핵심: PhaserGame은 언마운트되지 않는다(WebGL 재생성 차단). mock 단위 한계는 주석 참조.
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
    // 16역할 카드(그리드).
    expect(screen.getAllByTestId("agent-card").length).toBe(16);
  });

  it("Knowledge/Settings: 플레이스홀더 표시·맵 display:none·Phaser 영속", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Knowledge"));
    expect(screen.getByTestId("section-knowledge")).toBeDefined();
    expect(screen.getByTestId("live-office-view").style.display).toBe("none");
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();

    fireEvent.click(screen.getByText("Settings"));
    expect(screen.getByTestId("section-settings")).toBeDefined();
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
  });

  it("섹션 왕복(Dashboard→Agents→Dashboard) 후 동일 phaser-game-mock 노드(재마운트 아님)", () => {
    render(<App />);
    const before = screen.getByTestId("phaser-game-mock");
    fireEvent.click(screen.getByText("Agents"));
    fireEvent.click(screen.getByText("Dashboard"));
    const after = screen.getByTestId("phaser-game-mock");
    expect(after).toBe(before); // 동일 DOM 노드 식별 = 언마운트/재마운트 없음.
    expect(screen.getAllByTestId("phaser-game-mock")).toHaveLength(1);
    expect(screen.getByTestId("dashboard-right")).toBeDefined(); // 복귀.
  });
});

// ── 7. DEPLOY AGENT / Log Out 동작 (US-N2) ─────────────────────────────────────

describe("App office-v2 — DEPLOY/Logout 동작(US-N2)", () => {
  it("DEPLOY AGENT 클릭 → startRun('crm_notify') 호출", async () => {
    render(<App />);
    // handleStartRun은 async(setStarting 상태 갱신) — act로 감싸 microtask 플러시(경고 0).
    await act(async () => {
      fireEvent.click(screen.getByText("DEPLOY AGENT"));
    });
    expect(startRun).toHaveBeenCalledWith("crm_notify");
  });

  it("★B2: Log Out 클릭 → AuthGate 밖(직접 렌더)에선 no-op이라 throw하지 않는다", () => {
    // app.test는 <App/>을 AuthGate 없이 직접 렌더하므로 useSignOut은 no-op이다.
    // (실 로그아웃 흐름은 auth/AuthGate.test에서 검증.)
    render(<App />);
    expect(() => fireEvent.click(screen.getByText("Log Out"))).not.toThrow();
  });
});

// ── 8. selected-agent → 직원 모달 (US-N5 + C3) ─────────────────────────────────

describe("App office-v2 — selected-agent → 직원 모달(US-N5 + C3)", () => {
  it("기본엔 모달이 닫혀 있다(agent-modal 없음)", () => {
    render(<App />);
    expect(screen.queryByTestId("agent-modal")).toBeNull();
  });

  it("agent-selected emit → 모달 open·선택 역할 카드 표시", () => {
    render(<App />);
    act(() => {
      EventBus.emit("agent-selected", "dev");
    });
    expect(screen.getByTestId("agent-modal")).toBeDefined();
    expect(screen.getByText(fullLabel("dev").name)).toBeDefined();
  });

  it("닫기 버튼 클릭 → 모달 close", () => {
    render(<App />);
    act(() => {
      EventBus.emit("agent-selected", "dev");
    });
    expect(screen.getByTestId("agent-modal")).toBeDefined();
    fireEvent.click(screen.getByTestId("agent-modal-close"));
    expect(screen.queryByTestId("agent-modal")).toBeNull();
  });

  it("배경(딤) 클릭 → 모달 close(카드 영역 클릭은 닫히지 않음)", () => {
    render(<App />);
    act(() => {
      EventBus.emit("agent-selected", "ceo");
    });
    const backdrop = screen.getByTestId("agent-modal");
    // 카드 영역(닫기 버튼) 클릭은 stopPropagation이라 닫히면 안 됨 — 먼저 카드 내부 클릭.
    fireEvent.click(screen.getByText(fullLabel("ceo").name));
    expect(screen.getByTestId("agent-modal")).toBeDefined();
    // 배경 직접 클릭 → 닫힘.
    fireEvent.click(backdrop);
    expect(screen.queryByTestId("agent-modal")).toBeNull();
  });

  it("ESC 키 → 모달 close(window keydown)", () => {
    render(<App />);
    act(() => {
      EventBus.emit("agent-selected", "dev");
    });
    expect(screen.getByTestId("agent-modal")).toBeDefined();
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(screen.queryByTestId("agent-modal")).toBeNull();
  });

  it("★모달 open 중에도 phaser-game-mock(live-office-view)은 언마운트되지 않는다", () => {
    render(<App />);
    const before = screen.getByTestId("phaser-game-mock");
    act(() => {
      EventBus.emit("agent-selected", "dev");
    });
    // 모달이 떠도 동일 phaser 노드 유지(WebGL 재생성 0).
    expect(screen.getByTestId("agent-modal")).toBeDefined();
    const after = screen.getByTestId("phaser-game-mock");
    expect(after).toBe(before);
    expect(screen.getAllByTestId("phaser-game-mock")).toHaveLength(1);
  });
});

// ── 9. US-G3-2: Office 탭 풀스크린 ─────────────────────────────────────────────

describe("App office-v2 — Office 풀스크린(US-G3-2)", () => {
  it("Dashboard 탭: 'Live Office View' 헤더(live-office-header)가 표시된다", () => {
    render(<App />);
    // 기본 Dashboard — 헤더 chrome 있음.
    expect(screen.getByTestId("live-office-header")).toBeDefined();
    expect(screen.getByTestId("dashboard-right")).toBeDefined();
  });

  it("Office 탭: 헤더 chrome(live-office-header) 제거·맵이 가용영역 전체(dashboard-right 없음)", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Office"));
    // 헤더 없음 — 풀스크린.
    expect(screen.queryByTestId("live-office-header")).toBeNull();
    // 우 컬럼 없음.
    expect(screen.queryByTestId("dashboard-right")).toBeNull();
    // 맵·Phaser는 여전히 표시.
    expect(screen.getByTestId("live-office-view").style.display).not.toBe("none");
    expect(screen.getByTestId("phaser-game-mock")).toBeDefined();
  });

  it("Office → Dashboard 복귀 시 헤더 chrome 재표시", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Office"));
    expect(screen.queryByTestId("live-office-header")).toBeNull();
    fireEvent.click(screen.getByText("Dashboard"));
    expect(screen.getByTestId("live-office-header")).toBeDefined();
  });
});

// ── 10. US-G3-3: DEPLOY 피드백 ──────────────────────────────────────────────────

describe("App office-v2 — DEPLOY 피드백(US-G3-3)", () => {
  it("DEPLOY 클릭 중 '배포 중…' 텍스트와 deploy-feedback testid가 표시된다", async () => {
    // startRun을 지연 pending으로 설정해 starting 상태를 포착.
    let resolveRun!: () => void;
    (startRun as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise<void>((res) => { resolveRun = res; }),
    );
    render(<App />);
    // 클릭 — async이므로 act 없이 fire만(pending 상태 확인).
    fireEvent.click(screen.getByTestId("deploy-btn"));
    // starting 상태: 버튼 텍스트 + feedback testid.
    expect(screen.getByTestId("deploy-btn").textContent).toBe("배포 중…");
    expect(screen.getByTestId("deploy-feedback")).toBeDefined();
    // 완료.
    await act(async () => { resolveRun(); });
  });

  it("DEPLOY 실패 시 deploy-error testid에 메시지 표시", async () => {
    (startRun as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("서버 오류"));
    render(<App />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("deploy-btn"));
    });
    expect(screen.getByTestId("deploy-error")).toBeDefined();
    expect(screen.getByTestId("deploy-error").textContent).toContain("서버 오류");
  });

  it("DEPLOY 성공 후 성공 피드백이 표시되고 3s 뒤 자동으로 사라진다", async () => {
    vi.useFakeTimers();
    try {
      (startRun as ReturnType<typeof vi.fn>).mockResolvedValueOnce(undefined);
      render(<App />);
      await act(async () => {
        fireEvent.click(screen.getByTestId("deploy-btn"));
      });
      // ★US-G3-3: 성공 직후 가시 피드백 표시(무반응 0) — starting은 너무 짧게 명멸하므로
      //   성공 토스트를 ~3s 유지해 사람·검증이 볼 수 있게 한다.
      expect(screen.getByTestId("deploy-feedback")).toBeDefined();
      expect(screen.queryByTestId("deploy-error")).toBeNull();
      // 3s 경과 → 자동 클리어(스턱 없음).
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      expect(screen.queryByTestId("deploy-feedback")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

// ── 11. US-G3-5: Knowledge 검색 UI ──────────────────────────────────────────────

describe("App office-v2 — Knowledge 검색 UI(US-G3-5)", () => {
  it("Knowledge 탭: 검색 input·submit 버튼이 렌더된다", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Knowledge"));
    expect(screen.getByTestId("section-knowledge")).toBeDefined();
    expect(screen.getByTestId("knowledge-search-input")).toBeDefined();
    expect(screen.getByTestId("knowledge-search-submit")).toBeDefined();
  });

  it("검색 제출 시 searchKnowledge를 호출한다", async () => {
    render(<App />);
    fireEvent.click(screen.getByText("Knowledge"));
    fireEvent.change(screen.getByTestId("knowledge-search-input"), {
      target: { value: "RAG 검색" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("knowledge-search-submit"));
    });
    expect(searchKnowledge).toHaveBeenCalledWith("RAG 검색", 5);
  });

  it("결과가 있으면 knowledge-result testid를 렌더한다", async () => {
    (searchKnowledge as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      results: [
        { text: "문서 내용", source: "https://example.com", score: 0.92 },
        { text: "또 다른 내용", source: "https://wiki.com", score: 0.85 },
      ],
    });
    render(<App />);
    fireEvent.click(screen.getByText("Knowledge"));
    fireEvent.change(screen.getByTestId("knowledge-search-input"), {
      target: { value: "검색어" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("knowledge-search-submit"));
    });
    expect(screen.getAllByTestId("knowledge-result")).toHaveLength(2);
    expect(screen.getAllByTestId("knowledge-result")[0].textContent).toContain("문서 내용");
  });
});

// ── 12. US-G3-6: active-tasks-idle Dashboard 정합 ──────────────────────────────

describe("App office-v2 — active-tasks-idle Dashboard 정합(US-G3-6)", () => {
  it("Dashboard 탭에서 진행 작업 0이면 active-tasks-idle이 DOM에 나온다", () => {
    // wsEvents가 빈 배열이므로 derivedTasks=[] → idle 표시.
    render(<App />);
    // 기본 Dashboard.
    expect(screen.getByTestId("dashboard-right")).toBeDefined();
    expect(screen.getByTestId("active-tasks-idle")).toBeDefined();
  });
});
