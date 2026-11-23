/**
 * B2 #7 tile-deck e2e — 공간배치 덱빌더 퍼즐 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동·비로그라).
 *
 * ★AC-구동(C-1.3·12종 만다트): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/tile-deck.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비거나 load-bearing 라인(init/play/synergy/clear/coupling/negative)이 누락(부정 변이)이면
 *   계약 파생 불가 → FAIL(사후 날조/하드코딩 적발). clicker/puzzle/story-idle/farewell-ferry 선례.
 * ★[CORE] 공간 배치 결합(AC2/AC3): __playCard(cardId,x,y) → 셀 점유 AND boardScore 가 board 인접의
 *   순수 재계산서 창발. **같은 카드를 다른 위치에 두면 점수 상이**(position-dependent) — 상수 점수면 RED
 *   (단일 카운터/렌더-only theater 차단). 인접 같은타입(매칭) → combo 가 board 에서 창발.
 * ★클리어·비로그라 결정론(AC4): 설계된 배열(고정 손패)로 boardScore>=target → solved 창발(직접세팅 아님).
 *   고정 손패라 동일 플레이 → 동일 board(2회 deep-equal)·resetGame → 동일 손패 재배분.
 * ★부정-변이 실증(12종 만다트·C-1.3 동형): 정상 brief→PASS(부모) + coupling AC 제거 brief→FAIL(자식 exit≠0)
 *   **양 경로**를 한 호출로 구동(brief-driven·무하드코딩 *실증*). 인자 brief 경로(argv[2]) 있으면 자식 모드(자기-실증 생략).
 * ★결정론 grep: 게임 소스 gated 경로에 Date.now/performance.now/requestAnimationFrame read 0(렌더 on-demand·wall-clock 0).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_tile-deck_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/tile-deck.md. exit 0 = PASS, 비-0 = FAIL.
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
const briefPath = briefArg || path.join(REPO, "vault", "specs", "tile-deck.md");
const gameAbs = path.join(REPO, "games", "tile-deck", "index.html");
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
  const playLine = find("play:");
  const synergyLine = find("synergy:");
  const clearLine = find("clear:");
  const couplingLine = find("coupling:");
  const negativeLine = find("negative:");
  // load-bearing 6종 — 부재 시 계약 파생 불가 → provenance 위반(부정 변이/하드코딩).
  if (!initLine || !playLine || !synergyLine || !clearLine || !couplingLine || !negativeLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/play/synergy/clear/coupling/negative 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // play: __playCard 가 결합 액션 출처.
  if (!/__playcard/i.test(playLine)) fail("play 계약(__playCard) 누락 — 결합 액션 출처 불명.");
  // coupling anti-hollow 토큰(direct-set forbidden·position-dependent·boardScore>=target) load-bearing — 변이 시 출처 불명 → FAIL.
  if (!/direct-set forbidden/i.test(couplingLine) || !/position-dependent/i.test(couplingLine) || !/boardscore>=target/i.test(couplingLine.replace(/\s+/g, ""))) {
    fail("coupling 계약(direct-set forbidden·position-dependent·boardScore>=target) load-bearing 토큰 부재 — anti-hollow 출처 불명(부정 변이).");
  }
  // negative: rejected 명시.
  if (!/reject/i.test(negativeLine)) fail("negative 계약(rejected) 누락.");

  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  const baseM = synergyLine.match(/base=(\d+)/);
  const bonusM = synergyLine.match(/match_bonus=(\d+)/);
  const targetM = clearLine.match(/target=(\d+)/);
  if (init.gridW === undefined || init.gridH === undefined || init.cards === undefined || init.seed === undefined) {
    fail("init 계약 파싱 불가(gridW/gridH/cards/seed).");
  }
  if (!baseM || !bonusM) fail("synergy 계약(base/match_bonus) 파싱 불가 — 점수 모델 출처 불명.");
  if (!targetM) fail("clear 계약(target) 파싱 불가 — 클리어 임계 출처 불명.");
  return {
    gridW: init.gridW, gridH: init.gridH, cards: init.cards, seed: init.seed,
    base: Number(baseM[1]), bonus: Number(bonusM[1]), target: Number(targetM[1]),
  };
}

// ── 1.5) 부정-변이 실증: coupling AC 제거 brief → 자식 하네스 exit≠0(FAIL) 임을 실증 ──────
//   (정상 brief→PASS 는 본 프로세스가, 변이 brief→FAIL 은 자식이 — 양 경로 모두 구동·무하드코딩 *실증*.)
function selfDemoNegativeMutation() {
  const raw = fs.readFileSync(briefPath, "utf-8");
  const lines = raw.split("\n");
  const kept = lines.filter((l) => !/^\s*-\s*['"]?coupling:/i.test(l));   // load-bearing coupling: 라인 제거
  if (kept.length === lines.length) fail("부정-변이 준비 실패: 'coupling:' AC 라인 미발견(브리프 형식 변경?).");
  const tmp = path.join(os.tmpdir(), `tile-deck-mut-${process.pid}.md`);
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
  return { mutatedExit: status, childHint: (captured.split("\n").find((x) => /FAIL/.test(x)) || "").slice(0, 160) };
}

// ── 2) 결정론 grep(§2.5): 게임 소스 gated 경로에 wall-clock read 0 강제(belt-and-suspenders) ──
function assertNoWallClock() {
  const src = fs.readFileSync(gameAbs, "utf-8");
  const hits = src.match(/\b(Date\.now|performance\.now|requestAnimationFrame)\b/g) || [];
  if (hits.length > 0) {
    fail(
      `결정론 위반(grep): 게임 소스에 wall-clock read ${hits.length}건(${[...new Set(hits)].join(", ")}) — ` +
        "gated 경로는 결정론(렌더 on-demand)이어야 한다."
    );
  }
}

// ── 3) 계약 파생 + 부정-변이 자기-실증(자식 모드면 생략) ─────────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);
const negativeMutation = briefArg ? "(child-mode: self-demo skipped)" : selfDemoNegativeMutation();
assertNoWallClock();

// ── 4) playwright 구동 + 결합 불변식/시너지/클리어/결정론 단언 ──────────────────────
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
const results = {};
try {
  // 모바일 뷰포트 → 캔버스(360)가 96vw 로 축소 → CSS 스케일 보정 경로가 실제로 작동(렌더 sanity).
  const context = await browser.newContext({ viewport: { width: 360, height: 720 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text()); });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load" });
  await page.waitForFunction(() => !!window.__state && Array.isArray(window.__state.grid), { timeout: 5000 });

  const gs = () => page.evaluate(() => window.__state);
  const consts = () => page.evaluate(() => window.__constants);
  const play = (cardId, x, y) => page.evaluate(([c, xx, yy]) => window.__playCard(c, xx, yy), [cardId, x, y]);
  const reset = () => page.evaluate(() => window.__resetGame());
  const hasSetter = () => page.evaluate(() =>
    typeof window.__setScore !== "undefined" || typeof window.__setSolved !== "undefined" || typeof window.__setState !== "undefined");
  const idx = (x, y) => y * C.gridW + x;

  // (drift) 게임 상수 ↔ 브리프 파생값 일치(하드코딩 적발).
  const K = await consts();
  if (!K) fail("window.__constants 미정의 — brief↔게임 drift 단언 불가.");
  const driftPairs = [
    ["GRID_W", C.gridW], ["GRID_H", C.gridH], ["CARD_BASE", C.base],
    ["MATCH_BONUS", C.bonus], ["TARGET_SCORE", C.target], ["SEED", C.seed], ["HAND_SIZE", C.cards],
  ];
  for (const [k, v] of driftPairs) {
    if (K[k] !== v) fail(`drift: 게임 ${k}=${K[k]} ≠ 브리프 파생 ${v}.`);
  }
  if (await hasSetter()) fail("anti-hollow: score/solved 직접세터(__setScore/__setSolved/__setState) 존재 — 창발 위반(direct-set forbidden).");

  // (a) state_shape + init 계약(AC1) — grid 빈상태·hand 고정 길이·score=0·solved=false.
  await reset();
  const s0 = await gs();
  for (const k of ["grid", "hand", "score", "solved"]) {
    if (!(k in s0)) fail(`state_shape 키 누락: ${k}`);
  }
  if (!Array.isArray(s0.grid) || s0.grid.length !== C.gridW * C.gridH) fail(`init grid 길이 불일치: 기대 ${C.gridW * C.gridH} 실제 ${s0.grid && s0.grid.length}.`);
  if (s0.grid.some((c) => c !== null)) fail("init grid 가 빈상태 아님(점유 셀 존재).");
  if (!Array.isArray(s0.hand) || s0.hand.length !== C.cards) fail(`init hand 길이 불일치: 기대 ${C.cards} 실제 ${s0.hand && s0.hand.length}.`);
  if (s0.hand.some((h) => h.used)) fail("init hand 에 used 카드 존재(미배분 아님).");
  if (s0.score !== 0) fail(`init score 불일치: 기대 0 실제 ${s0.score}.`);
  if (s0.solved !== false) fail(`init solved 불일치: 기대 false 실제 ${s0.solved}.`);

  // 손패에서 타입별 cardId 추출(고정 손패·brief 파생값 미하드코딩·게임 상태서 발견).
  const fIds = s0.hand.filter((h) => h.type === "F").map((h) => h.id);
  const mIds = s0.hand.filter((h) => h.type === "M").map((h) => h.id);
  if (fIds.length < 3 || mIds.length < 3) fail(`손패 구성 불충분(설계 손패 3F+3M 기대): F=${fIds.length} M=${mIds.length}.`);

  // (b) ★[CORE] 배치 결합(AC2): __playCard → 셀 점유 AND board 재계산(점수 창발).
  await reset();
  const okPlace = await play(fIds[0], 0, 0);
  const sP = await gs();
  if (!okPlace) fail("유효 배치가 거부됨(빈 셀에 미사용 카드).");
  if (!sP.grid[idx(0, 0)] || sP.grid[idx(0, 0)].type !== "F") fail("배치 후 셀 미점유(또는 타입 불일치) — 셀 점유 결합 위반.");
  if (sP.score !== C.base) fail(`배치 후 board 재계산 불일치: 단일 타일 score 기대 ${C.base} 실제 ${sP.score}.`);
  const usedAfter = sP.hand.find((h) => h.id === fIds[0]);
  if (!usedAfter || !usedAfter.used) fail("배치 후 카드 used 표시 안 됨.");

  // (c) ★시너지 창발·position-dependent(AC3·falsifiable): 같은 둘째 카드를 인접 vs 고립 → 점수 상이.
  //   인접(같은타입 F-F): score = 2*base + bonus. 고립: score = 2*base. 상수면 RED.
  await reset();
  await play(fIds[0], 0, 0);
  await play(fIds[1], 1, 0);                                    // (0,0) 우측 인접·같은타입 → 매칭 combo
  const sAdj = await gs();
  await reset();
  await play(fIds[0], 0, 0);
  await play(fIds[1], 2, 2);                                    // 고립(비인접)
  const sIso = await gs();
  const expectAdj = 2 * C.base + C.bonus, expectIso = 2 * C.base;
  if (sAdj.score !== expectAdj) fail(`시너지 창발 위반: 인접 같은타입 score 기대 ${expectAdj} 실제 ${sAdj.score}.`);
  if (sIso.score !== expectIso) fail(`고립 배치 score 기대 ${expectIso} 실제 ${sIso.score}.`);
  if (sAdj.score === sIso.score) fail("RED: 같은 카드를 인접 vs 고립에 둬도 동점(상수 점수) — position-dependent 위반(hollow).");
  results.synergy = { adjacent: sAdj.score, isolated: sIso.score, positionDependent: sAdj.score !== sIso.score };

  // (d) negative(AC): 점유 셀 / 범위밖 / 사용된·미존재 cardId → false·상태 불변.
  await reset();
  await play(fIds[0], 0, 0);                                    // (0,0) 점유
  const preNegJSON = JSON.stringify(await gs());
  const negOccupied = await play(mIds[0], 0, 0);               // 점유 셀
  const negOob1 = await play(mIds[0], C.gridW, 0);            // x 범위밖
  const negOob2 = await play(mIds[0], 0, C.gridH);           // y 범위밖
  const negOob3 = await play(mIds[0], -1, 0);                 // 음수
  const negUsed = await play(fIds[0], 1, 1);                  // 이미 사용된 카드
  const negUnknown = await play(99999, 1, 1);                 // 미존재 카드
  const postNegJSON = JSON.stringify(await gs());
  if ([negOccupied, negOob1, negOob2, negOob3, negUsed, negUnknown].some((r) => r !== false)) {
    fail(`negative 위반: 거부돼야 할 배치가 수락됨 occupied=${negOccupied} oob=${negOob1}/${negOob2}/${negOob3} used=${negUsed} unknown=${negUnknown}.`);
  }
  if (preNegJSON !== postNegJSON) fail("negative 위반: 거부된 배치들이 window.__state 를 변경(우회 세팅 의심).");
  results.negative = { occupied: true, outOfRange: true, usedCard: true, unknownCard: true, stateUnchanged: true };

  // (e) ★클리어 창발(AC4): 설계된 배열(F 위행·M 중행)로 boardScore==target → solved 창발(직접세팅 아님).
  const playDesigned = async () => {
    await reset();
    await play(fIds[0], 0, 0); await play(fIds[1], 1, 0); await play(fIds[2], 2, 0);   // 위행 = 숲(F-F-F)
    await play(mIds[0], 0, 1); await play(mIds[1], 1, 1); await play(mIds[2], 2, 1);   // 중행 = 산(M-M-M)
    return gs();
  };
  const sClear = await playDesigned();
  if (sClear.score !== C.target) fail(`클리어 score 불일치: 설계 배열 기대 ${C.target} 실제 ${sClear.score}.`);
  if (sClear.solved !== true) fail(`클리어 미창발: score=${sClear.score}>=target=${C.target} 인데 solved=${sClear.solved}.`);
  if (sClear.solved !== (sClear.score >= C.target)) fail("solved 가 boardScore>=target 창발과 불일치(직접세팅 의심).");
  results.clear = { score: sClear.score, target: C.target, solved: sClear.solved };

  // (f) ★비로그라 결정론(AC4): 고정 손패 → 동일 플레이 2회 deep-equal·resetGame 동일 손패 재배분.
  const runA = await playDesigned();
  const runB = await playDesigned();
  if (JSON.stringify(runA) !== JSON.stringify(runB)) {
    fail(`결정론 위반: 고정 손패 동일 플레이 재실행 불일치(비로그라 위반)\nA=${JSON.stringify(runA.grid)}\nB=${JSON.stringify(runB.grid)}`);
  }
  await reset();
  const sR = await gs();
  if (sR.grid.some((c) => c !== null) || sR.score !== 0 || sR.solved !== false) fail("resetGame 후 grid/score/solved 미초기화.");
  if (JSON.stringify(sR.hand) !== JSON.stringify(s0.hand)) fail("resetGame 후 손패 재배분 불일치(동일 시드인데 다른 손패) — 비로그라/결정론 위반.");
  results.determinism = { rerunEqual: true, resetClean: true, handReproduced: true };

  // ── 종합 판정 ──────────────────────────────────────────────────────────────────
  const verdict = fatals.length === 0;   // 위 모든 단언은 실패 시 즉시 exit(1) → 여기 도달 = 통과
  console.log(JSON.stringify({ verdict, fatals: fatals.length, negativeMutation, results, contract: C, briefPath }, null, 2));
  if (!verdict) fail(`콘솔/페이지 에러 ${fatals.length}건: ${fatals.slice(0, 3).join(" | ")}`);
  console.log(
    "PASS: 배치 결합(셀 점유 AND board 재계산) · 시너지 창발(인접 매칭 combo·position-dependent·고립≠인접) · " +
      "negative(점유/범위밖/사용·미존재 거부·상태 불변) · 클리어 창발(설계 배열 boardScore==target→solved) · " +
      "비로그라 결정론(고정 손패 동일 플레이 deep-equal·resetGame 재배분) · 콘솔0 (브리프-구동 + 부정-변이 실증)"
  );
} finally {
  await browser.close();
  clearTimeout(guard);
}
