/**
 * B1 #9 story-idle e2e — 서사형 인크리멘탈 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(C-1.3·12종 만다트): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/story-idle.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비거나 load-bearing 라인(init/buy/coupling/beat)이 누락(부정 변이)이면 계약 파생 불가
 *   → FAIL(사후 날조/하드코딩 적발). verify 가 정상 brief→PASS·변이 brief→FAIL 양 경로를 구동(실증).
 * ★결합 불변식(AC2 [CORE]): __buyGenerator() → resource=old-cost AND rate+=1 AND generators+=1, 이후
 *   __tick(1) 이 **새** rate 만큼 가산. 단일 카운터(resource++)로는 불충족 → 렌더-only/stub theater 차단.
 * ★서사 게이트 창발(AC3): resource>=threshold[beat] 도달 시 beat 창발·monotonic(소비로 미회귀)·직접세팅 부재.
 * ★결정론 grep(§2.5·#9 step/tick): 게임 소스 gated 경로에 Date.now/performance.now/requestAnimationFrame
 *   read 0 을 grep 으로 자동 강제(belt-and-suspenders).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_story-idle_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/story-idle.md. (verify 가 부정 변이본 경로를 넘김.)
 * exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefPath = process.argv[2] || path.join(REPO, "vault", "specs", "story-idle.md");
const gameRel = path.join("games", "story-idle", "index.html");
const gameAbs = path.join(REPO, gameRel);
const gameUrl = "file://" + gameAbs;

const fail = (msg) => { console.error("FAIL: " + msg); process.exit(1); };

// ★e2e 하드 백스톱(≤30s·무한루프/hang 가드·자해 방지). 정상 경로는 수 초 내 종료.
const HARD_TIMEOUT_MS = 30000;
const guard = setTimeout(() => { console.error("FAIL: e2e 하드 타임아웃(30s) 초과 — hang 가드 발동."); process.exit(1); }, HARD_TIMEOUT_MS);
guard.unref();

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
  const buyLine = find("buy:");
  const couplingLine = find("coupling:");
  const beatLine = find("beat:");
  // load-bearing 4종(init/buy/coupling/beat)이 비면 계약 파생 불가 → provenance 위반(부정 변이/하드코딩).
  if (!initLine || !buyLine || !couplingLine || !beatLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/buy/coupling/beat 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // coupling anti-hollow 토큰(자원감소·rate증가·다음틱가산)이 load-bearing — 변이 시 출처 불명 → FAIL.
  if (!/old_resource-cost/i.test(couplingLine) || !/rate==old_rate\+1/i.test(couplingLine) || !/next_tick/i.test(couplingLine)) {
    fail("coupling 계약(자원감소·rate증가·다음틱가산) load-bearing 토큰 부재 — anti-hollow 출처 불명(부정 변이).");
  }
  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  const costM = buyLine.match(/cost=(\d+)/);
  const rateM = buyLine.match(/rate\+=(\d+)/);
  const thrM = beatLine.match(/thresholds=\[([\d,\s]+)\]/);
  if (!costM || !rateM) fail("buy 계약(cost/rate 증분) 파싱 불가.");
  if (!thrM) fail("beat 계약(thresholds 배열) 파싱 불가 — 서사 게이트 임계 출처 불명.");
  if (init.resource === undefined || init.rate === undefined || init.generators === undefined || init.beat === undefined) {
    fail("init 계약 파싱 불가(resource/rate/generators/beat).");
  }
  const thresholds = thrM[1].split(",").map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x));
  if (thresholds.length === 0) fail("thresholds 비어있음.");
  return { init, cost: Number(costM[1]), rateInc: Number(rateM[1]), thresholds };
}

// ── 2) 결정론 grep(§2.5·#9): 게임 소스 gated 경로에 wall-clock read 0 강제 ──────────
function assertNoWallClock() {
  const src = fs.readFileSync(gameAbs, "utf-8");
  const re = /\b(Date\.now|performance\.now|requestAnimationFrame)\b/g;
  const hits = src.match(re) || [];
  if (hits.length > 0) {
    fail(
      `결정론 위반(grep): 게임 소스에 wall-clock read ${hits.length}건(${[...new Set(hits)].join(", ")}) — ` +
        "gated 경로는 __tick(n) 논리 틱만 허용(별도 dt/now 누산 금지)."
    );
  }
  return true;
}

// ── 3) playwright 구동 + 결합 불변식/서사 게이트 단언 ──────────────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);
assertNoWallClock();

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
  page.setDefaultTimeout(15000);
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text());
  });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load" });

  // 헤드리스 결정론: 라이브 방치 타이머 동결(이후 __tick(n) 수동 스텝만 상태 변경).
  await page.evaluate(() => { if (window.__stopIdle) window.__stopIdle(); });

  const gs = () => page.evaluate(() => window.__state);
  const consts = () => page.evaluate(() => window.__constants);
  const click = (n) => page.evaluate((k) => { for (let i = 0; i < k; i++) window.__click(); }, n);
  const tick = (n) => page.evaluate((k) => window.__tick(k), n);
  const buy = () => page.evaluate(() => window.__buyGenerator());
  const reset = () => page.evaluate(() => window.__resetGame());
  const hasSetter = () => page.evaluate(() => typeof window.__setBeat !== "undefined" || typeof window.__setState !== "undefined");

  // (drift) 게임 상수 ↔ 브리프 파생 임계/cost 일치(하드코딩 적발)
  const K = await consts();
  if (!K) fail("window.__constants 미정의 — brief↔게임 drift 단언 불가.");
  if (K.GEN_COST !== C.cost) fail(`drift: 게임 GEN_COST=${K.GEN_COST} ≠ 브리프 cost=${C.cost}.`);
  if (JSON.stringify(K.NARRATIVE_THRESHOLD) !== JSON.stringify(C.thresholds)) {
    fail(`drift: 게임 임계=${JSON.stringify(K.NARRATIVE_THRESHOLD)} ≠ 브리프 thresholds=${JSON.stringify(C.thresholds)}.`);
  }
  if (await hasSetter()) fail("anti-hollow: beat 직접세터(__setBeat/__setState) 존재 — 창발 위반(direct-set forbidden).");

  // (a) 초기 상태 = 브리프 init 계약(AC1)
  await reset();
  const s0 = await gs();
  if (!s0 || typeof s0.resource !== "number" || !Array.isArray(s0.beatsShown)) fail("window.__state(또는 beatsShown 배열) 미정의.");
  for (const k of Object.keys(C.init)) {
    if (s0[k] !== C.init[k]) fail(`init 불일치 ${k}: 기대 ${C.init[k]} 실제 ${s0[k]}`);
  }
  if (s0.beatsShown.length !== 0) fail(`init beatsShown 비어있지 않음: ${s0.beatsShown.length}`);

  // (b) 방치 결정론(AC4): tick(n) → resource == old + rate*n (논리 틱)
  await reset();
  await tick(5);
  const sIdle = await gs();
  if (sIdle.resource !== C.init.resource + C.init.rate * 5) {
    fail(`방치 틱 불일치: 기대 ${C.init.resource + C.init.rate * 5} 실제 ${sIdle.resource} (rate=${C.init.rate})`);
  }
  if (sIdle.rate !== C.init.rate) fail(`방치 중 rate 변동(기대 불변 ${C.init.rate} 실제 ${sIdle.rate}).`);

  // (c) ★[CORE] 결합 불변식(AC2): tick 으로 cost 충전 → buy → resource=old-cost AND rate+=1 AND generators+=1
  await reset();
  await tick(C.cost);                                   // resource = cost(=10), rate=1
  const preBuy = await gs();
  const bought = await buy();
  if (!bought) fail("생성기 구매 실패(resource>=cost 인데 미구매).");
  const postBuy = await gs();
  const couplingBuy =
    postBuy.resource === preBuy.resource - C.cost &&
    postBuy.rate === preBuy.rate + C.rateInc &&
    postBuy.generators === preBuy.generators + 1;
  if (!couplingBuy) {
    fail(
      `결합 불변식(구매) 위반: pre(resource=${preBuy.resource},rate=${preBuy.rate},gen=${preBuy.generators}) ` +
        `post(resource=${postBuy.resource},rate=${postBuy.rate},gen=${postBuy.generators}) cost=${C.cost} Δrate=${C.rateInc}`
    );
  }

  // (d) ★결합 불변식(AC2): 구매 후 __tick(1) = **새** rate 만큼 가산(단일 카운터로는 불가)
  await tick(1);
  const sAfter = await gs();
  const couplingTick = sAfter.resource === postBuy.resource + postBuy.rate && postBuy.rate > preBuy.rate;
  if (!couplingTick) {
    fail(`결합 불변식(구매후 틱) 위반: 기대 ${postBuy.resource + postBuy.rate} 실제 ${sAfter.resource} (새 rate=${postBuy.rate}, 이전=${preBuy.rate})`);
  }

  // (e) ★서사 게이트 창발(AC3): resource>=threshold[0] 도달 시 beat 창발 + beatsShown 증가
  await reset();
  const t0 = C.thresholds[0];
  await tick(t0 - 1);                                   // 임계 직전(negative: 미발화)
  const below = await gs();
  if (below.beat !== 0 || below.beatsShown.length !== 0) {
    fail(`서사 게이트 negative 위반: 임계 미달(resource=${below.resource}<${t0})인데 beat=${below.beat} beatsShown=${below.beatsShown.length}.`);
  }
  await tick(1);                                        // 임계 도달 → beat 창발
  const at = await gs();
  if (at.beat !== 1 || at.beatsShown.length !== 1) {
    fail(`서사 게이트 창발 위반: 임계 도달(resource=${at.resource}>=${t0})인데 beat=${at.beat} beatsShown=${at.beatsShown.length} (기대 1/1).`);
  }
  if (typeof at.beatsShown[0] !== "string" || at.beatsShown[0].length === 0) fail("서사 비트 텍스트 미노출(빈 문자열).");

  // (f) ★monotonic 창발(현재자원 거울 falsify): beat 해금 후 자원 소비(buy)로 임계 아래로 내려가도 beat 미회귀
  const beforeSpend = await gs();                       // resource>=t0(=20), beat=1
  const spentOk = await buy();                          // resource -= cost(=10) → t0 아래로
  if (!spentOk) fail("monotonic 검증 준비 실패(buy 미성공).");
  const afterSpend = await gs();
  if (afterSpend.resource >= t0) fail(`monotonic 검증 준비 실패: 소비 후 자원(${afterSpend.resource})이 여전히 임계(${t0}) 이상.`);
  if (afterSpend.beat !== beforeSpend.beat || afterSpend.beatsShown.length !== beforeSpend.beatsShown.length) {
    fail(`monotonic 위반(현재자원 거울 의심): 임계 아래 소비 후 beat 회귀 ${beforeSpend.beat}→${afterSpend.beat}.`);
  }

  // (g) 다중 임계 한 번에 통과 창발: 큰 틱으로 마지막 임계까지 → beat == thresholds.length
  await reset();
  const last = C.thresholds[C.thresholds.length - 1];
  await tick(last);
  const sMax = await gs();
  if (sMax.beat !== C.thresholds.length || sMax.beatsShown.length !== C.thresholds.length) {
    fail(`다중 임계 창발 위반: resource=${sMax.resource}>=${last} 인데 beat=${sMax.beat}/${C.thresholds.length}.`);
  }

  // (h) negative(AC): resource<cost → buy 거부(상태 불변)
  await reset();
  await tick(C.cost - 1);                               // resource = cost-1 (< cost)
  const preNeg = await gs();
  const rejected = await buy();
  const postNeg = await gs();
  if (rejected !== false) fail("negative 위반: resource<cost 인데 buy 미거부.");
  if (postNeg.resource !== preNeg.resource || postNeg.rate !== preNeg.rate || postNeg.generators !== preNeg.generators) {
    fail("negative 위반: buy 거부인데 상태 변동(우회 세팅 의심).");
  }

  // (i) 결정론 재실행(AC4): 동일 입력열 → 동일 __state (resetGame 후 재현)
  const seq = async () => { await reset(); await tick(10); await buy(); await tick(2); await click(3); return gs(); };
  const runA = await seq();
  const runB = await seq();
  if (JSON.stringify(runA) !== JSON.stringify(runB)) {
    fail(`결정론 위반: 동일 입력열 재실행 불일치\nA=${JSON.stringify(runA)}\nB=${JSON.stringify(runB)}`);
  }

  const couplingInvariant = couplingBuy && couplingTick;
  const narrativeEmergence = at.beat === 1 && sMax.beat === C.thresholds.length && below.beat === 0;
  const verdict = couplingInvariant && narrativeEmergence && fatals.length === 0;

  console.log(JSON.stringify({
    verdict, couplingInvariant, narrativeEmergence, fatals: fatals.length,
    contract: C,
    sample: { s0, sIdle, preBuy, postBuy, sAfter, below, at, afterSpend, sMax, runA },
    briefPath,
  }, null, 2));

  if (!verdict) fail(`VERDICT false (coupling=${couplingInvariant} narrative=${narrativeEmergence} fatals=${fatals.length})`);
  console.log("PASS: #9 story-idle 결합 불변식 + 서사 게이트 창발 + 방치 결정론 + 무에러 (브리프-구동)");
} finally {
  await browser.close();
  clearTimeout(guard);
}
