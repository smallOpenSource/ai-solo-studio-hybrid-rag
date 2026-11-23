import { test, expect } from "@playwright/test";

/**
 * US-008(T8) 트랙 A — ★완료 게이트(브라우저 1사이클, 실서버 gemma).
 *
 * 증명(계획 §0 완료 게이트 · 수용 16): 브라우저에서 명령줄 0회로
 *   ① 페이지 로드 → SSE 연결(🟢)
 *   ② [워크플로 시작] 클릭 → 상태창에 SSE 이벤트 실시간 표시(▶ 워크플로 시작 …)
 *   ③ N3 email_send에서 멈추면 Inbox Review 탭에 승인 요청(경과시간) 노출
 *   ④ [승인] 버튼 클릭 (→ POST /api/inbox/{id}/approve, resume=False)
 *   ⑤ 폴링 루프 재개 → 완료(✓ 성공)가 상태창·Inbox(Notify)에 반영
 * 전 과정 브라우저 UI 클릭만 — CLI 0회. SSE·승인이 블로킹 0(데드락 미발생).
 *
 * data-testid 계약(프론트 US-006/007):
 *   start-run-btn / text-status-board / tab-review / tab-count-review /
 *   review-item-{id} / approve-btn-{id} / review-elapsed-{id} / tab-notify
 */

test("완료 게이트: 시작→실시간 관찰→Inbox 승인→재개 완료 (브라우저, 실서버)", async ({
  page,
}) => {
  // ① 페이지 로드 + SSE 연결.
  await page.goto("/");
  await expect(page.getByText("one_man_corp 직원 상태창")).toBeVisible();
  // 연결 상태 텍스트(🟢 연결됨)가 뜰 때까지 — SSE EventSource open.
  await expect(page.getByText(/🟢 연결됨/)).toBeVisible({ timeout: 30_000 });

  const board = page.getByTestId("text-status-board");
  await expect(board).toBeVisible();

  // SSE는 재연결 시 과거 이벤트도 재생하므로(read_since(0)) 보드가 비어 있다고
  // 가정하지 않는다. 새 시작/완료가 "한 줄 더" 늘어나는 것으로 본다(history-robust).
  const startedBefore = await board.getByText(/▶ 워크플로 시작/).count();
  const successBefore = await board.getByText(/✓ 성공/).count();

  // ② 워크플로 시작 — 실서버 crm_notify(4노드, N3 게이팅).
  await page.getByTestId("start-run-btn").click();

  // 상태창에 SSE 이벤트가 실시간으로 흐른다 — RUN_STARTED가 새로 추가("▶ 워크플로 시작").
  await expect(board.getByText(/▶ 워크플로 시작/)).toHaveCount(startedBefore + 1, {
    timeout: 60_000,
  });

  // ③ N3에서 멈춰 Review 탭에 승인 요청이 뜬다 — 탭 카운트 뱃지로 대기.
  await expect(page.getByTestId("tab-count-review")).toBeVisible({
    timeout: 180_000,
  });
  // Review 탭으로 전환(기본 탭이지만 명시적으로).
  await page.getByTestId("tab-review").click();

  // 승인 버튼(첫 항목) — data-testid="approve-btn-{interrupt_id}" 패턴.
  const approveBtn = page.locator('[data-testid^="approve-btn-"]').first();
  await expect(approveBtn).toBeVisible({ timeout: 30_000 });

  // 경과시간이 함께 노출(수용 16 — "경과시간과 함께").
  const elapsed = page.locator('[data-testid^="review-elapsed-"]').first();
  await expect(elapsed).toBeVisible();

  // ④ [승인] 클릭 → POST /api/inbox/{id}/approve(resume=False).
  await approveBtn.click();

  // ⑤ 재개 완료가 상태창에 반영 — RUN_FINISHED(success)가 새로 추가("✓ 성공").
  await expect(board.getByText(/✓ 성공/)).toHaveCount(successBefore + 1, {
    timeout: 180_000,
  });

  // 완료가 Inbox에도 반영 — Notify 탭에 RUN_FINISHED(워크플로 종료) 노출.
  await page.getByTestId("tab-notify").click();
  await expect(page.getByText(/워크플로 종료|RUN_FINISHED/).first()).toBeVisible({
    timeout: 30_000,
  });

  // 승인된 항목은 Review에서 사라짐(해소됨).
  await page.getByTestId("tab-review").click();
  await expect(
    page.locator('[data-testid^="approve-btn-"]')
  ).toHaveCount(0, { timeout: 30_000 });
});
