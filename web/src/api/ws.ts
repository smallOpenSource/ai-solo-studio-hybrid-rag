/**
 * WebSocket 구독 훅 (office-v2 US-0c) — SSE(api/sse.ts)와 **병존**(무수정).
 *
 * 백엔드 WS 계약(web_server/ws.py):
 *   - 클라가 접속 직후 `{"type":"hello","last_seq":N}`을 보내면 서버가 N+ 이후를
 *     프레임 `{"seq": <int>, "event": <OfficeEvent>}`로 흘린다(SSE 동등·결손0).
 *   - 프레임의 `.event`를 isOfficeEvent(types/events.ts 재사용)로 검증해 누적한다.
 *
 * react-use-websocket(robtaussig)로 auto-reconnect(상한20·exp backoff)·heartbeat를
 * 얻는다. 재연결 폭주 가드(H-01/US-006b 전례)를 reconnectAttempts 상한 + backoff로
 * 재현한다. ★api/sse.ts는 무수정(컷오버까지 병존).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import { isOfficeEvent, type OfficeEvent } from "../types/events";

// ── 프레임 파싱 (순수 함수 — 테스트 가능) ──────────────────────────────────────

/**
 * WS 프레임 1개 형태 — 백엔드 ws.py `_frame`의 미러.
 * `{"seq": <int>, "event": <OfficeEvent JSON>}`.
 */
export interface OfficeFrame {
  seq: number;
  event: OfficeEvent;
}

/**
 * lastJsonMessage(react-use-websocket가 JSON.parse한 객체)에서 유효한 프레임을
 * 추출한다. `.event`가 isOfficeEvent를 통과하고 `.seq`가 유한한 정수면 프레임,
 * 아니면 null(미지/잘못된 프레임은 무시 — SSE의 isOfficeEvent 가드와 동등).
 *
 * @param raw lastJsonMessage(null·임의 객체 가능).
 * @returns 유효한 OfficeFrame 또는 null.
 */
export function parseOfficeFrame(raw: unknown): OfficeFrame | null {
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const frame = raw as Record<string, unknown>;
  if (!isOfficeEvent(frame.event)) {
    return null;
  }
  const seqNum = Number(frame.seq);
  const seq = Number.isFinite(seqNum) && seqNum >= 0 ? seqNum : 0;
  return { seq, event: frame.event };
}

// ── 훅 결과 ─────────────────────────────────────────────────────────────────

export interface UseOfficeWebSocketResult {
  /** 수신된 OfficeEvent 누적 목록(append-only). */
  events: OfficeEvent[];
  /** 마지막 수신 seq(재연결 hello.last_seq 커서). */
  lastSeq: number;
  /** 현재 WS 연결 상태(ReadyState.OPEN). */
  connected: boolean;
  /** JSON 메시지 송신(채팅 입력 등 후속 US용·재export). */
  sendJsonMessage: (message: unknown) => void;
}

// ── 재연결 가드 상수 (H-01/US-006b 전례 — 폭주 차단) ─────────────────────────

/** 재연결 시도 상한(react-use-websocket 기본도 20이나 의도를 코드로 못 박음). */
const RECONNECT_ATTEMPTS = 20;
/** exp backoff 상한(ms) — 1s·2s·4s…최대 10s(저빈도 폴링). */
const RECONNECT_MAX_MS = 10_000;

/**
 * 재연결 간격 exp backoff: `min(1000 * 2^attempt, 10000)`.
 * 폭주(즉시 재연결 루프) 방지 — 시도가 늘수록 간격이 지수적으로 벌어진다.
 *
 * @param attempt 0부터 시작하는 재연결 시도 번호.
 */
export function reconnectBackoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, RECONNECT_MAX_MS);
}

/**
 * WS 구독 React 훅 — url에 접속해 프레임을 누적한다.
 *
 * - onOpen에 `sendJsonMessage({type:'hello',last_seq:<last seq seen>})`로 커서 송신.
 * - lastJsonMessage 프레임을 parseOfficeFrame으로 검증해 events에 누적·lastSeq 갱신.
 * - auto-reconnect(shouldReconnect→true·상한20·exp backoff)·heartbeat.
 *
 * ★stale 클로저 방지: lastSeq는 ref로 추적해 onOpen이 항상 최신 커서를 보낸다.
 *
 * @param url WS 엔드포인트(예 "/ws").
 */
export function useOfficeWebSocket(url: string): UseOfficeWebSocketResult {
  const [events, setEvents] = useState<OfficeEvent[]>([]);
  const [lastSeq, setLastSeq] = useState<number>(0);

  // 재연결 onOpen이 최신 커서를 참조하도록 ref로 추적(클로저 stale 방지).
  const lastSeqRef = useRef<number>(0);

  const { sendJsonMessage, lastJsonMessage, readyState } = useWebSocket(url, {
    // 접속 직후 hello로 리플레이 커서 송신(N+ 이후만 결손0 재생).
    onOpen: () => {
      sendJsonMessage({ type: "hello", last_seq: lastSeqRef.current });
    },
    // 재연결 폭주 가드: 항상 재연결하되 상한·exp backoff로 제한.
    shouldReconnect: () => true,
    reconnectAttempts: RECONNECT_ATTEMPTS,
    reconnectInterval: reconnectBackoffMs,
    // heartbeat: 죽은 연결 감지(서버는 ping을 무시·드레인 — recv_loop).
    heartbeat: true,
  });

  // ── lastJsonMessage 프레임 누적 ───────────────────────────────────────────
  useEffect(() => {
    const frame = parseOfficeFrame(lastJsonMessage);
    if (frame === null) {
      return;
    }
    setEvents((prev) => [...prev, frame.event]);
    if (frame.seq > 0) {
      lastSeqRef.current = frame.seq;
      setLastSeq(frame.seq);
    }
  }, [lastJsonMessage]);

  // sendJsonMessage 타입을 (message: unknown)=>void로 좁혀 재export(채팅 입력 등).
  const sendJson = useCallback(
    (message: unknown) => sendJsonMessage(message),
    [sendJsonMessage]
  );

  return {
    events,
    lastSeq,
    connected: readyState === ReadyState.OPEN,
    sendJsonMessage: sendJson,
  };
}
