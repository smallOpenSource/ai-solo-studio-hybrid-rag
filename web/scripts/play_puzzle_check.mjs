/**
 * P5 C4 e2e — 슬라이딩 타일 퍼즐 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(provenance): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/p5-slide-puzzle.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC가 비면(부정 변이) 계약 파생 불가 → FAIL(사후 날조/하드코딩 적발). play_clicker_check.mjs 선례.
 * ★결합 불변식(coupling AC): 유효 이동 = moves+=1 AND tiles 배열 실변경 AND 빈칸-인접 타일만 이동.
 *   비인접 클릭은 no-op(moves 불변)이어야 한다 — 단일 카운터(moves++만)로는 불충족 → stub/theater 차단.
 * ★상태전이(stage AC): solved false->true 정확히 1회. 3×3 결정론 보드를 BFS로 실제 정렬 완성해
 *   solved 전이를 관찰한다(렌더-only 무에러만으론 PASS 불가).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_puzzle_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/p5-slide-puzzle.md.
 *   ENV PUZZLE_NEGATIVE=1 → acceptance_criteria 를 비운 변이 계약을 강제(부정 변이 시연·FAIL 기대).
 * exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefPath = process.argv[2] || path.join(REPO, "vault", "specs", "p5-slide-puzzle.md");
const gameUrl = "file://" + path.join(REPO, "games", "slide-puzzle", "index.html");
const NEGATIVE = process.env.PUZZLE_NEGATIVE === "1";

const fail = (msg) => {
  console.error("FAIL: " + msg);
  process.exit(1);
};

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
  // 부정 변이 시연: AC 를 비워 계약 파생이 실패해야 함을 입증.
  return NEGATIVE ? [] : ac;
}

/**
 * 브리프 AC(state_shape/init/move/win/stage/coupling)에서 계약을 파생.
 * 게임 값(타일 배열·시드)에 하드코딩 금지 — 브리프 라인이 비면 파생 불가 → FAIL.
 */
function deriveContract(ac) {
  const find = (p) => ac.find((s) => s.trim().toLowerCase().startsWith(p));
  const stateShapeLine = find("state_shape:");
  const initLine = find("init:");
  const moveLine = find("move:");
  const winLine = find("win:");
  const stageLine = find("stage:");
  const couplingLine = find("coupling:");
  if (!stateShapeLine || !initLine || !moveLine || !winLine || !stageLine || !couplingLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가" +
        "(state_shape/init/move/win/stage/coupling 중 누락) — AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }

  // state_shape: 노출돼야 할 키 집합.
  const shapeKeys = stateShapeLine
    .replace(/^[^:]*:/, "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (!shapeKeys.includes("tiles") || !shapeKeys.includes("moves") || !shapeKeys.includes("solved")) {
    fail("state_shape 계약에 tiles/moves/solved 필수 키 누락.");
  }

  // init: size=N moves=0 solved=false 파생.
  const initNums = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) initNums[m[1]] = Number(m[2]);
  if (initNums.size === undefined || initNums.moves === undefined) {
    fail("init 계약(size/moves) 파싱 불가.");
  }
  const initSolved = /solved=false/i.test(initLine);
  if (!initSolved) fail("init 계약 solved=false 누락.");

  // move: moves+=1 (증분 단언의 출처가 브리프임을 입증).
  if (!/moves\+=1/i.test(moveLine)) fail("move 계약 moves+=1 누락.");
  const moveAdjacency = /adjacent_to_blank/i.test(moveLine);

  // win: tiles==goal_order (1..8, blank_last) — goal 배열 파생.
  const goalLast = /blank_last/i.test(winLine);
  const goalRangeM = winLine.match(/1\.\.(\d+)/);
  if (!goalRangeM) fail("win 계약 goal_order(1..N) 파싱 불가.");
  const goalMax = Number(goalRangeM[1]); // 8 → goal = [1..8, blank(0)]
  const goal = [];
  for (let i = 1; i <= goalMax; i++) goal.push(i);
  if (goalLast) goal.push(0); // blank=0 last

  // stage: solved false->true 정확히 1회.
  const stageOnce = /false-?>?true/i.test(stageLine.replace(/\s+/g, "")) || /false.*true/i.test(stageLine);

  // coupling: moves++ AND tiles 변경 AND blank-adjacent only.
  const couplingMoves = /moves/i.test(couplingLine) && /increment/i.test(couplingLine);
  const couplingTiles = /tiles/i.test(couplingLine) && /change/i.test(couplingLine);
  const couplingAdjacent = /blank-?adjacent/i.test(couplingLine);
  if (!couplingMoves || !couplingTiles || !couplingAdjacent) {
    fail("coupling 계약(moves++ AND tiles change AND blank-adjacent) 파싱 불가.");
  }

  return {
    shapeKeys,
    size: initNums.size,
    initMoves: initNums.moves,
    goal,
    moveAdjacency,
    stageOnce,
  };
}

// ── BFS: 결정론 보드를 실제 정렬까지 풀이(인접 이동만) → solved 전이 falsifiable ──
function neighborsIdx(blank, size) {
  const r = Math.floor(blank / size), c = blank % size;
  const out = [];
  if (r > 0) out.push(blank - size);
  if (r < size - 1) out.push(blank + size);
  if (c > 0) out.push(blank - 1);
  if (c < size - 1) out.push(blank + 1);
  return out;
}

function solveBFS(startTiles, goal, size) {
  const goalKey = goal.join(",");
  const startKey = startTiles.join(",");
  if (startKey === goalKey) return []; // 이미 정렬(init 계약 위반이지만 BFS 차원에서는 빈 경로)
  const seen = new Set([startKey]);
  // queue 항목: { tiles, blank, path(이동시킨 타일 인덱스 시퀀스) }
  let queue = [{ tiles: startTiles.slice(), blank: startTiles.indexOf(0), path: [] }];
  const MAX = 400000; // 3×3 가해상태 181440 상한
  let visited = 0;
  while (queue.length) {
    const next = [];
    for (const cur of queue) {
      if (++visited > MAX) return null;
      for (const nIdx of neighborsIdx(cur.blank, size)) {
        const t = cur.tiles.slice();
        t[cur.blank] = t[nIdx];
        t[nIdx] = 0;
        const key = t.join(",");
        if (seen.has(key)) continue;
        seen.add(key);
        const path = cur.path.concat(nIdx); // 빈칸 인접의 nIdx 타일을 빈칸으로 민다
        if (key === goalKey) return path;
        next.push({ tiles: t, blank: nIdx, path });
      }
    }
    queue = next;
  }
  return null;
}

// ── 2) playwright 구동 + 단언 ──────────────────────────────────────────────────
const C = deriveContract(loadBriefAC());

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
  const neighbors = () => page.evaluate(() => window.__neighbors());
  const move = (idx) => page.evaluate((k) => window.__move(k), idx);

  // (a) state_shape + init 계약 — window.__gameState 노출 + 초기값.
  const s0 = await gs();
  if (!s0 || typeof s0 !== "object") fail("window.__gameState 미정의(노출 AC 위반).");
  for (const k of C.shapeKeys) {
    if (!(k in s0)) fail(`state_shape 키 누락: ${k}`);
  }
  if (!Array.isArray(s0.tiles)) fail("tiles 배열 아님.");
  if (s0.size !== C.size) fail(`init size 불일치: 기대 ${C.size} 실제 ${s0.size}`);
  if (s0.moves !== C.initMoves) fail(`init moves 불일치: 기대 ${C.initMoves} 실제 ${s0.moves}`);
  if (s0.solved !== false) fail(`init solved 불일치: 기대 false 실제 ${s0.solved}`);
  // scrambled_solvable: init 보드는 정렬상태가 아니어야 함.
  if (s0.tiles.join(",") === C.goal.join(",")) fail("init tiles 가 이미 goal(미셔플) — scrambled 계약 위반.");

  // (b) ★결합 불변식 — 유효 이동: moves++ AND tiles 실변경 AND 빈칸-인접만.
  const nb0 = await neighbors();
  if (!Array.isArray(nb0) || nb0.length === 0) fail("__neighbors 빈칸 인접 후보 없음.");
  const tilesBefore = s0.tiles.slice();
  const blankBefore = s0.tiles.indexOf(0);
  const movedIdx = nb0[0]; // 빈칸 인접 타일
  const movedTileValue = tilesBefore[movedIdx];
  const ok1 = await move(movedIdx);
  if (!ok1) fail("빈칸-인접 유효 이동이 거부됨(moveTile false).");
  const s1 = await gs();
  const movesIncreased = s1.moves === s0.moves + 1;
  const tilesChanged = s1.tiles.join(",") !== tilesBefore.join(",");
  // 결합: 이동한 타일이 빈칸 자리로, 원자리는 빈칸이 됨.
  const couplingPlacement = s1.tiles[blankBefore] === movedTileValue && s1.tiles[movedIdx] === 0;
  const movedWasAdjacent = nb0.includes(movedIdx); // 출처: __neighbors(빈칸-인접)
  const couplingValidMove = movesIncreased && tilesChanged && couplingPlacement && movedWasAdjacent;
  if (!couplingValidMove) {
    fail(
      `결합 불변식(유효 이동) 위반: moves ${s0.moves}->${s1.moves} ` +
        `tilesChanged=${tilesChanged} placement=${couplingPlacement} adjacent=${movedWasAdjacent}`
    );
  }

  // (c) ★결합 불변식(부정면) — 비인접 클릭은 no-op(moves 불변·tiles 불변).
  //   현재 빈칸의 비인접 인덱스를 하나 고른다.
  const s1b = await gs();
  const blankNow = s1b.tiles.indexOf(0);
  const nbNow = await neighbors();
  let nonAdjIdx = -1;
  for (let i = 0; i < s1b.tiles.length; i++) {
    if (i !== blankNow && !nbNow.includes(i)) {
      nonAdjIdx = i;
      break;
    }
  }
  if (nonAdjIdx === -1) fail("비인접 인덱스 없음(3×3 에서는 존재해야 함).");
  const movesPre = s1b.moves;
  const tilesPre = s1b.tiles.slice();
  const ok2 = await move(nonAdjIdx);
  const s1c = await gs();
  const noOpHeld = ok2 === false && s1c.moves === movesPre && s1c.tiles.join(",") === tilesPre.join(",");
  if (!noOpHeld) {
    fail(
      `결합 불변식(비인접 no-op) 위반: ok=${ok2} moves ${movesPre}->${s1c.moves} ` +
        `tilesChanged=${s1c.tiles.join(",") !== tilesPre.join(",")} (비인접 이동이 상태를 바꿈)`
    );
  }

  // (d) ★상태전이 solved false->true: BFS 로 결정론 보드를 실제 정렬 → solved 1회 전이.
  const preSolve = await gs();
  if (preSolve.solved !== false) fail("정렬 전 solved 가 이미 true.");
  const solvePath = solveBFS(preSolve.tiles.slice(), C.goal, C.size);
  if (solvePath === null) fail("BFS 풀이 실패(보드가 solvable 하지 않거나 상태공간 초과).");
  let solvedTransitions = 0;
  let prevSolved = preSolve.solved;
  for (const idx of solvePath) {
    const okM = await move(idx);
    if (!okM) fail(`BFS 경로 이동 거부(비인접?) idx=${idx} — 보드/이동 로직 불일치.`);
    const st = await gs();
    if (prevSolved === false && st.solved === true) solvedTransitions++;
    prevSolved = st.solved;
  }
  const sFinal = await gs();
  const reachedGoal = sFinal.tiles.join(",") === C.goal.join(",");
  const solvedTransitionOnce = reachedGoal && sFinal.solved === true && solvedTransitions === 1;
  if (!solvedTransitionOnce) {
    fail(
      `solved 전이(false->true 1회) 미충족: reachedGoal=${reachedGoal} ` +
        `solved=${sFinal.solved} transitions=${solvedTransitions}`
    );
  }

  // (e) win 잠금 — 승리 후 추가 이동은 거부(solved 1회 전이 보장·중복 토글 없음).
  const nbWin = await neighbors();
  const winLockMoves = sFinal.moves;
  const okWin = nbWin.length ? await move(nbWin[0]) : false;
  const sAfterWin = await gs();
  const winLockHeld = okWin === false && sAfterWin.moves === winLockMoves && sAfterWin.solved === true;

  // ── 판정 ──
  const couplingInvariant = couplingValidMove && noOpHeld;
  const stateTransition = movesIncreased && solvedTransitionOnce;
  const verdict = couplingInvariant && stateTransition && winLockHeld && fatals.length === 0;

  console.log(
    JSON.stringify(
      {
        verdict,
        couplingInvariant,
        stateTransition,
        winLockHeld,
        fatals: fatals.length,
        metrics: {
          init: { size: s0.size, moves: s0.moves, solved: s0.solved },
          validMove: { movesBefore: s0.moves, movesAfter: s1.moves, tilesChanged, movedIdx, movedTileValue, movedWasAdjacent, couplingPlacement },
          nonAdjacentNoOp: { nonAdjIdx, accepted: ok2, movesUnchanged: s1c.moves === movesPre },
          solve: { bfsMoves: solvePath.length, reachedGoal, solvedTransitions, finalMoves: sFinal.moves },
          winLock: { accepted: okWin, movesUnchanged: sAfterWin.moves === winLockMoves },
        },
        goal: C.goal,
        briefPath,
        negativeMode: NEGATIVE,
      },
      null,
      2
    )
  );

  if (!verdict) {
    fail(
      `VERDICT false (coupling=${couplingInvariant} transition=${stateTransition} ` +
        `winLock=${winLockHeld} fatals=${fatals.length})`
    );
  }
  console.log("PASS: C4 결합 불변식 + 상태전이(solved false->true 1회) + win잠금 + 무에러 (브리프-구동)");
} finally {
  await browser.close();
}
