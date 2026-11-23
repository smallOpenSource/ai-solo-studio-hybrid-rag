/**
 * SSE 구독 훅 단위 테스트 (Vitest).
 *
 * 검증 항목:
 *   - mock EventSource로 이벤트 누적
 *   - 미지 타입 무시 (타입 가드)
 *   - last_seq 재연결 (Last-Event-ID)
 *   - 파싱 오류 무시
 *   - cleanup(close) 호출
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { subscribeSseWithFactory } from "../api/sse";
import { isOfficeEvent, type OfficeEvent } from "../types/events";

// ── Mock EventSource ──────────────────────────────────────────────────────────

/**
 * 테스트용 mock EventSource.
 * 실제 네트워크 없이 onmessage를 수동으로 트리거한다.
 */
class MockEventSource {
  url: string;
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
  }

  /**
   * 테스트에서 수동으로 메시지를 트리거.
   * lastEventId를 지정하면 백엔드 `id:{seq}` 줄이 EventSource.lastEventId로
   * 노출되는 것을 시뮬레이션한다 — seq 정확성 테스트에 필수.
   */
  triggerMessage(data: string, lastEventId = "") {
    const ev = new MessageEvent("message", { data, lastEventId });
    this.onmessage?.(ev);
  }

  /** 테스트에서 수동으로 오픈 이벤트를 트리거 */
  triggerOpen() {
    this.onopen?.(new Event("open"));
  }

  /** 테스트에서 수동으로 에러 이벤트를 트리거 */
  triggerError() {
    this.onerror?.(new Event("error"));
  }

  close() {
    this.closed = true;
  }
}

// mock EventSource 팩토리
let lastMockEs: MockEventSource | null = null;
function mockFactory(url: string): EventSource {
  lastMockEs = new MockEventSource(url);
  return lastMockEs as unknown as EventSource;
}

const BASE_PAYLOAD = {
  run_id: "r1",
  ts: "2026-06-08T00:00:00Z",
  schema_v: 1,
};

beforeEach(() => {
  lastMockEs = null;
});

// ── 기본 구독 동작 ────────────────────────────────────────────────────────────

describe("subscribeSseWithFactory — 기본 동작", () => {
  it("유효한 이벤트를 onEvent 콜백으로 전달해야 한다", () => {
    const received: OfficeEvent[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (ev) => received.push(ev) },
      0,
      mockFactory
    );

    const payload = JSON.stringify({
      ...BASE_PAYLOAD,
      family: "Lifecycle",
      type: "RUN_STARTED",
      workflow: "crm_notify",
    });
    lastMockEs!.triggerMessage(payload);

    expect(received).toHaveLength(1);
    expect(received[0].type).toBe("RUN_STARTED");
  });

  it("여러 이벤트를 순서대로 누적해야 한다", () => {
    const received: OfficeEvent[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (ev) => received.push(ev) },
      0,
      mockFactory
    );

    const events = [
      { ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" },
      { ...BASE_PAYLOAD, family: "Text", type: "TEXT_MESSAGE", role: "assistant", content: "안녕", image_url: null },
      { ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_FINISHED", workflow: "crm_notify", status: "success" },
    ];

    for (const ev of events) {
      lastMockEs!.triggerMessage(JSON.stringify(ev));
    }

    expect(received).toHaveLength(3);
    expect(received[0].type).toBe("RUN_STARTED");
    expect(received[1].type).toBe("TEXT_MESSAGE");
    expect(received[2].type).toBe("RUN_FINISHED");
  });

  it("onOpen 콜백이 open 이벤트에서 호출되어야 한다", () => {
    const onOpen = vi.fn();
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: vi.fn(), onOpen },
      0,
      mockFactory
    );
    lastMockEs!.triggerOpen();
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("onError 콜백이 error 이벤트에서 호출되어야 한다", () => {
    const onError = vi.fn();
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: vi.fn(), onError },
      0,
      mockFactory
    );
    lastMockEs!.triggerError();
    expect(onError).toHaveBeenCalledOnce();
  });

  it("onEvent 콜백에 실제 seq(lastEventId)가 두 번째 인자로 전달되어야 한다", () => {
    // 백엔드 SSE id:{seq}가 lastEventId로 노출됨을 검증
    const receivedSeqs: number[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (_ev, seq) => receivedSeqs.push(seq) },
      0,
      mockFactory
    );

    const payload = JSON.stringify({
      ...BASE_PAYLOAD,
      family: "Lifecycle",
      type: "RUN_STARTED",
      workflow: "crm_notify",
    });
    // lastEventId "100" — events.length(1)와 다른 실제 전역 seq
    lastMockEs!.triggerMessage(payload, "100");

    expect(receivedSeqs).toHaveLength(1);
    expect(receivedSeqs[0]).toBe(100);
  });
});

// ── 미지 타입 무시 ────────────────────────────────────────────────────────────

describe("subscribeSseWithFactory — 미지 타입 무시", () => {
  it("알 수 없는 type은 onEvent를 호출하지 않아야 한다", () => {
    const onEvent = vi.fn();
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent },
      0,
      mockFactory
    );

    const unknownPayload = JSON.stringify({
      ...BASE_PAYLOAD,
      family: "Future",
      type: "UNKNOWN_FUTURE_EVENT",
      data: "미래 이벤트",
    });
    lastMockEs!.triggerMessage(unknownPayload);

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("여러 미지 타입 중 알려진 타입만 전달해야 한다", () => {
    const received: OfficeEvent[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (ev) => received.push(ev) },
      0,
      mockFactory
    );

    // 미지 타입
    lastMockEs!.triggerMessage(JSON.stringify({
      ...BASE_PAYLOAD, family: "X", type: "CUSTOM_EVENT_A",
    }));

    // 알려진 타입
    lastMockEs!.triggerMessage(JSON.stringify({
      ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_STARTED", workflow: "test",
    }));

    // 또 다른 미지 타입
    lastMockEs!.triggerMessage(JSON.stringify({
      ...BASE_PAYLOAD, family: "Y", type: "CUSTOM_EVENT_B",
    }));

    // 알려진 타입
    lastMockEs!.triggerMessage(JSON.stringify({
      ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_ERROR", error: "오류",
    }));

    expect(received).toHaveLength(2);
    expect(received[0].type).toBe("RUN_STARTED");
    expect(received[1].type).toBe("RUN_ERROR");
  });

  it("JSON 파싱 오류가 발생해도 다음 이벤트를 처리해야 한다", () => {
    const received: OfficeEvent[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (ev) => received.push(ev) },
      0,
      mockFactory
    );

    // 깨진 JSON — 무시되어야 함
    lastMockEs!.triggerMessage("{ invalid json }");

    // 유효한 이벤트 — 처리되어야 함
    lastMockEs!.triggerMessage(JSON.stringify({
      ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_STARTED", workflow: "test",
    }));

    expect(received).toHaveLength(1);
    expect(received[0].type).toBe("RUN_STARTED");
  });

  it("null JSON은 무시해야 한다", () => {
    const onEvent = vi.fn();
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent },
      0,
      mockFactory
    );

    lastMockEs!.triggerMessage("null");
    expect(onEvent).not.toHaveBeenCalled();
  });
});

// ── last_seq 재연결 ───────────────────────────────────────────────────────────

describe("subscribeSseWithFactory — last_seq 재연결 (Last-Event-ID)", () => {
  it("last_seq > 0이면 URL에 ?last_seq=N 쿼리가 붙어야 한다", () => {
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: vi.fn() },
      42,
      mockFactory
    );

    // mock EventSource가 받은 URL 확인
    expect(lastMockEs!.url).toBe("/events/stream?last_seq=42");
  });

  it("last_seq = 0이면 URL에 쿼리가 붙지 않아야 한다", () => {
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: vi.fn() },
      0,
      mockFactory
    );

    expect(lastMockEs!.url).toBe("/events/stream");
  });

  it("last_seq가 양수이면 재연결 시 해당 seq 이후부터 수신해야 한다 (URL 단언)", () => {
    // 첫 번째 구독 (seq=0에서 시작)
    subscribeSseWithFactory("/events/stream", { onEvent: vi.fn() }, 0, mockFactory);
    expect(lastMockEs!.url).toBe("/events/stream");

    // 재연결 (seq=10부터 이어받기)
    subscribeSseWithFactory("/events/stream", { onEvent: vi.fn() }, 10, mockFactory);
    expect(lastMockEs!.url).toBe("/events/stream?last_seq=10");
  });

  it("lastSeq는 events.length가 아닌 lastEventId 실제 seq여야 한다 (결함 재발 방지)", () => {
    // 핵심 회귀 방지 테스트:
    // 이벤트 2개를 수신하되 lastEventId가 100, 101 (DB 전역 seq)
    // events.length 근사라면 lastSeq=2가 되지만, 실제 seq는 101이어야 함
    const receivedSeqs: number[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (_ev, seq) => receivedSeqs.push(seq) },
      0,
      mockFactory
    );

    const evA = JSON.stringify({ ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_STARTED", workflow: "w" });
    const evB = JSON.stringify({ ...BASE_PAYLOAD, family: "Lifecycle", type: "RUN_FINISHED", workflow: "w", status: "success" });

    // seq 100, 101 — events.length(1,2)와 전혀 다른 값
    lastMockEs!.triggerMessage(evA, "100");
    lastMockEs!.triggerMessage(evB, "101");

    expect(receivedSeqs).toEqual([100, 101]);
    // 재연결 시 마지막 seq(101)가 URL에 사용되어야 함
    const lastSeq = receivedSeqs[receivedSeqs.length - 1];
    expect(lastSeq).toBe(101);
    expect(lastSeq).not.toBe(2); // events.length 근사 금지

    // 실제 재연결에 lastSeq=101이 사용됨을 URL로 확인
    subscribeSseWithFactory("/events/stream", { onEvent: vi.fn() }, lastSeq, mockFactory);
    expect(lastMockEs!.url).toBe("/events/stream?last_seq=101");
  });

  it("lastEventId가 비어 있으면 seq 0을 전달해야 한다 (id: 줄 없는 메시지 방어)", () => {
    const receivedSeqs: number[] = [];
    subscribeSseWithFactory(
      "/events/stream",
      { onEvent: (_ev, seq) => receivedSeqs.push(seq) },
      0,
      mockFactory
    );

    const payload = JSON.stringify({
      ...BASE_PAYLOAD,
      family: "Lifecycle",
      type: "RUN_STARTED",
      workflow: "w",
    });
    // lastEventId 없음 — id: 줄 없는 경우 폴백
    lastMockEs!.triggerMessage(payload, "");

    expect(receivedSeqs).toHaveLength(1);
    expect(receivedSeqs[0]).toBe(0);
  });
});

// ── cleanup ───────────────────────────────────────────────────────────────────

describe("subscribeSseWithFactory — cleanup", () => {
  it("cleanup 함수 호출 시 EventSource.close()가 불려야 한다", () => {
    const cleanup = subscribeSseWithFactory(
      "/events/stream",
      { onEvent: vi.fn() },
      0,
      mockFactory
    );

    expect(lastMockEs!.closed).toBe(false);
    cleanup();
    expect(lastMockEs!.closed).toBe(true);
  });
});

// ── isOfficeEvent 직접 단언 (SSE 파이프라인) ─────────────────────────────────

describe("isOfficeEvent — SSE 파이프라인 통합", () => {
  it("JSON.parse 결과가 isOfficeEvent를 통과해야 실제로 전달된다", () => {
    // 이 테스트는 SSE 파이프라인의 타입 가드 위치를 직접 검증
    const validJson = JSON.stringify({
      ...BASE_PAYLOAD,
      family: "Interrupt",
      type: "INTERRUPT_REQUESTED",
      node_id: "N3",
      tool: "email_send",
      risk: "needs-approval",
      payload: { task_id: 1 },
    });

    const parsed = JSON.parse(validJson) as unknown;
    expect(isOfficeEvent(parsed)).toBe(true);
  });

  it("미지 타입은 isOfficeEvent를 통과하지 못해야 한다", () => {
    const unknownJson = JSON.stringify({
      ...BASE_PAYLOAD,
      family: "Custom",
      type: "MY_CUSTOM_TYPE",
    });

    const parsed = JSON.parse(unknownJson) as unknown;
    expect(isOfficeEvent(parsed)).toBe(false);
  });
});
