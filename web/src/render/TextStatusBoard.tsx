/**
 * TextStatusBoard — 텍스트 상태창 (Renderer 인터페이스 첫 구현, US-006).
 *
 * 이벤트 누적 리스트를 시간순 텍스트로 렌더한다.
 * 이미지 토글 ON이면 TextMessage(chat) 항목에 감정 분류 이미지를 표시.
 *
 * Renderer 인터페이스 구현 — EventFeed에 주입 가능.
 * Phase 4c 2D 렌더러 교체 시 이 파일만 다른 구현체로 바꾸면 됨.
 */

import { useEffect, useRef } from "react";
import type { OfficeEvent } from "../types/events";
import type { Renderer } from "./Renderer";
import { classifyEmotion } from "./emotion";
import {
  getRolePortraitPath,
  PORTRAIT_FALLBACK,
} from "../config/emotionImages";
import { pickRole, ROLE_NAMES, type Role } from "./twoD/roleCharacter";

// ── 역할 포트레이트 폴백 체인 ────────────────────────────────────────────────
//
// 자산은 일부만 존재(생성중)하므로 <img onError>로 단계적 폴백한다:
//   1차 role_emotion → onError 시 role_neutral → onError 시 PORTRAIT_FALLBACK.
// 무한 onError 루프 방지: 각 단계는 1회만 시도(다음 단계 src로 교체 후 종료).

/**
 * TEXT_MESSAGE 발화자의 역할을 결정론으로 고른다.
 *
 * TEXT_MESSAGE에는 node_id가 없으므로(deriveScene 캐리포워드 참조),
 * 런타임에 node_id가 있으면 우선 사용하고(백엔드 확장 대비), 없으면 role
 * 문자열로 폴백한다. 어느 경우든 pickRole이 결정론으로 8역할에 매핑한다.
 */
function pickSpeakerRole(ev: Extract<OfficeEvent, { type: "TEXT_MESSAGE" }>): Role {
  const nid = (ev as { node_id?: string }).node_id;
  return pickRole(nid && nid.length > 0 ? nid : ev.role);
}

/**
 * 단계적 폴백 onError 핸들러 팩토리.
 * 현재 단계 src 로드 실패 시 next로 교체. PORTRAIT_FALLBACK 단계에서 또 실패하면
 * onerror를 비워(null) 무한 루프를 끊는다.
 */
function makePortraitFallback(role: Role) {
  const neutralPath = getRolePortraitPath(role, "neutral");
  return (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    // src는 절대 URL로 정규화되므로 경로 포함 여부로 현재 단계를 판정한다.
    if (!img.src.includes(neutralPath)) {
      // 1차(role_emotion) 실패 → role_neutral 시도.
      img.src = neutralPath;
    } else if (!img.src.includes(PORTRAIT_FALLBACK)) {
      // role_neutral 실패 → 최종 폴백.
      img.src = PORTRAIT_FALLBACK;
    } else {
      // 최종 폴백도 실패 → 루프 차단(더 이상 onError 미발생).
      img.onerror = null;
    }
  };
}

// ── 이벤트 타입별 텍스트 포맷터 ─────────────────────────────────────────────

/**
 * 이벤트를 사람이 읽기 좋은 한 줄 텍스트로 변환한다.
 */
function formatEventText(ev: OfficeEvent): string {
  switch (ev.type) {
    case "RUN_STARTED":
      return `▶ 워크플로 시작: ${ev.workflow}`;

    case "RUN_FINISHED": {
      let statusLabel: string;
      if (ev.status === "success") {
        statusLabel = "✓ 성공";
      } else if (ev.status === "rejected") {
        statusLabel = "✗ 거부됨";
      } else if (ev.status === "failed") {
        statusLabel = "✗ 실패";
      } else {
        statusLabel = ev.status;
      }
      return `■ 워크플로 종료 [${statusLabel}]: ${ev.workflow}`;
    }

    case "RUN_ERROR":
      return `✗ 워크플로 오류: ${ev.error}`;

    case "TEXT_MESSAGE":
      // chat 항목은 역할 표시 포함
      return `💬 [${ev.role}] ${ev.content}`;

    case "TOOL_CALL_START":
      return `⚙ 도구 호출 시작: ${ev.name} (노드: ${ev.node_id})`;

    case "TOOL_CALL_RESULT":
      return `✔ 도구 호출 결과: ${ev.name} (노드: ${ev.node_id})`;

    case "STATE_SNAPSHOT":
      return `📋 상태 스냅샷: ${Object.keys(ev.accumulated).length}개 키`;

    case "STATE_DELTA": {
      const changedKeys = Object.keys(ev.accumulated).join(", ");
      return `△ 상태 변화: ${changedKeys || "(없음)"}`;
    }

    case "INTERRUPT_REQUESTED":
      return `⏸ 승인 대기: ${ev.tool} (노드: ${ev.node_id}, 위험도: ${ev.risk})`;

    case "INTERRUPT_RESOLVED":
      return `▶ 승인 완료: ${ev.tool} → ${ev.decision} (노드: ${ev.node_id})`;

    case "NODE_FAILED":
      return `✗ 노드 실패: ${ev.node_id} [${ev.reason}] ${ev.detail}`;

    default:
      // 알 수 없는 이벤트 타입 — 타입명만 표시
      return `[${(ev as OfficeEvent).type}] ${(ev as OfficeEvent).ts}`;
  }
}

// ── 이벤트 단일 행 컴포넌트 ──────────────────────────────────────────────────

interface EventRowProps {
  ev: OfficeEvent;
  /** 이미지 토글 ON 여부 */
  showImage: boolean;
}

/**
 * 단일 이벤트 행.
 * TextMessage일 때 이미지 토글 ON이면 감정 분류 이미지를 표시한다.
 */
function EventRow({ ev, showImage }: EventRowProps) {
  const text = formatEventText(ev);
  const ts = ev.ts ? new Date(ev.ts).toLocaleTimeString("ko-KR") : "";

  // TextMessage(chat)에서만 이미지 슬롯 처리
  let imageUrl: string | null = null;
  let onPortraitError:
    | ((e: React.SyntheticEvent<HTMLImageElement>) => void)
    | undefined;
  if (showImage && ev.type === "TEXT_MESSAGE") {
    if (ev.image_url) {
      // 백엔드가 채운 image_url 우선(폴백 체인 없음 — 명시 경로 신뢰).
      imageUrl = ev.image_url;
    } else {
      // 없으면 발화자 역할 + 감정 분류로 포트레이트 선택(+onError 폴백 체인).
      const role = pickSpeakerRole(ev);
      const label = classifyEmotion(ev.content);
      imageUrl = getRolePortraitPath(role, label);
      onPortraitError = makePortraitFallback(role);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "4px 0",
        borderBottom: "1px solid #eee",
      }}
    >
      {/* 시간 표시 */}
      <span
        style={{ color: "#999", fontSize: 11, minWidth: 70, flexShrink: 0 }}
      >
        {ts}
      </span>

      {/* 역할 포트레이트 슬롯 — 토글 ON + TextMessage일 때만(리스트는 작게 32px) */}
      {imageUrl && (
        <img
          src={imageUrl}
          alt="직원 감정 이미지"
          data-testid="emotion-image"
          onError={onPortraitError}
          style={{
            width: 32,
            height: 32,
            objectFit: "cover",
            borderRadius: 4,
            flexShrink: 0,
          }}
        />
      )}

      {/* 이벤트 텍스트 */}
      <span style={{ fontFamily: "monospace", fontSize: 13 }}>{text}</span>
    </div>
  );
}

// ── 발화자 클로즈업 패널 (보드 상단 — 가장 최근 TEXT_MESSAGE 1건) ──────────────

interface SpeakerPortraitProps {
  /** 렌더할 이벤트 목록(가장 최근 TEXT_MESSAGE를 역방향 탐색). */
  events: OfficeEvent[];
}

/**
 * 가장 최근 TEXT_MESSAGE 발화자를 큰 클로즈업 포트레이트로 표시한다.
 *
 * role=pickSpeakerRole, emotion=classifyEmotion(content) → 큰 포트레이트(140px)
 * + 역할명 + 대사. 최근 TEXT_MESSAGE가 없으면 null(패널 숨김).
 * 포트레이트는 per-row와 동일한 단계적 폴백 체인을 쓴다.
 */
function SpeakerPortrait({ events }: SpeakerPortraitProps) {
  // 역방향으로 가장 최근 TEXT_MESSAGE 탐색.
  let latest: Extract<OfficeEvent, { type: "TEXT_MESSAGE" }> | null = null;
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].type === "TEXT_MESSAGE") {
      latest = events[i] as Extract<OfficeEvent, { type: "TEXT_MESSAGE" }>;
      break;
    }
  }

  // 최근 TEXT_MESSAGE 없음 → 패널 숨김.
  if (!latest) {
    return null;
  }

  const role = pickSpeakerRole(latest);
  const label = classifyEmotion(latest.content);
  const portrait = getRolePortraitPath(role, label);

  return (
    <div
      data-testid="speaker-portrait"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "10px 12px",
        marginBottom: 8,
        borderRadius: 10,
        background: "#fff",
        boxShadow: "0 2px 8px rgba(0,0,0,0.12)",
      }}
    >
      <img
        src={portrait}
        alt={`${ROLE_NAMES[role]} 표정`}
        data-testid="speaker-portrait-img"
        onError={makePortraitFallback(role)}
        style={{
          width: 140,
          height: 140,
          objectFit: "cover",
          borderRadius: 12,
          boxShadow: "0 1px 4px rgba(0,0,0,0.18)",
          flexShrink: 0,
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
          {ROLE_NAMES[role]}
        </div>
        <div style={{ fontSize: 14, color: "#333", lineHeight: 1.4 }}>
          {latest.content}
        </div>
      </div>
    </div>
  );
}

// ── TextStatusBoard 컴포넌트 (Renderer 구현체) ───────────────────────────────

interface TextStatusBoardProps {
  /** 렌더할 이벤트 목록 (append-only) */
  events: OfficeEvent[];
  /** 이미지 토글 ON 여부 (기본 false) */
  showImage?: boolean;
}

/**
 * 텍스트 상태창 컴포넌트.
 *
 * - 이벤트를 시간순 텍스트 행으로 렌더
 * - 새 이벤트 추가 시 자동 스크롤(하단 유지)
 * - showImage=true면 TextMessage에 감정 분류 이미지 표시
 */
export function TextStatusBoard({ events, showImage = false }: TextStatusBoardProps) {
  // 자동 스크롤을 위한 ref — 리스트 맨 아래 sentinel
  const bottomRef = useRef<HTMLDivElement>(null);

  // 새 이벤트가 추가될 때마다 하단으로 스크롤
  // jsdom(테스트 환경)에서는 scrollIntoView 미구현 → 함수 존재 확인 후 호출
  useEffect(() => {
    if (bottomRef.current && typeof bottomRef.current.scrollIntoView === "function") {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [events]);

  return (
    <div
      data-testid="text-status-board"
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        background: "#fafafa",
      }}
    >
      {/* 발화자 클로즈업 패널 — ★스크롤 밖 고정(항상 보임·자동스크롤에 안 가려짐) */}
      {showImage && <SpeakerPortrait events={events} />}

      {/* 이벤트 리스트 — 이 영역만 스크롤 */}
      <div style={{ padding: 8, maxHeight: 320, overflowY: "auto" }}>
        {events.length === 0 ? (
          <p style={{ color: "#999", fontStyle: "italic", margin: 0 }}>
            이벤트 대기 중…
          </p>
        ) : (
          events.map((ev, idx) => (
            <EventRow key={idx} ev={ev} showImage={showImage} />
          ))
        )}
        {/* 자동 스크롤 sentinel */}
        <div ref={bottomRef} data-testid="scroll-sentinel" />
      </div>
    </div>
  );
}

// ── Renderer 인터페이스 구현체 래퍼 ─────────────────────────────────────────

/**
 * Renderer 인터페이스 구현체 (showImage 설정 고정).
 * EventFeed에 주입할 때 객체 형태로 쓸 수 있다.
 *
 * 사용 예:
 *   const renderer = new TextStatusBoardRenderer(true);
 *   <EventFeed events={events} renderer={renderer} />
 */
export class TextStatusBoardRenderer implements Renderer {
  private showImage: boolean;

  constructor(showImage = false) {
    this.showImage = showImage;
  }

  render(events: OfficeEvent[]) {
    return <TextStatusBoard events={events} showImage={this.showImage} />;
  }
}
