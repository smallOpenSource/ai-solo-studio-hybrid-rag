/**
 * B2 #11 block-burg e2e — 턴제 테트로미노 도시건설 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(C-1.3·12종 만다트): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/block-burg.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비거나 load-bearing 라인(init/place/synergy/coupling/negative)이 누락(부정 변이)이면
 *   계약 파생 불가 → FAIL(사후 날조/하드코딩 적발). verify 가 정상 brief→PASS·변이 brief→FAIL 양 경로 구동(실증).
 * ★[CORE] 결합 불변식(AC2·anti-hollow·단일 카운터 불가):
 *   - __placePiece(pieceId,x,y,rot) → 셀 스탬프 AND townValue == **독립 재계산**(harness 자체 recompute,
 *     board 인접 순수함수)·게임값과 일치. direct-set 세터(__setTownValue) 부재.
 *   - 위치 의존성(falsifiable): 같은 조각을 **인접** 배치 vs **고립** 배치 → townValue 상이(점유 셀 수는 동일).
 *     동일하면(placed*const hollow) → RED. 인접이 가치를 가른다(도시가치 창발).
 * ★턴제 결정론(AC4): 시드 고정 큐·동일 배치열 → 동일 grid+townValue. wall-clock read 0(게임 소스 grep 강제·
 *   §2.5 belt-and-suspenders·낙하 타이머/rAF 금지). negative: 겹침/범위밖/차례아님 거부(상태 불변).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_block-burg_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/block-burg.md. (briefArg 있으면 자식 모드 — 부정-변이 자기-실증 생략.)
 * exit 0 = PASS, 비-0 = FAIL.
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
const briefArg = process.argv[2];                                   // 자식 모드(부정-변이) 표식 겸용
const briefPath = briefArg || path.join(REPO, "vault", "specs", "block-burg.md");
const gameRel = path.join("games", "block-burg", "index.html");
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
    timeout: 15000,
  });
  return JSON.parse(raw);
}

function deriveContract(ac) {
  const find = (p) => ac.find((s) => s.trim().toLowerCase().startsWith(p));
  const initLine = find("init:");
  const placeLine = find("place:");
  const synergyLine = find("synergy:");
  const couplingLine = find("coupling:");
  const negativeLine = find("negative:");
  // load-bearing 5종(init/place/synergy/coupling/negative)이 비면 계약 파생 불가 → provenance 위반(부정 변이/하드코딩).
  if (!initLine || !placeLine || !synergyLine || !couplingLine || !negativeLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/place/synergy/coupling/negative 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // place: __placePiece·reject 토큰(배치/거부 계약 출처).
  if (!/__placePiece/i.test(placeLine) || !/reject/i.test(placeLine)) {
    fail("place 계약(__placePiece·reject) 토큰 부재 — 배치/거부 계약 출처 불명.");
  }
  // coupling anti-hollow load-bearing 토큰(재계산·위치의존·placed*const 부정) — 변이 시 출처 불명 → FAIL.
  if (!/recompute/i.test(couplingLine) || !/different position/i.test(couplingLine) || !/placed\*const/i.test(couplingLine)) {
    fail("coupling 계약(recompute·different position·not placed*const) load-bearing 토큰 부재 — anti-hollow 출처 불명(부정 변이).");
  }
  if (!/reject/i.test(negativeLine)) fail("negative 계약(rejected) 누락.");
  // init 파싱(placed/townValue/queueLen/width/height)
  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  for (const k of ["placed", "townValue", "queueLen", "width", "height"]) {
    if (init[k] === undefined) fail(`init 계약 파싱 불가(${k}).`);
  }
  // synergy 파싱(base + zone-type 쌍 표) — 게임 __constants 와 drift 단언의 정본.
  const baseM = synergyLine.match(/base=(\d+)/);
  if (!baseM) fail("synergy 계약(base) 파싱 불가.");
  const synergy = {};
  for (const m of synergyLine.matchAll(/([FIKR]{2})=(-?\d+)/g)) synergy[m[1]] = Number(m[2]);
  if (Object.keys(synergy).length === 0) fail("synergy 쌍 파싱 불가 — 시너지 표 출처 불명.");
  // 전 쌍 비-0(인접이 항상 가치에 기여 — 위치 의존성 falsifiable 보장).
  for (const k of Object.keys(synergy)) {
    if (synergy[k] === 0) fail(`synergy ${k}=0 — 전 쌍 비-0 위반(falsifiable 위치의존 보장 깨짐).`);
  }
  return { init, base: Number(baseM[1]), synergy, width: init.width, height: init.height };
}

// ── 2) 결정론 grep(§2.5·#11 턴제): 게임 소스 gated 경로에 wall-clock read 0 강제 ──────
function assertNoWallClock() {
  const src = fs.readFileSync(gameAbs, "utf-8");
  const re = /\b(Date\.now|performance\.now|requestAnimationFrame)\b/g;
  const hits = src.match(re) || [];
  if (hits.length > 0) {
    fail(
      `결정론 위반(grep): 게임 소스에 wall-clock read ${hits.length}건(${[...new Set(hits)].join(", ")}) — ` +
        "턴제는 이벤트 구동만 허용(낙하 타이머/rAF/now 누산 금지)."
    );
  }
  return true;
}

// ── 3) 부정-변이 실증: coupling AC 제거 brief → 자식 하네스 exit≠0(FAIL) 임을 실증 ──────
//   (정상 brief→PASS 는 본 프로세스가, 변이 brief→FAIL 은 자식이 — 양 경로 모두 구동·무하드코딩 *실증*.)
function selfDemoNegativeMutation() {
  const raw = fs.readFileSync(briefPath, "utf-8");
  const lines = raw.split("\n");
  const out = [];
  let removing = false, removed = 0;
  for (const l of lines) {
    if (/^\s*-\s*['"]?coupling:/i.test(l)) { removing = true; removed++; continue; }  // load-bearing coupling: 라인 제거
    if (removing) {                                                                    // 후속 연속(continuation) 라인도 제거
      if (/^\s*-\s/.test(l) || /^\S/.test(l) || l.trim() === "") { removing = false; }
      else { removed++; continue; }
    }
    out.push(l);
  }
  if (removed === 0) fail("부정-변이 준비 실패: 'coupling:' AC 라인 미발견(브리프 형식 변경?).");
  const tmp = path.join(os.tmpdir(), `block-burg-mut-${process.pid}.md`);
  fs.writeFileSync(tmp, out.join("\n"), "utf-8");
  let status = 0, captured = "";
  try {
    execFileSync(process.execPath, [__filename, tmp], { env: process.env, encoding: "utf-8", stdio: "pipe", timeout: 20000 });
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

// ── 4) 계약 파생 + grep + 부정-변이 자기-실증(자식 모드면 생략) ──────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);
assertNoWallClock();
const negativeMutation = briefArg ? "(child-mode: self-demo skipped)" : selfDemoNegativeMutation();

// harness 자체 독립 재계산(게임과 동일 식이되 별도 구현 — townValue 가 board 인접 순수함수임을 *독립* 검증).
function recompute(grid) {
  const W = C.width, H = C.height, BASE = C.base, SYN = C.synergy;
  const sp = (a, b) => (a < b ? a + b : b + a);
  let v = 0;
  for (let i = 0; i < grid.length; i++) if (grid[i]) v += BASE;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x, t = grid[i];
      if (!t) continue;
      if (x + 1 < W && grid[i + 1]) v += SYN[sp(t, grid[i + 1])];
      if (y + 1 < H && grid[i + W]) v += SYN[sp(t, grid[i + W])];
    }
  }
  return v;
}
const occ = (g) => g.filter((c) => c).length;

// ── 5) playwright 구동 + 결합 불변식 단언 ──────────────────────────────────────
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
const results = {};
try {
  // 모바일 뷰포트 → 캔버스(360)가 96vw 로 축소 → CSS 스케일 보정 경로가 실제로 작동.
  const context = await browser.newContext({ viewport: { width: 380, height: 820 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text()); });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load", timeout: 15000 });
  await page.waitForFunction(() => !!window.__state && !!window.__constants, { timeout: 5000 });

  const gs = () => page.evaluate(() => window.__state);
  const consts = () => page.evaluate(() => window.__constants);
  const place = (id, x, y, r) => page.evaluate(([a, b, c, d]) => window.__placePiece(a, b, c, d), [id, x, y, r]);
  const canPlace = (id, x, y, r) => page.evaluate(([a, b, c, d]) => window.__canPlace(a, b, c, d), [id, x, y, r]);
  const cells = (id, x, y, r) => page.evaluate(([a, b, c, d]) => window.__pieceCells(a, b, c, d), [id, x, y, r]);
  const reset = () => page.evaluate(() => window.__resetGame());
  const hasSetter = () => page.evaluate(() => typeof window.__setTownValue !== "undefined" || typeof window.__setState !== "undefined");

  // (drift) 게임 __constants ↔ 브리프 파생 시너지/기본가치/구조 일치(하드코딩 적발)
  const K = await consts();
  if (!K) fail("window.__constants 미정의 — brief↔게임 drift 단언 불가.");
  if (K.BASE !== C.base) fail(`drift: 게임 BASE=${K.BASE} ≠ 브리프 base=${C.base}.`);
  if (K.WIDTH !== C.width) fail(`drift: 게임 WIDTH=${K.WIDTH} ≠ 브리프 width=${C.width}.`);
  if (K.HEIGHT !== C.height) fail(`drift: 게임 HEIGHT=${K.HEIGHT} ≠ 브리프 height=${C.height}.`);
  if (K.QUEUE_LEN !== C.init.queueLen) fail(`drift: 게임 QUEUE_LEN=${K.QUEUE_LEN} ≠ 브리프 queueLen=${C.init.queueLen}.`);
  if (!K.SYN || Object.keys(K.SYN).length !== Object.keys(C.synergy).length) {
    fail(`drift: 시너지 쌍 수 불일치(게임 ${K.SYN ? Object.keys(K.SYN).length : "∅"} ≠ 브리프 ${Object.keys(C.synergy).length}).`);
  }
  for (const k of Object.keys(C.synergy)) {
    if (K.SYN[k] !== C.synergy[k]) fail(`drift: 시너지 ${k} 게임 ${K.SYN[k]} ≠ 브리프 ${C.synergy[k]}.`);
  }
  if (await hasSetter()) fail("anti-hollow: townValue 직접세터(__setTownValue/__setState) 존재 — 창발 위반(direct-set forbidden).");

  // (a) 초기 상태 = 브리프 init 계약(AC1)
  await reset();
  const s0 = await gs();
  if (!s0 || !Array.isArray(s0.grid) || !Array.isArray(s0.queue)) fail("window.__state(grid/queue 배열) 미정의.");
  if (s0.placed !== C.init.placed) fail(`init placed: 기대 ${C.init.placed} 실제 ${s0.placed}`);
  if (s0.townValue !== C.init.townValue) fail(`init townValue: 기대 ${C.init.townValue} 실제 ${s0.townValue}`);
  if (s0.queue.length !== C.init.queueLen) fail(`init queueLen: 기대 ${C.init.queueLen} 실제 ${s0.queue.length}`);
  if (s0.grid.length !== C.width * C.height) fail(`init grid 크기: 기대 ${C.width * C.height} 실제 ${s0.grid.length}`);
  if (occ(s0.grid) !== 0) fail(`init grid 비어있지 않음: 점유 ${occ(s0.grid)}`);
  // 시드 고정 큐 재현: reset 후 큐가 동일(타입 시퀀스).
  await reset();
  const s0b = await gs();
  if (JSON.stringify(s0.queue.map((q) => q.type)) !== JSON.stringify(s0b.queue.map((q) => q.type))) {
    fail("시드 고정 위반: reset 후 큐 타입 시퀀스 상이(비결정 RNG 의심).");
  }

  // (b) 배치 계획 탐색(브라우저 내 1회): p0(조각0 첫 합법 배치)·조각1 인접/고립/겹침 후보.
  const plan = await page.evaluate(() => {
    const W = window.__constants.WIDTH, H = window.__constants.HEIGHT;
    window.__resetGame();
    const adjacent = (cs, O) => {
      const oset = new Set(O.map((c) => c[0] + "," + c[1]));
      const dd = [[1, 0], [-1, 0], [0, 1], [0, -1]];
      return cs.some((c) => dd.some((d) => oset.has((c[0] + d[0]) + "," + (c[1] + d[1]))));
    };
    const inRange = (cs) => cs.every((c) => c[0] >= 0 && c[0] < W && c[1] >= 0 && c[1] < H);
    let p0 = null;
    for (let y = 0; y < H && !p0; y++) for (let x = 0; x < W && !p0; x++) for (let r = 0; r < 4 && !p0; r++) {
      if (window.__canPlace(0, x, y, r)) p0 = { x, y, rot: r };
    }
    if (!p0) return { error: "조각0 합법 배치 없음" };
    const O = window.__pieceCells(0, p0.x, p0.y, p0.rot);
    window.__placePiece(0, p0.x, p0.y, p0.rot);                       // placed=1 (조각1 탐색 위해)
    const oset = new Set(O.map((c) => c[0] + "," + c[1]));
    let adj = null, far = null, overlap = null;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) for (let r = 0; r < 4; r++) {
      const cs = window.__pieceCells(1, x, y, r);
      if (!cs) continue;
      if (window.__canPlace(1, x, y, r)) {
        if (!adj && adjacent(cs, O)) adj = { x, y, rot: r };
        if (!far && !adjacent(cs, O)) far = { x, y, rot: r };
      } else if (!overlap && inRange(cs) && cs.some((c) => oset.has(c[0] + "," + c[1]))) {
        overlap = { x, y, rot: r };                                  // 범위내 + 조각0 과 겹침(겹침 negative 후보)
      }
    }
    window.__resetGame();
    return { p0, O, adj, far, overlap };
  });
  if (plan.error) fail(`배치 계획 탐색 실패: ${plan.error}`);
  if (!plan.adj) fail("조각1 인접 배치 후보 미발견(8×8 인데 — 그리드/형상 점검).");
  if (!plan.far) fail("조각1 고립 배치 후보 미발견(위치 의존성 probe 불가).");
  const { p0, adj, far, overlap } = plan;

  // (c) ★[CORE] 결합 불변식 + 위치 의존성(falsifiable): 인접 vs 고립 → townValue 상이(점유 셀 수는 동일).
  async function scenario(second) {
    await reset();
    if (!(await place(0, p0.x, p0.y, p0.rot))) fail("시나리오 준비: 조각0 배치 실패.");
    if (!(await place(1, second.x, second.y, second.rot))) fail("시나리오 준비: 조각1 배치 실패.");
    return gs();
  }
  const sAdj = await scenario(adj);
  const sFar = await scenario(far);
  // 게임 townValue == harness 독립 재계산(board 인접 순수함수·direct-set 아님).
  if (sAdj.townValue !== recompute(sAdj.grid)) fail(`결합 위반(인접): 게임 townValue=${sAdj.townValue} ≠ 독립 재계산 ${recompute(sAdj.grid)}.`);
  if (sFar.townValue !== recompute(sFar.grid)) fail(`결합 위반(고립): 게임 townValue=${sFar.townValue} ≠ 독립 재계산 ${recompute(sFar.grid)}.`);
  if (sAdj.placed !== 2 || sFar.placed !== 2) fail(`배치 결합 위반: placed 기대 2 (인접 ${sAdj.placed}, 고립 ${sFar.placed}).`);
  if (occ(sAdj.grid) !== occ(sFar.grid)) fail(`위치 의존성 probe 무효: 점유 셀 수 상이(인접 ${occ(sAdj.grid)} ≠ 고립 ${occ(sFar.grid)}).`);
  if (sAdj.townValue === sFar.townValue) {
    fail(`★위치 의존성 실패(RED): 인접/고립 townValue 동일(${sAdj.townValue}) — 점유 셀 수 동일한데 가치 불변 = placed*const hollow 의심.`);
  }
  results.coupling = { townValueAdj: sAdj.townValue, townValueFar: sFar.townValue, occupied: occ(sAdj.grid), recomputeMatch: true, positionDependent: true };

  // (d) negative: 범위밖 / 겹침 / 차례아님 → 거부(false·grid/townValue/placed 불변)
  await reset();
  if (!(await place(0, p0.x, p0.y, p0.rot))) fail("negative 준비: 조각0 배치 실패.");
  const pre = await gs();
  const rejRange = await place(1, C.width + 5, C.height + 5, 0);     // 범위밖
  const postRange = await gs();
  if (rejRange !== false) fail("negative 위반: 범위밖 배치 미거부.");
  if (JSON.stringify(postRange) !== JSON.stringify(pre)) fail("negative 위반: 범위밖 거부인데 상태 변동(우회 스탬프 의심).");
  if (overlap) {                                                     // 겹침(범위내·조각0 과 충돌)
    const rejOverlap = await place(1, overlap.x, overlap.y, overlap.rot);
    const postOverlap = await gs();
    if (rejOverlap !== false) fail("negative 위반: 겹침 배치 미거부.");
    if (JSON.stringify(postOverlap) !== JSON.stringify(pre)) fail("negative 위반: 겹침 거부인데 상태 변동.");
  }
  const rejTurn = await place(2, far.x, far.y, far.rot);             // 차례아님(placed=1 인데 pieceId=2)
  const postTurn = await gs();
  if (rejTurn !== false) fail("negative 위반: 차례아님(out-of-turn) 배치 미거부.");
  if (JSON.stringify(postTurn) !== JSON.stringify(pre)) fail("negative 위반: 차례아님 거부인데 상태 변동.");
  results.negative = { outOfRange: true, overlap: !!overlap, outOfTurn: true, stateUnchanged: true };

  // (e) 회전 지원(AC place: rot 0..3): 조각0 rot=1 geometry 4셀(렌더/배치 경로 가동).
  await reset();
  const rotCells = await cells(0, 2, 2, 1);
  if (!Array.isArray(rotCells) || rotCells.length !== 4) fail(`회전 geometry 위반: rot=1 셀 수 기대 4 실제 ${rotCells ? rotCells.length : "∅"}.`);

  // (f) 턴제 결정론(AC4): 동일 배치열(시드 고정 큐) → 동일 window.__state (resetGame 후 재현).
  const seq = async () => {
    await reset();
    await place(0, p0.x, p0.y, p0.rot);
    await place(1, adj.x, adj.y, adj.rot);
    return gs();
  };
  const runA = await seq();
  const runB = await seq();
  if (JSON.stringify(runA) !== JSON.stringify(runB)) {
    fail(`턴제 결정론 위반: 동일 배치열 재실행 불일치\nA=${JSON.stringify(runA).slice(0, 200)}\nB=${JSON.stringify(runB).slice(0, 200)}`);
  }
  if (runA.townValue !== recompute(runA.grid)) fail("결정론 재실행 townValue ≠ 독립 재계산(순수성 위반).");

  // (g) resetGame: 런 간 잔존 0
  await reset();
  const sR = await gs();
  if (sR.placed !== 0 || sR.townValue !== 0 || occ(sR.grid) !== 0) {
    fail(`resetGame 잔존: placed=${sR.placed} townValue=${sR.townValue} 점유=${occ(sR.grid)}.`);
  }
  results.reset = { clean: true };

  const verdict = fatals.length === 0;   // 위 모든 단언은 실패 시 즉시 exit(1) → 여기 도달 = 통과
  console.log(JSON.stringify({
    verdict, fatals: fatals.length, negativeMutation, results, contract: C,
    sample: { s0Placed: s0.placed, sAdj: sAdj.townValue, sFar: sFar.townValue, plan: { p0, adj, far, overlap } },
    briefPath,
  }, null, 2));
  if (!verdict) fail(`콘솔/페이지 에러 ${fatals.length}건: ${fatals.slice(0, 3).join(" | ")}`);
  console.log(
    "PASS: #11 block-burg 배치 결합(townValue==독립재계산·위치 의존 인접≠고립) · 턴제 결정론(시드 고정 큐·동일 배치열 동일 상태·wall-clock 0) · " +
      "negative(범위밖/겹침/차례아님 거부·상태 불변) · resetGame 잔존0 · 콘솔0 (브리프-구동 + 부정-변이 실증)"
  );
} finally {
  await browser.close();
  clearTimeout(guard);
}
