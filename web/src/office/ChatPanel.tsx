/**
 * ChatPanel — Discord류 사이드 채팅 패널 (office-v2 US-0e).
 *
 * 스펙 §2.5: 오피스(Phaser 캔버스)가 메인, 좌/우 사이드에 채팅 윈도우(Discord와
 * 동일 UX). ★접기/펼치기(collapsible)·좌우 토글 도킹. WS로 받은 TEXT_MESSAGE를
 * Discord류(아바타 이니셜·유저명=role·타임스탬프·role별 정렬)로 표시한다.
 *
 * props로 events(OfficeEvent[])를 받아 TEXT_MESSAGE만 필터해 렌더한다. 입력→WS 송신은
 * sendJsonMessage prop으로 받되 PoC는 표시 위주(채팅 송신 폼은 후속 US에서 확장).
 *
 * 데이터 코어(events.ts/SceneState)는 0수정 — 패널은 raw 이벤트 스트림을 직접 소비한다.
 */

import { useMemo, useState } from "react";
import type { OfficeEvent, TextMessage } from "../types/events";
import { pickRoleKey, ROLE_KEY_NAMES, type RoleKey } from "./sceneLogic";
import { classifyEmotion } from "../render/emotion";
import { getRolePortraitPath, PORTRAIT_FALLBACK } from "../config/emotionImages";

// ── 도킹/표시 헬퍼 (순수 — 테스트 가능) ────────────────────────────────────────

/** 패널 도킹 위치. */
export type ChatSide = "left" | "right";

/** events에서 TEXT_MESSAGE만 시간순으로 추린다(채팅 표시 대상). */
export function selectTextMessages(events: OfficeEvent[]): TextMessage[] {
  return events.filter((ev): ev is TextMessage => ev.type === "TEXT_MESSAGE");
}

/** role을 표시용 유저명으로 — Discord류 라벨(assistant→직원·user→나·system→시스템). */
export function roleLabel(role: string): string {
  if (role === "user") return "나";
  if (role === "system") return "시스템";
  return "직원"; // assistant(에이전트 발화) 및 그 외.
}

/** role을 아바타 이니셜로 — 한 글자(Discord 아바타 폴백). */
export function roleInitial(role: string): string {
  return roleLabel(role).charAt(0);
}

// ── 발화자 16 역할 초상 (assistant 메시지 — 역할별 얼굴) ────────────────────────
//
// ★TextMessage는 node_id를 담지 않으므로(events.ts: role·content·image_url만),
// node_id가 있으면(백엔드 확장 대비) 그것을, 없으면 role 문자열을 식별자로 pickRoleKey에
// 넘긴다. assistant만 초상 표시 — user/system은 기존 라벨/이니셜 유지.

/** assistant 발화자의 16 역할을 결정론으로 고른다(node_id 우선·없으면 role). */
function pickSpeakerRoleKey(msg: TextMessage): RoleKey {
  const nid = (msg as { node_id?: string }).node_id;
  return pickRoleKey(nid && nid.length > 0 ? nid : msg.role);
}

/** assistant 발화자의 표시명(역할명 — generic "직원" 대신 "개발자" 등). */
export function speakerLabel(msg: TextMessage): string {
  if (msg.role !== "assistant") return roleLabel(msg.role);
  return ROLE_KEY_NAMES[pickSpeakerRoleKey(msg)];
}

/**
 * 단계적 초상 폴백 onError 핸들러 — TextStatusBoard와 동일 체인.
 *   1차 {role}_{emotion} → role_neutral → PORTRAIT_FALLBACK → (루프 차단).
 * 마지막 단계에서도 실패하면 onerror=null로 무한 루프를 끊는다.
 */
function makePortraitFallback(role: RoleKey) {
  const neutralPath = getRolePortraitPath(role, "neutral");
  return (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (!img.src.includes(neutralPath)) {
      img.src = neutralPath;
    } else if (!img.src.includes(PORTRAIT_FALLBACK)) {
      img.src = PORTRAIT_FALLBACK;
    } else {
      img.onerror = null;
    }
  };
}

/** ISO ts를 HH:MM 타임스탬프로(표시용·실패 시 원문 폴백). */
export function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface ChatPanelProps {
  /** WS 수신 이벤트 누적(TEXT_MESSAGE만 표시). */
  events: OfficeEvent[];
  /** JSON 송신(채팅 입력 등 — PoC는 미사용·후속 US). */
  sendJsonMessage?: (message: unknown) => void;
}

// ── ChatPanel ──────────────────────────────────────────────────────────────────

/**
 * Discord류 채팅 패널 — 좌/우 토글·접기/펼치기.
 * 접으면(collapsed) 폭이 줄어 오피스가 전체폭에 가까워지고, 펼치면 사이드 도킹된다.
 */
export function ChatPanel({ events }: ChatPanelProps) {
  const [side, setSide] = useState<ChatSide>("right");
  // ★C2: 기본 접힘으로 시작(펼침 토글 버튼으로 표면화 — chat-toggle-collapse).
  const [collapsed, setCollapsed] = useState(true);

  // TEXT_MESSAGE만 추려 표시(events 변경 시에만 재계산).
  const messages = useMemo(() => selectTextMessages(events), [events]);

  /** 좌↔우 도킹 토글. */
  const toggleSide = () => setSide((s) => (s === "left" ? "right" : "left"));
  /** 접기↔펼치기 토글. */
  const toggleCollapsed = () => setCollapsed((c) => !c);

  return (
    <div
      data-testid="chat-panel"
      data-side={side}
      data-collapsed={collapsed}
      style={{
        display: "flex",
        flexDirection: "column",
        // 좌측 도킹이면 그리드 순서를 앞으로(order) — 우측은 기본.
        order: side === "left" ? -1 : 1,
        width: collapsed ? 44 : 320,
        minWidth: collapsed ? 44 : 320,
        height: 560,
        border: "1px solid #ccc",
        borderRadius: 8,
        background: "#2b2d31", // Discord 다크.
        color: "#dbdee1",
        overflow: "hidden",
      }}
    >
      {/* 헤더: 제목 + 좌우/접기 토글 버튼 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 10px",
          background: "#1e1f22",
          borderBottom: "1px solid #111",
        }}
      >
        {!collapsed && (
          <span style={{ fontSize: 14, fontWeight: "bold", flex: 1 }}># 채팅</span>
        )}
        <button
          data-testid="chat-toggle-side"
          onClick={toggleSide}
          title={side === "left" ? "우측으로" : "좌측으로"}
          style={chatBtnStyle}
        >
          {side === "left" ? "▶" : "◀"}
        </button>
        <button
          data-testid="chat-toggle-collapse"
          onClick={toggleCollapsed}
          title={collapsed ? "펼치기" : "접기"}
          style={chatBtnStyle}
        >
          {collapsed ? "💬" : "✕"}
        </button>
      </div>

      {/* 메시지 목록(접으면 숨김) */}
      {!collapsed && (
        <div
          data-testid="chat-messages"
          style={{ flex: 1, overflowY: "auto", padding: "8px 10px" }}
        >
          {messages.length === 0 ? (
            <div style={{ fontSize: 12, color: "#888" }}>아직 메시지가 없습니다.</div>
          ) : (
            messages.map((msg, i) => <ChatMessageRow key={i} msg={msg} />)
          )}
        </div>
      )}
    </div>
  );
}

// ── 메시지 1행 (Discord류: 아바타·유저명·타임스탬프·role별 정렬) ────────────────

/** 사용자(나) 메시지는 우측, 그 외(직원/시스템)는 좌측 정렬. */
function ChatMessageRow({ msg }: { msg: TextMessage }) {
  const mine = msg.role === "user";
  // assistant(에이전트 발화)는 16 역할 초상 표시 — user/system은 기존 이니셜 아바타 유지.
  const isAssistant = msg.role === "assistant";
  return (
    <div
      data-testid="chat-message"
      data-role={msg.role}
      style={{
        display: "flex",
        flexDirection: mine ? "row-reverse" : "row",
        gap: 8,
        marginBottom: 10,
      }}
    >
      {/* 아바타: assistant=역할 초상(폴백 체인)·그 외=이니셜 원형 */}
      {isAssistant ? (
        <ChatRolePortrait msg={msg} />
      ) : (
        <div
          aria-hidden
          style={{
            width: 32,
            height: 32,
            borderRadius: "50%",
            background: mine ? "#5865f2" : "#4e5058",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 13,
            fontWeight: "bold",
            flexShrink: 0,
          }}
        >
          {roleInitial(msg.role)}
        </div>
      )}
      {/* 본문: 유저명(assistant면 역할명) + 타임스탬프 + 내용 */}
      <div style={{ textAlign: mine ? "right" : "left", minWidth: 0 }}>
        <div style={{ fontSize: 12, marginBottom: 2 }}>
          <span style={{ fontWeight: "bold" }}>{speakerLabel(msg)}</span>{" "}
          <span style={{ color: "#949ba4", fontSize: 11 }}>{formatTimestamp(msg.ts)}</span>
        </div>
        <div style={{ fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {msg.content}
        </div>
      </div>
    </div>
  );
}

/**
 * assistant 발화자의 16 역할 초상 아바타(32px 원형·onError 폴백 체인).
 * role=pickSpeakerRoleKey, emotion=classifyEmotion(content)로 초상 경로를 고르고,
 * 자산 미존재 시 role_neutral → PORTRAIT_FALLBACK으로 단계적 폴백(깨짐 0).
 */
function ChatRolePortrait({ msg }: { msg: TextMessage }) {
  const role = pickSpeakerRoleKey(msg);
  const emotion = classifyEmotion(msg.content);
  return (
    <img
      src={getRolePortraitPath(role, emotion)}
      alt={`${ROLE_KEY_NAMES[role]} 표정`}
      data-testid="chat-portrait"
      data-role-key={role}
      onError={makePortraitFallback(role)}
      style={{
        width: 32,
        height: 32,
        borderRadius: "50%",
        objectFit: "cover",
        background: "#4e5058",
        flexShrink: 0,
      }}
    />
  );
}

/** 헤더 토글 버튼 공통 스타일. */
const chatBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#dbdee1",
  cursor: "pointer",
  fontSize: 13,
  padding: "2px 6px",
};
