/**
 * WS 훅 단위 테스트 (office-v2 US-0c·Vitest).
 *
 * 검증 항목:
 *   - parseOfficeFrame: 프레임 `.event`→isOfficeEvent 통과/탈락·seq 추출.
 *   - reconnectBackoffMs: exp backoff(상한 10s) 폭주 가드.
 *   - useOfficeWebSocket: hello 페이로드(last_seq 커서)·프레임 누적·lastSeq 전진.
 *
 * react-use-websocket은 mock한다(실제 WS 연결 없이 onOpen/lastJsonMessage 주입).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// ── react-use-websocket mock ──────────────────────────────────────────────────
// useWebSocket을 제어 가능한 stub으로 대체: 등록된 options를 캡처해 onOpen을
// 수동 트리거하고, lastJsonMessage를 외부에서 주입한다.

interface CapturedWsOptions {
  onOpen?: () => void;
  shouldReconnect?: () => boolean;
  reconnectAttempts?: number;
  reconnectInterval?: (attempt: number) => number;
  heartbeat?: boolean;
}

const wsState: {
  url: string | null;
  options: CapturedWsOptions | null;
  sendJsonMessage: ReturnType<typeof vi.fn>;
  lastJsonMessage: unknown;
  readyState: number;
} = {
  url: null,
  options: null,
  sendJsonMessage: vi.fn(),
  lastJsonMessage: null,
  readyState: 1, // ReadyState.OPEN
};

vi.mock("react-use-websocket", () => ({
  __esModule: true,
  // ReadyState enum 미러(OPEN=1).
  ReadyState: { UNINSTANTIATED: -1, CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 },
  default: (url: string, options: CapturedWsOptions) => {
    wsState.url = url;
    wsState.options = options;
    return {
      sendJsonMessage: wsState.sendJsonMessage,
      lastJsonMessage: wsState.lastJsonMessage,
      readyState: wsState.readyState,
    };
  },
}));

// ── mock 이후 import ───────────────────────────────────────────────────────────
import {
  parseOfficeFrame,
  reconnectBackoffMs,
  useOfficeWebSocket,
} from "../api/ws";

const BASE = { run_id: "r1", ts: "2026-06-13T00:00:00Z", schema_v: 1 };

function runStartedEvent() {
  return { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" };
}

beforeEach(() => {
  wsState.url = null;
  wsState.options = null;
  wsState.sendJsonMessage = vi.fn();
  wsState.lastJsonMessage = null;
  wsState.readyState = 1;
});

// ── parseOfficeFrame ───────────────────────────────────────────────────────────

describe("parseOfficeFrame — 프레임 파싱(.event→isOfficeEvent)", () => {
  it("유효 프레임은 {seq,event}를 돌려준다", () => {
    const frame = parseOfficeFrame({ seq: 7, event: runStartedEvent() });
    expect(frame).not.toBeNull();
    expect(frame!.seq).toBe(7);
    expect(frame!.event.type).toBe("RUN_STARTED");
  });

  it(".event가 미지 타입이면 null(탈락)", () => {
    const frame = parseOfficeFrame({
      seq: 3,
      event: { ...BASE, family: "X", type: "UNKNOWN_FUTURE" },
    });
    expect(frame).toBeNull();
  });

  it(".event가 없으면 null", () => {
    expect(parseOfficeFrame({ seq: 1 })).toBeNull();
  });

  it("null/비객체는 null", () => {
    expect(parseOfficeFrame(null)).toBeNull();
    expect(parseOfficeFrame("nope")).toBeNull();
    expect(parseOfficeFrame(42)).toBeNull();
  });

  it("seq가 비유한/음수면 0으로 폴백(event는 유효)", () => {
    const frame = parseOfficeFrame({ seq: -5, event: runStartedEvent() });
    expect(frame).not.toBeNull();
    expect(frame!.seq).toBe(0);

    const frame2 = parseOfficeFrame({ event: runStartedEvent() }); // seq 없음.
    expect(frame2).not.toBeNull();
    expect(frame2!.seq).toBe(0);
  });
});

// ── reconnectBackoffMs ─────────────────────────────────────────────────────────

describe("reconnectBackoffMs — exp backoff 폭주 가드", () => {
  it("시도가 늘수록 지수적으로 커진다(1s·2s·4s…)", () => {
    expect(reconnectBackoffMs(0)).toBe(1000);
    expect(reconnectBackoffMs(1)).toBe(2000);
    expect(reconnectBackoffMs(2)).toBe(4000);
    expect(reconnectBackoffMs(3)).toBe(8000);
  });

  it("10s 상한을 넘지 않는다(폭주 차단)", () => {
    expect(reconnectBackoffMs(4)).toBe(10_000);
    expect(reconnectBackoffMs(20)).toBe(10_000);
  });
});

// ── useOfficeWebSocket ─────────────────────────────────────────────────────────

describe("useOfficeWebSocket — hello 페이로드·프레임 누적·seq 커서", () => {
  it("onOpen 시 hello({type,last_seq})를 송신한다(초기 커서 0)", () => {
    renderHook(() => useOfficeWebSocket("/ws"));
    expect(wsState.url).toBe("/ws");

    // onOpen 수동 트리거(실제 WS open 시뮬레이션).
    act(() => {
      wsState.options!.onOpen?.();
    });

    expect(wsState.sendJsonMessage).toHaveBeenCalledWith({ type: "hello", last_seq: 0 });
  });

  it("auto-reconnect 옵션이 설정된다(shouldReconnect→true·상한20·backoff·heartbeat)", () => {
    renderHook(() => useOfficeWebSocket("/ws"));
    const opts = wsState.options!;
    expect(opts.shouldReconnect?.()).toBe(true);
    expect(opts.reconnectAttempts).toBe(20);
    expect(opts.reconnectInterval).toBe(reconnectBackoffMs);
    expect(opts.heartbeat).toBe(true);
  });

  it("connected는 readyState===OPEN(1)일 때 true", () => {
    wsState.readyState = 1;
    const { result } = renderHook(() => useOfficeWebSocket("/ws"));
    expect(result.current.connected).toBe(true);
  });

  it("connected는 readyState!==OPEN일 때 false", () => {
    wsState.readyState = 0; // CONNECTING
    const { result } = renderHook(() => useOfficeWebSocket("/ws"));
    expect(result.current.connected).toBe(false);
  });

  it("유효 프레임 수신 시 events에 누적·lastSeq가 프레임 seq로 전진", () => {
    wsState.lastJsonMessage = { seq: 100, event: runStartedEvent() };
    const { result } = renderHook(() => useOfficeWebSocket("/ws"));

    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe("RUN_STARTED");
    // lastSeq는 events.length(1)가 아닌 실제 프레임 seq(100).
    expect(result.current.lastSeq).toBe(100);
  });

  it("미지 .event 프레임은 무시(events 누적 0·lastSeq 0)", () => {
    wsState.lastJsonMessage = {
      seq: 5,
      event: { ...BASE, family: "X", type: "UNKNOWN_FUTURE" },
    };
    const { result } = renderHook(() => useOfficeWebSocket("/ws"));

    expect(result.current.events).toHaveLength(0);
    expect(result.current.lastSeq).toBe(0);
  });

  it("재연결 onOpen은 마지막 seq를 hello 커서로 보낸다(stale 0)", () => {
    // 1) seq=42 프레임 수신 → lastSeq 42.
    wsState.lastJsonMessage = { seq: 42, event: runStartedEvent() };
    const { result } = renderHook(() => useOfficeWebSocket("/ws"));
    expect(result.current.lastSeq).toBe(42);

    // 2) 재연결 onOpen → hello.last_seq가 42여야 한다(ref로 최신 추적).
    act(() => {
      wsState.options!.onOpen?.();
    });
    expect(wsState.sendJsonMessage).toHaveBeenCalledWith({ type: "hello", last_seq: 42 });
  });

  it("sendJsonMessage를 반환해 외부 송신 가능(채팅 입력 등 후속 US)", () => {
    const { result } = renderHook(() => useOfficeWebSocket("/ws"));
    result.current.sendJsonMessage({ type: "chat", text: "hi" });
    expect(wsState.sendJsonMessage).toHaveBeenCalledWith({ type: "chat", text: "hi" });
  });
});
