/**
 * office-v2 US-0g — 라이브 PoC 게이트 검증 (실 브라우저·번들 chromium).
 *
 * 검증(계획 §11 US-0g·게이트):
 *   - office-v2 토글 → phaser-game 컨테이너 + <canvas> + WebGL 컨텍스트(실 Phaser 부팅)
 *   - 채팅 패널(chat-panel) + 좌/우·접기 토글 동작
 *   - WS(/ws) 라이브 연결(page websocket 이벤트)
 *   - ★StrictMode/뷰토글 leak0: office↔text 5회 토글 후 canvas 누적 0(game.destroy 가드)
 *   - 치명 콘솔/페이지 오류 0(SwiftShader 경고는 비치명·기존 필터 동일)
 *   - 스크린샷(work/office_v2_poc.png)
 */
import { chromium } from "@playwright/test";

const URL = "http://127.0.0.1:33006/";

function isFatal(text) {
  const t = text.toLowerCase();
  if (t.includes("context lost") || t.includes("webgl_context_lost")) return true;
  if (t.includes("derivescene")) return true;
  if (t.includes("uncaught") || t.includes("unhandled")) return true;
  if (t.includes("404") || t.includes("failed to load")) return true;
  if (t.includes("websocket") && t.includes("error")) return true;
  return false;
}

const fatals = [];
let wsConnected = false;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
page.on("console", (m) => {
  if (m.type() === "error" && isFatal(m.text())) fatals.push(m.text());
});
page.on("pageerror", (e) => fatals.push(String(e)));
page.on("websocket", (ws) => {
  if (ws.url().includes("/ws")) wsConnected = true;
});

await page.goto(URL, { waitUntil: "load" });
await page.waitForTimeout(500);

// office-v2 뷰 토글
await page.click('[data-testid="view-toggle-office"]');
await page.waitForTimeout(1800); // Phaser 부팅(preload/create)

// Phaser 캔버스 + WebGL
const container = await page.locator('[data-testid="phaser-game"]').count();
const canvasInitial = await page.evaluate(
  () => document.querySelectorAll("canvas").length,
);
const hasWebGL = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  if (!c) return false;
  return !!(c.getContext("webgl2") || c.getContext("webgl"));
});

// 채팅 패널 + 토글
const chatPanel = await page.locator('[data-testid="chat-panel"]').count();
const sideToggle = await page.locator('[data-testid="chat-toggle-side"]').count();
const collapseToggle = await page
  .locator('[data-testid="chat-toggle-collapse"]')
  .count();
// 토글 클릭(오류 없이 동작)
if (sideToggle) await page.click('[data-testid="chat-toggle-side"]');
if (collapseToggle) await page.click('[data-testid="chat-toggle-collapse"]');
await page.waitForTimeout(200);
if (collapseToggle) await page.click('[data-testid="chat-toggle-collapse"]'); // 다시 펼침

// 스크린샷(office-v2 PoC)
await page.screenshot({ path: "work/office_v2_poc.png" });

// ★leak0: office↔text 5회 토글 후 canvas 누적 확인(game.destroy 가드)
for (let i = 0; i < 5; i++) {
  await page.click('[data-testid="view-toggle-text"]');
  await page.waitForTimeout(150);
  await page.click('[data-testid="view-toggle-office"]');
  await page.waitForTimeout(350);
}
const canvasAfter = await page.evaluate(
  () => document.querySelectorAll("canvas").length,
);

// 결과
console.log("PHASER_CONTAINER:", container);
console.log("CANVAS_INITIAL:", canvasInitial);
console.log("HAS_WEBGL:", hasWebGL);
console.log("WS_CONNECTED:", wsConnected);
console.log(
  "CHAT_PANEL:",
  chatPanel,
  "SIDE_TOGGLE:",
  sideToggle,
  "COLLAPSE_TOGGLE:",
  collapseToggle,
);
console.log("CANVAS_AFTER_5x_TOGGLE:", canvasAfter, "(leak0 = <=1)");
console.log("CONSOLE_ERRORS(fatal):", fatals.length, fatals.slice(0, 5));

const leak0 = canvasAfter <= 1;
const verdict =
  container >= 1 &&
  canvasInitial >= 1 &&
  hasWebGL &&
  wsConnected &&
  chatPanel >= 1 &&
  sideToggle >= 1 &&
  collapseToggle >= 1 &&
  leak0 &&
  fatals.length === 0;
console.log("VERDICT:", verdict ? "PASS" : "FAIL");

await browser.close();
process.exit(verdict ? 0 : 1);
