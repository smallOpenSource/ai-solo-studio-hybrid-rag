import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // 개발 서버: 백엔드 SSE/API 요청을 FastAPI(8000)로 프록시
  server: {
    proxy: {
      "/events": "http://localhost:8000",
      "/api": "http://localhost:8000",
    },
  },
  test: {
    // Vitest 환경 설정
    environment: "jsdom",
    globals: true,
    setupFiles: [],
    // hang 가드(H-07): 테스트/훅 타임아웃을 명시한다. 값은 vitest 기본(5000ms)과
    // 동일하나, 의도를 코드로 못 박아 SSE/타이머 mock이 fake timer 없이 실시간
    // 대기로 매달리는 것을 상한으로 차단한다(브라우저 테스트는 ulimit/OS timeout
    // 가드 밖 — node 프로세스라 여기서 명시적 상한이 유일한 방어선).
    testTimeout: 5000,
    hookTimeout: 5000,
    // Vitest는 src/ 단위 테스트만 수집한다. tests/ 의 Playwright e2e
    // (*.spec.ts, US-008 트랙 A)는 Playwright 러너 전용이므로 제외한다
    // (그러지 않으면 Vitest가 playwright의 test()를 잘못 수집해 실패).
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules/**", "dist/**", "tests/**"],
  },
});
