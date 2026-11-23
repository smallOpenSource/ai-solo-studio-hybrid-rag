import { defineConfig } from "vite";

export default defineConfig({
  // 상대 에셋 경로(`./assets/...`) — 기본 base:'/'는 절대경로라 file://·서브경로 서빙 시 404(모바일
  // 웹 접속 실결함). 상대 base 는 루트·서브경로·file:// 어디서나 로드된다.
  base: "./",
  plugins: [],
  test: {
    globals: true,
    // hang 가드(H-07): 무한 대기 차단
    testTimeout: 5000,
    hookTimeout: 5000,
    // 순수 TS 스키마/시뮬 테스트 — node 환경 기본 OK
    include: ["src/**/*.{test,spec}.ts"],
    exclude: ["node_modules/**", "dist/**"],
  },
});
