/**
 * 게임 오디오 검증 — clicker / slide-puzzle / Phaser survivor (all-in-one /deep-research·BGM+SFX).
 *
 * 절차적 Web Audio(칩튠)를 무음(헤드리스)서 결정론 검증 — window.__audio 훅으로 autoplay unlock·
 * per-action SFX 트리거·BGM 시작·mute·비차단을 단언("들리는지"는 실기기 몫). deep-research wu1iezbr4 근거.
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 node web/scripts/play_audio_check.mjs
 * exit 0 = PASS(전 AC), 비-0 = FAIL.
 */
import http from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, devices } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const fileUrl = (p) => "file://" + path.join(REPO, p);
const DEV = devices["iPhone 13"];

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml" };
function startStatic(rootDir) {
  const server = http.createServer((req, res) => {
    let p = decodeURIComponent((req.url || "/").split("?")[0]);
    if (p === "/") p = "/index.html";
    const fp = path.join(rootDir, p);
    if (!fp.startsWith(rootDir) || !existsSync(fp) || !statSync(fp).isFile()) { res.writeHead(404); res.end("404"); return; }
    res.writeHead(200, { "content-type": MIME[path.extname(fp)] || "application/octet-stream" });
    createReadStream(fp).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port })));
}

const results = [];
const rec = (id, ok, d) => { results.push(ok); console.log(`${ok ? "[PASS]" : "[FAIL]"} ${id} — ${d}`); };
const A = (page) => page.evaluate(() => window.__audio);
async function c2s(page, sel, cx, cy) {
  return page.evaluate(({ sel, cx, cy }) => { const c = document.querySelector(sel); const r = c.getBoundingClientRect(); return { x: r.left + cx * (r.width / c.width), y: r.top + cy * (r.height / c.height) }; }, { sel, cx, cy });
}

const browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
async function withPage(opts, fn) {
  const ctx = await browser.newContext({ ...DEV });
  const page = await ctx.newPage();
  const errs = [];
  page.on("console", (m) => m.type() === "error" && !/favicon/i.test(m.text()) && errs.push(m.text()));
  page.on("pageerror", (e) => errs.push(String(e)));
  if (opts.noAudio) await page.addInitScript(() => { try { delete window.AudioContext; delete window.webkitAudioContext; } catch (e) {} });
  try { return await fn(page, errs, ctx); } finally { await ctx.close(); }
}

try {
  // ───────── CLICKER ─────────
  await withPage({}, async (page, errs) => {
    await page.goto(fileUrl("games/clicker-mvp/index.html"), { waitUntil: "load" });
    const pre = await A(page);
    rec("AC1-clicker(제스처전 무음)", pre.ctxState === "none" && pre.bgmStarted === false, `ctxState=${pre.ctxState} bgm=${pre.bgmStarted}`);
    // 첫 탭(제스처) → unlock
    let pt = await c2s(page, "#game", 170, 190);
    await page.touchscreen.tap(pt.x, pt.y);
    await page.waitForTimeout(350);
    const post = await A(page);
    rec("AC2-clicker(unlock)", post.unlocked && post.bgmStarted && post.ctxState === "running", `ctxState=${post.ctxState} unlocked=${post.unlocked} bgm=${post.bgmStarted}`);
    rec("AC3-clicker(click SFX)", post.sfx.count >= 1 && post.sfx.last === "click", `sfx.count=${post.sfx.count} last=${post.sfx.last}`);
    // 강화(score>=10 후 버튼) → upgrade SFX
    for (let i = 0; i < 9; i++) await page.touchscreen.tap(pt.x, pt.y);
    pt = await c2s(page, "#game", 400, 150);
    await page.touchscreen.tap(pt.x, pt.y);
    const up = await A(page);
    rec("AC3b-clicker(upgrade SFX)", up.sfx.last === "upgrade" && errs.length === 0, `last=${up.sfx.last} errs=${errs.length}`);
    // mute (click 으로 결정론·헤드리스 tap→click 합성 회피)
    await page.locator("#mute").click();
    const mu = await A(page);
    rec("AC6-clicker(mute)", mu.muted === true, `muted=${mu.muted}`);
  });

  // ───────── SLIDE-PUZZLE ─────────
  await withPage({}, async (page, errs) => {
    await page.goto(fileUrl("games/slide-puzzle/index.html"), { waitUntil: "load" });
    const K = { CELL: 110, PAD: 8, ORIGIN_Y: 50 };
    const cell = (i) => ({ x: (i % 3) * (K.CELL + K.PAD) + K.PAD + K.CELL / 2, y: K.ORIGIN_Y + Math.floor(i / 3) * (K.CELL + K.PAD) + K.CELL / 2 });
    const nb = await page.evaluate(() => window.__neighbors());
    const cp = cell(nb[0]); const pt = await c2s(page, "#game", cp.x, cp.y);
    await page.touchscreen.tap(pt.x, pt.y);   // 타일 탭 = unlock + move SFX
    await page.waitForTimeout(350);
    const post = await A(page);
    rec("AC2-puzzle(unlock)", post.unlocked && post.bgmStarted && post.ctxState === "running", `ctxState=${post.ctxState} bgm=${post.bgmStarted}`);
    rec("AC4-puzzle(move SFX)", post.sfx.last === "move", `last=${post.sfx.last}`);
    await page.locator("#reshuffle").click();
    const rs = await A(page);
    rec("AC4b-puzzle(shuffle SFX)", rs.sfx.last === "shuffle" && errs.length === 0, `last=${rs.sfx.last} errs=${errs.length}`);
    await page.locator("#mute").click();
    const mu = await A(page);
    rec("AC6-puzzle(mute)", mu.muted === true, `muted=${mu.muted}`);
  });

  // ───────── PHASER SURVIVOR (http 서빙) ─────────
  const ph = await startStatic(path.join(REPO, "game", "dist"));
  try {
    await withPage({}, async (page, errs) => {
      await page.goto(`http://127.0.0.1:${ph.port}/index.html`, { waitUntil: "load" });
      await page.waitForSelector("#app canvas", { timeout: 8000 });
      const pre = await A(page);
      rec("AC1-survivor(제스처전 무음)", pre && pre.ctxState === "none" && pre.bgmStarted === false, `ctxState=${pre && pre.ctxState} bgm=${pre && pre.bgmStarted}`);
      // 캔버스 탭 = unlock(오토플레이라 게임입력 없음)
      const box = await page.locator("#app canvas").boundingBox();
      await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
      await page.waitForTimeout(400);
      const post = await A(page);
      rec("AC2-survivor(unlock+BGM)", post.unlocked && post.bgmStarted && post.ctxState === "running", `ctxState=${post.ctxState} bgm=${post.bgmStarted}`);
      // 오토플레이 진행 → 자동발사로 적 처치 → kill SFX(state-diff). 첫 처치 ~5-6s 소요 → kills↑까지 폴링.
      const before = post.sfx.count;
      let killsSeen = 0;
      for (let i = 0; i < 16; i++) {
        await page.waitForTimeout(800);
        killsSeen = await page.evaluate(() => (window.__survivorState ? window.__survivorState.kills : 0));
        if (killsSeen > 0) break;
      }
      const after = await A(page);
      rec("AC5-survivor(kill SFX)", killsSeen > 0 && after.sfx.count > before && after.sfx.last === "kill" && errs.length === 0, `kills=${killsSeen} sfx.count ${before}->${after.sfx.count} last=${after.sfx.last} errs=${errs.length}`);
      await page.locator("#mute").click();
      const mu = await A(page);
      rec("AC6-survivor(mute)", mu.muted === true, `muted=${mu.muted}`);
    });
  } finally { ph.server.close(); }

  // ───────── AC7 비차단(AudioContext 제거) ─────────
  await withPage({ noAudio: true }, async (page, errs) => {
    await page.goto(fileUrl("games/clicker-mvp/index.html"), { waitUntil: "load" });
    const s0 = await page.evaluate(() => ({ ...window.__gameState }));
    const pt = await c2s(page, "#game", 170, 190);
    await page.touchscreen.tap(pt.x, pt.y);
    const s1 = await page.evaluate(() => ({ ...window.__gameState }));
    const au = await A(page);
    rec("AC7(비차단·AudioContext 제거)", s1.score === s0.score + s0.clickPower && errs.length === 0 && au.ctxState === "none", `score ${s0.score}->${s1.score} ctxState=${au.ctxState} errs=${errs.length}`);
  });
} finally {
  await browser.close();
}

const fail = results.filter((x) => !x).length;
console.log(`\n######## 게임 오디오: PASS=${results.length - fail} FAIL=${fail} ########`);
if (fail) process.exit(1);
console.log("PASS: 전 오디오 AC 충족 (iPhone 13 에뮬·무음 훅 검증)");
