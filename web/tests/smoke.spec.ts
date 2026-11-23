import { test, expect } from "@playwright/test";

/**
 * US-008(T8) 트랙 A — smoke(브라우저, 실서버). 풀 게이트(gate.spec.ts) 전제.
 *
 * 증명(수용 15): 브라우저에서 명령줄 0회로
 *   ① 페이지 로드 → 정적 서빙(web/dist)이 단일 포트로 뜸(FastAPI가 프론트+API 한 포트).
 *   ② SSE 연결(🟢 연결됨) — EventSource가 /events/stream에 붙음(실시간 채널 살아 있음).
 *   ③ Inbox(GET /api/inbox)·정적 이미지(/work/1.png)가 같은 포트로 응답.
 *
 * ★smoke는 워크플로를 시작하지 않는다(설계 결정):
 *   브라우저 [시작] 버튼은 crm_notify 풀런(4노드)을 띄워 N3에서 suspend → 전역 락을
 *   계속 점유한다(동시성 1). smoke가 그걸 시작하면 풀 게이트(gate.spec.ts)가 409로
 *   막힌다. 따라서 "1클릭 시작→완료"의 비서스펜드 smoke는 트랙 B(run_smoke 직접 호출,
 *   test_web_e2e.py::TestRealServerSmokeHttp)가 실서버로 실증하고, 트랙 A smoke는
 *   "브라우저↔단일 포트 서버 통합(페이지·SSE·API·정적)"만 본다. 워크플로 시작·승인·
 *   재개는 오직 gate.spec.ts에서만 — 락을 점유하는 단일 사이클로.
 */

test("smoke: 페이지 로드 + 단일 포트 서빙 + SSE 연결 (브라우저, 실서버)", async ({
  page,
  request,
}) => {
  // ① 페이지 로드(정적 서빙 web/dist) — FastAPI 단일 포트로 프론트가 뜸.
  await page.goto("/");
  await expect(page.getByText("one_man_corp 직원 상태창")).toBeVisible();

  // ② SSE 연결(🟢 연결됨) — EventSource가 /events/stream에 붙음.
  await expect(page.getByText(/🟢 연결됨/)).toBeVisible({ timeout: 30_000 });

  // 상태창·Inbox UI가 같은 페이지에 렌더됨(통합 골격).
  await expect(page.getByTestId("text-status-board")).toBeVisible();
  await expect(page.getByTestId("inbox")).toBeVisible();
  await expect(page.getByTestId("start-run-btn")).toBeVisible();

  // ③ 같은 포트로 API·정적이 응답 — GET /api/inbox(3분류) + /work/1.png(이미지).
  const inbox = await request.get("/api/inbox");
  expect(inbox.ok()).toBeTruthy();
  const data = await inbox.json();
  expect(data).toHaveProperty("review");
  expect(data).toHaveProperty("notify");
  expect(data).toHaveProperty("question");

  const img = await request.get("/work/1.png");
  expect(img.ok()).toBeTruthy(); // 직원 이미지 플레이스홀더 정적 서빙(이미지 토글용).
});
