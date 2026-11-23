/**
 * ReviewTab — 승인 대기 항목 탭 (US-007).
 *
 * 미해소 INTERRUPT_REQUESTED 목록을 표시한다.
 * 각 항목에 [승인]/[거부] 버튼이 있고 버튼 클릭 시
 * POST /api/inbox/{id}/approve|reject 를 호출한 후 목록을 갱신한다.
 *
 * 낙관적 갱신: 버튼 클릭 즉시 항목을 목록에서 제거하고
 * API 오류 시 목록을 복구한다.
 */

import { useState } from "react";
import type { ReviewItem } from "../api/rest";
import { approve, reject } from "../api/rest";

// ── 경과시간 포맷 ─────────────────────────────────────────────────────────────

/**
 * ISO-8601 타임스탬프를 받아 "X분 전" 형식의 경과시간 문자열을 반환한다.
 * 1시간 이상이면 "X시간 Y분 전", 1분 미만이면 "방금" 으로 표시.
 */
export function formatElapsed(ts: string, now = new Date()): string {
  const start = new Date(ts);
  if (isNaN(start.getTime())) return "";
  const diffMs = now.getTime() - start.getTime();
  if (diffMs < 0) return "방금";
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "방금";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}분 전`;
  const hours = Math.floor(diffMin / 60);
  const mins = diffMin % 60;
  return mins > 0 ? `${hours}시간 ${mins}분 전` : `${hours}시간 전`;
}

// ── ReviewTab ─────────────────────────────────────────────────────────────────

interface ReviewTabProps {
  /** 승인 대기 항목 목록 */
  items: ReviewItem[];
  /** 항목 목록 갱신 콜백 (낙관적 갱신 or 재조회) */
  onRefresh: () => void;
}

/**
 * 승인 대기 탭.
 * - 도구 이름·위험도·경과시간 표시
 * - [승인]/[거부] 버튼 → approve(id)/reject(id) 호출 → 낙관적 갱신
 */
export function ReviewTab({ items, onRefresh }: ReviewTabProps) {
  // 처리 중인 항목 id 집합 (중복 클릭 방지)
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set());
  // 오류 메시지 (항목 id → 오류 문자열)
  const [errors, setErrors] = useState<Record<number, string>>({});

  /** 승인 또는 거부 처리 공통 로직 */
  async function handleDecision(id: number, decision: "approve" | "reject") {
    // 이미 처리 중이면 무시 (중복 클릭 방지)
    if (pendingIds.has(id)) return;

    // 처리 중 상태로 전환
    setPendingIds((prev) => new Set([...prev, id]));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });

    try {
      if (decision === "approve") {
        await approve(id);
      } else {
        await reject(id);
      }
      // 성공 시 목록 재조회로 갱신
      onRefresh();
    } catch (err) {
      // 오류 시 에러 메시지 표시
      setErrors((prev) => ({
        ...prev,
        [id]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      // 처리 중 해제
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  // 항목 없음
  if (items.length === 0) {
    return (
      <p
        data-testid="review-empty"
        style={{ color: "#999", fontStyle: "italic", margin: "8px 0" }}
      >
        승인 대기 항목이 없습니다.
      </p>
    );
  }

  return (
    <ul
      data-testid="review-list"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {items.map((item) => {
        const isPending = pendingIds.has(item.id);
        const errMsg = errors[item.id];

        return (
          <li
            key={item.id}
            data-testid={`review-item-${item.id}`}
            style={{
              padding: "10px 12px",
              marginBottom: 8,
              border: "1px solid #e0c080",
              borderRadius: 6,
              background: "#fffbf0",
            }}
          >
            {/* 항목 정보 */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              {/* 도구 이름 */}
              <span
                data-testid={`review-tool-${item.id}`}
                style={{ fontWeight: "bold", fontSize: 14 }}
              >
                {item.tool}
              </span>

              {/* 위험도 */}
              <span
                data-testid={`review-risk-${item.id}`}
                style={{
                  fontSize: 12,
                  color: "#c05000",
                  background: "#ffe8cc",
                  padding: "2px 6px",
                  borderRadius: 4,
                }}
              >
                {item.risk}
              </span>

              {/* 노드 ID */}
              <span style={{ fontSize: 12, color: "#666" }}>
                노드: {item.node_id}
              </span>

              {/* 경과시간 */}
              <span
                data-testid={`review-elapsed-${item.id}`}
                style={{ fontSize: 12, color: "#888", marginLeft: "auto" }}
              >
                {formatElapsed(item.ts)}
              </span>
            </div>

            {/* 오류 메시지 */}
            {errMsg && (
              <p
                data-testid={`review-error-${item.id}`}
                style={{ color: "#c00", fontSize: 12, margin: "4px 0 0" }}
              >
                오류: {errMsg}
              </p>
            )}

            {/* 액션 버튼 */}
            <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
              <button
                data-testid={`approve-btn-${item.id}`}
                disabled={isPending}
                onClick={() => handleDecision(item.id, "approve")}
                style={{
                  padding: "5px 14px",
                  background: isPending ? "#aaa" : "#2a7",
                  color: "#fff",
                  border: "none",
                  borderRadius: 4,
                  cursor: isPending ? "not-allowed" : "pointer",
                  fontSize: 13,
                }}
              >
                {isPending ? "처리 중…" : "승인"}
              </button>

              <button
                data-testid={`reject-btn-${item.id}`}
                disabled={isPending}
                onClick={() => handleDecision(item.id, "reject")}
                style={{
                  padding: "5px 14px",
                  background: isPending ? "#aaa" : "#c44",
                  color: "#fff",
                  border: "none",
                  borderRadius: 4,
                  cursor: isPending ? "not-allowed" : "pointer",
                  fontSize: 13,
                }}
              >
                {isPending ? "처리 중…" : "거부"}
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
