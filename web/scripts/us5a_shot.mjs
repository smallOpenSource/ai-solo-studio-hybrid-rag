/** US-5a 라이브 캡처 — office-v2 Phaser 맵(타일맵+16역할) 실렌더 스크린샷. */
import { chromium } from "@playwright/test";

const URL = "http://127.0.0.1:33006/";
const errs = [];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } });
page.on("pageerror", (e) => errs.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") errs.push(m.text());
});

await page.goto(URL, { waitUntil: "load" });
await page.waitForTimeout(700);
await page.click('[data-testid="view-toggle-office"]');
await page.waitForTimeout(3500); // Phaser preload(tilemap+16 PNG)+create.

const canvas = await page.evaluate(() => document.querySelectorAll("canvas").length);
const hasGL = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  return !!(c && (c.getContext("webgl2") || c.getContext("webgl")));
});
await page.screenshot({ path: "work/lpc/us5a_live_top.png" });

await browser.close();
const fatal = errs.filter(
  (e) => !/SwiftShader|swiftshader|GroupMarkerNotSet|Failed to load resource.*favicon/i.test(e),
);
console.log("CANVAS:", canvas, "WEBGL:", hasGL);
console.log("FATAL_ERRORS:", fatal.length, fatal.slice(0, 6));
console.log("SHOT: work/lpc/us5a_live_top.png");
process.exit(canvas >= 1 && hasGL && fatal.length === 0 ? 0 : 1);
