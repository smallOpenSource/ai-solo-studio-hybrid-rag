import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { AuthGate } from "./auth/AuthGate.tsx";

// 앱 진입점 — React StrictMode로 감싸 개발 중 잠재적 문제를 조기 발견.
// ★AuthGate로 App을 감싸 로그인 게이트를 둔다(인증 후에만 App 마운트 → SSE/WS 연결).
const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("root 엘리먼트를 찾을 수 없습니다.");
}

createRoot(rootEl).render(
  <StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </StrictMode>
);
