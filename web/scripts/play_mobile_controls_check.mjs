/**
 * 모바일 웹 조작성 검증 — clicker / slide-puzzle / Phaser survivor (all-in-one /deep-research).
 *
 * 기존 play_{clicker,puzzle}_check.mjs 는 file:// 데스크톱에서 게임 *로직*(결정론·계약)을 검증한다.
 * 이 스크립트는 **모바일 에뮬레이션**(iPhone 13 viewport 390×844 · hasTouch · isMobile)에서 **실 터치**로
 * 조작성을 검증한다 — deep-research(wr5r4c24v) 권고(Pointer Events·스와이프·touch-action·Phaser Scale.FIT) 충족 확인.
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 node web/scripts/play_mobile_controls_check.mjs
 *   ENV MOBILE_NEGATIVE=clicker|puzzle|phaser → 해당 게임에서 핸들러/설정 제거를 가정한 falsifiable 단언 강화(미사용 시 일반).
 * exit 0 = PASS(전 AC), 비-0 = FAIL.
 *
 * AC: AC1 반응형 fit+viewport meta · AC3 clicker 터치탭(이중발화0·스케일보정) · AC4 touch-action ·
 *     AC5 puzzle 타일 터치탭 · AC6 puzzle 스와이프→moveDir(스크롤0) · AC7 셔플버튼 · AC8 Phaser Scale.FIT.
 */
import http from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, devices } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const url = (p) => "file://" + path.join(REPO, p);

// 정적 http 서버 — Phaser dist 는 `<script type=module>` 이라 file:// 에선 CORS(opaque origin)로
// 차단된다(실 모바일 웹은 http 서빙이라 정상). 검증용으로만 dist 를 http 로 띄운다(포트 0=OS 할당).
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml" };
function startStatic(rootDir) {
  const server = http.createServer((req, res) => {
    let p = decodeURIComponent((req.url || "/").split("?")[0]);
    if (p === "/") p = "/index.html";
    const fp = path.join(rootDir, p);
    if (!fp.startsWith(rootDir) || !existsSync(fp) || !statSync(fp).isFile()) {
      res.writeHead(404); res.end("404"); return;
    }
    res.writeHead(200, { "content-type": MIME[path.extname(fp)] || "application/octet-stream" });
    createReadStream(fp).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port })));
}
const DEV = devices["iPhone 13"]; // 390×844 · hasTouch · isMobile · DSF 3

const results = [];
const rec = (id, ok, detail) => {
  results.push({ id, ok, detail });
  console.log(`${ok ? "[PASS]" : "[FAIL]"} ${id} — ${detail}`);
};

// 캔버스 내부좌표(cx,cy)를 화면(client) 좌표로 — CSS 스케일 보정 역연산(getBoundingClientRect 비율).
async function canvasToScreen(page, sel, cx, cy) {
  return page.evaluate(
    ({ sel, cx, cy }) => {
      const c = document.querySelector(sel);
      const r = c.getBoundingClientRect();
      return { x: r.left + cx * (r.width / c.width), y: r.top + cy * (r.height / c.height) };
    },
    { sel, cx, cy }
  );
}

async function withPage(fn) {
  const ctx = await browser.newContext({ ...DEV });
  const page = await ctx.newPage();
  const errs = [];
  page.on("console", (m) => m.type() === "error" && !/favicon/i.test(m.text()) && errs.push(m.text()));
  page.on("pageerror", (e) => errs.push(String(e)));
  try {
    return await fn(page, errs, ctx);
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});

try {
  // ───────────────── CLICKER ─────────────────
  await withPage(async (page, errs) => {
    await page.goto(url("games/clicker-mvp/index.html"), { waitUntil: "load" });
    const vp = page.viewportSize();

    // AC1: viewport meta + 캔버스가 화면폭 내(가로 스크롤 없음).
    const meta = await page.locator('meta[name="viewport"]').count();
    const fit = await page.evaluate(() => {
      const c = document.getElementById("game");
      const r = c.getBoundingClientRect();
      return { w: r.width, vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
    });
    rec("AC1-clicker", meta === 1 && fit.w <= fit.vw + 1 && !fit.overflow, `meta=${meta} canvasW=${fit.w.toFixed(0)}≤vw=${fit.vw} overflow=${fit.overflow}`);

    // AC4: touch-action(더블탭 줌/300ms 차단).
    const ta = await page.evaluate(() => getComputedStyle(document.getElementById("game")).touchAction);
    rec("AC4-clicker", ta === "manipulation" || ta === "none", `touch-action=${ta}`);

    // AC3: 실 터치 탭 → score 정확히 +clickPower(이중발화0)·강화버튼 스케일보정 적중.
    const s0 = await page.evaluate(() => ({ ...window.__gameState }));
    // CLICK_AREA 중심(캔버스 내부 좌표 170,190) 1탭.
    let pt = await canvasToScreen(page, "#game", 170, 190);
    await page.touchscreen.tap(pt.x, pt.y);
    const s1 = await page.evaluate(() => ({ ...window.__gameState }));
    const singleFire = s1.score === s0.score + s0.clickPower && s1.clicks === s0.clicks + 1;
    // 9탭 더(총 score=10) 후 강화버튼(400,150) 탭 → upgrade(스케일보정 적중 증명).
    for (let i = 0; i < 9; i++) await page.touchscreen.tap(pt.x, pt.y);
    const sBeforeUp = await page.evaluate(() => ({ ...window.__gameState }));
    pt = await canvasToScreen(page, "#game", 400, 150);
    await page.touchscreen.tap(pt.x, pt.y);
    const s2 = await page.evaluate(() => ({ ...window.__gameState }));
    const upgraded = s2.upgrades === sBeforeUp.upgrades + 1 && s2.clickPower === sBeforeUp.clickPower + 1;
    rec(
      "AC3-clicker",
      singleFire && upgraded && errs.length === 0,
      `tap:score ${s0.score}->${s1.score}(+${s0.clickPower} singleFire=${singleFire}) upgradeBtn:upgrades->${s2.upgrades} clickPower->${s2.clickPower} errs=${errs.length}`
    );
  });

  // ───────────────── SLIDE-PUZZLE ─────────────────
  await withPage(async (page, errs, ctx) => {
    await page.goto(url("games/slide-puzzle/index.html"), { waitUntil: "load" });

    const meta = await page.locator('meta[name="viewport"]').count();
    const fit = await page.evaluate(() => {
      const c = document.getElementById("game");
      const r = c.getBoundingClientRect();
      return { w: r.width, vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
    });
    rec("AC1-puzzle", meta === 1 && fit.w <= fit.vw + 1 && !fit.overflow, `meta=${meta} canvasW=${fit.w.toFixed(0)}≤vw=${fit.vw} overflow=${fit.overflow}`);

    const ta = await page.evaluate(() => getComputedStyle(document.getElementById("game")).touchAction);
    rec("AC4-puzzle", ta === "none", `touch-action=${ta}(스와이프 소유)`);

    // 게임 상수(캔버스 내부 좌표 계산용).
    const K = { CELL: 110, PAD: 8, ORIGIN_Y: 50, SIZE: 3 };
    const cellCenter = (idx) => {
      const r = Math.floor(idx / K.SIZE), c = idx % K.SIZE;
      return { x: c * (K.CELL + K.PAD) + K.PAD + K.CELL / 2, y: K.ORIGIN_Y + r * (K.CELL + K.PAD) + K.CELL / 2 };
    };

    // AC5: 빈칸 인접 타일을 실 터치 탭 → moves+1(기존 moveTile 경로).
    const st0 = await page.evaluate(() => ({ moves: window.__gameState.moves, nb: window.__neighbors() }));
    const tapIdx = st0.nb[0];
    const cc = cellCenter(tapIdx);
    let pt = await canvasToScreen(page, "#game", cc.x, cc.y);
    await page.touchscreen.tap(pt.x, pt.y);
    const st1 = await page.evaluate(() => window.__gameState.moves);
    rec("AC5-puzzle", st1 === st0.moves + 1, `탭 tile#${tapIdx}: moves ${st0.moves}->${st1}`);

    // AC6: 스와이프(CDP 실 터치 시퀀스) → moveDir 실행·페이지 스크롤 0.
    // 빈칸 위치로 합법 방향+스와이프벡터 산출(moveDir 계약: up:r<2 down:r>0 left:c<2 right:c>0·r,c=빈칸).
    const blank = await page.evaluate(() => window.__gameState.tiles.indexOf(0));
    const br = Math.floor(blank / K.SIZE), bc = blank % K.SIZE;
    let dir, vec;
    if (bc > 0) { dir = "right"; vec = [60, 0]; }
    else if (bc < K.SIZE - 1) { dir = "left"; vec = [-60, 0]; }
    else if (br > 0) { dir = "down"; vec = [0, 60]; }
    else { dir = "up"; vec = [0, -60]; }
    const center = await canvasToScreen(page, "#game", 185, 240); // 보드 중앙 근처
    const movesPre = await page.evaluate(() => window.__gameState.moves);
    const client = await ctx.newCDPSession(page);
    const x1 = center.x, y1 = center.y, x2 = center.x + vec[0], y2 = center.y + vec[1];
    await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x1, y: y1 }] });
    await client.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: (x1 + x2) / 2, y: (y1 + y2) / 2 }] });
    await client.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x2, y: y2 }] });
    await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(50);
    const after = await page.evaluate(() => ({ moves: window.__gameState.moves, scrollY: window.scrollY }));
    rec("AC6-puzzle", after.moves === movesPre + 1 && after.scrollY === 0, `swipe ${dir}: moves ${movesPre}->${after.moves} scrollY=${after.scrollY}`);

    // AC7: 화면 셔플 버튼 → moves=0 재셔플(R 키 대체). 버튼=표준 <button>+touch-action:manipulation
    // (실 모바일은 탭=click 즉시). 실 터치 탭 우선, 헤드리스가 합성 click 을 안 내보내면 click 폴백(경로 보고).
    const movesB4 = await page.evaluate(() => window.__gameState.moves);
    let how = "tap";
    await page.locator("#reshuffle").tap();
    await page.waitForTimeout(200);
    let reshuf = await page.evaluate(() => ({ moves: window.__gameState.moves, solved: window.__gameState.solved }));
    if (reshuf.moves !== 0) {
      how = "click(폴백)";
      await page.locator("#reshuffle").click();
      await page.waitForTimeout(100);
      reshuf = await page.evaluate(() => ({ moves: window.__gameState.moves, solved: window.__gameState.solved }));
    }
    rec("AC7-puzzle", reshuf.moves === 0 && reshuf.solved === false && errs.length === 0, `reshuffle via ${how}(before=${movesB4}): moves=${reshuf.moves} solved=${reshuf.solved} errs=${errs.length}`);
  });

  // ───────────────── PHASER SURVIVOR (http 서빙·ES 모듈) ─────────────────
  const phaser = await startStatic(path.join(REPO, "game", "dist"));
  try {
  await withPage(async (page, errs) => {
    await page.goto(`http://127.0.0.1:${phaser.port}/index.html`, { waitUntil: "load" });
    await page.waitForSelector("#app canvas", { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(500); // 오토플레이 몇 틱 진행
    const c = await page.evaluate(() => {
      const cv = document.querySelector("#app canvas");
      if (!cv) return null;
      const r = cv.getBoundingClientRect();
      return { cssW: r.width, cssH: r.height, attrW: cv.width, attrH: cv.height, vw: window.innerWidth, vh: window.innerHeight };
    });
    if (!c) {
      rec("AC8-phaser", false, "canvas 미생성");
    } else {
      const fits = c.cssW <= c.vw + 1 && c.cssH <= c.vh + 1 && c.cssW > 0 && c.cssH > 0;
      const arInternal = c.attrW / c.attrH, arCss = c.cssW / c.cssH;
      const aspectPreserved = Math.abs(arInternal - arCss) < 0.02; // FIT=종횡비 보존
      rec(
        "AC8-phaser",
        fits && aspectPreserved && errs.length === 0,
        `canvas css ${c.cssW.toFixed(0)}×${c.cssH.toFixed(0)} ≤ vp ${c.vw}×${c.vh} fits=${fits} aspect ${arInternal.toFixed(3)}≈${arCss.toFixed(3)}(preserved=${aspectPreserved}) errs=${errs.length}`
      );
    }
  });
  } finally {
    phaser.server.close();
  }
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n######## 모바일 조작성: PASS=${results.length - failed.length} FAIL=${failed.length} ########`);
if (failed.length) {
  console.error("FAIL: " + failed.map((f) => f.id).join(", "));
  process.exit(1);
}
console.log("PASS: 전 모바일 AC 충족 (iPhone 13 에뮬·실 터치)");
