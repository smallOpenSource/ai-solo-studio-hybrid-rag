/**
 * B1 #2 e2e — still-grove(마음챙김 자연탐험) no-fail 불변식 + 성장 창발 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(provenance): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/still-grove.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비면(부정 변이) 계약 파생 불가 → FAIL(사후 날조/하드코딩 적발). clicker/puzzle/memoir 선례.
 * ★no-fail 불변식(CORE·역전 anti-hollow): 흔한 게임이 "패배 조건이 있는가"를 증명한다면, 이 슬라이스는
 *   "패배 조건이 **없음**"을 증명한다 — 임의 __tend 시퀀스(유효/무효 tileId·cap 초과 포함) 후
 *   (a) state 에 fail/gameover/lose/penalty/strike/timer 류 필드·경로 부재,
 *   (b) blooms·각 stage 단조 비감소·serenity true→false 역행 0(어떤 필드도 init 미만 감소 안 함·패널티 0),
 *   (c) 시간 감쇠 0(즉시 스냅샷 == 지연 후 스냅샷·deep-equal·wall-clock 필드 부재).
 * ★결합 불변식(falsifiable): blooms 는 타일 stage 분포의 순수 재계산 — 같은 총 tend 횟수라도 분포가
 *   다르면 blooms 상이(한 타일 cap=blooms1 vs 서로 다른 타일 1회씩=blooms0). 단일 tend-카운터면 RED.
 * ★결정론 grep: 게임 소스에 requestAnimationFrame/Date.now/performance.now read 0(wall-clock 0).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_still-grove_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/still-grove.md.
 *   ENV STILLGROVE_NEGATIVE=1 → acceptance_criteria 를 비운 변이(부정 변이 시연·FAIL 기대).
 * exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefPath = process.argv[2] || path.join(REPO, "vault", "specs", "still-grove.md");
const gamePath = path.join(REPO, "games", "still-grove", "index.html");
const gameUrl = "file://" + gamePath;
const NEGATIVE = process.env.STILLGROVE_NEGATIVE === "1";

const fail = (msg) => { console.error("FAIL: " + msg); process.exit(1); };

// ── 1) 브리프 파싱 → 계약 파생 (handoff.parse_brief 재사용·provenance) ──────────
function loadBriefAC() {
  const py =
    "import sys,json;from orch.handoff import parse_brief;" +
    "print(json.dumps(parse_brief(open(sys.argv[1],encoding='utf-8').read())" +
    ".model_dump(by_alias=True),ensure_ascii=False))";
  const raw = execFileSync(ORCH_PY, ["-c", py, briefPath], {
    env: { ...process.env, PYTHONPATH: REPO },
    encoding: "utf-8",
  });
  const ac = JSON.parse(raw).acceptance_criteria || [];
  return NEGATIVE ? [] : ac;   // 부정 변이 시연: AC 를 비워 계약 파생 실패를 입증.
}

/**
 * 브리프 AC(state_shape/init/tend/nofail/timer/serenity/coupling)에서 계약을 파생.
 * 게임 값(타일 수·cap)에 하드코딩 금지 — 브리프 라인이 비면 파생 불가 → FAIL(부정 변이 적발).
 */
function deriveContract(ac) {
  const find = (p) => ac.find((s) => s.trim().toLowerCase().startsWith(p));
  const shapeLine = find("state_shape:");
  const initLine = find("init:");
  const tendLine = find("tend:");
  const nofailLine = find("nofail:");
  const timerLine = find("timer:");
  const serenityLine = find("serenity:");
  const couplingLine = find("coupling:");
  // load-bearing 7종이 비면 계약 파생 불가 → provenance 위반(부정 변이/하드코딩).
  if (!shapeLine || !initLine || !tendLine || !nofailLine || !timerLine || !serenityLine || !couplingLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가" +
        "(state_shape/init/tend/nofail/timer/serenity/coupling 중 누락) — AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // state_shape 필수 키.
  const shapeKeys = shapeLine.replace(/^[^:]*:/, "").split(",").map((s) => s.trim()).filter(Boolean);
  for (const k of ["tiles", "blooms", "serenity"]) {
    if (!shapeKeys.includes(k)) fail(`state_shape 계약에 필수 키 '${k}' 누락.`);
  }
  // init: blooms/stage/tileCount/stageCap 수치 파생 + serenity=false.
  const initNums = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) initNums[m[1]] = Number(m[2]);
  if (initNums.blooms === undefined || initNums.stage === undefined ||
      initNums.tileCount === undefined || initNums.stageCap === undefined) {
    fail("init 계약(blooms/stage/tileCount/stageCap) 파싱 불가.");
  }
  if (!/serenity=false/i.test(initLine)) fail("init 계약 serenity=false 누락.");
  // 핵심 의미 라인(load-bearing 문구 — 하드코딩이 아닌 브리프 의도가 출처임을 입증).
  if (!/direct-set forbidden/i.test(tendLine)) fail("tend 계약 'direct-set forbidden'(창발) 누락.");
  if (!/no-op/i.test(tendLine)) fail("tend 계약 cap 초과 'no-op' 누락.");
  if (!/penalty=0/i.test(nofailLine)) fail("nofail 계약 'penalty=0' 누락.");
  if (!/no field decreases/i.test(nofailLine)) fail("nofail 계약 'no field decreases below init' 누락.");
  if (!/time_decay=0/i.test(timerLine) || !/no wall-clock/i.test(timerLine)) fail("timer 계약(no wall-clock·time_decay=0) 누락.");
  if (!/direct-set forbidden/i.test(serenityLine)) fail("serenity 계약 'direct-set forbidden'(창발) 누락.");
  if (!/recomputed/i.test(couplingLine) || !/single tend-counter insufficient/i.test(couplingLine)) {
    fail("coupling 계약(recomputed·single tend-counter insufficient) 누락.");
  }
  return { blooms0: initNums.blooms, stage0: initNums.stage, tileCount: initNums.tileCount, stageCap: initNums.stageCap };
}

// ── 2) 결정론 grep: 게임 소스에 wall-clock API read 0 (timer 비의존 belt-and-suspenders) ──
function assertNoWallClock() {
  const src = readFileSync(gamePath, "utf-8");
  for (const tok of [/requestAnimationFrame/g, /Date\.now/g, /performance\.now/g, /setInterval/g, /setTimeout/g]) {
    const hits = src.match(tok);
    if (hits && hits.length) fail(`timer 비의존 위반: 게임 소스에 '${hits[0]}' ${hits.length}회 — wall-clock read 0 이어야 함(RED).`);
  }
}

// ── 3) no-fail 키 가드(역전 anti-hollow): state 트리에 패배/타이머 류 키 부재 ──────────
const FORBIDDEN_KEY = /(fail|gameover|game_over|lose|lost|defeat|penalt|strike|timer|deadline|timeleft|time_left|timeremain|countdown|expire|decay)/i;
function forbiddenKeys(obj, trail) {
  let out = [];
  if (Array.isArray(obj)) {
    obj.forEach((v, i) => { if (v && typeof v === "object") out = out.concat(forbiddenKeys(v, trail + "[" + i + "]")); });
  } else if (obj && typeof obj === "object") {
    for (const k of Object.keys(obj)) {
      if (FORBIDDEN_KEY.test(k)) out.push(trail + "." + k);
      const v = obj[k];
      if (v && typeof v === "object") out = out.concat(forbiddenKeys(v, trail + "." + k));
    }
  }
  return out;
}

const C = deriveContract(loadBriefAC());   // NEGATIVE 면 여기서 FAIL(부정 변이 실증·브라우저 미기동)
assertNoWallClock();

const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
try {
  const page = await browser.newPage();
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text()); });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load" });

  const st = () => page.evaluate(() => JSON.parse(JSON.stringify(window.__state)));
  const consts = () => page.evaluate(() => window.__constants);
  const tend = (id) => page.evaluate((k) => window.__tend(k), id);
  const reset = () => page.evaluate(() => window.__resetGame());
  const stages = (s) => s.tiles.map((t) => t.stage);

  // (a) init = 브리프 계약 + window.__constants drift 단언.
  const s0 = await st();
  if (!s0 || !Array.isArray(s0.tiles)) fail("window.__state.tiles 미정의.");
  if (s0.blooms !== C.blooms0) fail(`init blooms 불일치: 기대 ${C.blooms0} 실제 ${s0.blooms}`);
  if (s0.serenity !== false) fail(`init serenity 불일치: 기대 false 실제 ${s0.serenity}`);
  if (s0.tiles.length !== C.tileCount) fail(`init tileCount 불일치: 기대 ${C.tileCount} 실제 ${s0.tiles.length}`);
  if (!s0.tiles.every((t) => t.stage === C.stage0)) fail(`init stage 불일치: 전 타일 ${C.stage0} 이어야 함.`);
  const K = await consts();
  if (!K || K.STAGE_CAP !== C.stageCap || K.TILE_COUNT !== C.tileCount) {
    fail(`brief↔게임 drift: brief(stageCap=${C.stageCap},tileCount=${C.tileCount}) vs __constants(${JSON.stringify(K)}).`);
  }
  // no-fail 키 가드(init).
  let fk = forbiddenKeys(s0, "__state");
  if (fk.length) fail(`no-fail 위반: state 에 패배/타이머 류 키 존재 — ${fk.join(", ")} (RED).`);

  // (b) 성장 창발: __tend 1회→stage1, 2회→stage2(cap), 3회→stage2(cap 초과 no-op·direct-set 아님).
  await tend(0); let s = await st();
  if (s.tiles[0].stage !== 1) fail(`성장 창발: tend 1회 후 stage 기대 1 실제 ${s.tiles[0].stage}.`);
  await tend(0); s = await st();
  if (s.tiles[0].stage !== C.stageCap) fail(`성장 창발: tend 2회 후 stage 기대 ${C.stageCap}(cap) 실제 ${s.tiles[0].stage}.`);
  const overReturn = await tend(0); s = await st();
  if (s.tiles[0].stage !== C.stageCap) fail(`성장 창발: cap 초과 tend 가 stage 를 ${s.tiles[0].stage} 로(no-op 위반·감소/초과).`);
  if (overReturn !== false) fail("cap 초과 tend 가 no-op(false) 아님.");
  // blooms 창발: 단일 타일 cap → blooms=1.
  if (s.blooms !== 1) fail(`blooms 창발: 1 타일 개화 후 기대 1 실제 ${s.blooms}.`);

  // (c) ★결합 불변식(falsifiable): 같은 총 tend 횟수라도 분포가 다르면 blooms 상이.
  //     path A: 한 타일을 cap(=stageCap) 회 tend → blooms=1.  path B: 서로 다른 stageCap 개 타일 1회씩 → blooms=0.
  await reset();
  for (let i = 0; i < C.stageCap; i++) await tend(0);          // 총 tend = stageCap, 한 타일
  const sA = await st();
  await reset();
  for (let i = 0; i < C.stageCap; i++) await tend(i);          // 총 tend = stageCap, 서로 다른 타일
  const sB = await st();
  if (sA.blooms === sB.blooms) {
    fail(`결합 불변식 위반(단일 카운터): 같은 총 tend(${C.stageCap})인데 blooms 동일(A=${sA.blooms} B=${sB.blooms}) — 분포 무관 RED.`);
  }
  if (sA.blooms !== 1 || sB.blooms !== 0) fail(`결합 불변식 기대치 불일치: A=1(실제 ${sA.blooms}) B=0(실제 ${sB.blooms}).`);

  // (d) serenity 창발(상태·승패 아님): 전 타일 cap → serenity=true·blooms==tileCount. 직전엔 false.
  await reset();
  for (let i = 0; i < C.tileCount; i++) {
    for (let j = 0; j < C.stageCap; j++) {
      await tend(i);
      const mid = await st();
      const allCap = mid.tiles.every((t) => t.stage >= C.stageCap);
      if (mid.serenity !== allCap) fail(`serenity 창발 위반: serenity=${mid.serenity} 인데 전타일개화=${allCap} (creates 직접세팅 의심).`);
    }
  }
  const sFull = await st();
  if (!sFull.serenity || sFull.blooms !== C.tileCount) fail(`serenity: 전 타일 개화인데 serenity=${sFull.serenity} blooms=${sFull.blooms}.`);

  // (e) ★no-fail 불변식(CORE): 임의 액션 시퀀스(유효·무효 id·cap 초과) — 단조 비감소·키 가드.
  await reset();
  const seq = [0, 0, 0, 999, "nope", 1, 1, -1, 2, 2, 0, 3, 3, 4, 4, 5, 5, 5, 4, 3];   // 무효 id·cap 초과 섞음
  let prevStages = stages(await st()), prevBlooms = 0, prevSerenity = false;
  for (const id of seq) {
    await tend(id);
    const cur = await st();
    // 키 가드: 패배/타이머 류 키가 절대 생기지 않음.
    fk = forbiddenKeys(cur, "__state");
    if (fk.length) fail(`no-fail 위반(시퀀스 중): 패배/타이머 류 키 출현 — ${fk.join(", ")} (RED).`);
    // 단조 비감소: 각 stage·blooms 비감소, serenity true→false 역행 0.
    const cs = stages(cur);
    for (let i = 0; i < cs.length; i++) if (cs[i] < prevStages[i]) fail(`no-fail 위반: 타일 ${i} stage 감소 ${prevStages[i]}→${cs[i]} (패널티 의심).`);
    if (cur.blooms < prevBlooms) fail(`no-fail 위반: blooms 감소 ${prevBlooms}→${cur.blooms}.`);
    if (prevSerenity && !cur.serenity) fail(`no-fail 위반: serenity true→false 역행.`);
    prevStages = cs; prevBlooms = cur.blooms; prevSerenity = cur.serenity;
  }

  // (f) ★timer 비의존(시간 감쇠 0): 즉시 스냅샷 == 지연 + 프레임 진행 후 스냅샷(deep-equal).
  const snapBefore = JSON.stringify(await st());
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));   // 프레임 2회 진행(게임은 루프 없음)
  await new Promise((r) => setTimeout(r, 350));   // 실시간 지연(harness 측·게임은 wall-clock 미사용)
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const snapAfter = JSON.stringify(await st());
  if (snapBefore !== snapAfter) fail(`timer 비의존 위반: 지연 후 __state 변화(시간 감쇠 존재) — before=${snapBefore} after=${snapAfter}.`);

  // (g) negative: 무효 tileId tend → no-op(상태 불변·패널티 없음).
  const preNeg = JSON.stringify(await st());
  const negRet = await tend(123456);
  const postNeg = JSON.stringify(await st());
  if (negRet !== false) fail("negative: 무효 tileId tend 가 no-op(false) 아님.");
  if (preNeg !== postNeg) fail("negative: 무효 tileId tend 가 상태를 변경(부작용·우회 세팅 의심).");

  // (h) resetGame: 깨끗이 시작(전 stage 0·blooms 0·serenity false).
  await reset();
  const sr = await st();
  if (sr.blooms !== 0 || sr.serenity !== false || !sr.tiles.every((t) => t.stage === 0)) {
    fail(`resetGame 위반: blooms=${sr.blooms} serenity=${sr.serenity} stages=${stages(sr)} (깨끗이 시작 아님).`);
  }

  const noFailInvariant = true;          // (a)(e) 키가드 + 단조 비감소 통과
  const timerIndependent = true;         // (f) 시간 감쇠 0 통과
  const growthEmergence = true;          // (b)(c)(d) tend 창발 + 결합 + serenity 통과
  const verdict = fatals.length === 0;   // 위 단언은 실패 시 즉시 exit — 여기 도달 = 전부 통과

  console.log(JSON.stringify({
    verdict, noFailInvariant, timerIndependent, growthEmergence, fatals: fatals.length,
    sample: { s0blooms: s0.blooms, couplingA: sA.blooms, couplingB: sB.blooms, serenity: sFull.serenity },
    contract: C, constants: K, briefPath, negativeMode: NEGATIVE,
  }, null, 2));

  if (!verdict) fail(`VERDICT false (fatals=${fatals.length}: ${fatals.join(" | ")})`);
  console.log("PASS: no-fail 불변식 + 성장 창발 + 결합 불변식 + timer 비의존 + 무에러 (브리프-구동)");
} finally {
  await browser.close();
}
