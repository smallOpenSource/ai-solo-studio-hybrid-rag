/**
 * WA 온보딩 통과 → 게임씬 입장 검증.
 * 이름입력 → Continue → 워카선택 → 카메라(fake media 자동허용) → 게임씬.
 * 게임씬이 타일셋 PNG(office-chunk-1-*.png)를 fetch하면 입장 확정.
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers node web/scripts/live_check_wa_enter.mjs [shot]
 */
import { chromium } from "@playwright/test";

const FRONT = "https://studio.example.com:33157/";
const outShot = process.argv[2] || "/tmp/wa_enter_shot.png";
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
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    ignoreHTTPSErrors: true,
    permissions: ["camera", "microphone"],
  });
  const page = await ctx.newPage();
  const net = {};
  const wsEvents = [];
  const cerr = [];
  page.on("response", (r) => {
    if (r.url().includes("office-chunk-1")) net.tileset = r.status();
  });
  page.on("websocket", (ws) => {
    wsEvents.push("OPEN  " + ws.url().slice(0, 70));
    ws.on("close", () => wsEvents.push("CLOSE " + ws.url().slice(0, 50)));
    ws.on("socketerror", (e) => wsEvents.push("ERR   " + String(e).slice(0, 80)));
  });
  page.on("console", (m) => { if (m.type() === "error") cerr.push(m.text().slice(0, 160)); });

  await page.goto(FRONT, { waitUntil: "load", timeout: 30000 });
  await sleep(4000);

  // 1) 이름 입력
  try {
    await page.getByPlaceholder(/name/i).fill("AIGS-Bot", { timeout: 5000 });
  } catch {
    try { await page.locator('input[type="text"]').first().fill("AIGS-Bot", { timeout: 4000 }); } catch {}
  }

  // 2) 전진 버튼 반복 클릭 (이름→워카→카메라→게임)
  const labels = [/let.?s go/i, /jump into/i, /continue/i, /finish/i, /next/i, /save/i, /^go$/i, /^ok$/i, /play/i, /enter/i, /start/i, /계속/, /저장/, /시작/, /입장/];
  const seen = [];
  for (let i = 0; i < 12 && net.tileset === undefined; i++) {
    await sleep(1800);
    const btns = await page.locator("button:visible").allInnerTexts().catch(() => []);
    seen.push(`[${i}] ${JSON.stringify(btns.slice(0, 8))}`);
    let clicked = false;
    for (const re of labels) {
      const b = page.getByRole("button", { name: re });
      if (await b.count().catch(() => 0)) {
        try { await b.first().click({ timeout: 2500 }); clicked = true; break; } catch {}
      }
    }
    if (!clicked) {
      // 워카 카드/이미지 먼저 선택해야 Continue 활성화되는 경우
      try {
        const card = page.locator('[class*="woka" i] img, .woka, img[alt*="woka" i], canvas + button').first();
        if (await card.count()) await card.click({ timeout: 1500 });
      } catch {}
    }
  }

  await sleep(18000); // 룸 WS 연결 + GameScene 전환 대기
  const canvas = await page.evaluate(() => {
    const c = document.querySelector("canvas");
    return c ? { found: true, w: c.width, h: c.height } : { found: false };
  });
  const bodyText = await page.evaluate(() =>
    document.body ? document.body.innerText.slice(0, 300) : ""
  );
  await page.screenshot({ path: outShot });

  const connecting = /connecting/i.test(bodyText);
  console.log("=== WA ENTER CHECK ===");
  console.log("net      :", JSON.stringify(net));
  console.log("canvas   :", JSON.stringify(canvas));
  console.log("steps    :");
  seen.forEach((s) => console.log("   " + s));
  console.log("ws       :");
  wsEvents.forEach((w) => console.log("   " + w));
  console.log("cerr     :", cerr.slice(0, 8));
  console.log("body     :", JSON.stringify(bodyText.slice(0, 140)));
  console.log("connecting?", connecting);
  console.log("shot     :", outShot);
  const inGame = net.tileset === 200 && !connecting;
  console.log("VERDICT  :", inGame ? "IN-OFFICE (connected, game scene)" : net.tileset === 200 ? "ENTERED-BUT-CONNECTING" : "PARTIAL");
  await ctx.close();
  process.exit(inGame ? 0 : 2);
} finally {
  await browser.close();
}
