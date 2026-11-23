/**
 * 렌더러 추상 경계 단위 테스트 (Vitest).
 *
 * 검증 항목 (수용 기준 13):
 *   - 피드(EventFeed)가 렌더러 무관함 — 렌더러 교체 가능
 *   - 함수형 렌더러(RenderFn) 지원
 *   - Renderer 인터페이스 구현체 지원
 *   - 두 렌더러가 같은 이벤트 피드를 소비 가능 (교체 가능성)
 */

import { describe, it, expect, vi } from "vitest";
import { EventFeed } from "../render/Renderer";
import type { Renderer, RenderFn } from "../render/Renderer";
import type { OfficeEvent } from "../types/events";

// ── 테스트용 이벤트 목록 ──────────────────────────────────────────────────────

const BASE = {
  run_id: "r1",
  ts: "2026-06-08T00:00:00Z",
  schema_v: 1,
};

const SAMPLE_EVENTS: OfficeEvent[] = [
  { ...BASE, family: "Lifecycle", type: "RUN_STARTED", workflow: "crm_notify" },
  { ...BASE, family: "Text", type: "TEXT_MESSAGE", role: "assistant", content: "안녕", image_url: null },
  { ...BASE, family: "Lifecycle", type: "RUN_FINISHED", workflow: "crm_notify", status: "success" },
];

// ── 함수형 렌더러(RenderFn) ───────────────────────────────────────────────────

describe("EventFeed — 함수형 렌더러(RenderFn)", () => {
  it("함수형 렌더러가 events를 받아 반환값을 그대로 반환해야 한다", () => {
    const renderFn: RenderFn = (events) => `이벤트 수: ${events.length}개`;

    const result = EventFeed({ events: SAMPLE_EVENTS, renderer: renderFn });
    expect(result).toBe("이벤트 수: 3개");
  });

  it("빈 이벤트 목록도 함수형 렌더러에 전달되어야 한다", () => {
    const renderFn: RenderFn = (events) => events.length;
    const result = EventFeed({ events: [], renderer: renderFn });
    expect(result).toBe(0);
  });

  it("함수형 렌더러가 이벤트 배열의 내용을 받아야 한다", () => {
    const receivedEvents: OfficeEvent[] = [];
    const renderFn: RenderFn = (events) => {
      receivedEvents.push(...events);
      return null;
    };

    EventFeed({ events: SAMPLE_EVENTS, renderer: renderFn });

    expect(receivedEvents).toHaveLength(3);
    expect(receivedEvents[0].type).toBe("RUN_STARTED");
    expect(receivedEvents[2].type).toBe("RUN_FINISHED");
  });

  it("함수형 렌더러가 vi.fn()으로 검증 가능해야 한다", () => {
    const renderFn = vi.fn((_events: OfficeEvent[]) => "렌더 결과");

    EventFeed({ events: SAMPLE_EVENTS, renderer: renderFn });

    expect(renderFn).toHaveBeenCalledOnce();
    expect(renderFn).toHaveBeenCalledWith(SAMPLE_EVENTS);
  });
});

// ── Renderer 인터페이스 구현체 ────────────────────────────────────────────────

describe("EventFeed — Renderer 인터페이스 구현체", () => {
  it("Renderer.render()가 호출되어야 한다", () => {
    const renderer: Renderer = {
      render: vi.fn((events) => `렌더: ${events.length}`),
    };

    const result = EventFeed({ events: SAMPLE_EVENTS, renderer });
    expect(renderer.render).toHaveBeenCalledOnce();
    expect(renderer.render).toHaveBeenCalledWith(SAMPLE_EVENTS);
    expect(result).toBe("렌더: 3");
  });

  it("빈 이벤트 목록도 Renderer.render()에 전달되어야 한다", () => {
    const renderer: Renderer = {
      render: vi.fn(() => "빈 목록"),
    };

    EventFeed({ events: [], renderer });
    expect(renderer.render).toHaveBeenCalledWith([]);
  });
});

// ── 렌더러 교체 가능성 (피드 무관) ───────────────────────────────────────────

describe("렌더러 교체 가능성 — 피드가 렌더러 무관", () => {
  it("같은 이벤트 피드에 다른 렌더러를 주입하면 다른 결과가 나와야 한다", () => {
    // 텍스트 렌더러 — 이벤트를 텍스트로 표시
    const textRenderer: RenderFn = (events) =>
      events.map((e) => `[TEXT] ${e.type}`).join("\n");

    // "2D 렌더러" 자리 — Phase 4c에서 구현될 예정
    const twoDRenderer: RenderFn = (events) =>
      events.map((e) => `[2D] ${e.type}`).join("|");

    const textResult = EventFeed({ events: SAMPLE_EVENTS, renderer: textRenderer });
    const twoDResult = EventFeed({ events: SAMPLE_EVENTS, renderer: twoDRenderer });

    // 같은 이벤트지만 렌더러에 따라 다른 표현
    expect(textResult).toContain("[TEXT] RUN_STARTED");
    expect(twoDResult).toContain("[2D] RUN_STARTED");
    expect(textResult).not.toContain("[2D]");
    expect(twoDResult).not.toContain("[TEXT]");
  });

  it("렌더러를 교체해도 이벤트 피드(events 배열)가 변경되지 않아야 한다", () => {
    const originalEvents = [...SAMPLE_EVENTS];

    // 첫 번째 렌더러 — 이벤트를 변형하려 시도 (비순수)
    const mutatingRenderer: RenderFn = (events) => {
      // 렌더러 내부에서 배열을 변형해도 원본에 영향 없음
      const copy = [...events];
      copy.push({
        ...BASE,
        family: "Lifecycle",
        type: "RUN_STARTED",
        workflow: "extra",
      });
      return copy.length;
    };

    EventFeed({ events: SAMPLE_EVENTS, renderer: mutatingRenderer });

    // 원본 배열은 변경되지 않아야 함
    expect(SAMPLE_EVENTS).toHaveLength(originalEvents.length);
  });

  it("Renderer 인터페이스 구현체와 함수형 렌더러가 동일한 이벤트를 처리해야 한다", () => {
    const classRenderer: Renderer = {
      render: (events) => events.length * 10,
    };

    const fnRenderer: RenderFn = (events) => events.length * 10;

    const classResult = EventFeed({ events: SAMPLE_EVENTS, renderer: classRenderer });
    const fnResult = EventFeed({ events: SAMPLE_EVENTS, renderer: fnRenderer });

    expect(classResult).toBe(fnResult);
  });

  it("빈 이벤트에서 시작해 이벤트가 추가될 때 렌더러가 항상 최신 목록을 받아야 한다", () => {
    const snapshots: number[] = [];
    const trackingRenderer: RenderFn = (events) => {
      snapshots.push(events.length);
      return null;
    };

    // 0개 → 렌더
    EventFeed({ events: [], renderer: trackingRenderer });
    // 2개 → 렌더
    EventFeed({ events: SAMPLE_EVENTS.slice(0, 2), renderer: trackingRenderer });
    // 3개 → 렌더
    EventFeed({ events: SAMPLE_EVENTS, renderer: trackingRenderer });

    expect(snapshots).toEqual([0, 2, 3]);
  });
});

// ── 2D 렌더러 자리 예약 확인 ─────────────────────────────────────────────────

describe("Phase 4c 2D 렌더러 자리 — 인터페이스 호환성", () => {
  it("2D 렌더러 구현체가 Renderer 인터페이스를 충족하면 EventFeed에 주입 가능해야 한다", () => {
    // Phase 4c에서 실제 Phaser3 렌더러가 들어올 자리
    // 지금은 플레이스홀더 구현으로 인터페이스 호환성만 검증
    const placeholder2DRenderer: Renderer = {
      render: (_events: OfficeEvent[]) => {
        // Phase 4c: Phaser3 캔버스 반환 예정
        return "2D 렌더러 자리 (Phase 4c)";
      },
    };

    const result = EventFeed({ events: SAMPLE_EVENTS, renderer: placeholder2DRenderer });
    expect(result).toBe("2D 렌더러 자리 (Phase 4c)");
  });
});
