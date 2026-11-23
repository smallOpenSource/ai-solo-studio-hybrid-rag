/**
 * WA 직원 아바타 상주 서비스 — 9 페르소나를 WorkAdventure 오피스에 상주 아바타로 입장시킨다.
 *
 * 설계(리소스 안전): 단일 headless chromium + 페르소나당 1 context/page(엔진 공유).
 *   - 로스터 = orch /agents(canonical·personas.yaml−secretary). 이름 = label(김도윤…).
 *   - 암호 게이트(play :33157 basic-auth) 통과 = context httpCredentials(.env HITL_OWNER·값 비출력).
 *   - 입장 = office-chunk-1 tileset 200 + room WS. 스태거 입장(thundering herd 방지).
 *   - 헬스루프: canvas 소실 시 재입장. SIGTERM/SIGINT → 전 세션 정리 후 exit0.
 *   - 종료성: 페르소나 수 고정(무한 스폰 0)·각 입장 시도 바운드(클릭 ≤12). watchdog=systemd Restart.
 *
 * 실행: cd /opt/studio/web && PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
 *       node scripts/wa_avatar_presence.mjs
 */
import { chromium } from "@playwright/test";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";

const FRONT = "https://studio.example.com:33157/";
const ORCH_AGENTS = "http://127.0.0.1:28202/agents";
const STAGGER_MS = 4000;
const HEALTH_MS = 45000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString(), ...a);

// ★in-world presence 발행(verify_phase6 조건부 라이브 게이트가 소비) — systemd active 만으론
// 입장 0 이어도 통과(green-fake) 가능하므로 실 in-world 카운트+fresh ts 를 파일로 내보낸다.
const STATUS_PATH = "/opt/studio/state/wa_avatars_status.json";
function writeStatus(inWorld, total) {
  try {
    mkdirSync("/opt/studio/state", { recursive: true });
    writeFileSync(STATUS_PATH, JSON.stringify({ in_world: inWorld, total, ts: Math.floor(Date.now() / 1000) }));
  } catch (e) { log("status write 실패", String(e).slice(0, 60)); }
}

function loadCreds() {
  const env = {};
  for (const line of readFileSync("/opt/studio/.env", "utf8").split("\n")) {
    const s = line.trim();
    if (!s || s.startsWith("#") || !s.includes("=")) continue;
    const i = s.indexOf("=");
    env[s.slice(0, i).trim()] = s.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
  return { username: env.HITL_OWNER_NAME, password: env.HITL_OWNER_PASSWORD };
}

async function roster() {
  const r = await fetch(ORCH_AGENTS);
  const j = await r.json();
  return (j.agents || []).map((a) => ({ id: a.id, name: a.label })).filter((a) => a.name);
}

const LABELS = [/let.?s go/i, /jump into/i, /continue/i, /finish/i, /next/i, /save/i, /^go$/i, /^ok$/i, /play/i, /enter/i, /start/i, /계속/, /저장/, /시작/, /입장/];

async function enter(page, name) {
  let tileset;
  page.on("response", (r) => { if (r.url().includes("office-chunk-1")) tileset = r.status(); });
  let gotoOk = false;
  for (let g = 0; g < 2 && !gotoOk; g++) {
    try { await page.goto(FRONT, { waitUntil: "load", timeout: 45000 }); gotoOk = true; }
    catch { await sleep(3000); }
  }
  await sleep(4000);
  try { await page.getByPlaceholder(/name/i).fill(name, { timeout: 5000 }); }
  catch { try { await page.locator('input[type="text"]').first().fill(name, { timeout: 4000 }); } catch {} }
  for (let i = 0; i < 12 && tileset === undefined; i++) {
    await sleep(1800);
    let clicked = false;
    for (const re of LABELS) {
      const b = page.getByRole("button", { name: re });
      if (await b.count().catch(() => 0)) { try { await b.first().click({ timeout: 2500 }); clicked = true; break; } catch {} }
    }
    if (!clicked) { try { const card = page.locator('[class*="woka" i] img, .woka, img[alt*="woka" i]').first(); if (await card.count()) await card.click({ timeout: 1500 }); } catch {} }
  }
  await sleep(12000);
  return tileset === 200;
}

const creds = loadCreds();
if (!creds.username || !creds.password) { log("FATAL: HITL_OWNER creds 미설정(.env)"); process.exit(1); }

let personas;
try { personas = await roster(); } catch (e) { log("FATAL: orch /agents 조회 실패", String(e).slice(0, 80)); process.exit(1); }
if (!personas.length) { log("FATAL: 로스터 비어있음"); process.exit(1); }
log(`로스터 ${personas.length}명:`, personas.map((p) => p.name).join(", "));

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox", "--disable-setuid-sandbox",
    "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
    "--host-resolver-rules=MAP studio.example.com 127.0.0.1",
    "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
  ],
});

const sessions = [];
async function spawn(p) {
  const ctx = await browser.newContext({
    viewport: { width: 800, height: 600 },
    ignoreHTTPSErrors: true,
    permissions: ["camera", "microphone"],
    httpCredentials: creds,
  });
  const page = await ctx.newPage();
  const ok = await enter(page, p.name);
  sessions.push({ p, ctx, page });
  log(`[avatar] ${p.name} (${p.id}) ${ok ? "IN-OFFICE" : "ENTER-INCOMPLETE"}`);
  return ok;
}

let entered = 0;
for (const p of personas) { try { if (await spawn(p)) entered++; } catch (e) { log(`[avatar] ${p.name} ERROR`, String(e).slice(0, 80)); } await sleep(STAGGER_MS); }
log(`상주 시작: ${entered}/${personas.length} IN-OFFICE`);
writeStatus(entered, personas.length);

let stop = false;
const shutdown = async () => { if (stop) return; stop = true; log("SIGTERM → 정리"); try { await browser.close(); } catch {} process.exit(0); };
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

while (!stop) {
  await sleep(HEALTH_MS);
  let inWorld = 0;
  for (const s of sessions) {
    if (stop) break;
    let alive = await s.page.evaluate(() => !!document.querySelector("canvas")).catch(() => false);
    if (!alive) {
      log(`[avatar] ${s.p.name} 드롭 → 재입장`);
      try { alive = await enter(s.page, s.p.name); log(`[avatar] ${s.p.name} 재입장 ${alive ? "OK" : "FAIL"}`); } catch (e) { log(`[avatar] ${s.p.name} 재입장 ERROR`, String(e).slice(0, 80)); }
    }
    if (alive) inWorld++;
  }
  if (!stop) writeStatus(inWorld, personas.length);
}
