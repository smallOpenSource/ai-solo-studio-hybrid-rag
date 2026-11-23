/**
 * 렌더러 추상 경계 (수용 기준 13).
 *
 * 설계 원칙:
 *   이벤트 피드(구독부) ↔ 렌더부를 분리한다.
 *   EventFeed가 이벤트 목록을 관리하고,
 *   Renderer 인터페이스를 구현한 어떤 렌더러든 주입받아 표시한다.
 *
 *   - 텍스트 렌더러(TextStatusBoard) — US-006에서 첫 구현
 *   - 2D 렌더러(TwoDRenderer) — Phase 4c 자리 (미구현)
 *
 * 피드(EventFeed)는 렌더러를 전혀 모른다:
 *   렌더러를 교체해도 피드 코드는 변경 없음 → 추상 분리 충족.
 */

import type { ReactNode } from "react";
import type { OfficeEvent } from "../types/events";

// ── Renderer 인터페이스 ───────────────────────────────────────────────────────

/**
 * 렌더러 인터페이스.
 * OfficeEvent 배열을 받아 ReactNode(표현)를 반환한다.
 *
 * 구현체:
 *   - TextRenderer (인라인 함수 형태로도 사용 가능)
 *   - TextStatusBoard — US-006에서 구현
 *   - TwoDRenderer — Phase 4c 예약 (PixiJS 기반)
 */
export interface Renderer {
  render(events: OfficeEvent[]): ReactNode;
}

/**
 * 함수형 렌더러 타입 — React 함수 컴포넌트와 동일한 시그니처.
 * Renderer 인터페이스 대신 더 가볍게 사용할 수 있다.
 */
export type RenderFn = (events: OfficeEvent[]) => ReactNode;

// ── EventFeed (피드 컨테이너) — 렌더러를 주입받음 ────────────────────────────

export interface EventFeedProps {
  /** 구독된 이벤트 목록 (append-only) */
  events: OfficeEvent[];
  /**
   * 렌더러 — 인터페이스 구현체 또는 함수형 렌더러.
   * 피드가 렌더러 구현을 알 필요 없음 → 추상 분리 핵심.
   */
  renderer: Renderer | RenderFn;
}

/**
 * EventFeed — 이벤트 피드 컨테이너.
 *
 * 렌더러(Renderer 또는 RenderFn)를 주입받아
 * events 배열을 표시한다.
 * 피드 자체는 어떻게 표시할지 모른다 — 렌더러가 결정한다.
 */
export function EventFeed({ events, renderer }: EventFeedProps): ReactNode {
  // 함수형 렌더러 또는 Renderer 인터페이스 구현체 모두 지원
  if (typeof renderer === "function") {
    return renderer(events);
  }
  return renderer.render(events);
}

// ── Phase 4c 자리 예약 (2D 렌더러) ───────────────────────────────────────────
// Phase 4c에서 TwoDRenderer가 이 인터페이스를 구현할 예정.
// export class TwoDRenderer implements Renderer {
//   render(events: OfficeEvent[]): ReactNode {
//     // PixiJS 기반 2D 오피스 맵 렌더링 (Phase 4c 구현)
//     throw new Error("TwoDRenderer: Phase 4c에서 구현 예정");
//   }
// }
