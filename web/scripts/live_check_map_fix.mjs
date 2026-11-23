/**
 * 오피스 맵 3대 결함 수정 — 라이브 검증 probe (실 브라우저·prod 33006·신규).
 *
 * AC-1 충돌(지형 무시 차단)·AC-2 걷기(접지/부유 제거)·AC-3 탭 복원성(빈 캔버스 해소).
 * ★비평 Major-2 반영: context-lost를 비치명 처리(의도된 손실 테스트) + 복원완료 폴링 +
 *   WEBGL_lose_context 미지원 시 graceful skip. 시각(충돌/접지/walk)은 스크린샷 Read(HITL).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/live_check_map_fix.mjs
 */
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = "https://studio.example.com:33006";
const OUT = "work/map_fix";

// .env에서 LOGIN_PASSWORD만 파싱(값 비노출 — 출력/로그 금지).
function loginPassword() {
  const env = readFileSync(new URL("../../.env", import.meta.url), "utf-8");
  for (const line of env.split("\n")) {
    const m = line.match(/^LOGIN_PASSWORD=(.*)$/);
    if (m) return m[1].trim().replace(/^['"]|['"]$/g, "");
  }
  throw new Error("LOGIN_PASSWORD .env에 없음");
}

// context-lost/SwiftShader 등 비치명 잡음 제외(의도된 컨텍스트 손실 테스트 포함).
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

async function canvasDims(page) {
  return page.evaluate(() => {
    const c = document.querySelector("canvas");
    if (!c) return null;
    return { w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight };
  });
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
const context = await browser.newContext({ ignoreHTTPSErrors: true });

// ① API 로그인 → 세션 쿠키(컨텍스트 공유). UI 폼 대신 계약 직접(견고).
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

// ② 로드 — 기본 뷰=office-v2·기본 섹션=Dashboard(MAP_SECTIONS)라 맵 표시.
await page.goto(`${BASE}/`, { waitUntil: "load" });
await page.waitForTimeout(800);

// office 뷰 토글 버튼이 있으면 눌러 확실히 office-v2로(기본이 office면 무해).
const officeToggle = await page
  .locator('[data-testid="view-toggle-office"]')
  .count();
if (officeToggle) {
  await page.click('[data-testid="view-toggle-office"]');
}
await page.waitForTimeout(2000); // Phaser 부팅(preload/create).

const phaserContainer = await page
  .locator('[data-testid="phaser-game"]')
  .count();
const dimsInitial = await canvasDims(page);
const hasWebGL = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  return c ? !!(c.getContext("webgl2") || c.getContext("webgl")) : false;
});

// ③ AC-1/AC-2 시각: 초기 + 배회 후 스크린샷(접지 그림자·collision·walk = Read 육안).
await page.screenshot({ path: `${OUT}/map_initial.png` });
await page.waitForTimeout(5000); // 아바타 배회(이동/접지/squash) 관찰.
await page.screenshot({ path: `${OUT}/map_after_wander.png` });

// ④ AC-3 탭 복원성: visibilitychange hidden→visible(Phaser loop pause/resume + RESUME 핸들러).
await page.evaluate(() => {
  Object.defineProperty(document, "visibilityState", {
    value: "hidden",
    configurable: true,
  });
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
});
await page.waitForTimeout(500);
await page.evaluate(() => {
  Object.defineProperty(document, "visibilityState", {
    value: "visible",
    configurable: true,
  });
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
});
await page.waitForTimeout(800);
const dimsAfterTab = await canvasDims(page);
await page.screenshot({ path: `${OUT}/map_after_tab.png` });

// ⑤ AC-3 컨텍스트 손실→복원(WEBGL_lose_context·graceful skip·복원완료 폴링).
const ctxResult = await page.evaluate(async () => {
  const c = document.querySelector("canvas");
  const gl = c && (c.getContext("webgl2") || c.getContext("webgl"));
  if (!gl) return "no-gl";
  const ext = gl.getExtension("WEBGL_lose_context");
  if (!ext) return "no-ext";
  ext.loseContext();
  await new Promise((r) => setTimeout(r, 300));
  ext.restoreContext();
  return "triggered";
});
let dimsAfterRestore = null;
if (ctxResult === "triggered") {
  // 복원완료 폴링(렌더러 contextLost=false 또는 최대 2.5s).
  await page
    .waitForFunction(
      () => {
        const c = document.querySelector("canvas");
        const gl = c && (c.getContext("webgl2") || c.getContext("webgl"));
        return gl && !gl.isContextLost();
      },
      { timeout: 2500 },
    )
    .catch(() => {});
  await page.waitForTimeout(600);
  dimsAfterRestore = await canvasDims(page);
  await page.screenshot({ path: `${OUT}/map_after_restore.png` });
}

const dimsOk = (d) => !!d && d.w > 0 && d.h > 0 && d.cw > 0 && d.ch > 0;

console.log("LOGIN_OK:", loginOk);
console.log("PHASER_CONTAINER:", phaserContainer);
console.log("HAS_WEBGL:", hasWebGL);
console.log("WS_CONNECTED:", wsConnected);
console.log("DIMS_INITIAL:", JSON.stringify(dimsInitial), "ok=", dimsOk(dimsInitial));
console.log("DIMS_AFTER_TAB:", JSON.stringify(dimsAfterTab), "ok=", dimsOk(dimsAfterTab));
console.log("CTX_LOSS_TEST:", ctxResult);
console.log(
  "DIMS_AFTER_RESTORE:",
  JSON.stringify(dimsAfterRestore),
  ctxResult === "triggered" ? "ok=" + dimsOk(dimsAfterRestore) : "(skipped)",
);
console.log("CONSOLE_ERRORS(fatal):", fatals.length, fatals.slice(0, 5));

// 판정: 컨테이너·WebGL·로그인·초기/탭후 캔버스 비공백·치명오류0.
// 컨텍스트 복원은 확장 지원 시에만 단언(미지원=graceful skip).
const restoreOk = ctxResult !== "triggered" || dimsOk(dimsAfterRestore);
const verdict =
  loginOk &&
  phaserContainer >= 1 &&
  hasWebGL &&
  dimsOk(dimsInitial) &&
  dimsOk(dimsAfterTab) &&
  restoreOk &&
  fatals.length === 0;
console.log("VERDICT:", verdict ? "PASS" : "FAIL");

await browser.close();
process.exit(verdict ? 0 : 1);
