/**
 * P2.5 C4 e2e — 클리커 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(C-1.3): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/<id>.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC가 비면(부정 변이) 계약 파생 불가 → FAIL(사후 날조/하드코딩 적발).
 * ★결합 불변식(M-1): 업그레이드 구매 시 score=old-cost AND clickPower+=Δ, 이후 클릭이
 *   새 clickPower 만큼 증가. 단일 카운터(score++)로는 불충족 → 렌더-only/stub theater 차단.
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_clicker_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/p25-clicker-mvp.md. (verify 가 부정 변이본 경로를 넘김.)
 * exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefPath = process.argv[2] || path.join(REPO, "vault", "specs", "p25-clicker-mvp.md");
const gameUrl = "file://" + path.join(REPO, "games", "clicker-mvp", "index.html");

const fail = (msg) => { console.error("FAIL: " + msg); process.exit(1); };

// ── 1) 브리프 파싱 → 계약 파생 (handoff.parse_brief 재사용·provenance) ──────────
function loadBrief() {
  const py =
    "import sys,json;from orch.handoff import parse_brief;" +
    "print(json.dumps(parse_brief(open(sys.argv[1],encoding='utf-8').read())" +
    ".model_dump(by_alias=True),ensure_ascii=False))";
  const raw = execFileSync(ORCH_PY, ["-c", py, briefPath], {
    env: { ...process.env, PYTHONPATH: REPO },
    encoding: "utf-8",
  });
  return JSON.parse(raw);
}

function deriveContract(ac) {
  const find = (p) => ac.find((s) => s.trim().toLowerCase().startsWith(p));
  const initLine = find("init:");
  const clickLine = find("click:");
  const upgradeLine = find("upgrade:");
  const couplingLine = find("coupling:");
  if (!initLine || !clickLine || !upgradeLine || !couplingLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/click/upgrade/coupling 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  const costM = upgradeLine.match(/cost=(\d+)/);
  const powM = upgradeLine.match(/clickPower\+=(\d+)/);
  if (!costM || !powM) fail("upgrade 계약(cost/clickPower 증분) 파싱 불가.");
  if (init.score === undefined || init.clickPower === undefined) fail("init 계약 파싱 불가.");
  return { init, cost: Number(costM[1]), powInc: Number(powM[1]) };
}

// ── 2) playwright 구동 + 결합 불변식 단언 ──────────────────────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
const fatals = [];
try {
  const page = await browser.newPage();
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text());
  });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load" });

  const gs = () => page.evaluate(() => window.__gameState);
  const click = (n) => page.evaluate((k) => { for (let i = 0; i < k; i++) window.__click(); }, n);
  const buy = () => page.evaluate(() => window.__buyUpgrade());

  // (a) 초기 상태 = 브리프 init 계약
  const s0 = await gs();
  if (!s0 || typeof s0.score !== "number") fail("window.__gameState 미정의.");
  for (const k of Object.keys(C.init)) {
    if (s0[k] !== C.init[k]) fail(`init 불일치 ${k}: 기대 ${C.init[k]} 실제 ${s0[k]}`);
  }

  // (b) 클릭 누적 = clickPower (상태전이 S0)
  await click(10);
  const s1 = await gs();
  if (s1.score !== C.init.clickPower * 10) fail(`클릭 누적 불일치: 기대 ${C.init.clickPower * 10} 실제 ${s1.score}`);
  if (s1.clicks !== 10) fail(`clicks 불일치: 기대 10 실제 ${s1.clicks}`);
  const scoreIncreased = s1.score > s0.score;

  // (c) ★결합 불변식: 업그레이드 구매 → score=old-cost AND clickPower+=Δ
  const preBuy = await gs();
  const bought = await buy();
  if (!bought) fail("업그레이드 구매 실패(score>=cost 인데 미구매).");
  const postBuy = await gs();
  const couplingBuy =
    postBuy.score === preBuy.score - C.cost &&
    postBuy.clickPower === preBuy.clickPower + C.powInc &&
    postBuy.upgrades === preBuy.upgrades + 1;
  if (!couplingBuy) {
    fail(
      `결합 불변식(구매) 위반: pre(score=${preBuy.score},power=${preBuy.clickPower}) ` +
        `post(score=${postBuy.score},power=${postBuy.clickPower}) cost=${C.cost} Δpow=${C.powInc}`
    );
  }

  // (d) ★결합 불변식: 구매 후 1클릭 = 새 clickPower 만큼 증가(단일 카운터로는 불가)
  await click(1);
  const s2 = await gs();
  const couplingClick = s2.score === postBuy.score + postBuy.clickPower && postBuy.clickPower > preBuy.clickPower;
  if (!couplingClick) {
    fail(`결합 불변식(구매후 클릭) 위반: 기대 ${postBuy.score + postBuy.clickPower} 실제 ${s2.score} (새power=${postBuy.clickPower})`);
  }

  // (e) 상태전이 S0→S1: score>=50 → stage 1
  let guard = 0;
  while ((await gs()).score < 50 && guard < 100) { await click(1); guard++; }
  const s3 = await gs();
  const stageTransition = s3.stage === 1 && s3.score >= 50;
  if (!stageTransition) fail(`stage 전이 실패: score=${s3.score} stage=${s3.stage} (기대 stage=1)`);

  const couplingInvariant = couplingBuy && couplingClick;
  const stateTransition = scoreIncreased && stageTransition;
  const verdict = couplingInvariant && stateTransition && fatals.length === 0;

  console.log(JSON.stringify({
    verdict, couplingInvariant, stateTransition, fatals: fatals.length,
    sample: { s0, s1, postBuy, s2, s3 }, contract: C, briefPath,
  }, null, 2));

  if (!verdict) fail(`VERDICT false (coupling=${couplingInvariant} transition=${stateTransition} fatals=${fatals.length})`);
  console.log("PASS: C4 결합 불변식 + 상태전이 + 무에러 (브리프-구동)");
} finally {
  await browser.close();
}
