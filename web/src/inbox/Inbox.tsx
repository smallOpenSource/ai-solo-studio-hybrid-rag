/**
 * Inbox — Agent Inbox 메인 컴포넌트 (US-007).
 *
 * Review / Notify / Question 3탭으로 구성.
 * GET /api/inbox 를 폴링(5초 간격)해 목록을 갱신한다.
 *
 * 탭 구조:
 *   Review   — 승인 대기 (INTERRUPT_REQUESTED 미해소)
 *   Notify   — 알림 (NODE_FAILED escalation|gating, RUN_FINISHED, RUN_ERROR)
 *   Question — 질문 (NODE_FAILED question)
 */

import { useCallback, useEffect, useState } from "react";
import type { InboxData, ReviewItem, NotifyItem, QuestionItem } from "../api/rest";
import { fetchInbox } from "../api/rest";
import { ReviewTab } from "./ReviewTab";
import { NotifyTab } from "./NotifyTab";
import { QuestionTab } from "./QuestionTab";

// ── 탭 타입 ───────────────────────────────────────────────────────────────────
type TabId = "review" | "notify" | "question";

const TAB_LABELS: Record<TabId, string> = {
  review: "Review",
  notify: "Notify",
  question: "Question",
};

// ── Inbox ─────────────────────────────────────────────────────────────────────

interface InboxProps {
  /** 폴링 간격(ms). 기본 5000. 테스트에서는 0으로 끄거나 mock 사용. */
  pollIntervalMs?: number;
}

/**
 * Agent Inbox 메인 컴포넌트.
 * - 마운트 시 /api/inbox 호출로 초기 데이터 로드
 * - pollIntervalMs(기본 5s) 주기로 재조회 (Review 승인 후 갱신 포함)
 * - ReviewTab의 onRefresh 콜백으로 즉시 재조회 가능
 */
export function Inbox({ pollIntervalMs = 5000 }: InboxProps) {
  const [activeTab, setActiveTab] = useState<TabId>("review");
  const [data, setData] = useState<InboxData>({
    review: [],
    notify: [],
    question: [],
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** /api/inbox 재조회 함수 (ReviewTab onRefresh 콜백으로도 사용) */
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchInbox();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // 마운트 시 초기 로드 + 폴링
  useEffect(() => {
    void refresh();

    if (pollIntervalMs <= 0) return; // 폴링 비활성화 (테스트용)

    const timerId = setInterval(() => {
      void refresh();
    }, pollIntervalMs);

    return () => clearInterval(timerId);
  }, [refresh, pollIntervalMs]);

  // 탭별 카운트
  const counts: Record<TabId, number> = {
    review: data.review.length,
    notify: data.notify.length,
    question: data.question.length,
  };

  return (
    <div
      data-testid="inbox"
      style={{
        border: "1px solid #ccc",
        borderRadius: 8,
        overflow: "hidden",
        background: "#fff",
      }}
    >
      {/* 헤더 */}
      <div
        style={{
          padding: "8px 12px",
          background: "#f7f7f7",
          borderBottom: "1px solid #ddd",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <h2 style={{ fontSize: 15, margin: 0 }}>Agent Inbox</h2>
        {loading && (
          <span style={{ fontSize: 12, color: "#999" }}>갱신 중…</span>
        )}
        {error && (
          <span
            data-testid="inbox-error"
            style={{ fontSize: 12, color: "#c00" }}
          >
            오류: {error}
          </span>
        )}
      </div>

      {/* 탭 헤더 */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid #ddd",
          background: "#fafafa",
        }}
      >
        {(["review", "notify", "question"] as TabId[]).map((tab) => (
          <button
            key={tab}
            data-testid={`tab-${tab}`}
            onClick={() => setActiveTab(tab)}
            style={{
              flex: 1,
              padding: "8px 0",
              border: "none",
              borderBottom:
                activeTab === tab ? "2px solid #2a7" : "2px solid transparent",
              background: "transparent",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: activeTab === tab ? "bold" : "normal",
              color: activeTab === tab ? "#2a7" : "#555",
            }}
          >
            {TAB_LABELS[tab]}
            {/* 카운트 뱃지 */}
            {counts[tab] > 0 && (
              <span
                data-testid={`tab-count-${tab}`}
                style={{
                  marginLeft: 5,
                  background: tab === "review" ? "#e84" : "#888",
                  color: "#fff",
                  borderRadius: 10,
                  padding: "1px 6px",
                  fontSize: 11,
                }}
              >
                {counts[tab]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* 탭 콘텐츠 */}
      <div style={{ padding: "12px" }}>
        {activeTab === "review" && (
          <ReviewTab items={data.review as ReviewItem[]} onRefresh={refresh} />
        )}
        {activeTab === "notify" && (
          <NotifyTab items={data.notify as NotifyItem[]} />
        )}
        {activeTab === "question" && (
          <QuestionTab items={data.question as QuestionItem[]} />
        )}
      </div>
    </div>
  );
}
