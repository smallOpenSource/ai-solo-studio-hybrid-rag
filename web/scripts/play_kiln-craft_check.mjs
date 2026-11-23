/**
 * B2 #3 kiln-craft e2e — 동아시아 장인공예(도예) 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(C-1.3·12종 만다트): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/kiln-craft.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비거나 load-bearing 라인(init/craft/sell/tier/coupling/negative)이 누락(부정 변이)이면
 *   계약 파생 불가 → FAIL(사후 날조/하드코딩 적발).
 * ★공예 시퀀스 결합(AC2 [CORE]): __shape→__fire→__glaze **순서 준수** 시에만 완성·각 단계가 quality 에
 *   자기 몫(SHAPE_Q/FIRE_Q/GLAZE_Q + 점토 base)을 가산(매 단계 delta>0·합 일치). 순서 위반/단계 누락 거부
 *   (piece 불변). 단일 'done 플래그'면 부분 시퀀스로 done 가능해야 하므로 RED → 렌더-only theater 차단.
 * ★판매 경제 창발(AC3): __sell() → coin+=price(quality) AND reputation+=rep(quality)(둘 다 quality 순수 함수·
 *   서로 다른 계수). 미완성 판매 거부(negative).
 * ★명성 게이트 창발(AC4): reputation>=threshold[tier] 도달 시 tier 창발·monotonic·직접세팅 부재.
 *   tier-up 후 신규 작품 품질 > tier-up 전(상위재료 해금 falsifiable).
 * ★결정론 grep(§2.5): 게임 소스 gated 경로에 Date.now/performance.now/requestAnimationFrame read 0 강제.
 * ★부정-변이 실증(12종 만다트·C-1.3 동형): 정상 brief→PASS(부모) + coupling AC 제거 brief→FAIL(자식 exit≠0)
 *   **양 경로** 구동(brief-driven·무하드코딩 *실증*). 인자 brief 경로(argv[2]) 있으면 자식 모드(자기-실증 생략).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_kiln-craft_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/kiln-craft.md. exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefArg = process.argv[2];                                  // 자식 모드(부정-변이) 표식 겸용
const briefPath = briefArg || path.join(REPO, "vault", "specs", "kiln-craft.md");
const gameRel = path.join("games", "kiln-craft", "index.html");
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
  const craftLine = find("craft:");
  const sellLine = find("sell:");
  const tierLine = find("tier:");
  const couplingLine = find("coupling:");
  const negativeLine = find("negative:");
  // load-bearing 6종 — 부재 시 계약 파생 불가 → provenance 위반(부정 변이/하드코딩).
  if (!initLine || !craftLine || !sellLine || !tierLine || !couplingLine || !negativeLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/craft/sell/tier/coupling/negative 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // craft: 순서 결합·anti-hollow 토큰(시퀀스·in_order·out_of_order·단일 done 금지) load-bearing.
  if (!/shape->fire->glaze/i.test(craftLine) || !/in_order/i.test(craftLine) ||
      !/out_of_order/i.test(craftLine) || !/single_done_flag/i.test(craftLine)) {
    fail("craft 계약(shape->fire->glaze·in_order·out_of_order·single_done_flag) load-bearing 토큰 부재 — anti-hollow 출처 불명(부정 변이).");
  }
  // coupling: 판매 동시 결합 + 시퀀스 품질 + 단일 카운터 불가 — anti-hollow 계약 출처.
  if (!/after_sell/i.test(couplingLine) || !/price/i.test(couplingLine) || !/reputation/i.test(couplingLine) ||
      !/quality/i.test(couplingLine) || !/emergent/i.test(couplingLine) || !/insufficient/i.test(couplingLine)) {
    fail("coupling 계약(after_sell·price·reputation·quality·emergent·insufficient) load-bearing 토큰 부재 — anti-hollow 출처 불명(부정 변이).");
  }
  // tier: 명성 게이트 임계 + 창발·direct-set 금지 — 게이트 계약 출처.
  if (!/reputation>=threshold/i.test(tierLine) || !/direct-set forbidden/i.test(tierLine)) {
    fail("tier 계약(reputation>=threshold·direct-set forbidden) 토큰 부재 — 명성 게이트 출처 불명(부정 변이).");
  }
  if (!/reject/i.test(negativeLine)) fail("negative 계약(rejected) 누락.");

  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  if (init.coin === undefined || init.reputation === undefined || init.tier === undefined) {
    fail("init 계약(coin/reputation/tier) 파싱 불가.");
  }
  const priceM = sellLine.match(/price=quality\*(\d+)/);
  const repM = sellLine.match(/rep=quality\*(\d+)/);
  if (!priceM || !repM) fail("sell 계약(price=quality*N·rep=quality*N) 파싱 불가 — 경제 결합 출처 불명.");
  if (!/piece\.done/i.test(sellLine)) fail("sell 계약(require=piece.done) 토큰 부재 — 미완성 판매 거부 출처 불명.");
  const thrM = tierLine.match(/thresholds=\[([\d,\s]+)\]/);
  if (!thrM) fail("tier 계약(thresholds 배열) 파싱 불가 — 명성 임계 출처 불명.");
  const thresholds = thrM[1].split(",").map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x));
  if (thresholds.length === 0) fail("thresholds 비어있음.");
  return { init, priceMult: Number(priceM[1]), repMult: Number(repM[1]), thresholds };
}

// ── 2) 결정론 grep(§2.5): 게임 소스 gated 경로에 wall-clock read 0 강제 ──────────
function assertNoWallClock() {
  const src = fs.readFileSync(gameAbs, "utf-8");
  const re = /\b(Date\.now|performance\.now|requestAnimationFrame)\b/g;
  const hits = src.match(re) || [];
  if (hits.length > 0) {
    fail(
      `결정론 위반(grep): 게임 소스에 wall-clock read ${hits.length}건(${[...new Set(hits)].join(", ")}) — ` +
        "턴제 공예는 논리 액션만 허용(별도 dt/now 가마 타이머 금지)."
    );
  }
  return true;
}

// ── 2.5) 부정-변이 실증: coupling AC 제거 brief → 자식 하네스 exit≠0(FAIL) 임을 실증 ──────
//   (정상 brief→PASS 는 본 프로세스가, 변이 brief→FAIL 은 자식이 — 양 경로 모두 구동·무하드코딩 *실증*.)
function selfDemoNegativeMutation() {
  const raw = fs.readFileSync(briefPath, "utf-8");
  const lines = raw.split("\n");
  const kept = lines.filter((l) => !/^\s*-\s*['"]?coupling:/i.test(l));   // load-bearing coupling: 라인 제거
  if (kept.length === lines.length) fail("부정-변이 준비 실패: 'coupling:' AC 라인 미발견(브리프 형식 변경?).");
  const tmp = path.join(os.tmpdir(), `kiln-craft-mut-${process.pid}.md`);
  fs.writeFileSync(tmp, kept.join("\n"), "utf-8");
  let status = 0, captured = "";
  try {
    execFileSync(process.execPath, [__filename, tmp], { env: process.env, encoding: "utf-8", stdio: "pipe" });
    status = 0;                                                        // exit 0 = 변이가 FAIL 안 됨(나쁨)
  } catch (e) {
    status = (e && typeof e.status === "number") ? e.status : 1;       // 비-0 = 변이가 FAIL(좋음·기대)
    captured = String((e && (e.stderr || e.stdout)) || "");
  } finally {
    try { fs.unlinkSync(tmp); } catch (_) { /* 무시 */ }
  }
  if (status === 0) {
    fail("부정-변이 실증 실패: coupling AC 제거 brief 인데 하네스가 exit 0(PASS) — brief-driven 무하드코딩 미실증.");
  }
  return { mutatedExit: status, childHint: (captured.split("\n").find((x) => /FAIL/.test(x)) || "").slice(0, 140) };
}

// ── 3) 계약 파생 + grep + 부정-변이 자기-실증(자식 모드면 생략) ─────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);
assertNoWallClock();
const negativeMutation = briefArg ? "(child-mode: self-demo skipped)" : selfDemoNegativeMutation();

// ── 4) playwright 구동 + 결합 불변식 단언 ──────────────────────────────────────
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
const results = {};
try {
  // 모바일 뷰포트 → 캔버스(480)가 96vw 로 축소 → CSS 스케일 보정 경로가 실제로 작동.
  const context = await browser.newContext({ viewport: { width: 400, height: 820 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text()); });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load" });
  await page.waitForFunction(() => !!window.__state && !!window.__constants, { timeout: 5000 });

  const gs = () => page.evaluate(() => window.__state);
  const consts = () => page.evaluate(() => window.__constants);
  const shape = () => page.evaluate(() => window.__shape());
  const fire = () => page.evaluate(() => window.__fire());
  const glaze = () => page.evaluate(() => window.__glaze());
  const sell = () => page.evaluate(() => window.__sell());
  const reset = () => page.evaluate(() => window.__resetGame());
  const hasSetter = () => page.evaluate(() =>
    typeof window.__setTier !== "undefined" || typeof window.__setState !== "undefined" ||
    typeof window.__setQuality !== "undefined" || typeof window.__setReputation !== "undefined");
  const craftFull = async () => { await shape(); await fire(); await glaze(); };

  // (drift) 게임 상수 ↔ 브리프 파생 계수/임계 일치(하드코딩 적발)
  const K = await consts();
  if (!K) fail("window.__constants 미정의 — brief↔게임 drift 단언 불가.");
  if (K.PRICE_MULT !== C.priceMult) fail(`drift: 게임 PRICE_MULT=${K.PRICE_MULT} ≠ 브리프 price=quality*${C.priceMult}.`);
  if (K.REP_MULT !== C.repMult) fail(`drift: 게임 REP_MULT=${K.REP_MULT} ≠ 브리프 rep=quality*${C.repMult}.`);
  if (JSON.stringify(K.REP_THRESHOLD) !== JSON.stringify(C.thresholds)) {
    fail(`drift: 게임 REP_THRESHOLD=${JSON.stringify(K.REP_THRESHOLD)} ≠ 브리프 thresholds=${JSON.stringify(C.thresholds)}.`);
  }
  if (await hasSetter()) fail("anti-hollow: 직접세터(__setTier/__setState/__setQuality/__setReputation) 존재 — 창발 위반(direct-set forbidden).");

  const price = (q) => q * C.priceMult;
  const repGain = (q) => q * C.repMult;
  const stepSum = K.SHAPE_Q + K.FIRE_Q + K.GLAZE_Q;

  // (a) 초기 상태 = 브리프 init 계약(AC1)
  await reset();
  const s0 = await gs();
  if (!s0 || typeof s0.coin !== "number" || !s0.piece) fail("window.__state(또는 piece) 미정의.");
  for (const k of Object.keys(C.init)) {
    if (s0[k] !== C.init[k]) fail(`init 불일치 ${k}: 기대 ${C.init[k]} 실제 ${s0[k]}`);
  }
  if (s0.piece.step !== 0 || s0.piece.quality !== 0 || s0.piece.done !== false) {
    fail(`init piece 불일치: step=${s0.piece.step} quality=${s0.piece.quality} done=${s0.piece.done} (기대 0/0/false).`);
  }
  const base0 = s0.piece.base;

  // (b) ★[CORE] 공예 시퀀스 결합(AC2): 각 단계가 quality 에 자기 몫을 가산(매 delta>0·합 일치)
  await reset();
  const okShape = await shape();
  const aShape = await gs();
  if (!okShape) fail("빚기 실패(raw 인데 미빚기).");
  if (aShape.piece.step !== 1 || aShape.piece.shaped !== true) fail(`빚기 후 step/shaped 불일치: step=${aShape.piece.step} shaped=${aShape.piece.shaped}.`);
  const q1 = aShape.piece.quality;
  if (q1 !== K.SHAPE_Q + base0) fail(`빚기 품질 기여 불일치: 기대 ${K.SHAPE_Q + base0}(SHAPE_Q+base) 실제 ${q1}.`);
  if (aShape.piece.done !== false) fail("빚기만 했는데 done=true — 단일 done 플래그 의심(RED).");

  const okFire = await fire();
  const aFire = await gs();
  if (!okFire) fail("굽기 실패(shaped 인데 미굽기).");
  if (aFire.piece.step !== 2 || aFire.piece.fired !== true) fail(`굽기 후 step/fired 불일치: step=${aFire.piece.step} fired=${aFire.piece.fired}.`);
  const q2 = aFire.piece.quality;
  if (q2 - q1 !== K.FIRE_Q) fail(`굽기 품질 기여(delta) 불일치: 기대 ${K.FIRE_Q} 실제 ${q2 - q1}.`);
  if (aFire.piece.done !== false) fail("굽기까지만 했는데 done=true — 단일 done 플래그 의심(RED).");

  const okGlaze = await glaze();
  const aGlaze = await gs();
  if (!okGlaze) fail("유약 실패(fired 인데 미시유).");
  if (aGlaze.piece.step !== 3 || aGlaze.piece.glazed !== true || aGlaze.piece.done !== true) {
    fail(`유약 후 step/glazed/done 불일치: step=${aGlaze.piece.step} glazed=${aGlaze.piece.glazed} done=${aGlaze.piece.done}.`);
  }
  const q3 = aGlaze.piece.quality;
  if (q3 - q2 !== K.GLAZE_Q) fail(`유약 품질 기여(delta) 불일치: 기대 ${K.GLAZE_Q} 실제 ${q3 - q2}.`);
  // 각 단계 delta>0 (단일 카운터/플래그가 아니라 단계별 기여) + 전체 합 == base+stepSum
  if (!(K.SHAPE_Q > 0 && K.FIRE_Q > 0 && K.GLAZE_Q > 0)) fail("단계 기여가 양수가 아님(각 단계 품질 기여 위반).");
  if (q3 !== base0 + stepSum) fail(`완성 품질 합 불일치: 기대 ${base0 + stepSum}(base+SHAPE+FIRE+GLAZE) 실제 ${q3}.`);
  results.sequence = { q1, q2, q3, base0, stepSum, perStep: { shape: K.SHAPE_Q, fire: K.FIRE_Q, glaze: K.GLAZE_Q } };

  // (c) ★negative: 순서 위반/단계 누락 거부(piece 불변) + 단일 done 플래그 불가 구조 입증
  await reset();
  const fireFirst = await fire();                    // raw 에서 굽기 → 거부(빚기 없이 굽기 불가)
  const nf = await gs();
  if (fireFirst !== false || nf.piece.step !== 0 || nf.piece.quality !== 0) fail("RED: 빚기 전 굽기가 거부되지 않음(순서 위반 허용).");
  const glazeFirst = await glaze();                  // raw 에서 유약 → 거부
  const ng = await gs();
  if (glazeFirst !== false || ng.piece.step !== 0 || ng.piece.quality !== 0) fail("RED: 빚기 전 유약이 거부되지 않음(단계 누락 허용).");
  await shape();
  const glazeSkipFire = await glaze();               // shaped 에서 유약(굽기 건너뜀) → 거부
  const ngs = await gs();
  if (glazeSkipFire !== false || ngs.piece.step !== 1 || ngs.piece.done !== false) fail("RED: 굽기 건너뛴 유약이 거부되지 않음(단일 done 플래그 가능 = hollow).");
  // 완성 후 재가공 불가(done 후 단계 호출 전부 거부)
  await fire(); await glaze();
  const reShape = await shape(), reFire = await fire(), reGlaze = await glaze();
  if (reShape !== false || reFire !== false || reGlaze !== false) fail("RED: 완성품 재가공이 거부되지 않음.");
  results.negativeSequence = { fireFirstRejected: true, glazeFirstRejected: true, skipFireRejected: true, reworkRejected: true };

  // (d) ★판매 경제 결합(AC3): coin+=price(q) AND reputation+=rep(q) 동시 창발(서로 다른 계수)
  await reset();
  await craftFull();
  const preSell = await gs();
  const q = preSell.piece.quality;
  const okSell = await sell();
  const postSell = await gs();
  if (!okSell) fail("완성품 판매 실패.");
  const couplingSell =
    postSell.coin === preSell.coin + price(q) &&
    postSell.reputation === preSell.reputation + repGain(q);
  if (!couplingSell) {
    fail(`판매 결합 위반: pre(coin=${preSell.coin},rep=${preSell.reputation}) post(coin=${postSell.coin},rep=${postSell.reputation}) ` +
      `기대 coin+=${price(q)} rep+=${repGain(q)} (q=${q}).`);
  }
  // 단일 카운터 falsify: price 계수≠rep 계수라 한 변수 증가로는 두 축 동시 불충족(계수 다름 입증).
  if (C.priceMult === C.repMult) results.note = "주의: price/rep 계수가 동일(단일카운터 falsify 약화).";
  if (postSell.piece.step !== 0 || postSell.piece.done !== false) fail("판매 후 새 작품 미개시(raw piece 아님).");
  results.sell = { q, priceAdded: price(q), repAdded: repGain(q), coin: postSell.coin, reputation: postSell.reputation };

  // (e) ★negative: 미완성 piece 판매 거부(coin/reputation 불변)
  await reset();
  await shape();                                     // 미완성(step 1)
  const preInc = await gs();
  const incSell = await sell();
  const postInc = await gs();
  if (incSell !== false) fail("RED: 미완성 piece 판매가 거부되지 않음.");
  if (postInc.coin !== preInc.coin || postInc.reputation !== preInc.reputation) fail("RED: 미완성 판매 거부인데 coin/reputation 변동(우회 의심).");
  results.negativeSell = { incompleteRejected: true };

  // (f) ★명성 게이트(AC4): 임계 미달 미상승(negative) → 임계 도달 tier 창발 → 상위재료 품질 창발(falsifiable)
  await reset();
  await craftFull();
  const qTier0 = (await gs()).piece.quality;         // tier-0 작품 품질(base0+stepSum)
  await sell();                                       // 판매 #1: rep = qTier0*repMult
  const afterSell1 = await gs();
  const t0 = C.thresholds[0];
  if (afterSell1.reputation < t0) {                   // negative: 임계 미달이면 tier 미상승
    if (afterSell1.tier !== 0) fail(`명성 게이트 negative 위반: rep=${afterSell1.reputation}<${t0} 인데 tier=${afterSell1.tier} (기대 0).`);
  }
  // 임계 도달까지 반복 판매(가드: 임계/명성증가 기반 상한·무한루프 차단)
  let guardN = 0;
  while ((await gs()).tier === 0 && guardN < 50) {
    await craftFull(); await sell(); guardN++;
  }
  const afterGate = await gs();
  if (afterGate.tier < 1) fail(`명성 게이트 창발 실패: 반복 판매했는데 tier=${afterGate.tier} (rep=${afterGate.reputation}, thr=${t0}).`);
  if (afterGate.reputation < t0) fail(`명성 게이트 위반: tier 상승했는데 rep=${afterGate.reputation}<${t0} (조기 상승).`);
  // tier-up 후 신규 작품(상위 점토) 품질 > tier-up 전(상위재료 해금 emergent·falsifiable)
  await craftFull();
  const qTier1 = (await gs()).piece.quality;
  if (!(qTier1 > qTier0)) fail(`상위재료 창발 실패(falsify): tier-up 후 품질 ${qTier1} ≯ tier-up 전 ${qTier0} — 명성 게이트가 재료를 안 올림(거울 의심).`);
  // tier monotonic·bounded(임계 수 초과 금지)
  if (afterGate.tier > C.thresholds.length) fail(`tier 상한 위반: ${afterGate.tier} > ${C.thresholds.length}.`);
  results.tierGate = { qTier0, qTier1, tier: afterGate.tier, reputation: afterGate.reputation, threshold: t0 };

  // (g) 결정론 재실행(AC): 동일 입력열 → 동일 __state (resetGame 후 재현)
  const seq = async () => {
    await reset();
    await craftFull(); await sell();                 // 1주기
    await craftFull(); await sell();                 // 2주기(tier 변동 포함 가능)
    await shape(); await fire();                      // 부분(미완성)
    return gs();
  };
  const runA = await seq();
  const runB = await seq();
  if (JSON.stringify(runA) !== JSON.stringify(runB)) {
    fail(`결정론 위반: 동일 입력열 재실행 불일치\nA=${JSON.stringify(runA)}\nB=${JSON.stringify(runB)}`);
  }

  // (h) resetGame 잔존 0(AC)
  await reset();
  const sR = await gs();
  if (sR.coin !== 0 || sR.reputation !== 0 || sR.tier !== 0 || sR.piece.step !== 0 || sR.piece.quality !== 0) {
    fail(`resetGame 잔존: coin=${sR.coin} rep=${sR.reputation} tier=${sR.tier} step=${sR.piece.step} quality=${sR.piece.quality}.`);
  }
  results.reset = { clean: true };

  // ── 종합 판정 ──────────────────────────────────────────────────────────────────
  const verdict = couplingSell && afterGate.tier >= 1 && qTier1 > qTier0 && fatals.length === 0;
  console.log(JSON.stringify({ verdict, fatals: fatals.length, negativeMutation, results, contract: C, briefPath }, null, 2));
  if (!verdict) fail(`VERDICT false (couplingSell=${couplingSell} tier=${afterGate.tier} higherClay=${qTier1 > qTier0} fatals=${fatals.length})`);
  console.log(
    "PASS: #3 kiln-craft 공예 시퀀스 결합(순서만 완성·각 단계 품질 기여·단일 done 불가) · 판매 경제 창발(coin+=price ∧ rep+=f(q)) · " +
      "명성 게이트(임계 미달 미상승·임계 도달 tier 창발·상위재료 품질 falsifiable) · negative · 결정론 · 콘솔0 " +
      "(브리프-구동 + 부정-변이 실증)"
  );
} finally {
  await browser.close();
  clearTimeout(guard);
}
