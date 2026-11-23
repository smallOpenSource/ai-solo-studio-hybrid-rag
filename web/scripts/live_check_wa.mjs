/**
 * WA 라이브 검증 — 사용자가 겪은 경로(공개 루트 진입)를 헤드리스로 재현.
 *
 * VM 은 공개 도메인 https-egress 가 막혀 있어 --host-resolver-rules 로
 * studio.example.com → 127.0.0.1 매핑(로컬 TLS 프록시로 라우팅·인증서 CN 그대로 일치).
 * WebGL(Phaser) → swiftshader. WS 상시연결 앱이라 networkidle 금지(load + 고정대기).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
 *        node web/scripts/live_check_wa.mjs [shotPath]
 */
import { chromium } from "@playwright/test";

const FRONT = "https://studio.example.com:33157/";
const outShot = process.argv[2] || "/tmp/wa_live_shot.png";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--host-resolver-rules=MAP studio.example.com 127.0.0.1",
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
  ],
});
try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const net = {};
  const failed = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text().slice(0, 200));
  });
  page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 200)));
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("office.tmj")) net["office.tmj"] = r.status();
    else if (u.includes("office-chunk-1")) net["tileset.png"] = r.status();
    else if (u.includes("mapDetails") || u.includes("map-details")) net["mapDetails"] = r.status();
  });
  page.on("requestfailed", (r) =>
    failed.push(`${r.url().slice(0, 90)} :: ${r.failure()?.errorText}`)
  );

  await page.goto(FRONT, { waitUntil: "load", timeout: 30000 });
  await sleep(15000); // SPA 라우팅 + mapDetails + Phaser 초기화 + 맵 로드

  const finalUrl = page.url();
  const canvas = await page.evaluate(() => {
    const c = document.querySelector("canvas");
    return c ? { found: true, w: c.width, h: c.height } : { found: false };
  });
  const bodyText = await page.evaluate(() =>
    document.body ? document.body.innerText.slice(0, 600) : ""
  );
  await page.screenshot({ path: outShot, fullPage: false });

  const blob = bodyText + "\n" + consoleErrors.join("\n") + "\n" + pageErrors.join("\n");
  const formatErr = blob.includes("UNSUPPORTED_URL_FORMAT") || /Unsupported (path|URL)/i.test(blob);
  const routedToRoom = finalUrl.includes("/_/global/");

  console.log("=== WA LIVE CHECK ===");
  console.log("finalUrl   :", finalUrl);
  console.log("routedRoom :", routedToRoom);
  console.log("net        :", JSON.stringify(net));
  console.log("canvas     :", JSON.stringify(canvas));
  console.log("formatErr? :", formatErr);
  console.log("failedReq  :", failed.slice(0, 6));
  console.log("consoleErr :", consoleErrors.slice(0, 10));
  console.log("pageErr    :", pageErrors.slice(0, 5));
  console.log("bodyHead   :", JSON.stringify(bodyText.slice(0, 160)));
  console.log("shot       :", outShot);

  const mapOK = net["office.tmj"] === 200 && net["tileset.png"] === 200;
  const verdict = !formatErr && routedToRoom && mapOK && canvas.found;
  console.log("VERDICT    :", verdict ? "PASS" : "INVESTIGATE");
  await context.close();
  process.exit(verdict ? 0 : 2);
} finally {
  await browser.close();
}
