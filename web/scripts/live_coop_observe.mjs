/**
 * 오피스맵 라이브 협업 관측 — US-10. 라이브 게임빌드가 도는 동안 오피스를 시계열로
 * 다회 캡처해 직원(역할)들이 작업하는 모습(협업=직렬 배턴 / 병렬=동시 2명)을 증거화한다.
 *
 * - login(POST /api/login·비번 .env·평문 미출력) → office 뷰 토글 → N장 시계열 캡처.
 * - 각 캡처: 타임스탬프 + 캔버스 치수 + WS 연결. 활동은 SSE/WS 이벤트로 오피스가 표시.
 * - 코드 변경 없음(관찰 전용·throwaway·커밋 제외). 실 서버 33006.
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/live_coop_observe.mjs <shots> <interval_ms> <out_dir>
 */
import { mkdirSync, readFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = "https://studio.example.com:33006";
const SHOTS = Number(process.argv[2] || 12);
const INTERVAL = Number(process.argv[3] || 22000);
const OUT = process.argv[4] || ".omc/state/coop-observe";
mkdirSync(OUT, { recursive: true });

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
  if (t.includes("swiftshader") || t.includes("favicon")) return false;
  if (t.includes("context lost") || t.includes("context_lost")) return false;
  if (t.includes("derivescene")) return true;
  if (t.includes("uncaught") || t.includes("unhandled")) return true;
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

const officeToggle = await page.locator('[data-testid="view-toggle-office"]').count();
if (officeToggle) await page.click('[data-testid="view-toggle-office"]');
await page.waitForTimeout(2500); // Phaser preload/create.

const t0 = Date.now();
const shots = [];
for (let i = 0; i < SHOTS; i++) {
  const dims = await page.evaluate(() => {
    const c = document.querySelector("canvas");
    return c ? { w: c.width, h: c.height } : null;
  });
  // 활동 텍스트(말풍선·상태 라벨)도 함께 긁어 어떤 직원이 일하는지 단서 확보.
  const activity = await page.evaluate(() => {
    const txt = document.body.innerText || "";
    return txt.split("\n").filter((l) =>
      /작업|working|제작|설계|밸런스|콘텐츠|조립|QA|tool|run|진행/i.test(l)
    ).slice(0, 8);
  });
  const path = `${OUT}/coop_${String(i).padStart(2, "0")}.png`;
  await page.screenshot({ path });
  const ts = ((Date.now() - t0) / 1000).toFixed(0);
  shots.push({ i, t: `+${ts}s`, dims, activity });
  console.log(`SHOT ${i} +${ts}s dims=${JSON.stringify(dims)} ws=${wsConnected} act=${JSON.stringify(activity)}`);
  if (i < SHOTS - 1) await page.waitForTimeout(INTERVAL);
}

console.log("LOGIN_OK:", loginOk);
console.log("WS_CONNECTED:", wsConnected);
console.log("CONSOLE_ERRORS(fatal):", fatals.length, fatals.slice(0, 5));
console.log("SHOTS:", shots.length, "→", OUT);

await browser.close();
process.exit(loginOk ? 0 : 1);
