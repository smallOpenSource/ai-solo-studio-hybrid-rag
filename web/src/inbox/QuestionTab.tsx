/**
 * QuestionTab — 질문 탭 (US-007).
 *
 * NODE_FAILED(question) 항목을 표시한다.
 * 답변 입력 자리(textarea)만 제공하고 전송은 미구현(후속 US).
 * 각 항목 분류·렌더에 집중한다.
 */

import { useState } from "react";
import type { QuestionItem } from "../api/rest";

// ── QuestionTab ───────────────────────────────────────────────────────────────

interface QuestionTabProps {
  items: QuestionItem[];
}

/**
 * 질문 탭.
 * - 질문 내용(detail) + 노드 ID 표시
 * - 답변 입력 textarea (전송은 후속 — placeholder)
 */
export function QuestionTab({ items }: QuestionTabProps) {
  // 각 항목의 답변 입력값 (seq → 문자열)
  const [answers, setAnswers] = useState<Record<number, string>>({});

  function setAnswer(seq: number, value: string) {
    setAnswers((prev) => ({ ...prev, [seq]: value }));
  }

  if (items.length === 0) {
    return (
      <p
        data-testid="question-empty"
        style={{ color: "#999", fontStyle: "italic", margin: "8px 0" }}
      >
        질문이 없습니다.
      </p>
    );
  }

  return (
    <ul
      data-testid="question-list"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {items.map((item) => (
        <li
          key={item.seq}
          data-testid={`question-item-${item.seq}`}
          style={{
            padding: "10px 12px",
            marginBottom: 8,
            border: "1px solid #b0c4de",
            borderRadius: 6,
            background: "#f0f5ff",
          }}
        >
          {/* 질문 내용 */}
          <div style={{ marginBottom: 6 }}>
            <span
              data-testid={`question-label-${item.seq}`}
              style={{ fontSize: 13, fontWeight: "bold", color: "#336" }}
            >
              [질문] {item.detail}
            </span>
            <span style={{ fontSize: 11, color: "#888", marginLeft: 8 }}>
              노드: {item.node_id}
            </span>
          </div>

          {/* 답변 입력 (placeholder — 전송은 후속 US) */}
          <textarea
            data-testid={`question-answer-${item.seq}`}
            value={answers[item.seq] ?? ""}
            onChange={(e) => setAnswer(item.seq, e.target.value)}
            placeholder="답변을 입력하세요 (전송은 추후 구현 예정)"
            rows={2}
            style={{
              width: "100%",
              fontSize: 13,
              padding: "4px 8px",
              borderRadius: 4,
              border: "1px solid #b0c4de",
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />

          {/* 전송 버튼 placeholder (미구현) */}
          <button
            data-testid={`question-send-btn-${item.seq}`}
            disabled
            style={{
              marginTop: 6,
              padding: "4px 12px",
              fontSize: 12,
              background: "#ccc",
              border: "none",
              borderRadius: 4,
              cursor: "not-allowed",
              color: "#888",
            }}
          >
            전송 (준비 중)
          </button>
        </li>
      ))}
    </ul>
  );
}
