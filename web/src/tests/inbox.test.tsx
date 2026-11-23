/**
 * Agent Inbox UI 단위 테스트 (US-007, 수용 기준 12).
 *
 * 검증 항목:
 *   1. Inbox 3탭 분류 렌더 (mock /api/inbox 응답)
 *   2. Review [승인] 버튼 → POST /api/inbox/{id}/approve 정확 호출
 *   3. Review [거부] 버튼 → POST /api/inbox/{id}/reject 정확 호출
 *   4. 호출 후 목록 갱신 (onRefresh 호출 확인)
 *   5. 경과시간(formatElapsed) 표시
 *   6. NotifyTab 읽음 처리
 *   7. QuestionTab 답변 입력 자리 렌더
 *   8. 탭 카운트 뱃지
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Inbox } from "../inbox/Inbox";
import { ReviewTab, formatElapsed } from "../inbox/ReviewTab";
import { NotifyTab } from "../inbox/NotifyTab";
import { QuestionTab } from "../inbox/QuestionTab";
import type { ReviewItem, NotifyItem, QuestionItem } from "../api/rest";

// ── 픽스처 ───────────────────────────────────────────────────────────────────

const REVIEW_ITEM_1: ReviewItem = {
  id: 1,
  node_id: "N3",
  tool: "email_send",
  risk: "needs-approval",
  ts: "2026-06-08T10:00:00Z",
  payload: { task_id: 1 },
};

const REVIEW_ITEM_2: ReviewItem = {
  id: 2,
  node_id: "N4",
  tool: "db_write",
  risk: "needs-approval",
  ts: "2026-06-08T09:30:00Z",
};

const NOTIFY_ITEM_1: NotifyItem = {
  seq: 10,
  type: "RUN_FINISHED",
  run_id: "run-1",
  status: "success",
  ts: "2026-06-08T10:05:00Z",
};

const NOTIFY_ITEM_FAILED: NotifyItem = {
  seq: 11,
  type: "NODE_FAILED",
  run_id: "run-1",
  node_id: "N2",
  reason: "escalation",
  detail: "최대 재시도 초과",
  ts: "2026-06-08T10:02:00Z",
};

const QUESTION_ITEM_1: QuestionItem = {
  seq: 20,
  type: "NODE_FAILED",
  run_id: "run-1",
  node_id: "N5",
  reason: "question",
  detail: "고객 이메일 주소를 확인할 수 없습니다. 직접 입력해 주세요.",
  ts: "2026-06-08T10:03:00Z",
};

/** mock /api/inbox 응답 */
const MOCK_INBOX_DATA = {
  review: [REVIEW_ITEM_1, REVIEW_ITEM_2],
  notify: [NOTIFY_ITEM_1, NOTIFY_ITEM_FAILED],
  question: [QUESTION_ITEM_1],
};

/** 빈 Inbox 응답 */
const EMPTY_INBOX_DATA = { review: [], notify: [], question: [] };

// ── fetch mock 설정 ───────────────────────────────────────────────────────────

// 각 테스트 전에 fetch mock을 초기화한다
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

/** fetch를 성공 응답으로 mock하는 헬퍼 */
function mockFetchSuccess(data: unknown) {
  const mockFetch = vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(data),
    status: 200,
    statusText: "OK",
  });
  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
}

/** fetch를 GET/POST별로 다르게 mock하는 헬퍼 */
function mockFetchByMethod(getData: unknown, postData: unknown = { ok: true }) {
  const mockFetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(postData),
        status: 200,
        statusText: "OK",
      });
    }
    // GET /api/inbox
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(getData),
      status: 200,
      statusText: "OK",
    });
  });
  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
}

// ── formatElapsed 단위 테스트 ─────────────────────────────────────────────────

describe("formatElapsed — 경과시간 포맷", () => {
  it("30초 전 → '방금'", () => {
    const now = new Date("2026-06-08T10:00:30Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("방금");
  });

  it("59초 전 → '방금'", () => {
    const now = new Date("2026-06-08T10:00:59Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("방금");
  });

  it("1분 전 → '1분 전'", () => {
    const now = new Date("2026-06-08T10:01:00Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("1분 전");
  });

  it("45분 전 → '45분 전'", () => {
    const now = new Date("2026-06-08T10:45:00Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("45분 전");
  });

  it("1시간 전 → '1시간 전'", () => {
    const now = new Date("2026-06-08T11:00:00Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("1시간 전");
  });

  it("1시간 30분 전 → '1시간 30분 전'", () => {
    const now = new Date("2026-06-08T11:30:00Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("1시간 30분 전");
  });

  it("2시간 전 → '2시간 전'", () => {
    const now = new Date("2026-06-08T12:00:00Z");
    expect(formatElapsed("2026-06-08T10:00:00Z", now)).toBe("2시간 전");
  });

  it("빈 문자열 ts → ''", () => {
    expect(formatElapsed("", new Date())).toBe("");
  });

  it("잘못된 ts → ''", () => {
    expect(formatElapsed("not-a-date", new Date())).toBe("");
  });
});

// ── ReviewTab 단위 테스트 ────────────────────────────────────────────────────

describe("ReviewTab — 렌더 + 승인/거부 버튼", () => {
  it("항목이 없으면 '승인 대기 항목이 없습니다' 메시지를 표시한다", () => {
    render(<ReviewTab items={[]} onRefresh={() => {}} />);
    expect(screen.getByTestId("review-empty")).toBeTruthy();
    expect(screen.getByText(/승인 대기 항목이 없습니다/)).toBeTruthy();
  });

  it("항목 목록(도구·위험도·노드)을 렌더한다", () => {
    render(<ReviewTab items={[REVIEW_ITEM_1]} onRefresh={() => {}} />);
    expect(screen.getByTestId(`review-tool-${REVIEW_ITEM_1.id}`)).toBeTruthy();
    expect(screen.getByText("email_send")).toBeTruthy();
    expect(screen.getByText("needs-approval")).toBeTruthy();
  });

  it("경과시간이 표시된다", () => {
    render(<ReviewTab items={[REVIEW_ITEM_1]} onRefresh={() => {}} />);
    const elapsed = screen.getByTestId(`review-elapsed-${REVIEW_ITEM_1.id}`);
    // 내용이 비어 있지 않아야 함 (실제 값은 실행 시각에 따라 다름)
    expect(elapsed).toBeTruthy();
    // ts가 과거라 "방금"이거나 "X분 전" 형식이어야 함
    expect(elapsed.textContent).toMatch(/방금|분 전|시간/);
  });

  it("[승인] 버튼이 POST /api/inbox/{id}/approve 를 정확히 호출한다", async () => {
    const mockFetch = mockFetchByMethod(EMPTY_INBOX_DATA);
    const onRefresh = vi.fn();

    render(<ReviewTab items={[REVIEW_ITEM_1]} onRefresh={onRefresh} />);

    const approveBtn = screen.getByTestId(`approve-btn-${REVIEW_ITEM_1.id}`);
    fireEvent.click(approveBtn);

    // POST 호출 확인
    await waitFor(() => {
      const calls = mockFetch.mock.calls;
      const postCall = calls.find(
        (c) =>
          c[0] === `/api/inbox/${REVIEW_ITEM_1.id}/approve` &&
          (c[1] as RequestInit)?.method === "POST"
      );
      expect(postCall).toBeTruthy();
    });
  });

  it("[거부] 버튼이 POST /api/inbox/{id}/reject 를 정확히 호출한다", async () => {
    const mockFetch = mockFetchByMethod(EMPTY_INBOX_DATA);
    const onRefresh = vi.fn();

    render(<ReviewTab items={[REVIEW_ITEM_1]} onRefresh={onRefresh} />);

    const rejectBtn = screen.getByTestId(`reject-btn-${REVIEW_ITEM_1.id}`);
    fireEvent.click(rejectBtn);

    await waitFor(() => {
      const calls = mockFetch.mock.calls;
      const postCall = calls.find(
        (c) =>
          c[0] === `/api/inbox/${REVIEW_ITEM_1.id}/reject` &&
          (c[1] as RequestInit)?.method === "POST"
      );
      expect(postCall).toBeTruthy();
    });
  });

  it("승인 성공 후 onRefresh 콜백이 호출된다 (목록 갱신)", async () => {
    mockFetchByMethod(EMPTY_INBOX_DATA);
    const onRefresh = vi.fn();

    render(<ReviewTab items={[REVIEW_ITEM_1]} onRefresh={onRefresh} />);

    fireEvent.click(screen.getByTestId(`approve-btn-${REVIEW_ITEM_1.id}`));

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledTimes(1);
    });
  });

  it("거부 성공 후 onRefresh 콜백이 호출된다 (목록 갱신)", async () => {
    mockFetchByMethod(EMPTY_INBOX_DATA);
    const onRefresh = vi.fn();

    render(<ReviewTab items={[REVIEW_ITEM_1]} onRefresh={onRefresh} />);

    fireEvent.click(screen.getByTestId(`reject-btn-${REVIEW_ITEM_1.id}`));

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledTimes(1);
    });
  });

  it("여러 항목을 모두 렌더한다", () => {
    render(
      <ReviewTab items={[REVIEW_ITEM_1, REVIEW_ITEM_2]} onRefresh={() => {}} />
    );
    expect(screen.getByTestId(`review-item-${REVIEW_ITEM_1.id}`)).toBeTruthy();
    expect(screen.getByTestId(`review-item-${REVIEW_ITEM_2.id}`)).toBeTruthy();
  });

  it("approve/reject 버튼 URL에 올바른 id가 포함된다 (id=2)", async () => {
    const mockFetch = mockFetchByMethod(EMPTY_INBOX_DATA);
    const onRefresh = vi.fn();

    render(<ReviewTab items={[REVIEW_ITEM_2]} onRefresh={onRefresh} />);

    fireEvent.click(screen.getByTestId(`approve-btn-${REVIEW_ITEM_2.id}`));

    await waitFor(() => {
      const calls = mockFetch.mock.calls;
      const postCall = calls.find(
        (c) =>
          c[0] === `/api/inbox/${REVIEW_ITEM_2.id}/approve` &&
          (c[1] as RequestInit)?.method === "POST"
      );
      expect(postCall).toBeTruthy();
    });
  });
});

// ── NotifyTab 단위 테스트 ────────────────────────────────────────────────────

describe("NotifyTab — 렌더 + 읽음 처리", () => {
  it("항목 없으면 '알림이 없습니다' 메시지 표시", () => {
    render(<NotifyTab items={[]} />);
    expect(screen.getByTestId("notify-empty")).toBeTruthy();
  });

  it("RUN_FINISHED 항목을 렌더한다", () => {
    render(<NotifyTab items={[NOTIFY_ITEM_1]} />);
    expect(screen.getByTestId(`notify-label-${NOTIFY_ITEM_1.seq}`)).toBeTruthy();
    expect(screen.getByText(/워크플로 종료/)).toBeTruthy();
  });

  it("NODE_FAILED(escalation) 항목을 렌더한다", () => {
    render(<NotifyTab items={[NOTIFY_ITEM_FAILED]} />);
    expect(screen.getByText(/노드 실패/)).toBeTruthy();
    expect(screen.getByText(/escalation/)).toBeTruthy();
    expect(screen.getByText(/최대 재시도 초과/)).toBeTruthy();
  });

  it("[읽음] 버튼 클릭 후 읽음 완료 표시가 나타난다", () => {
    render(<NotifyTab items={[NOTIFY_ITEM_1]} />);

    const readBtn = screen.getByTestId(`notify-read-btn-${NOTIFY_ITEM_1.seq}`);
    fireEvent.click(readBtn);

    expect(screen.getByTestId(`notify-read-done-${NOTIFY_ITEM_1.seq}`)).toBeTruthy();
    // 읽음 버튼은 사라져야 함
    expect(screen.queryByTestId(`notify-read-btn-${NOTIFY_ITEM_1.seq}`)).toBeNull();
  });
});

// ── QuestionTab 단위 테스트 ──────────────────────────────────────────────────

describe("QuestionTab — 렌더 + 답변 입력", () => {
  it("항목 없으면 '질문이 없습니다' 메시지 표시", () => {
    render(<QuestionTab items={[]} />);
    expect(screen.getByTestId("question-empty")).toBeTruthy();
  });

  it("질문 내용과 노드 ID를 렌더한다", () => {
    render(<QuestionTab items={[QUESTION_ITEM_1]} />);
    expect(screen.getByTestId(`question-label-${QUESTION_ITEM_1.seq}`)).toBeTruthy();
    expect(screen.getByText(/고객 이메일 주소를 확인할 수 없습니다/)).toBeTruthy();
  });

  it("답변 입력 textarea가 렌더된다", () => {
    render(<QuestionTab items={[QUESTION_ITEM_1]} />);
    const textarea = screen.getByTestId(`question-answer-${QUESTION_ITEM_1.seq}`);
    expect(textarea).toBeTruthy();
  });

  it("textarea에 답변을 입력할 수 있다", () => {
    render(<QuestionTab items={[QUESTION_ITEM_1]} />);
    const textarea = screen.getByTestId(
      `question-answer-${QUESTION_ITEM_1.seq}`
    ) as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "test@example.com" } });
    expect(textarea.value).toBe("test@example.com");
  });

  it("전송 버튼은 disabled 상태이다 (후속 구현 placeholder)", () => {
    render(<QuestionTab items={[QUESTION_ITEM_1]} />);
    const sendBtn = screen.getByTestId(
      `question-send-btn-${QUESTION_ITEM_1.seq}`
    ) as HTMLButtonElement;
    expect(sendBtn.disabled).toBe(true);
  });
});

// ── Inbox 통합 테스트 (3탭 분류 렌더) ───────────────────────────────────────

describe("Inbox — 3탭 분류 렌더 (mock fetch)", () => {
  it("초기 렌더 시 GET /api/inbox 를 호출한다", async () => {
    const mockFetch = mockFetchSuccess(MOCK_INBOX_DATA);

    // pollIntervalMs=0 → 폴링 비활성화 (초기 호출만)
    render(<Inbox pollIntervalMs={0} />);

    await waitFor(() => {
      // GET /api/inbox 호출 확인
      const calls = mockFetch.mock.calls;
      const getCall = calls.find(
        // GET 식별은 method 부재로 판정한다(다른 테스트와 동일 기준). fetch에
        // hang 가드용 { signal } 옵션이 붙어도 method는 없으므로 GET 식별 무변.
        (c) => c[0] === "/api/inbox" && (c[1] as RequestInit)?.method === undefined
      );
      expect(getCall).toBeTruthy();
    });
  });

  it("3탭(Review/Notify/Question) 탭 버튼이 모두 렌더된다", async () => {
    mockFetchSuccess(MOCK_INBOX_DATA);

    render(<Inbox pollIntervalMs={0} />);

    await waitFor(() => {
      expect(screen.getByTestId("tab-review")).toBeTruthy();
      expect(screen.getByTestId("tab-notify")).toBeTruthy();
      expect(screen.getByTestId("tab-question")).toBeTruthy();
    });
  });

  it("Review 탭에 카운트 뱃지가 표시된다", async () => {
    mockFetchSuccess(MOCK_INBOX_DATA);

    render(<Inbox pollIntervalMs={0} />);

    await waitFor(() => {
      // MOCK_INBOX_DATA.review = 2개
      const badge = screen.getByTestId("tab-count-review");
      expect(badge.textContent).toBe("2");
    });
  });

  it("기본 탭은 Review이고 Review 항목이 렌더된다", async () => {
    mockFetchSuccess(MOCK_INBOX_DATA);

    render(<Inbox pollIntervalMs={0} />);

    await waitFor(() => {
      // review-list가 보여야 함
      expect(screen.getByTestId("review-list")).toBeTruthy();
      expect(screen.getByText("email_send")).toBeTruthy();
    });
  });

  it("Notify 탭 클릭 시 Notify 항목이 렌더된다", async () => {
    mockFetchSuccess(MOCK_INBOX_DATA);

    render(<Inbox pollIntervalMs={0} />);

    // Notify 탭으로 전환
    await waitFor(() => screen.getByTestId("tab-notify"));
    fireEvent.click(screen.getByTestId("tab-notify"));

    await waitFor(() => {
      expect(screen.getByTestId("notify-list")).toBeTruthy();
      expect(screen.getByText(/워크플로 종료/)).toBeTruthy();
    });
  });

  it("Question 탭 클릭 시 Question 항목이 렌더된다", async () => {
    mockFetchSuccess(MOCK_INBOX_DATA);

    render(<Inbox pollIntervalMs={0} />);

    await waitFor(() => screen.getByTestId("tab-question"));
    fireEvent.click(screen.getByTestId("tab-question"));

    await waitFor(() => {
      expect(screen.getByTestId("question-list")).toBeTruthy();
      expect(screen.getByText(/고객 이메일 주소를 확인할 수 없습니다/)).toBeTruthy();
    });
  });

  it("빈 응답이면 각 탭에 빈 메시지가 표시된다", async () => {
    mockFetchSuccess(EMPTY_INBOX_DATA);

    render(<Inbox pollIntervalMs={0} />);

    // Review 탭 (기본) — 빈 메시지
    await waitFor(() => {
      expect(screen.getByTestId("review-empty")).toBeTruthy();
    });

    // Notify 탭으로 전환
    fireEvent.click(screen.getByTestId("tab-notify"));
    await waitFor(() => {
      expect(screen.getByTestId("notify-empty")).toBeTruthy();
    });

    // Question 탭으로 전환
    fireEvent.click(screen.getByTestId("tab-question"));
    await waitFor(() => {
      expect(screen.getByTestId("question-empty")).toBeTruthy();
    });
  });
});

// ── Review 승인/거부 → /api/inbox/{id}/approve|reject 정확 호출 (통합) ───────

describe("Inbox ReviewTab — 승인/거부 API 호출 + 재조회 통합", () => {
  it("[승인] 클릭 → POST /api/inbox/1/approve 호출 후 GET /api/inbox 재조회", async () => {
    // GET은 초기 로드(MOCK_INBOX_DATA), POST 후 재조회는 빈 목록
    let callCount = 0;
    const mockFetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}), status: 200, statusText: "OK" });
      }
      // GET /api/inbox: 첫 번째는 MOCK_INBOX_DATA, 이후는 빈 목록
      callCount++;
      const data = callCount === 1 ? MOCK_INBOX_DATA : EMPTY_INBOX_DATA;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data), status: 200, statusText: "OK" });
    });
    vi.stubGlobal("fetch", mockFetch);

    render(<Inbox pollIntervalMs={0} />);

    // Review 탭 항목 로드 대기
    await waitFor(() => screen.getByTestId(`approve-btn-${REVIEW_ITEM_1.id}`));

    // [승인] 클릭
    fireEvent.click(screen.getByTestId(`approve-btn-${REVIEW_ITEM_1.id}`));

    // POST /api/inbox/1/approve 호출 확인
    await waitFor(() => {
      const postCall = mockFetch.mock.calls.find(
        (c) =>
          c[0] === `/api/inbox/${REVIEW_ITEM_1.id}/approve` &&
          (c[1] as RequestInit)?.method === "POST"
      );
      expect(postCall).toBeTruthy();
    });

    // POST 후 GET /api/inbox 재조회 확인 (callCount >= 2)
    await waitFor(() => {
      expect(callCount).toBeGreaterThanOrEqual(2);
    });

    // 재조회 후 Review 목록이 비워짐
    await waitFor(() => {
      expect(screen.getByTestId("review-empty")).toBeTruthy();
    });
  });

  it("[거부] 클릭 → POST /api/inbox/1/reject 호출 후 재조회", async () => {
    let callCount = 0;
    const mockFetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}), status: 200, statusText: "OK" });
      }
      callCount++;
      const data = callCount === 1 ? MOCK_INBOX_DATA : EMPTY_INBOX_DATA;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data), status: 200, statusText: "OK" });
    });
    vi.stubGlobal("fetch", mockFetch);

    render(<Inbox pollIntervalMs={0} />);

    await waitFor(() => screen.getByTestId(`reject-btn-${REVIEW_ITEM_1.id}`));
    fireEvent.click(screen.getByTestId(`reject-btn-${REVIEW_ITEM_1.id}`));

    await waitFor(() => {
      const postCall = mockFetch.mock.calls.find(
        (c) =>
          c[0] === `/api/inbox/${REVIEW_ITEM_1.id}/reject` &&
          (c[1] as RequestInit)?.method === "POST"
      );
      expect(postCall).toBeTruthy();
    });

    await waitFor(() => {
      expect(callCount).toBeGreaterThanOrEqual(2);
    });
  });
});
