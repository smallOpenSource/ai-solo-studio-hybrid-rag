/**
 * SSE(Server-Sent Events) 구독 훅.
 *
 * 백엔드 SSE 형식: `id:{seq}\ndata:{payload_json}`
 * - id 필드가 seq(Last-Event-ID) — 재연결 시 결손 0
 * - data 필드가 payload_json — JSON.parse 후 타입 가드
 * - 미지 type은 무시 (알려진 10타입만 처리)
 */

import { useEffect, useRef, useState } from "react";
import { isOfficeEvent, type OfficeEvent } from "../types/events";

// ── 순수 구독 함수 (EventSource 생성 + 콜백) ──────────────────────────────────

export interface SseSubscribeOptions {
  /** 이벤트 수신 시 호출. seq는 백엔드 SSE id:{seq} 필드의 실제 전역 seq */
  onEvent: (event: OfficeEvent, seq: number) => void;
  /** 연결 완료 시 호출 */
  onOpen?: () => void;
  /** 연결 오류 시 호출 */
  onError?: (err: Event) => void;
}

/**
 * EventSource를 교체 가능하게 하는 팩토리 타입 (테스트용).
 * 프로덕션에서는 전역 EventSource를 사용한다.
 */
export type EventSourceFactory = (url: string) => EventSource;

/**
 * 테스트에서 mock EventSource를 주입할 수 있는 구독 함수.
 * last_seq가 양수이면 URL에 last_seq 쿼리를 붙여 재연결 커서를 전달한다.
 *
 * @returns cleanup 함수 (EventSource.close())
 */
export function subscribeSseWithFactory(
  url: string,
  options: SseSubscribeOptions,
  lastSeq = 0,
  factory: EventSourceFactory = (u) => new EventSource(u)
): () => void {
  const fullUrl = lastSeq > 0 ? `${url}?last_seq=${lastSeq}` : url;
  const es = factory(fullUrl);

  es.onopen = () => {
    options.onOpen?.();
  };

  es.onmessage = (msgEvent: MessageEvent) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(msgEvent.data as string);
    } catch {
      return;
    }
    if (!isOfficeEvent(parsed)) {
      return;
    }
    // lastEventId: 백엔드 `id:{seq}` 줄이 EventSource 표준으로 여기에 노출됨
    // NaN이거나 비어 있으면 0으로 폴백 (seq 갱신 무시)
    const seqNum = Number(msgEvent.lastEventId);
    const seq = Number.isFinite(seqNum) && seqNum > 0 ? seqNum : 0;
    options.onEvent(parsed, seq);
  };

  es.onerror = (err: Event) => {
    options.onError?.(err);
  };

  return () => {
    es.close();
  };
}

/**
 * EventSource를 생성해 SSE 스트림을 구독한다.
 * last_seq가 양수이면 URL에 Last-Event-ID 헤더 대신 쿼리로 재연결
 * (EventSource는 헤더 직접 지정 불가 — 브라우저가 자동으로 Last-Event-ID 전송).
 *
 * @returns cleanup 함수 (EventSource.close())
 */
export function subscribeSse(
  url: string,
  options: SseSubscribeOptions,
  lastSeq = 0
): () => void {
  return subscribeSseWithFactory(url, options, lastSeq);
}

// ── React 훅 래퍼 ─────────────────────────────────────────────────────────────

export interface UseEventStreamResult {
  /** 수신된 이벤트 누적 목록 (append-only) */
  events: OfficeEvent[];
  /** 마지막 수신 seq (재연결 시 Last-Event-ID로 사용) */
  lastSeq: number;
  /** 현재 SSE 연결 상태 */
  connected: boolean;
}

/**
 * SSE 구독 React 훅.
 *
 * - EventSource로 url을 구독
 * - 받은 이벤트를 events 리스트에 누적 (append-only)
 * - id 필드(seq)를 lastSeq로 추적 → 재연결 시 Last-Event-ID
 * - 미지 타입은 isOfficeEvent 타입 가드로 무시
 * - 컴포넌트 언마운트 시 EventSource 자동 정리
 */
export function useEventStream(url: string): UseEventStreamResult {
  const [events, setEvents] = useState<OfficeEvent[]>([]);
  const [lastSeq, setLastSeq] = useState<number>(0);
  const [connected, setConnected] = useState<boolean>(false);

  // 재연결 시 lastSeq를 참조하기 위한 ref (클로저 stale 방지)
  const lastSeqRef = useRef<number>(0);

  useEffect(() => {
    const cleanup = subscribeSse(
      url,
      {
        onOpen: () => setConnected(true),
        onError: () => setConnected(false),
        // seq: 백엔드 SSE id:{seq}에서 추출한 실제 전역 seq
        // events.length 근사 대신 실제 seq로 갱신 → 재연결 결손 0
        onEvent: (ev: OfficeEvent, seq: number) => {
          setEvents((prev) => [...prev, ev]);
          if (seq > 0) {
            lastSeqRef.current = seq;
            setLastSeq(seq);
          }
        },
      },
      lastSeqRef.current
    );

    return cleanup;
    // url이 바뀌면 재구독 — lastSeq는 ref로 접근하므로 의존성 불필요
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return { events, lastSeq, connected };
}
