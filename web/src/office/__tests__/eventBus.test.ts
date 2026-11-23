/**
 * EventBus 단위 테스트 (office-v2 US-0d·Vitest).
 *
 * EventBus는 Phaser.Events.EventEmitter 싱글톤이다. ★jsdom은 WebGL 미지원이라
 * 전체 `phaser` 패키지 import는 CanvasFeatures(HTMLCanvasElement.getContext) 초기화에서
 * 크래시한다(canvas npm 미설치). 단위 테스트는 EventEmitter 동작만 보면 되므로
 * `phaser`를 Node 내장 EventEmitter로 mock해 캔버스 init을 우회한다(Phaser.Game
 * 실제 생성·렌더는 e2e US-0g). emit/on/off·싱글톤 동일 참조를 검증한다.
 */

import { describe, it, expect, vi, afterEach } from "vitest";

// ★phaser mock — Phaser.Events.EventEmitter를 최소 인라인 emitter로 대체.
// EventBus.ts는 `new Phaser.Events.EventEmitter()`만 쓰므로 on/off/emit/
// removeAllListeners만 있으면 충분. 캔버스 init(CanvasFeatures) 우회 → jsdom 통과.
// ★vi.mock은 파일 최상단으로 호이스트되므로 emitter 클래스를 팩토리 *내부*에 정의한다
// (외부 top-level 변수 참조 금지 — Node 'events'/@types/node 무의존·tsc 통과).
vi.mock("phaser", () => {
  class TestEmitter {
    private readonly handlers = new Map<string, Array<(...a: unknown[]) => void>>();
    on(event: string, fn: (...a: unknown[]) => void): this {
      const list = this.handlers.get(event) ?? [];
      list.push(fn);
      this.handlers.set(event, list);
      return this;
    }
    off(event: string, fn: (...a: unknown[]) => void): this {
      const list = this.handlers.get(event);
      if (list) this.handlers.set(event, list.filter((h) => h !== fn));
      return this;
    }
    emit(event: string, ...args: unknown[]): boolean {
      const list = this.handlers.get(event) ?? [];
      for (const fn of [...list]) fn(...args);
      return list.length > 0;
    }
    removeAllListeners(): this {
      this.handlers.clear();
      return this;
    }
  }
  return {
    __esModule: true,
    default: { Events: { EventEmitter: TestEmitter } },
    Events: { EventEmitter: TestEmitter },
  };
});

import {
  EventBus,
  OFFICE_EVENT_CHANNEL,
  SCENE_READY_CHANNEL,
  AGENT_SELECTED_CHANNEL,
  AGENT_ACTIVITY_CHANNEL,
} from "../EventBus";

afterEach(() => {
  EventBus.removeAllListeners();
});

describe("EventBus — Phaser EventEmitter 싱글톤", () => {
  it("emit한 이벤트를 on 리스너가 인자와 함께 받는다", () => {
    const handler = vi.fn();
    EventBus.on("current-scene-ready", handler);

    const fakeScene = { key: "OfficeScene" };
    EventBus.emit("current-scene-ready", fakeScene);

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith(fakeScene);
  });

  it("off로 해제한 리스너는 더 이상 받지 않는다(누수 0 가드)", () => {
    const handler = vi.fn();
    EventBus.on("office-event", handler);
    EventBus.off("office-event", handler);

    EventBus.emit("office-event", { type: "TEXT_MESSAGE" });
    expect(handler).not.toHaveBeenCalled();
  });

  it("여러 'office-event'를 순서대로 전달한다", () => {
    const received: string[] = [];
    EventBus.on("office-event", (ev: { type: string }) => received.push(ev.type));

    EventBus.emit("office-event", { type: "RUN_STARTED" });
    EventBus.emit("office-event", { type: "TEXT_MESSAGE" });
    EventBus.emit("office-event", { type: "RUN_FINISHED" });

    expect(received).toEqual(["RUN_STARTED", "TEXT_MESSAGE", "RUN_FINISHED"]);
  });

  it("import 간 동일 싱글톤 참조다(모듈 스코프 1개)", async () => {
    const again = await import("../EventBus");
    expect(again.EventBus).toBe(EventBus);
  });
});

// ── 채널 상수 (★프론트 pub/sub — 백엔드 events 11종과 무관) ─────────────────────

describe("EventBus 채널 상수 — 프론트 pub/sub(US-N4/N8)", () => {
  it("채널 상수 값이 고정 문자열이다(오타 방지)", () => {
    expect(OFFICE_EVENT_CHANNEL).toBe("office-event");
    expect(SCENE_READY_CHANNEL).toBe("current-scene-ready");
    expect(AGENT_SELECTED_CHANNEL).toBe("agent-selected");
    expect(AGENT_ACTIVITY_CHANNEL).toBe("agent-activity");
  });

  it("agent-selected: emit한 역할을 on 리스너가 받는다(맵 클릭 → 선택)", () => {
    const handler = vi.fn();
    EventBus.on(AGENT_SELECTED_CHANNEL, handler);
    EventBus.emit(AGENT_SELECTED_CHANNEL, "dev");
    expect(handler).toHaveBeenCalledWith("dev");
  });

  it("agent-activity: emit한 byRole을 on 리스너가 받는다(활동 → 글로우)", () => {
    const handler = vi.fn();
    EventBus.on(AGENT_ACTIVITY_CHANNEL, handler);
    const byRole = { ceo: "working" };
    EventBus.emit(AGENT_ACTIVITY_CHANNEL, byRole);
    expect(handler).toHaveBeenCalledWith(byRole);
  });
});
