/**
 * 오피스맵 라이브 관찰 — C1(presence·idle-static) + C2(desk-clip 육안) 검증 probe.
 *
 * - idle 정지 검증: ~4s 간격 2장 캡처(PIL diff 0% 기대 = 배회/부유 없음·R5 현실성).
 * - desk-clip 육안: legal/hr/accountant/cs 스프라이트가 중앙 가구를 침범하는지 Read(HITL).
 * - 렌더 헬스: 캔버스 치수>0·WebGL·치명 콘솔오류 0.
 * 코드 변경 없음(관찰 전용). 실 서버 33006·throwaway(커밋 제외).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/live_office_observe.mjs
 */
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = "https://studio.example.com:33006";
const OUT = "work/next_candidates";

function loginPassword() {
  const env = readFileSync(new URL("../../.env", import.meta.url), "utf-8");
  for (const line of env.split("\n")) {
    const m = line.match(/^LOGIN_PASSWORD=(.*)$/);
    if (m) return m[1].trim().replace(/^['"]|['"]$/g, "");
  }
  throw new Error("LOGIN_PASSWORD .env에 없음");
}

function isFatal(text) {
  const t = text.toLowerCase();
  if (t.includes("context lost") || t.includes("webgl_context_lost")) return false;
  if (t.includes("context_lost_webgl")) return false;
  if (t.includes("swiftshader") || t.includes("favicon")) return false;
  if (t.includes("derivescene")) return true;
  if (t.includes("uncaught") || t.includes("unhandled")) return true;
  if (t.includes("failed to load") && !t.includes("favicon")) return true;
  return false;
}

const fatals = [];
let wsConnected = false;

const browser = await chromium.launch({
  headless: true,
  args: [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--ignore-gpu-blocklist",
    "--enable-unsafe-swiftshader",
  ],
});
const context = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1440, height: 900 },
});

const loginResp = await context.request.post(`${BASE}/api/login`, {
  data: { password: loginPassword() },
});
const loginOk = loginResp.ok();

const page = await context.newPage();
page.on("console", (m) => {
  if (m.type() === "error" && isFatal(m.text())) fatals.push(m.text());
});
page.on("pageerror", (e) => {
  if (isFatal(String(e))) fatals.push(String(e));
});
page.on("websocket", (ws) => {
  if (ws.url().includes("/ws")) wsConnected = true;
});

await page.goto(`${BASE}/`, { waitUntil: "load" });
await page.waitForTimeout(800);

// Office 전용 뷰로 전환(있으면).
const officeToggle = await page.locator('[data-testid="view-toggle-office"]').count();
if (officeToggle) await page.click('[data-testid="view-toggle-office"]');
await page.waitForTimeout(2500); // Phaser preload/create.

const dims = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  return c ? { w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight } : null;
});
const hasWebGL = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  return c ? !!(c.getContext("webgl2") || c.getContext("webgl")) : false;
});

// idle 정지 검증: 2장 ~4s 간격(이동/부유 없으면 동일).
await page.screenshot({ path: `${OUT}/office_idle_a.png` });
await page.waitForTimeout(4000);
await page.screenshot({ path: `${OUT}/office_idle_b.png` });

const dimsOk = !!dims && dims.w > 0 && dims.h > 0 && dims.cw > 0 && dims.ch > 0;

console.log("LOGIN_OK:", loginOk);
console.log("HAS_WEBGL:", hasWebGL);
console.log("WS_CONNECTED:", wsConnected);
console.log("DIMS:", JSON.stringify(dims), "ok=", dimsOk);
console.log("CONSOLE_ERRORS(fatal):", fatals.length, fatals.slice(0, 5));
console.log("SCREENSHOTS:", `${OUT}/office_idle_a.png`, `${OUT}/office_idle_b.png`);
console.log("VERDICT:", loginOk && dimsOk && hasWebGL && fatals.length === 0 ? "PASS" : "FAIL");

await browser.close();
process.exit(loginOk && dimsOk && hasWebGL && fatals.length === 0 ? 0 : 1);
