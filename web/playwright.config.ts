import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright 설정 — US-008(T8) 트랙 A(브라우저 e2e, 실서버).
 *
 * 하는 일:
 * - web/tests/*.spec.ts 를 chromium(헤드리스)으로 돌린다.
 * - webServer: scripts/serve.sh 로 FastAPI(uvicorn --workers 1)를 띄운다 —
 *   web/dist 정적(프론트) + API(SSE·inbox·run)를 단일 포트(endpoint.yaml web.port=8800)로.
 *   프론트는 빌드 산출(web/dist)을 서빙하므로 Vite dev 프록시가 아니라 단일 출처(CORS 0).
 * - baseURL = http://127.0.0.1:8800 (endpoint.yaml web 블록과 일치).
 *
 * ★단일 워커(--workers 1)는 web_server/__main__.py가 코드로 못 박는다(M2, 수용 20).
 *
 * ⚠️ 실서버 접촉(slow 등가) — 오프라인 게이트(npm run build·test, pytest -m "not slow")
 *   green 후에만. 실서버 LLM 호출에 노드당 수 초가 걸리므로 타임아웃을 넉넉히 둔다.
 *   브라우저 미설치 환경이면 이 트랙은 건너뛰고 트랙 B(tests/test_web_e2e.py, slow)로 대체.
 */
export default defineConfig({
  testDir: "./tests",
  // 실서버 워크플로(4노드 + 승인 재개)는 길다 — 테스트당 5분 상한.
  // ★H-11: 이 5분(300_000ms)은 **실서버 풀런 기준**이라 정당하다(값 유지).
  //   - 풀런 e2e는 N3에서 suspend된 뒤, 테스트 내부의 approve POST(자동승인)로
  //     완주한다 — 사람 개입 0. 자동승인 경로가 깨지면 매 e2e가 5분을 낭비하므로
  //     트랙 A spec은 반드시 자동 approve를 보장해야 한다.
  //   - 오프라인 게이트(vitest + pytest -m "not slow")가 green인 뒤에만 이 e2e를
  //     실행한다(아래 webServer 주석·파일 상단 ⚠️ 참조). 순서를 지켜 실서버 비용을
  //     불필요하게 태우지 않는다.
  timeout: 300_000,
  expect: { timeout: 120_000 },
  // 동시성 1 전제(전역 락·단일 실행) — e2e도 순차로(워커 1).
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: "http://127.0.0.1:8800",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // headless 환경 — 헤드리스 chromium.
    headless: true,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    // 루트 scripts/serve.sh — web/dist 없으면 빌드 후 uvicorn --workers 1 기동.
    command: "bash ../scripts/serve.sh",
    url: "http://127.0.0.1:8800",
    // 빌드+서버 기동에 시간이 걸릴 수 있다(빌드 1회 + conda run).
    timeout: 180_000,
    // 이미 떠 있는 서버가 있으면 재사용(로컬 반복 실행 편의).
    reuseExistingServer: true,
    stdout: "pipe",
    stderr: "pipe",
  },
});
