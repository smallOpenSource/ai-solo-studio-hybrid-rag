/**
 * CompanyChat — §9.4 Company Chat (P6c·패널 4).
 *
 * 데이터: orch SSE TEXT_MESSAGE 이벤트(회사 채팅·직원 발화). web/src/types/events.ts(=동결
 * web/events.ts)의 TextMessage 타입을 **import(읽기)만** 한다 — events.ts 수정 0.
 * 라이브에서는 useEventStream(SSE) 의 TEXT_MESSAGE 를 필터해 주입한다(컨테이너 책임).
 *
 * ★데이터바인딩(렌더-트랩 아님): 메시지 role/content 는 **props.messages 값에서 파생** — 고정 0.
 */

import { type TextMessage } from "../types/events";

export interface CompanyChatProps {
  /** TEXT_MESSAGE 이벤트(role/content)·동결 events.ts 타입(읽기 전용 import). */
  messages: TextMessage[];
}

function ChatLine({ m }: { m: TextMessage }) {
  const isAssistant = m.role === "assistant";
  return (
    <div
      className="control-chat-line"
      data-role={m.role}
      data-run-id={m.run_id}
      style={{
        padding: "3px 8px",
        fontSize: 13,
        display: "flex",
        gap: 6,
        flexDirection: "column",
        alignItems: isAssistant ? "flex-start" : "flex-end",
      }}
    >
      <span className="control-chat-role" style={{ fontSize: 10, color: "#64748b" }}>
        {m.role}
      </span>
      <span
        className="control-chat-content"
        style={{
          background: isAssistant ? "#1e293b" : "#1d4ed8",
          color: "#e2e8f0",
          borderRadius: 8,
          padding: "4px 8px",
          maxWidth: "85%",
          wordBreak: "break-word",
        }}
      >
        {m.content}
      </span>
    </div>
  );
}

/** Company Chat 패널 — 회사 채팅(TEXT_MESSAGE)을 시간순으로 렌더. */
export function CompanyChat({ messages }: CompanyChatProps) {
  return (
    <section
      className="control-company-chat"
      data-testid="company-chat"
      data-count={messages.length}
      aria-label="Company Chat"
      style={{
        background: "#0f172a",
        color: "#e2e8f0",
        borderRadius: 8,
        padding: 8,
        minWidth: 240,
        display: "flex",
        flexDirection: "column",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
      }}
    >
      <header style={{ fontSize: 12, fontWeight: 700, opacity: 0.8, padding: "2px 8px 6px" }}>
        회사 채팅 (Company Chat)
      </header>
      {messages.length === 0 ? (
        <div className="control-chat-empty" style={{ padding: 8, fontSize: 12, opacity: 0.6 }}>
          메시지 없음
        </div>
      ) : (
        <div className="control-chat-log" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {messages.map((m, i) => (
            <ChatLine key={`${m.run_id}-${m.ts}-${i}`} m={m} />
          ))}
        </div>
      )}
    </section>
  );
}
