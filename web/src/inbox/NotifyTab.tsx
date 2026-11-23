/**
 * NotifyTab — 알림 탭 (US-007).
 *
 * NODE_FAILED(escalation|gating) · RUN_FINISHED · RUN_ERROR 항목을 표시한다.
 * 읽음 처리는 로컬 상태로만 관리(서버에 저장하지 않음).
 */

import { useState } from "react";
import type { NotifyItem } from "../api/rest";

// ── NotifyTab ─────────────────────────────────────────────────────────────────

interface NotifyTabProps {
  items: NotifyItem[];
}

/** 이벤트 타입별 사람이 읽기 좋은 레이블 */
function typeLabel(item: NotifyItem): string {
  switch (item.type) {
    case "RUN_FINISHED":
      return `워크플로 종료 (${item.status ?? "??"})`;
    case "RUN_ERROR":
      return `워크플로 오류: ${item.error ?? ""}`;
    case "NODE_FAILED":
      return `노드 실패 [${item.reason ?? "?"}]: ${item.detail ?? ""}`;
    default:
      return item.type;
  }
}

/**
 * 알림 탭.
 * - 각 항목에 [읽음] 버튼 (로컬 상태만)
 * - 읽은 항목은 흐리게 표시
 */
export function NotifyTab({ items }: NotifyTabProps) {
  // 읽은 항목 seq 집합 (로컬 상태)
  const [readSeqs, setReadSeqs] = useState<Set<number>>(new Set());

  /** 읽음 처리 */
  function markRead(seq: number) {
    setReadSeqs((prev) => new Set([...prev, seq]));
  }

  if (items.length === 0) {
    return (
      <p
        data-testid="notify-empty"
        style={{ color: "#999", fontStyle: "italic", margin: "8px 0" }}
      >
        알림이 없습니다.
      </p>
    );
  }

  return (
    <ul
      data-testid="notify-list"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {items.map((item) => {
        const isRead = readSeqs.has(item.seq);

        return (
          <li
            key={item.seq}
            data-testid={`notify-item-${item.seq}`}
            style={{
              padding: "8px 12px",
              marginBottom: 6,
              border: "1px solid #ccc",
              borderRadius: 6,
              background: isRead ? "#f5f5f5" : "#f0f8ff",
              opacity: isRead ? 0.6 : 1,
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
          >
            {/* 알림 내용 */}
            <span
              data-testid={`notify-label-${item.seq}`}
              style={{ flex: 1, fontSize: 13, color: isRead ? "#999" : "#333" }}
            >
              {typeLabel(item)}
            </span>

            {/* 시각 */}
            <span style={{ fontSize: 11, color: "#aaa" }}>
              {item.ts ? new Date(item.ts).toLocaleTimeString("ko-KR") : ""}
            </span>

            {/* 읽음 버튼 */}
            {!isRead && (
              <button
                data-testid={`notify-read-btn-${item.seq}`}
                onClick={() => markRead(item.seq)}
                style={{
                  padding: "3px 10px",
                  fontSize: 12,
                  background: "#eee",
                  border: "1px solid #ccc",
                  borderRadius: 4,
                  cursor: "pointer",
                }}
              >
                읽음
              </button>
            )}

            {/* 읽음 완료 표시 */}
            {isRead && (
              <span
                data-testid={`notify-read-done-${item.seq}`}
                style={{ fontSize: 11, color: "#aaa" }}
              >
                ✓ 읽음
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
