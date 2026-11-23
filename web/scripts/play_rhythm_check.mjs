/**
 * rhythm-tale e2e — 리듬-내러티브 슬라이스 검증 (provenance: 브리프-구동·anti-hollow).
 *
 * ★AC-구동(provenance): 단언을 하드코딩하지 않고 **핸드오프 브리프**(vault/specs/rhythm-tale.md)를
 *   orch handoff.parse_brief 로 파싱해 acceptance_criteria 계약(judge 윈도우 45/90ms·ending 임계 0.70·
 *   noteCount 범위)을 파생한다. 브리프 AC 가 비면(부정 변이) 계약 파생 불가 → FAIL. clicker/puzzle 선례.
 * ★직격축 AC4(창발·非hollow): ending 은 tap→judge(deltaMs)→Perfect|Good|Miss→accuracy 창발→
 *   resolveEnding(accuracy) 의 결과여야 한다. 검증: (i) __autoPlay 는 judge() 경유 보정탭으로만 구동,
 *   (ii) accuracy=(perfect+good)/notesJudged 가 카운트에서 창발(독립 재계산 일치)·notesJudged==noteCount·
 *   판정카운트가 목표 a 와 정합, (iii) **falsifiable 인과쌍** — 같은 노트열·같은 judge 경로에서 오직 탭
 *   타이밍만 다른 두 런이 ending 을 가른다(accuracy/ending 직접세팅이면 통과 불가).
 * ★게이트 vs 보조스모크(nit-2): 결정론 훅 단언(__noteScreenY 순수·__judge 경계·__autoPlay 창발·__retry
 *   순환·params=state 함수)은 [GATE](하드). 실시간 keydown/포인터의 **판정 라벨**은 [SMOKE](비게이트·
 *   wall-clock 의존) — 단 "실입력이 judge/스케일 경로를 구동해 __state 가 변함"의 구조 단언은 [GATE].
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_rhythm_check.mjs [briefPath]
 *   briefPath 생략 시 vault/specs/rhythm-tale.md.
 *   ENV RHYTHM_NEGATIVE=1 → acceptance_criteria 를 비운 변이(부정 변이 시연·FAIL 기대).
 * exit 0 = PASS, 비-0 = FAIL.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const ORCH_PY = process.env.ORCH_PY || "/opt/conda/envs/orch/bin/python";
const briefPath = process.argv[2] || path.join(REPO, "vault", "specs", "rhythm-tale.md");
const gameUrl = "file://" + path.join(REPO, "games", "rhythm-tale", "index.html");
const NEGATIVE = process.env.RHYTHM_NEGATIVE === "1";

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
  return NEGATIVE ? [] : ac; // 부정 변이: AC 를 비워 계약 파생 실패를 입증.
}

/**
 * 브리프 AC(state_shape/init/judge/accuracy/ending/song/coupling)에서 계약 파생.
 * 윈도우·임계·noteCount 범위는 게임이 아니라 **브리프**에서만 파생(하드코딩 금지) → 브리프 라인이 비면 FAIL.
 */
function deriveContract(ac) {
  const find = (p) => ac.find((s) => s.trim().toLowerCase().startsWith(p));
  const stateShapeLine = find("state_shape:");
  const initLine = find("init:");
  const judgeLine = find("judge:");
  const accuracyLine = find("accuracy:");
  const endingLine = find("ending:");
  const songLine = find("song:");
  const couplingLine = find("coupling:");
  const negativeLine = find("negative:");
  // ── R2 신규 계약 라인(load-bearing — 부재 시 FAIL·하드코딩 차단) ──
  const sectionsLine = find("sections:");
  const finaleLine = find("finale:");
  const layersLine = find("layers:");
  const sectionSelectLine = find("sectionselect:");
  // ── R3 신규 계약 라인(load-bearing — 부재 시 FAIL·하드코딩 차단) ──
  const tracksLine = find("tracks:");
  const stagingLine = find("staging:");
  if (!stateShapeLine || !initLine || !judgeLine || !accuracyLine || !endingLine || !songLine || !couplingLine) {
    fail(
      "브리프 acceptance_criteria 에서 계약 파생 불가" +
        "(state_shape/init/judge/accuracy/ending/song/coupling 중 누락) — AC-구동 provenance 위반(부정 변이/하드코딩)."
    );
  }
  if (!sectionsLine || !finaleLine || !layersLine || !sectionSelectLine) {
    fail(
      "브리프 acceptance_criteria 에서 R2 계약 파생 불가" +
        "(sections/finale/layers/sectionSelect 중 누락) — load-bearing 신규 라인 부재(부정 변이/하드코딩)."
    );
  }
  // state_shape: 필수 키.
  const shapeKeys = stateShapeLine.replace(/^[^:]*:/, "").split(",").map((s) => s.trim()).filter(Boolean);
  for (const k of ["song", "accuracy", "notesJudged", "ending", "phase"]) {
    if (!shapeKeys.includes(k)) fail(`state_shape 계약 필수 키 누락: ${k}`);
  }
  // judge: 윈도우(ms).
  const pm = judgeLine.match(/perfect_window_ms=(\d+)/);
  const gm = judgeLine.match(/good_window_ms=(\d+)/);
  if (!pm || !gm) fail("judge 계약(perfect/good_window_ms) 파싱 불가.");
  // ending: 임계 + 직접세팅 금지 명시.
  const th = endingLine.match(/threshold=([\d.]+)/);
  if (!th) fail("ending 계약(threshold) 파싱 불가.");
  if (!/direct-set forbidden/i.test(endingLine)) fail("ending 계약 'direct-set forbidden'(anti-hollow) 누락.");
  // accuracy: 창발 + 직접세팅 금지.
  if (!/emergent/i.test(accuracyLine) || !/direct-set forbidden/i.test(accuracyLine)) {
    fail("accuracy 계약(emergent·direct-set forbidden) 누락 — anti-hollow 계약 출처 불명.");
  }
  // song: noteCount 범위.
  const nc = songLine.match(/noteCount in \[(\d+),\s*(\d+)\]/);
  if (!nc) fail("song 계약(noteCount in [min,max]) 파싱 불가.");
  // coupling: judge 경유 + noteScreenY 순수 + autoPlay judge 경유.
  if (!/judge\(/i.test(couplingLine) || !/noteScreenY pure/i.test(couplingLine) || !/autoPlay/i.test(couplingLine)) {
    fail("coupling 계약(judge 경유·noteScreenY pure·autoPlay) 누락.");
  }
  // negative(있으면): 직접세팅 금지 재확인.
  if (negativeLine && !/MUST NOT be set directly/i.test(negativeLine)) {
    fail("negative 계약 'MUST NOT be set directly' 누락.");
  }
  // ── R2: sections — count + sectionThreshold + 창발 + 직접세팅 금지 ──
  const secCount = sectionsLine.match(/count=(\d+)/i);
  const secTh = sectionsLine.match(/sectionThreshold=([\d.]+)/i);
  if (!secCount || !secTh) fail("sections 계약(count·sectionThreshold) 파싱 불가.");
  if (!/direct-set forbidden/i.test(sectionsLine)) fail("sections 계약 'direct-set forbidden'(anti-hollow) 누락.");
  // ── R2: finale — ≥3 티어 + 창발 + 직접세팅 금지 ──
  if (!/radiant/i.test(finaleLine) || !/bittersweet/i.test(finaleLine) || !/faded/i.test(finaleLine)) {
    fail("finale 계약(≥3 티어 radiant/bittersweet/faded) 누락.");
  }
  if (!/direct-set forbidden/i.test(finaleLine)) fail("finale 계약 'direct-set forbidden'(anti-hollow) 누락.");
  // ── R2: layers — 콤보 경계 + 결합 불변식(plannedVoices===activeLayers·single-source·bypass 금지) ──
  const bassC = layersLine.match(/bass_combo>=(\d+)/i);
  const percC = layersLine.match(/perc_combo>=(\d+)/i);
  if (!bassC || !percC) fail("layers 계약(bass_combo/perc_combo 경계) 파싱 불가.");
  if (!/voiceset===activeLayers/i.test(layersLine) || !/single-source/i.test(layersLine)) {
    fail("layers 계약(plannedVoices voiceset===activeLayers·single-source 결합 불변식) 누락.");
  }
  if (!/bypass-schedule forbidden/i.test(layersLine)) fail("layers 계약 'bypass-schedule forbidden'(anti-hollow) 누락.");
  if (!/scene_tone->waveform/i.test(layersLine)) fail("layers 계약(scene_tone->waveform) 누락.");
  // ── R2: sectionSelect ──
  if (!/__selectSection/i.test(sectionSelectLine)) fail("sectionSelect 계약(__selectSection) 누락.");
  // ── R3: tracks/staging load-bearing(부재 시 FAIL — 신규 라인 하드코딩 차단) ──
  if (!tracksLine || !stagingLine) {
    fail(
      "브리프 acceptance_criteria 에서 R3 계약 파생 불가(tracks/staging 누락) — load-bearing 신규 라인 부재(부정 변이/하드코딩)."
    );
  }
  const motifC = tracksLine.match(/motifcount=(\d+)/i);
  if (!motifC) fail("tracks 계약(motifCount=N·모티프 수=섹션 수 천장) 파싱 불가.");
  if (!/voiceset===activeLayers/i.test(tracksLine)) fail("tracks 계약(__activeTracks voiceset===activeLayers) 누락.");
  if (!/__sectiontheme/i.test(tracksLine) || !/section-distinct/i.test(tracksLine)) fail("tracks 계약(__sectionTheme section-distinct) 누락.");
  if (!/transpose/i.test(tracksLine)) fail("tracks 계약(melody freq===__sectionTheme transpose 등치) 누락.");
  if (!/bypass-theme forbidden/i.test(tracksLine)) fail("tracks 계약 'bypass-theme forbidden'(anti-hollow) 누락.");
  if (!/voice-counter invariant/i.test(tracksLine)) fail("tracks 계약(voice-counter invariant·R2 AC7(ii) 비회귀) 누락.");
  if (!/#scene/i.test(stagingLine)) fail("staging 계약(#scene 렌더 노드) 누락.");
  if (!/textContent/i.test(stagingLine) || !/render/i.test(stagingLine)) fail("staging 계약(render textContent 소비) 누락.");
  if (!/scene_tone->waveform/i.test(stagingLine)) fail("staging 계약(per-note scene_tone->waveform) 누락.");
  if (!/vestigial forbidden/i.test(stagingLine)) fail("staging 계약 'vestigial forbidden'(anti-hollow) 누락.");
  return {
    shapeKeys,
    PERFECT_MS: Number(pm[1]),
    GOOD_MS: Number(gm[1]),
    THRESHOLD: Number(th[1]),
    noteMin: Number(nc[1]),
    noteMax: Number(nc[2]),
    sectionCount: Number(secCount[1]),
    sectionThreshold: Number(secTh[1]),
    bassCombo: Number(bassC[1]),
    percCombo: Number(percC[1]),
    motifCount: Number(motifC[1]),
  };
}

// ── 판정 누적기(게이트/스모크 마커) ────────────────────────────────────────────
const checks = [];
const gate = (name, ok, detail) => checks.push({ kind: "GATE", name, ok: !!ok, detail: detail || "" });
const smoke = (name, ok, detail) => checks.push({ kind: "SMOKE", name, ok: !!ok, detail: detail || "" });
const approx = (a, b, tol) => Math.abs(a - b) <= tol;

// ── 2) playwright 구동 ─────────────────────────────────────────────────────────
const C = deriveContract(loadBriefAC());

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

  // 헬퍼
  const st = () => page.evaluate(() => window.__state);
  const consts = () => page.evaluate(() => window.__constants);
  const songNotes = () => page.evaluate(() => window.__songNotes());
  const retry = () => page.evaluate(() => window.__retry());
  const start = () => page.evaluate(() => window.__start());
  const autoPlay = (a) => page.evaluate((x) => window.__autoPlay(x), a);
  const tap = (lane, t) => page.evaluate((o) => window.__tapLane(o.lane, o.t), { lane, t });
  const noteY = (id, t) => page.evaluate((o) => window.__noteScreenY(o.id, o.t), { id, t });
  const judgeU = (d) => page.evaluate((x) => window.__judge(x), d);
  const aparams = () => page.evaluate(() => window.__audioParams());
  const vparams = () => page.evaluate(() => window.__visualParams());
  // ── R2 가산 헬퍼 ──
  const sectionNotes = () => page.evaluate(() => window.__sectionNotes());
  const activeLayers = (combo) => page.evaluate((c) => window.__activeLayers({ combo: c }), combo);
  const plannedVoices = (combo) => page.evaluate((c) => window.__plannedVoices({ combo: c }), combo);
  const autoPlayPerSection = (r) => page.evaluate((x) => window.__autoPlayPerSection(x), r);
  const selectSection = (i) => page.evaluate((x) => window.__selectSection(x), i);
  const resolveFinaleH = (ps) => page.evaluate((x) => window.__resolveFinale(x), ps);
  const voiceCalls = () => page.evaluate(() => ({ ...window.__audio.voiceCalls }));

  // ── C1: 콘솔/페이지 에러 0 ──
  const s0 = await st();
  gate("C1 console/pageerror==0 + __state 정의", !!s0 && typeof s0 === "object", `state=${!!s0}`);
  // 프레시 인트로(calm) 파라미터 표본 — AC6 의 '서사 state→음색' 절반 입증용(autoPlay 가 scene 을 바꾸기 전 캡처).
  const apIntro = await page.evaluate(() => window.__audioParams());
  const vpIntro = await page.evaluate(() => window.__visualParams());

  // ── 게임 상수 ↔ 브리프 파생 드리프트 검증(하드코딩/표류 적발) ──
  const K = await consts();
  gate("DRIFT judge/threshold 게임↔브리프 일치",
    K.PERFECT_MS === C.PERFECT_MS && K.GOOD_MS === C.GOOD_MS && approx(K.THRESHOLD, C.THRESHOLD, 1e-9),
    `game(${K.PERFECT_MS}/${K.GOOD_MS}/${K.THRESHOLD}) vs brief(${C.PERFECT_MS}/${C.GOOD_MS}/${C.THRESHOLD})`);

  // ── AC3: 1곡 + 서사 장면 ≥1 (분리형 모델 — ③ 입증 아님) ──
  gate("AC3 song 정의 + noteCount 범위",
    !!s0.song && s0.song.noteCount >= C.noteMin && s0.song.noteCount <= C.noteMax,
    `noteCount=${s0.song && s0.song.noteCount} in [${C.noteMin},${C.noteMax}]`);
  gate("AC3 init scenesShown>=1 + phase=intro + 카운트 0",
    s0.scenesShown >= 1 && s0.phase === "intro" && s0.score === 0 && s0.accuracy === 0 && s0.notesJudged === 0,
    `scenesShown=${s0.scenesShown} phase=${s0.phase}`);

  // ── AC1: __noteScreenY 순수 + 비트시각에 판정선 도달 ──
  const notes = await songNotes();
  const n0 = notes[0], n7 = notes[7];
  const yAt = await noteY(n0.id, n0.time);              // t=note.time → 판정선
  const ySpawn = await noteY(n0.id, n0.time - K.LEAD_TIME); // t=note.time-LEAD → 스폰(상단 0)
  const yMid = await noteY(n0.id, n0.time - K.LEAD_TIME / 2); // 중간(낙하 함수 단조 증가)
  const pureA = await noteY(n7.id, n7.time);
  const pureB = await noteY(n7.id, n7.time);            // 동일인자 → 동일출력(순수)
  gate("AC1 note.time 에 판정선 도달", approx(yAt, K.JUDGE_Y, 1e-6), `y=${yAt} JUDGE_Y=${K.JUDGE_Y}`);
  gate("AC1 스폰시 상단(0)·중간 단조 낙하(스텁 아님)",
    approx(ySpawn, 0, 1e-6) && yMid > ySpawn + 1 && yMid < yAt - 1, `spawn=${ySpawn} mid=${yMid} judge=${yAt}`);
  gate("AC1 순수성(동일인자→동일출력·내부 클럭 read 없음)", pureA === pureB, `${pureA}===${pureB}`);

  // ── AC2: judge 경계(단위) + tapLane on/off-beat(파이프라인) ──
  const jb = {
    p0: await judgeU(0), p44: await judgeU(C.PERFECT_MS - 1), pEdge: await judgeU(C.PERFECT_MS),
    gIn: await judgeU(C.PERFECT_MS + 1), gEdge: await judgeU(C.GOOD_MS), mIn: await judgeU(C.GOOD_MS + 1),
    mFar: await judgeU(200),
  };
  gate("AC2 judge 경계 ±45/±90 전이",
    jb.p0 === "Perfect" && jb.p44 === "Perfect" && jb.pEdge === "Perfect" &&
    jb.gIn === "Good" && jb.gEdge === "Good" && jb.mIn === "Miss" && jb.mFar === "Miss",
    JSON.stringify(jb));
  // 파이프라인: on-beat → Perfect + score↑, off-beat → Miss(점수 불변).
  await retry();
  const before = await st();
  await tap(n0.lane, n0.time);                          // on-beat
  const afterP = await st();
  await tap(notes[1].lane, notes[1].time + 0.2);        // off-beat(200ms) → Miss
  const afterM = await st();
  gate("AC2 on-beat→Perfect+score↑",
    afterP.lastJudgment === "Perfect" && afterP.score > before.score && afterP.perfect === 1,
    `label=${afterP.lastJudgment} score ${before.score}->${afterP.score}`);
  gate("AC2 off-beat→Miss(점수 불변)",
    afterM.lastJudgment === "Miss" && afterM.score === afterP.score && afterM.miss === 1,
    `label=${afterM.lastJudgment} score=${afterM.score}`);

  // ── AC4 (i)(ii): 창발 — __autoPlay(a) 보정탭 → accuracy 카운트에서 창발 → ending ──
  const chartBefore = JSON.stringify((await songNotes()).map((x) => [x.time, x.lane]));
  const hi = await autoPlay(0.95);
  const sHi = await st();
  const expHitsHi = Math.round(0.95 * s0.song.noteCount);
  const accRecalcHi = (sHi.perfect + sHi.good) / sHi.notesJudged; // 독립 재계산(창발 입증)
  gate("AC4(ii) 창발 hi: notesJudged==noteCount AND accuracy 재계산 일치 AND 카운트 정합",
    sHi.notesJudged === s0.song.noteCount &&
      approx(sHi.accuracy, accRecalcHi, 1e-9) &&
      approx(sHi.accuracy, 0.95, 0.03) &&
      sHi.perfect + sHi.good === expHitsHi && sHi.miss === s0.song.noteCount - expHitsHi,
    `acc=${sHi.accuracy} recalc=${accRecalcHi} P+G=${sHi.perfect + sHi.good}/${expHitsHi} miss=${sHi.miss}`);
  gate("AC4(i) hi ending=A (창발 accuracy>=threshold)", sHi.ending === "A", `ending=${sHi.ending} acc=${sHi.accuracy}`);

  const lo = await autoPlay(0.40);
  const sLo = await st();
  const expHitsLo = Math.round(0.40 * s0.song.noteCount);
  const accRecalcLo = (sLo.perfect + sLo.good) / sLo.notesJudged;
  gate("AC4(ii) 창발 lo: notesJudged==noteCount AND accuracy 재계산 일치 AND 카운트 정합",
    sLo.notesJudged === s0.song.noteCount &&
      approx(sLo.accuracy, accRecalcLo, 1e-9) &&
      approx(sLo.accuracy, 0.40, 0.03) &&
      sLo.perfect + sLo.good === expHitsLo && sLo.miss === s0.song.noteCount - expHitsLo,
    `acc=${sLo.accuracy} recalc=${accRecalcLo} P+G=${sLo.perfect + sLo.good}/${expHitsLo} miss=${sLo.miss}`);
  gate("AC4(i) lo ending=B (창발 accuracy<threshold)", sLo.ending === "B", `ending=${sLo.ending} acc=${sLo.accuracy}`);

  // ── AC4 (iii): falsifiable 인과쌍 — 같은 차트·같은 judge 경로, 오직 타이밍만 다른 두 런이 ending 갈림 ──
  const chartAfter = JSON.stringify((await songNotes()).map((x) => [x.time, x.lane]));
  // 가장 순수한 쌍: autoPlay(1.0)=전부 온비트(Δ0) vs autoPlay(0.0)=전부 200ms 오프 — 노트 동일·균일 오프셋만 반전.
  const allHit = await autoPlay(1.0); const sAll = await st();
  const allMiss = await autoPlay(0.0); const sNone = await st();
  gate("AC4(iii) 인과쌍: 동일 차트(런 간 노트열 불변)", chartBefore === chartAfter,
    `chartStable=${chartBefore === chartAfter}`);
  gate("AC4(iii) 인과쌍: 오직 타이밍만 달라 ending 갈림(1.0→A acc=1, 0.0→B acc=0)",
    sAll.ending === "A" && sAll.accuracy === 1 && sNone.ending === "B" && sNone.accuracy === 0 &&
      sAll.notesJudged === s0.song.noteCount && sNone.notesJudged === s0.song.noteCount,
    `all(${sAll.ending},${sAll.accuracy}) none(${sNone.ending},${sNone.accuracy})`);
  gate("AC4(iii) 인과쌍: 임계 교차가 분기 원인(accA>=th>accB)",
    sHi.accuracy >= C.THRESHOLD && sLo.accuracy < C.THRESHOLD && sHi.ending !== sLo.ending,
    `accHi=${sHi.accuracy} accLo=${sLo.accuracy} th=${C.THRESHOLD}`);

  // ── AC4 독립 파이프라인(non-autoPlay) 창발: 수동 30 Perfect + 10 Miss → acc 0.75 → ending A ──
  await retry();
  const man = await songNotes();
  for (let i = 0; i < man.length; i++) await tap(man[i].lane, man[i].time + (i < 30 ? 0 : 0.2));
  const sMan = await st();
  gate("AC4 독립 파이프라인 창발(autoPlay 없이 tap→judge→accuracy→ending)",
    sMan.notesJudged === s0.song.noteCount && sMan.perfect === 30 && sMan.miss === 10 &&
      approx(sMan.accuracy, 0.75, 1e-9) && sMan.ending === "A",
    `P=${sMan.perfect} M=${sMan.miss} acc=${sMan.accuracy} ending=${sMan.ending}`);

  // ── AC5: 결과 → __retry → play 순환 ──
  await autoPlay(0.95);
  const sRes = await st();
  await retry();
  const sPlay = await st();
  gate("AC5 phase result→__retry→play (+재무장)",
    sRes.phase === "result" && sPlay.phase === "play" && sPlay.notesJudged === 0 && sPlay.score === 0,
    `result.phase=${sRes.phase} retry.phase=${sPlay.phase}`);

  // ── AC6: audioParams/visualParams = state 의 결정론 순수 함수 ──
  await retry();                                        // accuracy0/combo0
  const vpLow = await vparams(); const apLow = await aparams();
  const vpLow2 = await vparams();                       // 동일 state → 동일 출력(결정론)
  await autoPlay(0.95);                                 // accuracy~0.95·combo·scene=hope
  const vpHigh = await vparams(); const apHigh = await aparams();
  gate("AC6 결정론(동일 state→동일 params)", JSON.stringify(vpLow) === JSON.stringify(vpLow2),
    `vpLow==vpLow2=${JSON.stringify(vpLow) === JSON.stringify(vpLow2)}`);
  gate("AC6 params=state 함수(상이 state→상이 params·visual)",
    JSON.stringify(vpLow) !== JSON.stringify(vpHigh) && vpLow.hue !== vpHigh.hue,
    `low.hue=${vpLow.hue} high.hue=${vpHigh.hue} low.tone=${vpLow.tone} high.tone=${vpHigh.tone}`);
  gate("AC6 params=state 함수(상이 state→상이 params·audio)",
    JSON.stringify(apLow) !== JSON.stringify(apHigh) && apLow.rootFreq !== apHigh.rootFreq,
    `low.rootFreq=${apLow.rootFreq} high.rootFreq=${apHigh.rootFreq} waveform ${apLow.waveform}->${apHigh.waveform}`);
  // ③ 서사 state→음색: 인트로(calm→triangle·hue0) vs 엔딩(hope→sine) — 연주 state 외 서사 state 의존도 입증.
  gate("AC6 서사 state→음색/색(intro calm:triangle,hue0 → ending hope:sine)",
    apIntro.waveform === "triangle" && vpIntro.tone === "calm" && vpIntro.hue === 0 && apHigh.waveform === "sine",
    `intro(${apIntro.waveform},${vpIntro.tone},hue${vpIntro.hue}) → high(${apHigh.waveform})`);

  // ════════════════════════════════════════════════════════════════════════════════
  // R2 가산 게이트 (AC7~10 + DRIFT 신규임계 + GOLDEN 차트) — 기존 AC1~6 비회귀 위에 가산
  // ════════════════════════════════════════════════════════════════════════════════
  const noteN = notes.length;

  // ── DRIFT+: 신규 임계(섹션수·sectionThreshold·콤보경계) 게임↔브리프 일치(하드코딩/표류 적발) ──
  gate("DRIFT+ 섹션수/sectionThreshold/콤보경계 게임↔브리프 일치",
    K.SECTION_COUNT === C.sectionCount && approx(K.SECTION_THRESHOLD, C.sectionThreshold, 1e-9) &&
      K.BASS_COMBO === C.bassCombo && K.PERC_COMBO === C.percCombo,
    `game(cnt${K.SECTION_COUNT}/th${K.SECTION_THRESHOLD}/b${K.BASS_COMBO}/p${K.PERC_COMBO}) vs brief(cnt${C.sectionCount}/th${C.sectionThreshold}/b${C.bassCombo}/p${C.percCombo})`);

  // ── GOLDEN: __songNotes times/lanes === 시드 0x1357 산출(section 이 rng 을 소비하면 lanes 표류 → 적발) ──
  const golden = (() => {
    function mulberry32(seed) { let a = seed >>> 0; return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
    const LANES = 4, rng = mulberry32(0x1357), out = []; let lastLane = -1;
    for (let i = 0; i < K.NOTE_COUNT; i++) {
      let lane = Math.floor(rng() * LANES);
      if (lane === lastLane) lane = (lane + 1) % LANES;
      lastLane = lane;
      out.push([+(K.NOTE_START + i * K.NOTE_GAP).toFixed(3), lane]);
    }
    return out;
  })();
  const liveChart = (await songNotes()).map((x) => [x.time, x.lane]);
  gate("GOLDEN 차트 times/lanes === 시드 0x1357 산출(section rng 비소비)",
    JSON.stringify(liveChart) === JSON.stringify(golden),
    `match=${JSON.stringify(liveChart) === JSON.stringify(golden)} n=${liveChart.length}/${golden.length}`);

  // ── 섹션 분할(노트수 합 = noteCount · section 인덱스 0..2) ──
  const secNotes = await sectionNotes();
  gate("R2 섹션 분할: 3섹션·합=noteCount·각 그룹 section 인덱스 일치",
    secNotes.length === C.sectionCount &&
      secNotes.reduce((s, g) => s + g.length, 0) === noteN &&
      secNotes.every((g, i) => g.every((n) => n.section === i)),
    `sizes=${secNotes.map((g) => g.length).join("/")} total=${noteN}`);

  // ── AC7 (i) 순수 상태함수: __activeLayers(콤보→레이어 집합) + __audioParams(scene→waveform) ──
  const layLo = await activeLayers(0);
  const layMid = await activeLayers(C.bassCombo);
  const layHi = await activeLayers(C.percCombo);
  const layLo2 = await activeLayers(0);
  gate("AC7(i) __activeLayers 콤보→레이어 집합 증가(순수·결정론)",
    layLo.length === 1 && layMid.length === 2 && layHi.length === 3 &&
      layLo.indexOf("melody") >= 0 && layMid.indexOf("bass") >= 0 && layHi.indexOf("perc") >= 0 &&
      JSON.stringify(layLo) === JSON.stringify(layLo2),
    `lo=${JSON.stringify(layLo)} mid=${JSON.stringify(layMid)} hi=${JSON.stringify(layHi)}`);
  await selectSection(0); const w0 = (await aparams()).waveform;
  await selectSection(1); const w1 = (await aparams()).waveform;
  await selectSection(2); const w2 = (await aparams()).waveform;
  await selectSection(0); const w0b = (await aparams()).waveform;
  gate("AC7(i) __audioParams scene(섹션 톤)→음색(waveform) 변조·결정론(필터 미도입)",
    w0 !== w1 && w1 !== w2 && w0 !== w2 && w0 === w0b,
    `w0=${w0} w1=${w1} w2=${w2} det=${w0 === w0b}`);

  // ── AC7 (ii) ★결합 불변식(hollow 차단): plannedVoices===activeLayers + scheduleMelody 실배선 성부 카운터 ──
  const pvLo = await plannedVoices(0);
  const pvHi = await plannedVoices(C.percCombo);
  const pvLo2 = await plannedVoices(0);
  const voiceSet = (pv) => [...new Set(pv.map((e) => e.voice))].sort();
  const alLoSorted = (await activeLayers(0)).slice().sort();
  const alHiSorted = (await activeLayers(C.percCombo)).slice().sort();
  gate("AC7(ii) __plannedVoices 성부집합 === __activeLayers (저·고 state)",
    JSON.stringify(voiceSet(pvLo)) === JSON.stringify(alLoSorted) &&
      JSON.stringify(voiceSet(pvHi)) === JSON.stringify(alHiSorted),
    `pvLo=${JSON.stringify(voiceSet(pvLo))} alLo=${JSON.stringify(alLoSorted)} pvHi=${JSON.stringify(voiceSet(pvHi))} alHi=${JSON.stringify(alHiSorted)}`);
  gate("AC7(ii) 콤보 저→고 성부 개수 증가 + __plannedVoices 순수(동일 state→동일 스케줄)",
    voiceSet(pvHi).length > voiceSet(pvLo).length && JSON.stringify(pvLo) === JSON.stringify(pvLo2),
    `lo=${voiceSet(pvLo).length} hi=${voiceSet(pvHi).length} pure=${JSON.stringify(pvLo) === JSON.stringify(pvLo2)}`);
  // ★성부별 scheduleTone 호출 카운터: 저콤보=멜로디만, 고콤보=멜로디+베이스+퍼커션(scheduleMelody 가 단성 잔존=우회=RED)
  const vc0 = await voiceCalls();
  await page.evaluate(() => { window.__state.combo = 0; window.__scheduleMelody(); });
  const vc1 = await voiceCalls();
  await page.evaluate((p) => { window.__state.combo = p; window.__scheduleMelody(); }, C.percCombo);
  const vc2 = await voiceCalls();
  const dLo = { melody: vc1.melody - vc0.melody, bass: vc1.bass - vc0.bass, perc: vc1.perc - vc0.perc };
  const dHi = { melody: vc2.melody - vc1.melody, bass: vc2.bass - vc1.bass, perc: vc2.perc - vc1.perc };
  gate("AC7(ii) 저콤보 scheduleMelody: 멜로디만 실스케줄(베이스/퍼커션 호출 0)",
    dLo.melody === noteN && dLo.bass === 0 && dLo.perc === 0,
    `melody=${dLo.melody}/${noteN} bass=${dLo.bass} perc=${dLo.perc}`);
  gate("AC7(ii) ★고콤보 scheduleMelody: 멜로디+베이스+퍼커션 실스케줄(단성 잔존=RED·hollow 차단 증명)",
    dHi.melody === noteN && dHi.bass === noteN && dHi.perc === noteN,
    `melody=${dHi.melody} bass=${dHi.bass} perc=${dHi.perc} (각 ==${noteN})`);
  await retry();  // combo 직접세팅 정리(상태 복원)

  // ── AC8: 3섹션 + 섹션 전이 게이트(전이 1회·perSection 창발·전곡 백본 비파괴) ──
  await retry();
  const sBeforeSec = await st();
  for (const n of secNotes[0]) await tap(n.lane, n.time);   // 섹션0 전 노트 온비트 판정
  const afterS0 = await st();
  const ps0 = afterS0.perSection[0];
  const ps0recalc = ps0.notesJudged ? (ps0.perfect + ps0.good) / ps0.notesJudged : 0;
  gate("AC8 섹션0 전 노트 판정→전이 1회(section 0→1·scenesShown++·phase 유지·미rearm)",
    afterS0.section === 1 && afterS0.scenesShown === sBeforeSec.scenesShown + 1 &&
      afterS0.notesJudged === secNotes[0].length && afterS0.phase === "play",
    `section=${afterS0.section} scenes ${sBeforeSec.scenesShown}->${afterS0.scenesShown} njudged=${afterS0.notesJudged}/${secNotes[0].length} phase=${afterS0.phase}`);
  gate("AC8 perSection[0] 창발(독립 재계산 일치·P+G+M=notesJudged·카운트 정합)",
    ps0.notesJudged === secNotes[0].length && ps0.perfect + ps0.good + ps0.miss === ps0.notesJudged &&
      approx(ps0.accuracy, ps0recalc, 1e-9) && ps0.perfect === secNotes[0].length && ps0.miss === 0,
    `P=${ps0.perfect} G=${ps0.good} M=${ps0.miss} acc=${ps0.accuracy} recalc=${ps0recalc}`);
  await autoPlay(1.0);
  const sFull = await st();
  gate("AC8 전곡 autoPlay→finishSong 비파괴(phase result·section 마지막 도달·ending 보존)",
    sFull.phase === "result" && sFull.notesJudged === noteN && sFull.section === C.sectionCount - 1 && sFull.ending === "A",
    `phase=${sFull.phase} njudged=${sFull.notesJudged} section=${sFull.section} ending=${sFull.ending}`);

  // ── AC9: 다중엔딩 ≥3(섹션 성과 누적·falsifiable·perSection 창발 3종·finale===resolveFinale) ──
  await autoPlayPerSection([1, 1, 1]); const s111 = await st();
  await autoPlayPerSection([1, 1, 0]); const s110 = await st();
  await autoPlayPerSection([0, 0, 0]); const s000 = await st();
  const distinctFinale = new Set([s111.finale, s110.finale, s000.finale]);
  gate("AC9 ≥3 distinct finale([1,1,1]→radiant·[1,1,0]→bittersweet·[0,0,0]→faded)",
    s111.finale === "radiant" && s110.finale === "bittersweet" && s000.finale === "faded" && distinctFinale.size >= 3,
    `111=${s111.finale} 110=${s110.finale} 000=${s000.finale} distinct=${distinctFinale.size}`);
  gate("AC9 단일 섹션 플립 falsifiable([1,1,1]↔[1,1,0] 섹션2만 반전→finale 갈림)",
    s111.finale !== s110.finale && s111.sectionsCleared === C.sectionCount && s110.sectionsCleared === C.sectionCount - 1,
    `cleared 111=${s111.sectionsCleared} 110=${s110.sectionsCleared}`);
  const profiles = [{ s: s111, a: [1, 1, 1] }, { s: s110, a: [1, 1, 0] }, { s: s000, a: [0, 0, 0] }];
  let emergentOk = true, emergentDetail = "";
  for (const { s, a } of profiles) {
    for (let i = 0; i < C.sectionCount; i++) {
      const ps = s.perSection[i], cnt = secNotes[i].length;
      const recalc = ps.notesJudged ? (ps.perfect + ps.good) / ps.notesJudged : 0;
      const ok = ps.notesJudged === cnt && ps.perfect + ps.good + ps.miss === ps.notesJudged &&
        approx(ps.accuracy, recalc, 1e-9) && approx(ps.accuracy, a[i], 0.1);
      if (!ok) { emergentOk = false; emergentDetail = `prof[${a}] sec${i}: njudged=${ps.notesJudged}/${cnt} acc=${ps.accuracy} recalc=${recalc}`; break; }
    }
    if (!emergentOk) break;
  }
  gate("AC9 perSection 창발 3종(notesJudged==섹션수 · |acc−a|≤tol · P/G/M 정합·독립 재계산)",
    emergentOk, emergentDetail || "all profiles ok");
  const rf111 = await resolveFinaleH(s111.perSection);
  const rf110 = await resolveFinaleH(s110.perSection);
  gate("AC9 state.finale === resolveFinale(perSection)(직접세팅 불가·창발 함수)",
    s111.finale === rf111 && s110.finale === rf110,
    `111(${s111.finale}===${rf111}) 110(${s110.finale}===${rf110})`);

  // ── AC10: 섹션 셀렉트(section=i·armed·phase play·판정 가능·softlock 회피) ──
  await selectSection(2);
  const sSel = await st();
  const armedS2 = (await songNotes()).filter((n) => n.section === 2).every((n) => !n.judged);
  const s2first = secNotes[2][0];
  await tap(s2first.lane, s2first.time);                    // 섹션2 첫 노트 온비트 → Perfect(판정 가능)
  const sAfterSelTap = await st();
  gate("AC10 __selectSection(2)→section=2·armed·phase play·판정 가능(softlock 회피)",
    sSel.section === 2 && sSel.phase === "play" && armedS2 &&
      sAfterSelTap.notesJudged === 1 && sAfterSelTap.lastJudgment === "Perfect",
    `section=${sSel.section} phase=${sSel.phase} armed=${armedS2} firstTap=${sAfterSelTap.lastJudgment}`);
  await retry();  // 상태 정리(이후 C2 실입력 테스트 준비)

  // ── AC7 [SMOKE] 실 startSong→scheduleMelody 경로가 다층 스케줄러를 구동(가청 feel·비게이트·결정론 카운터) ──
  const vcBeforeStart = await voiceCalls();

  // ── C2: 실입력 — 키 + 포인터(CSS 스케일 hit-test) 가 judge/스케일 경로를 구동 [구조=GATE / 판정라벨=SMOKE] ──
  await start();                                        // intro→play(실 제스처 경로 진입·scheduleMelody 발화)
  const vcAfterStart = await voiceCalls();
  smoke("AC7[SMOKE] 실 startSong 이 scheduleMelody 다층 경로 구동(멜로디 성부 호출 delta>0)",
    vcAfterStart.melody - vcBeforeStart.melody > 0, `delta melody=${vcAfterStart.melody - vcBeforeStart.melody}`);
  const tapsBeforeKey = (await st()).taps;
  await page.keyboard.press("f");                       // 실 keydown (레인 1)
  const sKey = await st();
  gate("C2 실 keydown 이 judge 경로 구동(taps 증가)", sKey.taps === tapsBeforeKey + 1, `taps ${tapsBeforeKey}->${sKey.taps}`);
  smoke("C2 실 keydown 판정 라벨(비게이트·실시간)", sKey.lastJudgment != null, `label=${sKey.lastJudgment}`);

  // 포인터: 작은 뷰포트로 캔버스 축소 → CSS 스케일 보정 경로 실제 통과(레인 2 중앙 클릭 → lastTapLane==2).
  await page.setViewportSize({ width: 360, height: 780 });
  const box = await page.locator("#game").boundingBox();
  const laneCssW = box.width / 4;
  const cx = box.x + laneCssW * 2 + laneCssW / 2;       // 레인 2(0-indexed) CSS 중앙
  const cy = box.y + box.height * 0.86;
  await start();
  const tapsBeforePtr = (await st()).taps;
  await page.mouse.click(cx, cy);
  const sPtr = await st();
  gate("C2 포인터 CSS 스케일 hit-test → 레인 매핑 정확(lastTapLane==2)",
    sPtr.lastTapLane === 2 && sPtr.taps === tapsBeforePtr + 1,
    `lastTapLane=${sPtr.lastTapLane} scaled(box.w=${Math.round(box.width)})`);

  // ════════════════════════════════════════════════════════════════════════════════
  // ★R2 Stage3 사각폐색: live-flow 직격 게이트 (헤드리스 훅이 못 보던 retry·다층 live 발화)
  //   수정 전 RED(설계 의도) — retry 가 songStart 미재설정·멜로디 미재스케줄·콤보 상승이 베이스/퍼커션 미발화.
  // ════════════════════════════════════════════════════════════════════════════════
  // ── MAJOR-1 retry-live: retry 가 startSong 타이밍 setup(songStart=A.now() + scheduleMelody)을 공유 ──
  await start();                                        // 실 제스처(songStart=A.now()·playbackActive·멜로디 스케줄)
  await page.waitForTimeout(180);                       // 마스터 클럭 진행(곡 진행 시뮬 — songStart stale 면 songRelTime 누적)
  const vcPreRetry = await voiceCalls();
  await retry();
  const vcPostRetry = await voiceCalls();
  const ssAfterRetry = await page.evaluate(() => window.__songStart());
  const nowAfterRetry = await page.evaluate(() => window.__audioTime());
  const relAfterRetry = await page.evaluate(() => window.__songTime());
  gate("MAJOR-1 retry-live: retry 가 멜로디 재스케줄(startSong setup 공유·scheduleMelody delta==noteCount)",
    vcPostRetry.melody - vcPreRetry.melody === noteN,
    `melodyΔ=${vcPostRetry.melody - vcPreRetry.melody}/${noteN}`);
  gate("MAJOR-1 retry-live: retry 가 songStart 를 A.now() 기준 재설정(stale 제거·songRel≈0·즉시 auto-miss 차단)",
    approx(ssAfterRetry, nowAfterRetry, 0.05) && relAfterRetry < 0.06,
    `songStart=${ssAfterRetry.toFixed(3)} now=${nowAfterRetry.toFixed(3)} songRel=${relAfterRetry.toFixed(3)}`);

  // ── MAJOR-2 다층 live: 강제 combo 주입이 아니라 라이브 judge 경로(콤보 상승)로 베이스/퍼커션 실스케줄 ──
  await start();                                        // playbackActive=true·songStart 재설정·멜로디(combo=0) 스케줄
  const vcLiveA = await voiceCalls();
  await page.evaluate((pc) => {                         // 첫 (pc+2) 노트를 온비트로 judge → 콤보를 퍼커션 경계 위로(라이브 경로)
    const ns = window.__songNotes();
    for (let i = 0; i < pc + 2 && i < ns.length; i++) window.__tapLane(ns[i].lane, ns[i].time);
  }, C.percCombo);
  const comboLive = (await st()).combo;
  const vcLiveB = await voiceCalls();
  gate("MAJOR-2 다층 live: 라이브 콤보 상승(judge 경로)→베이스/퍼커션 실스케줄(강제주입 아님·hollow 차단)",
    comboLive >= C.percCombo && (vcLiveB.bass - vcLiveA.bass) > 0 && (vcLiveB.perc - vcLiveA.perc) > 0,
    `combo=${comboLive} bassΔ=${vcLiveB.bass - vcLiveA.bass} percΔ=${vcLiveB.perc - vcLiveA.perc}`);
  await retry();                                        // 상태 정리(이후 스크린샷 autoPlay 가 재무장)

  // ════════════════════════════════════════════════════════════════════════════════
  // ★R3 가산 게이트 — R-AC11(다중트랙 테마·PITCH+TIMBRE 등치) · R-AC12(풀 ③ 연출 렌더 소비) · LF(live-flow)
  //   결합 불변식(테마 함수 등치) + 렌더 소비 DOM + 성부 카운터 불변 — 오디오/픽셀 측정 0. 기존 AC1~10 비회귀 위에 가산.
  // ════════════════════════════════════════════════════════════════════════════════
  const sectionTheme = (i) => page.evaluate((x) => window.__sectionTheme(x), i);
  const activeTracks = (s) => page.evaluate((x) => window.__activeTracks(x), s);
  const toneToWaveform = (t) => page.evaluate((x) => window.__toneToWaveform(x), t);
  const sectionScenes = () => page.evaluate(() => window.__sectionScenes());
  const sceneDomText = (sc) => page.evaluate((x) => window.__sceneDomText(x), sc);
  const sceneDom = () => page.evaluate(() => { const e = document.getElementById("scene"); return e ? e.textContent : null; });
  const raf2 = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const transposeJS = (f, s) => +(f * Math.pow(2, s / 12)).toFixed(3);
  const noteByTime = {};
  for (const n of notes) noteByTime[n.time] = n;       // time(고유·toFixed3) → {lane,section} (plannedVoices 이벤트엔 lane 부재 → 교차참조)
  const laneFreq = K.LANE_FREQ;

  // ── DRIFT+(R3): 모티프 수=섹션 수 게임↔브리프 일치(신규 천장·brief 파생·하드코딩 차단) ──
  gate("DRIFT+ R3 모티프수=섹션수 게임↔브리프 일치(SECTION_PITCH.length===SECTION_COUNT===brief motifCount)",
    C.motifCount === K.SECTION_COUNT && K.SECTION_PITCH.length === C.motifCount,
    `brief motifCount=${C.motifCount} game SECTION_COUNT=${K.SECTION_COUNT} pitchLen=${K.SECTION_PITCH.length}`);

  // ── R-AC11 (i) 순수: __sectionTheme 섹션 distinct·순수 + __activeTracks voice===activeLayers + motifId ──
  const th0 = await sectionTheme(0), th1 = await sectionTheme(1), th2 = await sectionTheme(2), th0b = await sectionTheme(0);
  gate("R-AC11(i) __sectionTheme 섹션 distinct·순수({pitchShift,tone}·인덱스 파생)",
    JSON.stringify(th0) !== JSON.stringify(th1) && JSON.stringify(th1) !== JSON.stringify(th2) &&
      JSON.stringify(th0) !== JSON.stringify(th2) && JSON.stringify(th0) === JSON.stringify(th0b) &&
      typeof th0.pitchShift === "number" && typeof th0.tone === "string",
    `th0=${JSON.stringify(th0)} th1=${JSON.stringify(th1)} th2=${JSON.stringify(th2)} pure=${JSON.stringify(th0) === JSON.stringify(th0b)}`);
  const trkLo = await activeTracks({ combo: 0, section: 0 });
  const trkHi = await activeTracks({ combo: C.percCombo, section: 1 });
  const trkVoiceSet = (t) => [...new Set(t.map((x) => x.voice))].sort();
  const alLo0 = (await activeLayers(0)).slice().sort();
  const alHi0 = (await activeLayers(C.percCombo)).slice().sort();
  gate("R-AC11(i) __activeTracks voice집합===__activeLayers(저·고 state·R2 AC7 브리지)·motifId===__sectionTheme(section)",
    JSON.stringify(trkVoiceSet(trkLo)) === JSON.stringify(alLo0) &&
      JSON.stringify(trkVoiceSet(trkHi)) === JSON.stringify(alHi0) &&
      trkLo.every((x) => JSON.stringify(x.motifId) === JSON.stringify(th0)) &&
      trkHi.every((x) => JSON.stringify(x.motifId) === JSON.stringify(th1)),
    `trkLoV=${JSON.stringify(trkVoiceSet(trkLo))} alLo=${JSON.stringify(alLo0)} trkHiV=${JSON.stringify(trkVoiceSet(trkHi))} alHi=${JSON.stringify(alHi0)}`);

  // ── R-AC11 (ii) ★PITCH 결합 = 테마 함수 등치(hollow 차단): melody freq === LANE_FREQ[lane]에 __sectionTheme transpose 적용한 독립 기대값 ──
  const pvAll = await plannedVoices(C.percCombo);      // melody+bass+perc·노트별 섹션 테마 bake
  const themes = [th0, th1, th2];
  const melodyEv = pvAll.filter((e) => e.voice === "melody");
  let pitchOk = true, pitchDetail = "", transposedCount = 0;
  for (const e of melodyEv) {
    const n = noteByTime[e.time];
    if (!n || e.section !== n.section) { pitchOk = false; pitchDetail = `section/time mismatch t=${e.time}`; break; }
    const expected = transposeJS(laneFreq[n.lane], themes[e.section].pitchShift);   // 독립 산출(테마 transpose 적용)
    if (Math.abs(e.freq - expected) > 1e-9) { pitchOk = false; pitchDetail = `t=${e.time} lane=${n.lane} sec=${e.section} freq=${e.freq} exp=${expected}`; break; }
    if (Math.abs(laneFreq[n.lane] - expected) > 1e-9) transposedCount++;            // raw≠기대 → 테마 실배선(테마 미소비 raw 면 RED)
  }
  gate("R-AC11(ii) ★PITCH 등치: melody freq===LANE_FREQ[lane]에 __sectionTheme transpose 적용 독립값(테마 미소비 raw→RED)",
    pitchOk && transposedCount > 0,
    `pitchOk=${pitchOk} transposed=${transposedCount}/${melodyEv.length}(raw≠기대) ${pitchDetail}`);

  // ── R-AC11 (ii) null/identity 테마 가드: same-lane cross-section melody freq 상이(섹션 무차별=동일→RED) ──
  const byLane = {};
  for (const e of melodyEv) { const n = noteByTime[e.time]; (byLane[n.lane] = byLane[n.lane] || []).push({ section: e.section, freq: e.freq }); }
  let crossOk = true, crossPairs = 0, crossDetail = "";
  for (const lane of Object.keys(byLane)) {
    const arr = byLane[lane];
    for (let i = 0; i < arr.length; i++) for (let j = i + 1; j < arr.length; j++) {
      if (arr[i].section !== arr[j].section) {
        crossPairs++;
        if (Math.abs(arr[i].freq - arr[j].freq) < 1e-9) { crossOk = false; crossDetail = `lane=${lane} sec${arr[i].section}/${arr[j].section} freq=${arr[i].freq}`; }
      }
    }
  }
  gate("R-AC11(ii) same-lane cross-section melody freq 상이(identity/null 테마 차단·≥1 쌍 존재)",
    crossOk && crossPairs > 0,
    `crossOk=${crossOk} pairs=${crossPairs} ${crossDetail}`);

  // ── R-AC11/12 TIMBRE 등치: 섹션별 melody 음색 === toneToWaveform(__sectionTheme(section).tone)·섹션 distinct ──
  const expW = [await toneToWaveform(th0.tone), await toneToWaveform(th1.tone), await toneToWaveform(th2.tone)];
  let timbreOk = true, timbreDetail = "";
  for (const e of melodyEv) {
    if (e.type !== expW[e.section]) { timbreOk = false; timbreDetail = `t=${e.time} sec=${e.section} type=${e.type} exp=${expW[e.section]}`; break; }
  }
  gate("R-AC11/12 TIMBRE 등치: __plannedVoices 섹션 melody 음색===toneToWaveform(__sectionTheme(section).tone)·섹션 distinct",
    timbreOk && expW[0] !== expW[1] && expW[1] !== expW[2] && expW[0] !== expW[2],
    `expW=${JSON.stringify(expW)} ${timbreDetail}`);

  // ── R-AC11 ★성부 카운터 정확값 불변 재단언(테마=freq/type 변조만·신규 성부 0·R2 AC7(ii) verbatim) ──
  const vcR0 = await voiceCalls();
  await page.evaluate(() => { window.__state.combo = 0; window.__scheduleMelody(); });
  const vcR1 = await voiceCalls();
  await page.evaluate((p) => { window.__state.combo = p; window.__scheduleMelody(); }, C.percCombo);
  const vcR2 = await voiceCalls();
  const dLoR = { melody: vcR1.melody - vcR0.melody, bass: vcR1.bass - vcR0.bass, perc: vcR1.perc - vcR0.perc };
  const dHiR = { melody: vcR2.melody - vcR1.melody, bass: vcR2.bass - vcR1.bass, perc: vcR2.perc - vcR1.perc };
  gate("R-AC11 성부 카운터 정확값 불변(저:melody==noteN·bass/perc==0 / 고:각==noteN·테마=freq/type 변조만·신규 성부 0)",
    dLoR.melody === noteN && dLoR.bass === 0 && dLoR.perc === 0 &&
      dHiR.melody === noteN && dHiR.bass === noteN && dHiR.perc === noteN,
    `lo=${JSON.stringify(dLoR)} hi=${JSON.stringify(dHiR)} noteN=${noteN}`);
  await retry();

  // ── R-AC12 풀 ③ 연출: 섹션 전이 → #scene DOM 렌더 소비(계산만=vestigial→RED) + per-note 톤 음색 ──
  await retry();
  await raf2();
  const sceneDomBefore = await sceneDom();             // intro 장면(전이 전)
  for (const n of secNotes[0]) await tap(n.lane, n.time);   // 섹션0 전 노트 온비트 → 섹션 전이(0→1)
  const sAfterTrans = await st();
  await raf2();                                         // render() 가 #scene 동기화(렌더 소비)
  const sceneDomAfter = await sceneDom();
  const scenes = await sectionScenes();
  const expScene1 = await sceneDomText(scenes[1]);
  gate("R-AC12 섹션 전이→#scene textContent===SECTION_SCENES[1] 렌더 소비(계산만=vestigial→RED)",
    sAfterTrans.section === 1 && sceneDomAfter === expScene1 && sceneDomBefore !== expScene1 &&
      typeof sceneDomAfter === "string" && sceneDomAfter.length > 0,
    `section=${sAfterTrans.section} before="${(sceneDomBefore || "").slice(0, 18)}" after="${(sceneDomAfter || "").slice(0, 24)}"`);
  const pvTimbre = await plannedVoices(0);             // 저콤보(melody only)·노트별 섹션 톤 음색
  const mel1 = pvTimbre.filter((e) => e.voice === "melody" && e.section === 1);
  const apHook = await aparams();                      // R2 __audioParams 훅 비회귀(scene→waveform 여전히 동작)
  gate("R-AC12 per-note 음색: 섹션1 melody 음색===toneToWaveform(__sectionTheme(1).tone)·__audioParams 훅 비회귀",
    mel1.length > 0 && mel1.every((e) => e.type === expW[1]) && typeof apHook.waveform === "string",
    `sec1 melody n=${mel1.length} type=${mel1[0] && mel1[0].type} exp=${expW[1]} apWave=${apHook.waveform}`);

  // ════════════════════════════════════════════════════════════════════════════════
  // ★R3 live-flow 직격 게이트 (LF-1 staging-live · LF-2 theme-live · LF-3 feel[SMOKE])
  //   헤드리스 훅 사각: 실 start→실클럭 진행→라이브 섹션 전이서 #scene 렌더 소비 + 미래 노트 테마 정합(테마 live hollow 0).
  // ════════════════════════════════════════════════════════════════════════════════
  await retry();                                       // 씬 인트로 리셋(직전 R-AC12 전이 잔존 제거 — 실 게임 result→retry→재연주 경로·scene=intro)
  await start();                                       // 실 제스처(songStart=A.now()·playbackActive·멜로디 스케줄·scene=intro 유지)
  await page.waitForTimeout(160);                      // 마스터 클럭 진행(라이브)
  await raf2();
  const sceneLiveBefore = await sceneDom();
  await page.evaluate(() => {                           // 섹션0 전 노트 라이브 judge(on-beat·콤보 상승=escalateLiveLayers)
    const ns = window.__songNotes().filter((n) => n.section === 0);
    for (const n of ns) window.__tapLane(n.lane, n.time);
  });
  const sLF = await st();
  await raf2();
  const sceneLiveAfter = await sceneDom();
  const expScene1b = await sceneDomText((await sectionScenes())[1]);
  gate("LF-1 staging-live: 라이브 섹션 전이(0→1)서 #scene 렌더 소비(헤드리스 훅 사각 직격·vestigial→RED)",
    sLF.section === 1 && sceneLiveAfter === expScene1b && sceneLiveBefore !== expScene1b,
    `section=${sLF.section} liveAfter="${(sceneLiveAfter || "").slice(0, 24)}"`);

  // LF-2 theme-live: 라이브 전이 후 미래(섹션≥1) 노트가 __sectionTheme transpose 등치 freq + 섹션 정합 음색(테마 live hollow 0)
  const pvLive = await plannedVoices(C.percCombo);     // 라이브 단일소스(plannedVoices) — 미래 노트 섹션 테마 bake
  const futureMel = pvLive.filter((e) => e.voice === "melody" && e.section >= 1);
  let lfOk = true, lfDetail = "";
  for (const e of futureMel) {
    const n = noteByTime[e.time];
    const expFreq = transposeJS(laneFreq[n.lane], themes[e.section].pitchShift);
    if (Math.abs(e.freq - expFreq) > 1e-9 || e.type !== expW[e.section]) {
      lfOk = false; lfDetail = `t=${e.time} sec=${e.section} freq=${e.freq}/${expFreq} type=${e.type}/${expW[e.section]}`; break;
    }
  }
  gate("LF-2 theme-live: 라이브 미래 노트 freq===__sectionTheme transpose 등치 + 섹션 정합 음색(테마 live hollow 0·강제주입 아님)",
    lfOk && futureMel.length > 0,
    `lfOk=${lfOk} futureMel=${futureMel.length} ${lfDetail}`);

  // LF-3 feel[SMOKE]: 라이브 전이 직후 연출 오버레이 가시 윈도우(sceneFlashUntil·실클럭·비게이트)
  const flashActive = await page.evaluate(() => (window.__audioTime() < window.__sceneFlashUntil() ? 1 : 0));
  smoke("LF-3 [SMOKE] 라이브 전이 직후 연출 오버레이 가시 윈도우(sceneFlashUntil·실클럭)",
    flashActive === 1, `flashActive=${flashActive}`);
  await retry();                                       // 상태 정리(이후 스크린샷 autoPlay 재무장)

  // ── 스크린샷(시각 보조·비게이트) ──
  await page.setViewportSize({ width: 480, height: 760 });
  await autoPlay(0.95);
  const shotDir = process.env.RHYTHM_SHOT_DIR || os.tmpdir(); // 기본 os.tmpdir()(repo 루트 오염 방지)·override = RHYTHM_SHOT_DIR
  await page.screenshot({ path: path.join(shotDir, "rhythm_check_result.png") }).catch(() => {});

  // ── 판정 집계 ──
  const gateFails = checks.filter((c) => c.kind === "GATE" && !c.ok);
  const smokeFails = checks.filter((c) => c.kind === "SMOKE" && !c.ok);
  const verdict = gateFails.length === 0 && fatals.length === 0;

  for (const c of checks) console.log(`[${c.kind}] ${c.ok ? "ok " : "XX "} ${c.name}${c.detail ? "  :: " + c.detail : ""}`);
  console.log(JSON.stringify({
    verdict, gates: checks.filter((c) => c.kind === "GATE").length, gateFails: gateFails.length,
    smokeFails: smokeFails.length, fatals: fatals.length,
    ac4: { hi: { ending: sHi.ending, acc: sHi.accuracy }, lo: { ending: sLo.ending, acc: sLo.accuracy },
      causalPair: { allHit: { e: sAll.ending, a: sAll.accuracy }, allMiss: { e: sNone.ending, a: sNone.accuracy } },
      manual: { ending: sMan.ending, acc: sMan.accuracy } },
    r2: { sectionSizes: secNotes.map((g) => g.length), golden: JSON.stringify(liveChart) === JSON.stringify(golden),
      activeLayers: { lo: layLo, hi: layHi }, voiceCalls: { lo: dLo, hi: dHi },
      finale: { "111": s111.finale, "110": s110.finale, "000": s000.finale } },
    r3: { motifCount: C.motifCount, themes, timbre: expW,
      pitch: { ok: pitchOk, transposed: transposedCount, crossPairs },
      staging: { sceneRendered: sceneDomAfter === expScene1, liveRendered: sceneLiveAfter === expScene1b }, lfThemeOk: lfOk },
    contract: C, briefPath, negativeMode: NEGATIVE,
  }, null, 2));

  if (fatals.length) console.error("fatals: " + JSON.stringify(fatals));
  if (!verdict) fail(`VERDICT false (gateFails=${gateFails.length} fatals=${fatals.length})`);
  console.log("PASS: rhythm-tale 전 AC 게이트 그린 — AC4 창발·falsifiable 인과쌍 포함 (브리프-구동·anti-hollow)");
} finally {
  await browser.close();
}
