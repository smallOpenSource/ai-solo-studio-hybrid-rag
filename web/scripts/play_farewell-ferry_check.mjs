/**
 * B1 #1 e2e — farewell-ferry 플레이 루프 + 결합 불변식 검증 (provenance: 브리프-구동).
 *
 * ★AC-구동(C-1.3): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/farewell-ferry.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약을 추출 → 그로부터 파생한다.
 *   브리프 AC 가 비면(부정 변이) 계약 파생 불가 → FAIL(사후 날조/하드코딩 적발).
 * ★결합 불변식(anti-hollow·단일 카운터 불가):
 *   - care 창발: __care(pid,gift) 맞는 선물 threshold 회 → bonds[pid] 1→2→3(매 스텝 +1)·틀린 선물 거부(bonds 불변)·cap.
 *   - farewell 결합: 미유대(bonds<thr) __farewell → 실패·released/lastMemory 불변(미유대 작별 성공이면 RED);
 *     만유대 → released 누적 AND lastMemory===passenger.memory(만유대일 때만 발화).
 *   - carry 순수: resolveEulogy(released,choices) 순수(동일 인자→동일)·다른 승객/다른 선택→상이 회고
 *     (다른 승객인데 동일 에필로그면 RED)·state.eulogy===resolveEulogy(직접세팅 아님)·resetGame 잔존 0.
 * ★부정-변이 실증(12종 만다트·C-1.3 동형): 정상 brief→PASS(부모) + carry AC 제거 brief→FAIL(자식 exit≠0)
 *   **양 경로** 구동(brief-driven·무하드코딩 *실증*). 인자 brief 경로(argv[2]) 있으면 자식 모드(자기-실증 생략).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_farewell-ferry_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/farewell-ferry.md. exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefArg = process.argv[2];                                   // 자식 모드(부정-변이) 표식 겸용
const briefPath = briefArg || path.join(REPO, "vault", "specs", "farewell-ferry.md");
const gameUrl = "file://" + path.join(REPO, "games", "farewell-ferry", "index.html");

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
  const careLine = find("care:");
  const farewellLine = find("farewell:");
  const carryLine = find("carry:");
  const negativeLine = find("negative:");
  // load-bearing 5종 — 부재 시 계약 파생 불가 → provenance 위반(부정 변이/하드코딩).
  if (!initLine || !careLine || !farewellLine || !carryLine || !negativeLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가(init/care/farewell/carry/negative 누락) — " +
        "AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  // care: __care·bonds·창발(emergent/direct-set forbidden) — anti-hollow 계약 출처.
  if (!/__care/i.test(careLine) || !/bonds/i.test(careLine) || !/(emergent|direct-set forbidden)/i.test(careLine)) {
    fail("care 계약(__care·bonds·emergent/direct-set forbidden) 누락 — anti-hollow 계약 출처 불명.");
  }
  // farewell: 임계 >=N·released·lastMemory — 결합 계약 출처.
  const thrM = farewellLine.match(/>=\s*(\d+)/);
  if (!thrM || !/released/i.test(farewellLine) || !/lastMemory/i.test(farewellLine)) {
    fail("farewell 계약(bonds[pid]>=N·released·lastMemory) 누락 — 결합 계약 출처 불명.");
  }
  // carry: resolveEulogy·pure·direct-set forbidden — carry/순수 계약 출처.
  if (!/resolveEulogy/i.test(carryLine) || !/\bpure\b/i.test(carryLine) || !/direct-set forbidden/i.test(carryLine)) {
    fail("carry 계약(resolveEulogy·pure·direct-set forbidden) 누락 — carry 계약 출처 불명.");
  }
  // negative: rejected 명시.
  if (!/reject/i.test(negativeLine)) fail("negative 계약(rejected) 누락.");
  const init = {};
  for (const m of initLine.matchAll(/(\w+)=(\d+)/g)) init[m[1]] = Number(m[2]);
  if (init.passengers === undefined || init.threshold === undefined || init.released === undefined) {
    fail("init 계약(passengers/threshold/released) 파싱 불가.");
  }
  const threshold = Number(thrM[1]);
  // DRIFT(브리프 내부): farewell 임계 == init threshold(둘 다 동일 brief 파생).
  if (threshold !== init.threshold) fail(`브리프 내부 DRIFT: farewell >=${threshold} ≠ init threshold=${init.threshold}.`);
  return { init, threshold };
}

// ── 1.5) 부정-변이 실증: carry AC 제거 brief → 자식 하네스 exit≠0(FAIL) 임을 실증 ──────
//   (정상 brief→PASS 는 본 프로세스가, 변이 brief→FAIL 은 자식이 — 양 경로 모두 구동·무하드코딩 *실증*.)
function selfDemoNegativeMutation() {
  const raw = fs.readFileSync(briefPath, "utf-8");
  const lines = raw.split("\n");
  const kept = lines.filter((l) => !/^\s*-\s*['"]?carry:/i.test(l));   // load-bearing carry: 라인 제거
  if (kept.length === lines.length) fail("부정-변이 준비 실패: 'carry:' AC 라인 미발견(브리프 형식 변경?).");
  const tmp = path.join(os.tmpdir(), `farewell-ferry-mut-${process.pid}.md`);
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
    fail("부정-변이 실증 실패: carry AC 제거 brief 인데 하네스가 exit 0(PASS) — brief-driven 무하드코딩 미실증.");
  }
  return { mutatedExit: status, childHint: (captured.split("\n").find((x) => /FAIL/.test(x)) || "").slice(0, 140) };
}

// ── 2) 계약 파생 + 부정-변이 자기-실증(자식 모드면 생략) ─────────────────────────
const brief = loadBrief();
const C = deriveContract(brief.acceptance_criteria || []);
const negativeMutation = briefArg ? "(child-mode: self-demo skipped)" : selfDemoNegativeMutation();

// ── 3) playwright 구동 + 결합 불변식 단언 ──────────────────────────────────────
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
const results = {};
try {
  // 모바일 뷰포트 → 캔버스(480)가 96vw 로 축소 → CSS 스케일 보정 경로가 실제로 작동.
  const context = await browser.newContext({ viewport: { width: 400, height: 800 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text()); });
  page.on("pageerror", (e) => fatals.push(String(e)));
  await page.goto(gameUrl, { waitUntil: "load", timeout: 15000 });
  await page.waitForFunction(() => !!window.__state, { timeout: 5000 });

  const gs = () => page.evaluate(() => window.__state);
  const care = (pid, g) => page.evaluate(([p, x]) => window.__care(p, x), [pid, g]);
  const farewell = (pid) => page.evaluate((p) => window.__farewell(p), pid);
  const choose = (pid, c) => page.evaluate(([p, x]) => window.__choose(p, x), [pid, c]);
  const reset = () => page.evaluate(() => window.__resetGame());
  const wanted = () => page.evaluate(() => window.__wanted());
  const memories = () => page.evaluate(() => window.__memories());
  const thr = () => page.evaluate(() => window.__threshold());
  const resolveEulogyH = (r, c) => page.evaluate(([rr, cc]) => window.__resolveEulogy(rr, cc), [r, c]);

  // ── AC1 init 계약(state_shape + 초기값) ─────────────────────────────────────────
  const s0 = await gs();
  if (!s0 || !Array.isArray(s0.passengers)) fail("window.__state 미정의/passengers 배열 아님.");
  if (s0.passengers.length !== C.init.passengers) fail(`init passengers: 기대 ${C.init.passengers} 실제 ${s0.passengers.length}`);
  if (s0.released.length !== C.init.released) fail(`init released: 기대 ${C.init.released} 실제 ${s0.released.length}`);
  if (s0.lastMemory !== null) fail(`init lastMemory: 기대 null 실제 ${s0.lastMemory}`);
  if (s0.eulogy !== null) fail(`init eulogy: 기대 null 실제 ${s0.eulogy}`);
  if (Object.keys(s0.choices).length !== 0) fail("init choices 비어있지 않음.");
  for (const p of s0.passengers) { if (s0.bonds[p.id] !== 0) fail(`init bonds[${p.id}]: 기대 0 실제 ${s0.bonds[p.id]}`); }
  // DRIFT(게임↔브리프): 게임 임계 == 브리프 임계.
  const gThr = await thr();
  if (gThr !== C.threshold) fail(`DRIFT threshold: 브리프 ${C.threshold} ≠ 게임 ${gThr}`);

  const w = await wanted();
  const mem = await memories();
  const pid0 = s0.passengers[0].id, pid1 = s0.passengers[1].id, pid2 = s0.passengers[2].id;
  const gift0 = w[pid0], wrongGift = w[pid1];          // pid1 의 정답선물 = pid0 에겐 오답
  const ch0a = s0.passengers[0].choices[0].id, ch0b = s0.passengers[0].choices[1].id;

  // ── AC2 [CORE] care 창발 + negative(틀린 선물) + cap ──────────────────────────
  const wrongOk = await care(pid0, wrongGift);
  const sWrong = await gs();
  if (wrongOk !== false) fail("negative: 틀린 선물 care 가 거부되지 않음.");
  if (sWrong.bonds[pid0] !== 0) fail(`negative: 틀린 선물인데 bonds[${pid0}] 변함(${sWrong.bonds[pid0]}).`);
  // 맞는 선물 threshold 회 → bonds 가 1씩 창발(매 스텝 정확히 +1·단일 세팅 불가).
  for (let k = 1; k <= C.threshold; k++) {
    const ok = await care(pid0, gift0);
    const sc = await gs();
    if (!ok) fail(`care ${k}회차 실패(맞는 선물인데 미증가).`);
    if (sc.bonds[pid0] !== k) fail(`care 창발 불일치: ${k}회차 기대 ${k} 실제 ${sc.bonds[pid0]}.`);
  }
  // cap: 만유대 후 추가 care → no-op(bounded).
  const overOk = await care(pid0, gift0);
  const sCap = await gs();
  if (overOk !== false || sCap.bonds[pid0] !== C.threshold) {
    fail(`cap 위반: 만유대 후 care no-op 아님(ok=${overOk} bond=${sCap.bonds[pid0]} thr=${C.threshold}).`);
  }
  results.care = { emergent: true, wrongRejected: true, cap: true, bond: sCap.bonds[pid0] };

  // ── AC3 farewell 결합 + negative(미유대 작별 성공이면 RED) ───────────────────────
  const preFw = await gs();
  const unbondedOk = await farewell(pid1);            // pid1 = bonds 0(미유대)
  const sUnbonded = await gs();
  if (unbondedOk !== false) fail("RED: 미유대 작별 성공(결합 위반).");
  if (sUnbonded.released.indexOf(pid1) >= 0) fail("RED: 미유대인데 released 에 추가됨.");
  if (sUnbonded.lastMemory !== preFw.lastMemory) fail("RED: 미유대 작별인데 lastMemory 변함.");
  // 만유대(pid0) → 성공·released 누적·lastMemory 발화(만유대일 때만).
  const fwOk = await farewell(pid0);
  const sFw = await gs();
  if (!fwOk) fail("만유대인데 farewell 실패.");
  if (sFw.released.indexOf(pid0) < 0) fail("farewell 성공인데 released 에 없음.");
  if (sFw.lastMemory !== mem[pid0]) fail(`lastMemory 발화 불일치: 기대 "${mem[pid0]}" 실제 "${sFw.lastMemory}".`);
  if ((await farewell(pid0)) !== false) fail("이미 떠난 영혼 farewell 재성공(중복 미차단).");
  results.farewell = { unbondedRejected: true, fullBondReleased: true, memoryUttered: true };

  // ── AC4 carry: 창발(직접세팅 아님)·순수·다른 승객/선택 → 상이 회고 ─────────────────
  const epEmergent = await resolveEulogyH(sFw.released, sFw.choices);
  if (sFw.eulogy !== epEmergent) fail(`carry 창발 위반: state.eulogy ≠ resolveEulogy(released,choices) — 직접세팅 의심.`);
  const pure1 = await resolveEulogyH([pid0], {});
  const pure2 = await resolveEulogyH([pid0], {});
  if (pure1 !== pure2) fail("carry: resolveEulogy 비순수(동일 인자 다른 출력).");
  const epP0 = await resolveEulogyH([pid0], {});
  const epP1 = await resolveEulogyH([pid1], {});
  if (epP0 === epP1) fail("RED: 다른 승객인데 동일 회고(carry falsifiable 실패).");
  const epChA = await resolveEulogyH([pid0], { [pid0]: ch0a });
  const epChB = await resolveEulogyH([pid0], { [pid0]: ch0b });
  if (epChA === epChB) fail("RED: 다른 작별선택인데 동일 회고.");
  // 라이브 __choose 가 eulogy 를 가른다(released 된 pid0 에 작별선택 적용).
  const cFrag = await choose(pid0, ch0a);
  const sChoose = await gs();
  if (!cFrag) fail("__choose 무효(선택 반영 실패).");
  if (sChoose.eulogy !== await resolveEulogyH(sChoose.released, sChoose.choices)) fail("__choose 후 eulogy ≠ resolveEulogy(직접세팅 의심).");
  if (sChoose.eulogy === sFw.eulogy) fail("__choose(작별선택) 후에도 eulogy 불변 — 선택 미반영.");
  results.carry = { pure: true, distinctPassengers: epP0 !== epP1, distinctChoice: epChA !== epChB, emergent: true };

  // ── 전체 루프: 3 영혼 전부 양육→작별→회고 누적 ──────────────────────────────────
  for (const pid of [pid1, pid2]) {
    for (let k = 0; k < C.threshold; k++) await care(pid, w[pid]);
    if (!(await farewell(pid))) fail(`전체 루프: ${pid} farewell 실패.`);
  }
  const sAll = await gs();
  if (sAll.released.length !== C.init.passengers) fail(`전체 루프: released ${sAll.released.length} ≠ ${C.init.passengers}.`);
  if (!sAll.eulogy || sAll.eulogy !== await resolveEulogyH(sAll.released, sAll.choices)) fail("전체 루프: 최종 eulogy 창발 불일치.");
  results.fullLoop = { released: sAll.released.length };

  // ── AC4 resetGame: 런 간 잔존 0 ────────────────────────────────────────────────
  await reset();
  const sR = await gs();
  if (sR.released.length !== 0 || sR.lastMemory !== null || sR.eulogy !== null || Object.keys(sR.choices).length !== 0) {
    fail(`resetGame 잔존: released=${sR.released.length} lastMemory=${sR.lastMemory} eulogy=${sR.eulogy} choices=${JSON.stringify(sR.choices)}.`);
  }
  for (const p of sR.passengers) { if (sR.bonds[p.id] !== 0) fail(`resetGame: bonds[${p.id}] 잔존 ${sR.bonds[p.id]}.`); }
  results.reset = { clean: true };

  // ── 종합 판정 ──────────────────────────────────────────────────────────────────
  const verdict = fatals.length === 0;   // 위 모든 단언은 실패 시 즉시 exit(1) → 여기 도달 = 통과
  console.log(JSON.stringify({ verdict, fatals: fatals.length, negativeMutation, results, contract: C, briefPath }, null, 2));
  if (!verdict) fail(`콘솔/페이지 에러 ${fatals.length}건: ${fatals.slice(0, 3).join(" | ")}`);
  console.log(
    "PASS: care 창발(틀린선물 거부·cap) · farewell 결합(미유대 거부·만유대 released+lastMemory) · " +
      "carry(resolveEulogy 순수·다른승객/선택 상이·창발·직접세팅 아님) · 전체루프 · resetGame 잔존0 · 콘솔0 " +
      "(브리프-구동 + 부정-변이 실증)"
  );
} finally {
  await browser.close();
}
