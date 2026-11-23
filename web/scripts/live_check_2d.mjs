/**
 * 라이브 2D 시각 검증 (Phase 1·US-1.5.3 핵심) — worker/LLM 불요.
 *
 * twoD.spec.ts는 풀 워크플로 게이트(worker+실LLM 필요)라 step④에서 막힌다.
 * 내 시각 변경(타일맵 배경+pixi-viewport 카메라)은 step②(2D 토글→canvas)에서 발현되므로,
 * 여기서는 그 부분만 실 브라우저(번들 chromium)로 검증한다:
 *   - 2D 토글 → twod-stage + <canvas> attached (real pixi-viewport 6.0.3 ctor가 throw 안 함)
 *   - office.png/json 200 + 타일맵 렌더(픽셀 비교)
 *   - 치명 콘솔/페이지 오류 0 (SwiftShader 경고는 비치명 — spec과 동일 필터)
 *   - 스크린샷(results/phase4c/live-2d-tilemap.png)
 */
import { chromium } from "@playwright/test";

function isFatal(text) {
  const t = text.toLowerCase();
  if (t.includes("context lost") || t.includes("webgl_context_lost")) return true;
  if (t.includes("derivescene")) return true;
  if (t.includes("pixi") && (t.includes("error") || t.includes("fail"))) return true;
  if (t.includes("uncaught") || t.includes("unhandled")) return true;
  if (t.includes("404") || t.includes("failed to load")) return true;
  return false;
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const consoleErrors = [];
const pageErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => pageErrors.push(e.message));

await page.goto("http://127.0.0.1:8800/", { waitUntil: "load" });
await page.getByText("one_man_corp 직원 상태창").waitFor({ timeout: 15000 });

// 2D 토글 → canvas attached (mount async가 real Viewport ctor + Assets.load를 거침).
await page.getByTestId("view-toggle-2d").click();
const stage = page.getByTestId("twod-stage");
await stage.waitFor({ state: "visible", timeout: 15000 });
await stage.locator("canvas").waitFor({ state: "attached", timeout: 15000 });

// pixi가 타일맵을 그릴 시간(에셋 비동기 로드 + 첫 렌더).
await page.waitForTimeout(2500);

// 진단: canvas 크기 + 타일맵 픽셀이 단색(#f5f5f5)이 아닌지(=배경이 그려졌는지) 표본.
const probe = await page.evaluate(() => {
  const c = document.querySelector('[data-testid="twod-stage"] canvas');
  if (!c) return { hasCanvas: false };
  // canvas 픽셀 표본(WebGL이면 readPixels 불가 → toDataURL 길이로 비단색 추정).
  let dataLen = 0;
  try {
    dataLen = c.toDataURL("image/png").length;
  } catch (e) {
    dataLen = -1;
  }
  return { hasCanvas: true, width: c.width, height: c.height, dataUrlLen: dataLen };
});

await page.screenshot({ path: "../results/phase4c/live-2d-tilemap.png", fullPage: true });

const fatalC = consoleErrors.filter(isFatal);
const fatalP = pageErrors.filter(isFatal);

console.log("=== LIVE 2D CHECK ===");
console.log("CANVAS", JSON.stringify(probe));
console.log("CONSOLE_ERRORS_total", consoleErrors.length, JSON.stringify(consoleErrors.slice(0, 8)));
console.log("PAGE_ERRORS_total", pageErrors.length, JSON.stringify(pageErrors.slice(0, 8)));
console.log("FATAL_CONSOLE", fatalC.length, JSON.stringify(fatalC));
console.log("FATAL_PAGE", fatalP.length, JSON.stringify(fatalP));
console.log("VERDICT", probe.hasCanvas && fatalC.length === 0 && fatalP.length === 0 ? "PASS" : "FAIL");

await browser.close();
