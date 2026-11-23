/**
 * Track M e2e — memoir-room 플레이 루프 + 결합(coupling) 인과 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동: 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/memoir-room.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비면(부정 변이) 계약 파생 불가 → FAIL(사후 날조/하드코딩 적발).
 * ★결합 불변식(anti-theater): slide-puzzle 하네스가 solved 를 BFS 로 실검증하듯,
 *   narrativeBeatShown 을 "현재 챕터의 전 슬롯이 placed 일 때만 true"로 **인과검증** —
 *   슬롯을 하나씩 채우며 마지막 직전까지 false, 마지막에 false→true 전이 정확히 1회.
 *   훅이 상태를 직접 세팅하는 걸 신뢰하지 않는다(placeItem 안에서만 창발).
 * ★실입력 1케이스: page.mouse 드래그(모바일 뷰포트 → CSS 스케일 보정 경로 실제 통과)로
 *   placed[slot]===item 단언(__placeItem 훅 직접호출만으로는 드래그/스케일 미검증).
 *
 * ★R2 가산(기존 AC1~5 비회귀·4챕터 일반화 + 신규 AC6~8):
 *   - AC6 4챕터 도달(유년→학창→사회초년→현재): totalChapters===4·ch1→4 순차·ch4 만 complete.
 *   - AC7 carry-forward: choices[chapter-1] 누적(idempotent)·resolveEpilogue(choices) 순수·
 *     **ch1 단일변인 + ch2 단독변주** 두 팔이 각각 에필로그를 가른다(전챕터 carry·ch1-only 사각 제거).
 *   - AC8 아이템 비네트: __placeItem 성공 시 lastVignette===item.vignette(아이템별 상이) AND
 *     노출 DOM 노드(#vignette) textContent 에 렌더 소비(vestigial echo 차단).
 *   신규 계약 라인(carry:/vignette:)·init.totalChapters=4 는 load-bearing(부재/표류 시 FAIL).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_memoir_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/memoir-room.md. exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefPath = process.argv[2] || path.join(REPO, "vault", "specs", "memoir-room.md");
const gameUrl = "file://" + path.join(REPO, "games", "memoir-room", "index.html");

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
  const placeLine = find("place:");
  const couplingLine = find("coupling:");
  const negativeLine = find("negative:");
  // ── R2 신규 계약 라인(load-bearing — 부재 시 FAIL·하드코딩 차단) ──
  const carryLine = find("carry:");
  const vignetteLine = find("vignette:");
  // ── 셋째 슬라이스 신규 계약 라인(load-bearing — 부재 시 FAIL·하드코딩 차단) ──
  const ambientMotifLine = find("ambientmotif:");
  const epilogueCodaLine = find("epiloguecoda:");
  // load-bearing 4종(init/place/coupling/negative)이 비면 계약 파생 불가 → provenance 위반.
  if (!initLine || !placeLine || !couplingLine || !negativeLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/place/coupling/negative 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // R2 신규 라인(carry/vignette) load-bearing — 부재 시 신규 AC 계약 파생 불가 → FAIL.
  if (!carryLine || !vignetteLine) {
    fail(
      "브리프 acceptance_criteria 에서 R2 계약 파생 불가(carry/vignette 누락) — " +
        "load-bearing 신규 라인 부재(부정 변이/하드코딩)."
    );
  }
  // carry: 핵심 토큰(resolveEpilogue·순수·직접세팅 금지)·anti-hollow 계약 출처.
  if (!/resolveEpilogue/i.test(carryLine) || !/\bpure\b/i.test(carryLine) || !/direct-set forbidden/i.test(carryLine)) {
    fail("carry 계약(resolveEpilogue·pure·direct-set forbidden) 누락 — anti-hollow 계약 출처 불명.");
  }
  // vignette: lastVignette·per-item 상이·렌더 소비(DOM textContent) 명시.
  if (!/lastVignette/i.test(vignetteLine) || !/per-item/i.test(vignetteLine) ||
      !(/textContent/i.test(vignetteLine) || /render/i.test(vignetteLine))) {
    fail("vignette 계약(lastVignette·per-item·render textContent) 누락 — 렌더 소비 계약 출처 불명.");
  }
  // 셋째 슬라이스 신규 라인(ambientMotif/epilogueCoda) load-bearing — 부재 시 신규 AC 계약 파생 불가 → FAIL.
  if (!ambientMotifLine || !epilogueCodaLine) {
    fail(
      "브리프 acceptance_criteria 에서 셋째 슬라이스 계약 파생 불가(ambientMotif/epilogueCoda 누락) — " +
        "load-bearing 신규 라인 부재(부정 변이/하드코딩)."
    );
  }
  // ambientMotif: __ambientPlan·순수·챕터 distinct·소비 카운터(voiceCalls) 명시·anti-hollow 출처.
  if (!/__ambientPlan/i.test(ambientMotifLine) || !/\bpure\b/i.test(ambientMotifLine) ||
      !/distinct/i.test(ambientMotifLine) || !/(counter|voiceCalls)/i.test(ambientMotifLine)) {
    fail("ambientMotif 계약(__ambientPlan·pure·chapter-distinct·소비 counter) 누락 — anti-hollow 계약 출처 불명.");
  }
  // epilogueCoda: resolveEpilogue·순수·패턴 코다 명시·패턴 코다 계약 출처.
  if (!/resolveEpilogue/i.test(epilogueCodaLine) || !/\bpure\b/i.test(epilogueCodaLine) ||
      !/coda/i.test(epilogueCodaLine)) {
    fail("epilogueCoda 계약(resolveEpilogue·pure·pattern coda) 누락 — 패턴 코다 계약 출처 불명.");
  }
  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  if (init.chapter === undefined || init.totalChapters === undefined) fail("init 계약(chapter/totalChapters) 파싱 불가.");
  if (init.totalChapters < 2) fail("브리프 totalChapters<2 — AC3(≥2챕터) 계약 불충족.");
  return { init };
}

// ── 2) playwright 구동 ──────────────────────────────────────────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);

const shotDir = path.join(os.tmpdir(), "memoir-shots");
try { fs.mkdirSync(shotDir, { recursive: true }); } catch (e) { /* 무시 */ }
const snap = async (page, name) => { try { await page.screenshot({ path: path.join(shotDir, name) }); } catch (e) { /* 시각 보조·비차단 */ } };

const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
const results = {};
try {
  // 모바일 뷰포트(400px) → 캔버스(480)가 96vw 로 축소 → CSS 스케일 보정 경로가 실제로 작동.
  const context = await browser.newContext({ viewport: { width: 400, height: 760 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text()); });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load" });
  await page.waitForFunction(() => !!window.__state, { timeout: 5000 });

  const gs = () => page.evaluate(() => window.__state);
  const place = (it, sl) => page.evaluate(([i, s]) => window.__placeItem(i, s), [it, sl]);
  const choose = (id) => page.evaluate((c) => window.__choose(c), id);
  const advance = () => page.evaluate(() => window.__advanceChapter());
  const toggleHint = () => page.evaluate(() => window.__toggleHint());
  const resetGame = () => page.evaluate(() => window.__resetGame());
  const solution = () => page.evaluate(() => window.__solution());
  const vignettes = () => page.evaluate(() => window.__vignettes());
  const resolveEpilogueH = (arr) => page.evaluate((a) => window.__resolveEpilogue(a), arr);
  const vignetteDom = () => page.evaluate(() => { const e = document.getElementById("vignette"); return e ? e.textContent : null; });
  const epilogueDom = () => page.evaluate(() => { const e = document.getElementById("epilogue"); return e ? e.textContent : null; });
  // 현재 챕터 전 아이템을 정답 슬롯에 배치(placeItem 경유·우회 아님).
  const placeAllCurrent = async (tag) => {
    const sol = await solution();
    for (const it of Object.keys(sol)) {
      const ok = await place(it, sol[it]);
      if (!ok) fail(`${tag}: 정답 슬롯 배치 실패 ${it} → ${sol[it]}`);
    }
    return gs();
  };

  // ── init 계약 (state_shape + 초기값) ─────────────────────────────────────────
  const s0 = await gs();
  if (!s0 || typeof s0.chapter !== "number") fail("window.__state 미정의.");
  if (s0.chapter !== C.init.chapter) fail(`init chapter: 기대 ${C.init.chapter} 실제 ${s0.chapter}`);
  // DRIFT(게임↔브리프): totalChapters 가 브리프(=4)와 일치해야 한다(하드코딩/표류 적발).
  if (s0.totalChapters !== C.init.totalChapters) fail(`init/DRIFT totalChapters: 브리프 ${C.init.totalChapters} ≠ 게임 ${s0.totalChapters}`);
  if (Number(s0.narrativeBeatShown) !== (C.init.narrativeBeatShown || 0)) fail(`init narrativeBeatShown: 기대 false 실제 ${s0.narrativeBeatShown}`);
  if (Number(s0.hintOn) !== (C.init.hintOn || 0)) fail(`init hintOn: 기대 false 실제 ${s0.hintOn}`);
  if (s0.branch !== null) fail(`init branch: 기대 null 실제 ${s0.branch}`);
  if (s0.phase !== "placing") fail(`init phase: 기대 placing 실제 ${s0.phase}`);
  if (Object.keys(s0.placed).length !== 0) fail("init placed 비어있지 않음.");
  // R2 신규 필드 초기값(가산·기존 보존).
  if (!Array.isArray(s0.choices) || s0.choices.length !== 0) fail(`init choices: 기대 [] 실제 ${JSON.stringify(s0.choices)}`);
  if (s0.lastVignette !== null) fail(`init lastVignette: 기대 null 실제 ${s0.lastVignette}`);
  if (s0.epilogue !== null) fail(`init epilogue: 기대 null 실제 ${s0.epilogue}`);
  const TOTAL = s0.totalChapters;

  // ── AC1 + C2: 실입력 드래그 1케이스(hit-test + CSS 스케일 보정 경로 실제 통과) ──
  const layout = await page.evaluate(() => {
    const c = document.getElementById("game");
    const r = c.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height, cw: c.width, ch: c.height };
  });
  const geom = await page.evaluate(() => window.__geom());
  const solAll = await solution();
  const sx = layout.width / layout.cw, sy = layout.height / layout.ch;
  const toClient = (rc) => ({ x: layout.left + (rc.x + rc.w / 2) * sx, y: layout.top + (rc.y + rc.h / 2) * sy });
  const draggedItem = Object.keys(geom.items)[0];
  const homeSlot = solAll[draggedItem];
  const from = toClient(geom.items[draggedItem]);
  const to = toClient(geom.slots[homeSlot]);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 5 });
  await page.mouse.move(to.x, to.y, { steps: 5 });
  await page.mouse.up();
  const afterDrag = await gs();
  await snap(page, "ac1_drag.png");
  if (afterDrag.placed[homeSlot] !== draggedItem) {
    fail(`AC1/C2 실입력 드래그 배치 실패: placed[${homeSlot}]=${afterDrag.placed[homeSlot]} (기대 ${draggedItem}, scale sx=${sx.toFixed(3)})`);
  }
  results.realDrag = { item: draggedItem, slot: homeSlot, scaleX: Number(sx.toFixed(3)) };

  // ── AC8(상태+렌더): 드래그 성공 아이템의 비네트가 lastVignette + #vignette DOM 에 반영 ──
  const vmapCh1 = await vignettes();
  const lvAfterDrag = afterDrag.lastVignette;
  const vdomAfterDrag = await vignetteDom();
  if (lvAfterDrag !== vmapCh1[draggedItem]) fail(`AC8: 배치 성공 후 lastVignette 불일치 (state=${lvAfterDrag} 기대 ${vmapCh1[draggedItem]}).`);
  if (vdomAfterDrag !== vmapCh1[draggedItem]) fail(`AC8: #vignette DOM 렌더 소비 실패 (dom=${JSON.stringify(vdomAfterDrag)} 기대 ${vmapCh1[draggedItem]}).`);

  // ── negative: 틀린 슬롯 배치 거부 + 미완성 챕터 비트 미출현 + lastVignette 불변 ──────
  const allItems = Object.keys(solAll);
  const remaining = allItems.filter((id) => id !== draggedItem);
  const negItem = remaining[0];
  const wrongSlot = solAll[remaining[1]];            // 다른 아이템의 정답 슬롯(비점유) = 이 아이템엔 오답
  const negOk = await place(negItem, wrongSlot);
  const negState = await gs();
  if (negOk !== false) fail("negative: 틀린 슬롯 배치가 거부되지 않음.");
  if (negState.placed[wrongSlot] !== undefined) fail("negative: 틀린 슬롯에 배치됨.");
  if (negState.narrativeBeatShown) fail("negative: 미완성 챕터인데 narrativeBeatShown=true.");
  if (negState.lastVignette !== lvAfterDrag) fail("negative: 틀린 슬롯 배치인데 lastVignette 변함(우회 세팅 의심).");

  // ── negative(직접·incompleteness 가드): 1챕터 미완성(narrativeBeatShown=false)에서 advance 거부 ──
  const incAdv = await advance();
  const incState = await gs();
  if (incAdv !== false || incState.chapter !== 1) fail(`negative(직접): 미완성 1챕터 advance 미거부 (incAdv=${incAdv}, chapter=${incState.chapter}).`);
  results.incompleteAdvanceRejected = incAdv === false && incState.chapter === 1;

  // ── AC8(per-item 상이): 두 번째 아이템 배치 → 비네트가 첫 아이템과 다르다(아이템별 상이) ──
  const secondItem = remaining[0];
  const okSec = await place(secondItem, solAll[secondItem]);
  if (!okSec) fail(`AC8: 두 번째 아이템 배치 실패 ${secondItem}.`);
  const sAfterSec = await gs();
  if (sAfterSec.lastVignette !== vmapCh1[secondItem]) fail(`AC8: 두 번째 아이템 lastVignette 불일치 (${sAfterSec.lastVignette} 기대 ${vmapCh1[secondItem]}).`);
  if (sAfterSec.lastVignette === lvAfterDrag) fail("AC8: 서로 다른 아이템이 동일 비네트(per-item 상이 위반).");
  if ((await vignetteDom()) !== vmapCh1[secondItem]) fail("AC8: 두 번째 아이템 #vignette DOM 미갱신.");
  results.vignette = { first: lvAfterDrag, second: sAfterSec.lastVignette, perItemDistinct: sAfterSec.lastVignette !== lvAfterDrag, domRendered: true };

  // ── 결합 인과검증(AC2): 슬롯을 하나씩 채우며 마지막 직전까지 false, 마지막에 true 전이 1회 ──
  //   이미 배치된 아이템(드래그1 + secondItem)을 제외한 미배치 아이템만 남겨 하나씩 채운다.
  const curPlaced = (await gs()).placed;
  const placedItemIds = Object.values(curPlaced);
  const toPlace = allItems.filter((id) => placedItemIds.indexOf(id) < 0);
  let transitions = 0;
  let prevBeat = (await gs()).narrativeBeatShown;     // 현재 = false(미완성)
  for (let k = 0; k < toPlace.length; k++) {
    const beforeBeat = (await gs()).narrativeBeatShown;
    if (k < toPlace.length - 1 && beforeBeat) fail("coupling: 완료 전 narrativeBeatShown 조기 true.");
    const ok = await place(toPlace[k], solAll[toPlace[k]]);
    if (!ok) fail(`정답 슬롯 배치 실패: ${toPlace[k]} → ${solAll[toPlace[k]]}`);
    const afterBeat = (await gs()).narrativeBeatShown;
    if (!prevBeat && afterBeat) transitions++;
    prevBeat = afterBeat;
  }
  const sFull = await gs();
  await snap(page, "ac2_beat.png");
  if (!sFull.narrativeBeatShown) fail("AC2: 전 슬롯 배치 후에도 narrativeBeatShown=false.");
  if (sFull.phase !== "beat") fail(`AC2: 비트 phase 비정상 (기대 beat, 실제 ${sFull.phase}).`);
  if (transitions !== 1) fail(`coupling: narrativeBeatShown false→true 전이가 정확히 1회가 아님 (${transitions}회).`);
  if (Object.keys(sFull.placed).length !== allItems.length) fail("AC2: 전 슬롯 placed 아님.");
  results.coupling = { transitions, placedCount: Object.keys(sFull.placed).length };
  const ch1Phase = sFull.phase;                       // AC6: ch1 완료 phase(=beat·최종 아님)

  // ── AC4: 선택지 → 분기(선택별 상이) + carry idempotent(멀티-choose 오염 0) ────────
  const branchA = await choose("a");
  const branchB = await choose("b");
  const branchA2 = await choose("a");                 // 같은 챕터 재선택 → choices[0] 덮어쓰기(append 금지)
  if (!branchA || !branchB || branchA === branchB) fail(`AC4: 분기 미생성/동일 (a=${JSON.stringify(branchA)} b=${JSON.stringify(branchB)}).`);
  const sAfterChoose = await gs();
  if (sAfterChoose.choices.length !== 1 || sAfterChoose.choices[0] !== "a") {
    fail(`AC7 carry idempotent: 멀티-choose 후 choices 오염 (기대 ['a'] 실제 ${JSON.stringify(sAfterChoose.choices)}).`);
  }
  if (branchA2 !== branchA) fail(`AC4: 동일 선택 재호출이 동일 branch 아님 (${branchA2} ≠ ${branchA}).`);
  results.branch = { a: branchA, b: branchB };
  results.carryIdempotent = sAfterChoose.choices.length === 1;

  // ── AC5: 힌트 토글 반전 + 오버레이(on/off) — 챕터 독립 ───────────────────────────
  const h0 = (await gs()).hintOn;
  const h1 = await toggleHint();
  await snap(page, "ac5_hint_on.png");
  const sH1 = await gs();
  const h2 = await toggleHint();
  await snap(page, "ac5_hint_off.png");
  const sH2 = await gs();
  if (!(h0 === false && h1 === true && sH1.hintOn === true && h2 === false && sH2.hintOn === false)) {
    fail(`AC5: 힌트 토글 반전 실패 (h0=${h0} h1=${h1} h2=${h2}).`);
  }
  results.hint = { from: h0, on: sH1.hintOn, off: sH2.hintOn };

  // ── AC3+AC6: 4챕터 일반화 — ch1→2→3→4 순차 도달·각 전이서 MAJOR 가드(branch 리셋)·ch4 만 complete ──
  //   MAJOR 회귀가드: advanceChapter 가 state.branch 를 리셋해야 다음 챕터 선택지(if branch==null)가 재출현.
  //   버그(branch 미리셋): stale branch 가 전파 → narrative 게이트 거짓 → 선택지 영구 소실. 각 전이서 직접 단언.
  const chapterPhases = [ch1Phase];                   // [ch1, ch2, ch3, ch4] 완료 phase 누적
  let prevChBranch = branchA;
  for (let target = 2; target <= TOTAL; target++) {
    await choose("a");                                // 흐름 재현: 직전 챕터 분기 선택 후 진행
    const adv = await advance();
    const sAdv = await gs();
    if (!adv || sAdv.chapter !== target) fail(`AC3/AC6: ch${target} 진입 실패 (adv=${adv}, chapter=${sAdv.chapter}).`);
    if (sAdv.phase !== "placing") fail(`AC3: ch${target} phase 비정상 (${sAdv.phase}).`);
    if (Object.keys(sAdv.placed).length !== 0) fail(`AC3: ch${target} placed 미리셋.`);
    // ★MAJOR 가드(각 전이): advance 후 branch===null(선택지 재출현 가능).
    if (sAdv.branch !== null) fail(`MAJOR: advanceChapter 후 ch${target} branch 미리셋(stale=${JSON.stringify(sAdv.branch)}) — 선택지 소실.`);
    // 미완성 ch{target} 에서 advance 거부(negative).
    if (target < TOTAL) {
      const advNeg = await advance();
      const sNeg = await gs();
      if (advNeg !== false || sNeg.chapter !== target) fail(`negative: 미완성 ch${target} advance 미거부 (advNeg=${advNeg}, chapter=${sNeg.chapter}).`);
    }
    // ch{target} 전 슬롯 배치(완료).
    const sCh = await placeAllCurrent(`ch${target}`);
    const expectPhase = (target === TOTAL) ? "complete" : "beat";
    if (!sCh.narrativeBeatShown) fail(`ch${target}: 전 슬롯 배치 후에도 narrativeBeatShown=false.`);
    if (sCh.phase !== expectPhase) fail(`AC6: ch${target} 완료 phase 기대 ${expectPhase} 실제 ${sCh.phase}.`);
    if (sCh.branch !== null) fail(`ch${target}: 완료 시 branch!=null(${JSON.stringify(sCh.branch)}) → 선택지 소실.`);
    chapterPhases.push(sCh.phase);
    // ch{target} __choose 가 직전 챕터와 다른 branch(분기 결정 실재) — 첫 전이(ch2)는 ch1 과 상이 명시.
    const chBranch = await choose("a");
    const sChosen = await gs();
    if (!chBranch || sChosen.branch !== chBranch) fail(`ch${target}: choose 후 state.branch 미반영.`);
    if (target === 2 && chBranch === branchA) fail(`ch2: choose 가 ch1 과 동일 branch (소실 가드).`);
    prevChBranch = chBranch;
  }
  await snap(page, "ac6_ch4_complete.png");
  // AC6 종합: totalChapters===4·ch1→4·오직 ch4(최종)만 complete.
  const onlyLastComplete = chapterPhases.every((ph, i) => (i === TOTAL - 1 ? ph === "complete" : ph === "beat"));
  if (TOTAL !== 4) fail(`AC6: totalChapters 기대 4 실제 ${TOTAL}.`);
  if (chapterPhases.length !== TOTAL || !onlyLastComplete) {
    fail(`AC6: 챕터 phase 시퀀스 비정상 (기대 [beat,beat,beat,complete] 실제 ${JSON.stringify(chapterPhases)}).`);
  }
  results.ac6 = { totalChapters: TOTAL, chapterPhases, onlyLastComplete };

  // ── AC7: carry-forward(누적 선택→에필로그) — resetGame 양팔·ch1 단일변인 + ch2 단독변주 ──
  //   resetGame 으로 깨끗이 시작(런 간 잔존 0) → 4챕터를 끝까지 진행하며 choices 누적 → ch4 완료+choose 시 epilogue 확정.
  //   인과 격리: 오직 한 챕터 선택만 a↔b 로 바꾸고 나머지 고정 → 에필로그 상이(falsifiable).
  const runToEpilogue = async (choiceVec) => {
    await resetGame();
    const sReset = await gs();
    if (sReset.choices.length !== 0 || sReset.chapter !== 1 || sReset.branch !== null || sReset.epilogue !== null) {
      fail(`AC7: resetGame 후 깨끗하지 않음 (choices=${JSON.stringify(sReset.choices)} chapter=${sReset.chapter} branch=${sReset.branch} epilogue=${sReset.epilogue}).`);
    }
    for (let ch = 1; ch <= choiceVec.length; ch++) {
      await placeAllCurrent(`carry ch${ch}`);
      const cb = await choose(choiceVec[ch - 1]);
      if (cb == null) fail(`AC7: ch${ch} choose ${choiceVec[ch - 1]} 무효.`);
      if (ch < choiceVec.length) {
        const adv = await advance();
        if (!adv) fail(`AC7: ch${ch}→${ch + 1} advance 실패.`);
      }
    }
    const sEnd = await gs();
    if (sEnd.phase !== "complete") fail(`AC7: 최종 챕터 완료 phase 비정상 (${sEnd.phase}).`);
    if (JSON.stringify(sEnd.choices) !== JSON.stringify(choiceVec)) {
      fail(`AC7: choices 누적 불일치 (기대 ${JSON.stringify(choiceVec)} 실제 ${JSON.stringify(sEnd.choices)}).`);
    }
    return sEnd;
  };
  const baseVec = Array(TOTAL).fill("a");             // ['a','a','a','a']
  const sBase = await runToEpilogue(baseVec);
  const epAAAA = sBase.epilogue;
  if (!epAAAA) fail("AC7: ch4 완료+choose 후 epilogue 미설정.");
  // 창발성: state.epilogue === resolveEpilogue(state.choices)(직접세팅 아님).
  const epFromPure = await resolveEpilogueH(sBase.choices);
  if (epAAAA !== epFromPure) fail(`AC7: epilogue 가 resolveEpilogue(choices) 와 불일치(직접세팅 의심) (${epAAAA} ≠ ${epFromPure}).`);
  // 렌더 소비: #epilogue DOM 에 에필로그 반영.
  const epDom = await epilogueDom();
  if (epDom !== epAAAA) fail(`AC7: #epilogue DOM 렌더 소비 실패 (dom=${JSON.stringify(epDom)} 기대 설정값).`);

  // 팔1: ch1 단일변인 — ['b','a','a','a'] vs base 에필로그 상이.
  const ch1Vec = baseVec.slice(); ch1Vec[0] = "b";
  const epCh1 = (await runToEpilogue(ch1Vec)).epilogue;
  if (epCh1 === epAAAA) fail(`AC7: ch1 단일변인인데 에필로그 동일 (carry-forward 거짓).`);

  // 팔2: ch2 단독변주 — ['a','b','a','a'] vs base 에필로그 상이(ch1-only 사각 제거·전챕터 carry).
  const ch2Vec = baseVec.slice(); ch2Vec[1] = "b";
  const epCh2 = (await runToEpilogue(ch2Vec)).epilogue;
  if (epCh2 === epAAAA) fail(`AC7: ch2 단독변주인데 에필로그 동일 (전챕터 carry 미입증·ch1-only 사각).`);
  if (epCh1 === epCh2) fail(`AC7: ch1변주·ch2변주 에필로그가 우연 동일 (프래그먼트 비-distinct 의심).`);

  // resolveEpilogue 순수성: 동일 choices → 동일 출력(2회 호출 일치).
  const pure1 = await resolveEpilogueH(baseVec);
  const pure2 = await resolveEpilogueH(baseVec);
  if (pure1 !== pure2 || pure1 !== epAAAA) fail(`AC7: resolveEpilogue 비순수(${pure1} / ${pure2} / base ${epAAAA}).`);
  results.carry = { epAAAA: epAAAA.slice(0, 24) + "…", ch1Differs: epCh1 !== epAAAA, ch2Differs: epCh2 !== epAAAA, pure: pure1 === pure2 };

  // ── M-AC9: 챕터별 다중트랙 앰비언트 모티프(순수 plan + 소비 카운터·hollow 차단) ────────────
  const ambientPlanH = (c) => page.evaluate((n) => window.__ambientPlan(n), c);
  const audioVoice = () => page.evaluate(() => {
    const a = window.__audio;
    return a && a.voiceCalls ? { melody: a.voiceCalls.melody, bass: a.voiceCalls.bass, perc: a.voiceCalls.perc, ambient: a.voiceCalls.ambient } : null;
  });
  if (!(await page.evaluate(() => typeof window.__ambientPlan === "function"))) fail("M-AC9: window.__ambientPlan 훅 부재.");
  const aplans = [];
  for (let c = 1; c <= TOTAL; c++) aplans.push(await ambientPlanH(c));
  // 순수: 동일 chapter → 동일 plan(재호출 일치).
  for (let c = 1; c <= TOTAL; c++) {
    const again = await ambientPlanH(c);
    if (JSON.stringify(again) !== JSON.stringify(aplans[c - 1])) fail(`M-AC9: __ambientPlan(${c}) 비순수(동일 chapter 다른 plan).`);
  }
  // 모티프 성부열(정적 단일코드 아님): 각 plan 은 ≥2 성부·각 성부 freq>0.
  for (let c = 1; c <= TOTAL; c++) {
    const p = aplans[c - 1];
    if (!Array.isArray(p) || p.length < 2) fail(`M-AC9: __ambientPlan(${c}) 성부열 아님(len=${p && p.length}).`);
    for (const v of p) {
      const f = (typeof v === "number") ? v : (v && v.freq);
      if (!(typeof f === "number" && f > 0)) fail(`M-AC9: __ambientPlan(${c}) 성부 freq 누락/비정상.`);
    }
  }
  // 챕터 distinct: 전 쌍 상이.
  for (let i = 0; i < aplans.length; i++) for (let j = i + 1; j < aplans.length; j++) {
    if (JSON.stringify(aplans[i]) === JSON.stringify(aplans[j])) fail(`M-AC9: __ambientPlan 챕터 ${i + 1}·${j + 1} 동일(챕터 distinct 위반).`);
  }
  // 소비 카운터(hollow 차단): 챕터 진입(advanceChapter→playAmbient) 시 ambient 카운터가 plan 성부 수만큼 증가.
  //   계산만 되고 A.ambient 미소비면 카운터 불변 → delta≠planLen → RED.
  await resetGame();
  const vBefore = await audioVoice();
  if (!vBefore || typeof vBefore.ambient !== "number") fail("M-AC9: __audio.voiceCalls.ambient 카운터 부재(가산 키 누락).");
  await placeAllCurrent("M-AC9 ch1");                  // ch1 완료 → narrativeBeatShown
  const advA9 = await advance();                        // → ch2 진입 → playAmbient(__ambientPlan(2))
  if (!advA9) fail("M-AC9: ch1→ch2 advance 실패(카운터 구동 불가).");
  const vAfter = await audioVoice();
  const planCh2Len = (await ambientPlanH(2)).length;
  const ambientDelta = vAfter.ambient - vBefore.ambient;
  if (ambientDelta !== planCh2Len) fail(`M-AC9: 챕터 진입 시 ambient 카운터 증가가 plan 성부 수와 불일치 (delta=${ambientDelta} 기대 ${planCh2Len}) — plan 미소비(hollow→RED).`);
  // 기존 melody/bass/perc 카운터 델타 비간섭(memoir 미사용 → 변화 0).
  if (vAfter.melody !== vBefore.melody || vAfter.bass !== vBefore.bass || vAfter.perc !== vBefore.perc) {
    fail(`M-AC9: 앰비언트 카운터 가산이 기존 melody/bass/perc 델타 간섭 (m ${vBefore.melody}->${vAfter.melody} b ${vBefore.bass}->${vAfter.bass} p ${vBefore.perc}->${vAfter.perc}).`);
  }
  results.ambient = { chapterDistinct: true, pure: true, consumeDelta: ambientDelta, planVoices: planCh2Len, mbpUnaffected: true };

  // ── M-AC10: 에필로그 패턴 코다(순수·≤3 티어·패턴 distinct·기존 합성/distinct 보존) ──────────
  const codaH = (vec) => page.evaluate((v) => window.__epilogueCoda(v), vec);
  if (!(await page.evaluate(() => typeof window.__epilogueCoda === "function"))) fail("M-AC10: window.__epilogueCoda 훅 부재.");
  const cdAAAA = await codaH(["a", "a", "a", "a"]);   // 일관(0b)
  const cdBBBB = await codaH(["b", "b", "b", "b"]);   // 일관(0a)
  const cdABAB = await codaH(["a", "b", "a", "b"]);   // 균형(2:2)
  const cdAABB = await codaH(["a", "a", "b", "b"]);   // 균형(2:2·위치만 다름)
  const cdAAAB = await codaH(["a", "a", "a", "b"]);   // 전환(3:1)
  // 패턴 상이 → 코다 상이: 일관 ≠ 균형 ≠ 전환.
  if (cdAAAA === cdABAB) fail("M-AC10: [a,a,a,a]·[a,b,a,b] 코다 동일(패턴 코다 미작동·hollow).");
  if (cdAAAA === cdAAAB) fail("M-AC10: [a,a,a,a]·[a,a,a,b] 코다 동일(일관≠전환 위반).");
  if (cdABAB === cdAAAB) fail("M-AC10: [a,b,a,b]·[a,a,a,b] 코다 동일(균형≠전환 위반).");
  // 패턴 기반(위치/값 무관): 분포 같으면 동일 코다 — aaaa≡bbbb·abab≡aabb.
  if (cdAAAA !== cdBBBB) fail("M-AC10: [a,a,a,a]·[b,b,b,b] 코다 상이(일관 패턴이 위치/값 의존 — 패턴 기반 위반).");
  if (cdABAB !== cdAABB) fail("M-AC10: [a,b,a,b]·[a,a,b,b] 코다 상이(균형 패턴이 위치 의존 — 패턴 기반 위반).");
  // ≤3 티어(천장·bounded).
  const codaSet = new Set([cdAAAA, cdBBBB, cdABAB, cdAABB, cdAAAB]);
  if (codaSet.size > 3) fail(`M-AC10: 코다 티어 ${codaSet.size} > 3(bounded 위반).`);
  // 순수: 동일 패턴 → 동일 코다(재호출 일치).
  if ((await codaH(["a", "a", "a", "a"])) !== cdAAAA) fail("M-AC10: __epilogueCoda 비순수.");
  // 코다가 resolveEpilogue 에 합성(소비)됨: 전체 에필로그가 코다를 포함(계산만·미합성=hollow→RED).
  const fullAAAA = await resolveEpilogueH(["a", "a", "a", "a"]);
  const fullABAB = await resolveEpilogueH(["a", "b", "a", "b"]);
  if (fullAAAA.indexOf(cdAAAA) < 0) fail("M-AC10: resolveEpilogue 가 코다를 합성 소비 안 함(계산만·hollow).");
  if (fullABAB.indexOf(cdABAB) < 0) fail("M-AC10: resolveEpilogue 가 패턴 코다를 합성 소비 안 함.");
  if (fullAAAA === fullABAB) fail("M-AC10: 패턴 상이인데 전체 에필로그 동일.");
  // ★M-AC7 비회귀 재단언(코다 가산이 기존 ch1/ch2 distinct 훼손하지 않음): 순수 hook 교차확인.
  const ep_aaaa = await resolveEpilogueH(["a", "a", "a", "a"]);
  const ep_baaa = await resolveEpilogueH(["b", "a", "a", "a"]);   // ch1 단독변주
  const ep_abaa = await resolveEpilogueH(["a", "b", "a", "a"]);   // ch2 단독변주
  if (ep_baaa === ep_aaaa) fail("M-AC10/M-AC7 비회귀: ch1 단독변주 에필로그 동일(코다 가산이 distinct 훼손).");
  if (ep_abaa === ep_aaaa) fail("M-AC10/M-AC7 비회귀: ch2 단독변주 에필로그 동일.");
  if (ep_baaa === ep_abaa) fail("M-AC10/M-AC7 비회귀: ch1·ch2 변주 우연 동일.");
  results.coda = { tiers: codaSet.size, patternDistinct: true, patternBased: true, consumedIntoEpilogue: true, m_ac7_preserved: true };

  // ── C3: 런처 진입 — 런처(games/index.html) 미편집(오케스트레이터 소관) → 본 하네스에선 스킵.
  results.c3 = "skipped (launcher not edited — orchestrator merges <li>)";

  // ── 종합 판정 ────────────────────────────────────────────────────────────────
  const verdict = fatals.length === 0;   // 위 모든 단언은 실패 시 즉시 exit(1) → 여기 도달 = 통과
  console.log(JSON.stringify({ verdict, fatals: fatals.length, results, briefPath, shotDir }, null, 2));
  if (!verdict) fail(`콘솔/페이지 에러 ${fatals.length}건: ${fatals.slice(0, 3).join(" | ")}`);
  console.log(
    "PASS: C1(콘솔0) · C2/AC1(실입력 드래그+스케일) · AC2(전슬롯→비트·coupling false→true 1회) · " +
      "negative(틀린슬롯·incomplete-advance 거부·lastVignette 불변) · " +
      "AC3/AC6(4챕터 ch1→4·각 전이 MAJOR branch리셋·ch4만 complete) · AC4(분기상이·carry idempotent) · " +
      "AC5(힌트반전) · AC7(carry-forward ch1·ch2 변인→에필로그 상이·resolveEpilogue 순수·렌더소비) · " +
      "AC8(비네트 per-item 상이·#vignette DOM 렌더소비) · " +
      "M-AC9(앰비언트 모티프 챕터 distinct·순수 + 성부 카운터 plan 소비·melody/bass/perc 비간섭) · " +
      "M-AC10(에필로그 패턴 코다 ≤3·패턴 distinct·위치무관·resolveEpilogue 합성 소비 + M-AC7 비회귀) " +
      "(브리프-구동·셋째 슬라이스)"
  );
} finally {
  await browser.close();
}
