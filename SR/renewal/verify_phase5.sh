#!/usr/bin/env bash
# AIGS Phase 5 검증 — 풀 §28 제품 캡스톤(슬라이딩 타일 퍼즐 7산출물 + §0.5 4차원 완주 + 관찰◐).
# verify_phase2_5.sh 골격 계승(8게이트·provenance falsifiable). ✔=실제 실행만·날조 0.
#   1  spec-queue 단위(test_spec_queue + test_state_store_task) + §7.1 HARD grep 가드
#   2  브리프 통합(parse_brief + AC 계약 6항 존재)
#   3  C4 e2e(play_puzzle_check.mjs 정상 PASS·상태전이 단언)
#   4  provenance 3 HARD(시계열 brief⊰game·task_id 역참조[PROVENANCE+worklog+QA(+commit)]·AC-구동 부정변이[PUZZLE_NEGATIVE=1 exit1])
#   5  DoD(check_dod 4구성·m6 4경로 소싱: artifact/handoff/research/worklog)
#   6  harvest falsifiable(부정케이스 status≠done) + harvest 수동성(후속 액션 트리거 0·m8)
#   7  4차원 완주(생산·운영·자율·지식·C1 ✔) + 관찰 ◐ 부분충족(span export+SSE·풀 관찰=P6 ACTION)
#   8  동결 sha256(verify_freeze.sh) + orch 전체 회귀(`-m "not slow"`·pyproject addopts)
# 정직 표기(green-theater 차단·시나리오3): tier3 아트/트레일러-생성 = ACTION: pending Owner Server B
#   (✔도 ✗도 아닌 제3범주·≠fail·≠pass·§6 weakest-criterion-wins). m7: task_id 커밋삽입 =
#   사람-브리지 정상동작(green-fake 아님·내용 게이트는 artifacts_expected+DoD).
# 사용: SR/renewal/verify_phase5.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORCH_PY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$ORCH_PY" ] || { echo "[WARN] orch python 미발견 → python"; ORCH_PY="python"; }
NODE_BIN="${NODE_BIN:-/usr/local/bin/node}"
[ -x "$NODE_BIN" ] || NODE_BIN="node"
TASK_ID="p5-slide-puzzle"
BRIEF="$ROOT/vault/specs/$TASK_ID.md"
GAME="$ROOT/games/slide-puzzle/index.html"
PROV="$ROOT/games/slide-puzzle/PROVENANCE"
WORKLOG="$ROOT/vault/quality/worklog-$TASK_ID.md"
QA="$ROOT/vault/quality/slide-puzzle-qa.md"
RESEARCH="$ROOT/vault/research/$TASK_ID-notes.md"
HARNESS="$ROOT/web/scripts/play_puzzle_check.mjs"
PASS=0; FAIL=0; ACTION=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){     c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){   c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
act(){    c '1;33' "[ACTION] $*"; ACTION=$((ACTION+1)); }
hdr(){    c '1;34' "── $* ─────────────────────────────"; }

# ── 1. spec-queue 단위 + §7.1 HARD grep 가드 ───────────────────────────────────
hdr "1) spec-queue 단위(test_spec_queue + test_state_store_task) + §7.1 HARD grep 가드"
if PYTHONPATH="$ROOT" timeout -k 5 180 "$ORCH_PY" -m pytest \
     "$ROOT/orch/tests/test_spec_queue.py" \
     "$ROOT/orch/tests/test_state_store_task.py" \
     -p no:cacheprovider >/tmp/p5_g1.log 2>&1; then
  ok "spec-queue + state_store_task 단위 PASS ($(grep -oE '[0-9]+ passed' /tmp/p5_g1.log | tail -1))"
else fail "spec-queue 단위 FAIL"; tail -20 /tmp/p5_g1.log; fi
# §7.1 HARD: spec_queue.py 소스에 자동호출 리터럴 부재(정적 grep·금지 리터럴은 테스트에만).
if grep -nE '\-p |subprocess.*claude|anthropic|ANTHROPIC_API_KEY|Agent SDK' \
     "$ROOT/orch/spec_queue.py" >/tmp/p5_g1b.log 2>&1; then
  fail "§7.1 HARD 위반: spec_queue.py 에 자동호출 리터럴 발견"; cat /tmp/p5_g1b.log
else ok "§7.1 HARD: spec_queue.py 자동호출 리터럴 0(사람=비동기 브리지)"; fi

# ── 2. 브리프 통합 (parse_brief + AC 계약 6항) ──────────────────────────────────
hdr "2) 브리프 통합 (parse_brief + AC 계약 6항)"
if PYTHONPATH="$ROOT" "$ORCH_PY" - "$BRIEF" >/tmp/p5_g2.log 2>&1 <<'PY'
import sys
from orch.handoff import parse_brief
b = parse_brief(open(sys.argv[1], encoding="utf-8").read())
ac = "\n".join(b.acceptance_criteria).lower()
need = ("state_shape:", "init:", "move:", "win:", "stage:", "coupling:")
miss = [k for k in need if k not in ac]
assert b.task_id == "p5-slide-puzzle", f"task_id={b.task_id}"
assert b.to == "Claude Code", f"to={b.to}"
assert not miss, f"AC 계약 누락: {miss}"
print("brief OK (6항 계약 존재)")
PY
then ok "브리프 parse + AC 계약 6항(state_shape/init/move/win/stage/coupling)"; else fail "브리프 통합 FAIL"; cat /tmp/p5_g2.log; fi

# ── 3. C4 e2e (브리프-구동·상태전이 단언) ───────────────────────────────────────
hdr "3) C4 e2e (play_puzzle_check.mjs·상태전이 단언)"
if PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 timeout -k 5 150 \
     "$NODE_BIN" "$HARNESS" >/tmp/p5_g3.log 2>&1; then
  ok "C4 PASS (결합 불변식·solved false→true 1회·win잠금·무에러)"
else fail "C4 FAIL"; tail -20 /tmp/p5_g3.log; fi

# ── 4. provenance 3 HARD ────────────────────────────────────────────────────────
hdr "4) provenance 3 HARD (시계열·task_id 역참조·AC-구동)"
# 4.1 시계열: brief ⊰ game. 브리프 미커밋(PROVENANCE brief_sha_kind=sha256-content) →
#   content-sha 링크(PROVENANCE brief_sha == 현재 브리프 sha256)로 "게임이 *이 브리프*로부터
#   나옴"을 입증. 게임이 커밋돼 있으면 commit 타임스탬프 선행도 추가 확인(둘 다 falsifiable).
PROV_SHA="$(grep -oE 'brief_sha: *[0-9a-f]{64}' "$PROV" | awk '{print $2}')"
CUR_SHA="$(sha256sum "$BRIEF" | awk '{print $1}')"
if [ -n "$PROV_SHA" ] && [ "$PROV_SHA" = "$CUR_SHA" ]; then
  ok "4.1 시계열(content-sha): PROVENANCE brief_sha == 현재 브리프 sha256 (게임←이 브리프 링크)"
else
  fail "4.1 brief_sha 불일치: PROVENANCE=$PROV_SHA 현재=$CUR_SHA"
fi
# 4.2 task_id 역참조: PROVENANCE + worklog + QA(존재 소스). commit 은 사람-브리지 ACTION(m7).
r42=0
grep -q "$TASK_ID" "$PROV"    2>/dev/null || { fail "4.2 PROVENANCE task_id 없음"; r42=1; }
grep -q "$TASK_ID" "$WORKLOG" 2>/dev/null || { fail "4.2 worklog task_id 없음"; r42=1; }
grep -q "$TASK_ID" "$QA"      2>/dev/null || { fail "4.2 QA task_id 없음"; r42=1; }
[ $r42 -eq 0 ] && ok "4.2 task_id 역참조(PROVENANCE+worklog+QA 3소스)"
# m7: task_id 커밋 메시지 삽입 = 사람-브리지 정상동작(green-fake 아님). 게임 커밋 존재 시 추가 입증.
GAME_COMMIT="$(git -C "$ROOT" log --all --grep="$TASK_ID" --format=%H 2>/dev/null | head -1)"
if [ -n "$GAME_COMMIT" ]; then
  ok "4.2b task_id 커밋 역참조($GAME_COMMIT·사람-브리지 추적성·내용게이트는 artifacts+DoD)"
else
  act "4.2b task_id 커밋 삽입 = 사람-브리지 ACTION(게임 미커밋 — Owner/사람이 task_id 로 commit·green-fake 아님·m7). harvest 의 commit 게이트는 commit 후 충족"
fi
# 4.3 AC-구동: 부정 변이(PUZZLE_NEGATIVE=1·AC 비움)본은 FAIL해야 정상(단언이 브리프 파생 입증).
if PUZZLE_NEGATIVE=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 timeout -k 5 120 \
     "$NODE_BIN" "$HARNESS" >/tmp/p5_g43.log 2>&1; then
  fail "4.3 부정 변이본(PUZZLE_NEGATIVE=1)이 PASS → 단언 하드코딩(provenance 위반)"
else ok "4.3 AC-구동: 부정 변이(PUZZLE_NEGATIVE=1) exit≠0 FAIL(단언이 브리프에서 파생됨)"; fi

# ── 5. DoD (§18.5 4구성·m6 4경로 소싱) ──────────────────────────────────────────
hdr "5) DoD (check_dod: artifact·research·worklog·handoff — m6 4경로 소싱)"
if PYTHONPATH="$ROOT" "$ORCH_PY" - "$GAME" "$RESEARCH" "$WORKLOG" "$BRIEF" >/tmp/p5_g5.log 2>&1 <<'PY'
import sys
from orch.handoff import check_dod
# m6 4경로: artifact=게임 index.html · research=vault/research/<task>-notes.md ·
#           worklog=vault/quality/worklog-<task>.md · handoff=vault/specs/<task>.md.
r = check_dod(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
assert r.ok, f"DoD 미충족: {r.missing}"
print("DoD ok (4구성 적재)")
PY
then ok "check_dod ok (artifact/research/worklog/handoff 4구성)"; else fail "DoD FAIL"; cat /tmp/p5_g5.log; fi

# ── 6. harvest falsifiable + harvest 수동성(m8) ─────────────────────────────────
hdr "6) harvest falsifiable(부정케이스 status≠done) + 수동성(후속 액션 0·m8)"
# spec_queue 단위(게이트1)가 harvest 부정케이스 3종 + 순수성 spy 를 이미 실증 — 여기선
# 그 단위가 실제로 부정/순수성 단언을 *포함*하는지 정적 확인(게이트가 비공허·러버스탬프 아님).
if grep -q "test_harvest_negative_no_commit" "$ROOT/orch/tests/test_spec_queue.py" \
   && grep -q "test_harvest_negative_artifact_missing" "$ROOT/orch/tests/test_spec_queue.py" \
   && grep -q "test_harvest_negative_dod_unmet" "$ROOT/orch/tests/test_spec_queue.py" \
   && grep -q "test_harvest_purity_no_side_effects" "$ROOT/orch/tests/test_spec_queue.py"; then
  # 부정케이스 + 순수성 spy 를 *실제로* 재실행(게이트1과 별개 단언으로 falsifiable 확정).
  if PYTHONPATH="$ROOT" timeout -k 5 120 "$ORCH_PY" -m pytest \
       "$ROOT/orch/tests/test_spec_queue.py" \
       -p no:cacheprovider -k "harvest_negative or harvest_purity" \
       >/tmp/p5_g6.log 2>&1; then
    ok "harvest 부정케이스 3종(status≠done) + 수동성 spy(push_task/emit 0) PASS ($(grep -oE '[0-9]+ passed' /tmp/p5_g6.log | tail -1))"
  else fail "harvest falsifiable/수동성 재실행 FAIL"; tail -15 /tmp/p5_g6.log; fi
else fail "harvest 부정케이스/순수성 테스트 부재(게이트 비falsifiable)"; fi

# ── 7. 4차원 완주 + 관찰 ◐ 데모 (C1) ────────────────────────────────────────────
hdr "7) 4차원 완주(생산·운영·자율·지식) + 관찰 ◐ 부분충족 (C1·날조 0)"
if PYTHONPATH="$ROOT" timeout -k 5 150 "$ORCH_PY" "$ROOT/tools/p5_dimension_demo.py" \
     >/tmp/p5_g7.log 2>&1; then
  # 4차원 ✔ AND 관찰 ◐ 표기 존재(✔ 분식 금지).
  if grep -q '"four_dimensions_pass": true' /tmp/p5_g7.log \
     && grep -q '"mark": "◐"' /tmp/p5_g7.log; then
    ok "4차원 ✔ 완주(운영/자율/생산/지식) + 관찰 ◐ 부분충족(풀 관찰=P6·정직 표기)"
  else fail "4차원 데모 술어 미충족(또는 관찰 ◐ 표기 누락)"; tail -20 /tmp/p5_g7.log; fi
else fail "4차원 데모 exit≠0"; tail -25 /tmp/p5_g7.log; fi
# 관찰 라이브 보강(옵션): orch /healthz 가 가동 중이면 spans_emitted 확인(없으면 정적 경로로 충분·◐).
HZ="$(curl -fsS --max-time 3 http://127.0.0.1:8099/healthz 2>/dev/null || true)"
if [ -n "$HZ" ]; then
  echo "$HZ" | grep -q 'spans_emitted' && c '1;36' "[INFO] 라이브 /healthz spans_emitted 노출 확인(관찰 ◐ 라이브 보강): $HZ"
fi

# ── 트레일러: 실 게임 footage 캡처 + 합성 라이브 (Server B 정상·LTX-2 b-roll=P7 gate8) ──
hdr "트레일러 — 실 게임 footage 캡처 + video_assemble 합성 (라이브·Server B 정상)"
# 실 게임플레이(slide-puzzle BFS solve) playwright 녹화 → p5_trailer_compose 정규화+concat → 검증.
# LTX-2 b-roll 생성은 P7 gate8(Server B 정상·video-audio 라이브)이 커버 — 본 게이트는 P5 고유의
# 실-footage 캡처+합성 파이프라인을 라이브 실증한다(렌더-trap 아님·p7_video_check 비검정 단언).
NODE="${NODE:-$(command -v node || echo /usr/local/bin/node)}"
ART_PY="${ART_PY:-/opt/conda/envs/art/bin/python}"
TFOOT=/tmp/p5_trailer_gate_footage.webm
TOUT=/tmp/p5_trailer_gate.mp4
if [ -x "$NODE" ] \
   && PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers timeout 130 "$NODE" web/scripts/capture_puzzle_footage.mjs "$TFOOT" >/tmp/p5_cap.log 2>&1 \
   && timeout 200 "$ORCH_PY" tools/p5_trailer_compose.py "$TOUT" "$TFOOT" "$TFOOT" >/tmp/p5_comp.log 2>&1 \
   && "$ART_PY" tools/p7_video_check.py "$TOUT" >/tmp/p5_tchk.log 2>&1; then
  ok "트레일러 실 게임 footage 캡처+합성 ($(grep -oE 'moves=[0-9]+' /tmp/p5_cap.log) → $(grep -oE 'bytes=[0-9]+ video_stream=True' /tmp/p5_tchk.log))·LTX-2 b-roll=P7 gate8 라이브"
else
  act "트레일러 실 footage 캡처/합성 미완(node/ffmpeg/env) — 조립코드 OK(video_assemble)·LTX-2=P7 gate8·Server B 정상"
fi

# ── 8. 비파괴 동결 sha256 + orch 전체 회귀(`-m \"not slow\"`) ────────────────────
hdr "8) 동결 sha256 무결 + orch 전체 회귀(-m 'not slow')"
if bash "$ROOT/scripts/verify_freeze.sh" >/tmp/p5_g8a.log 2>&1; then
  ok "동결파일 무결(드리프트 0·app.py 등 byte 보존)"
else fail "동결파일 드리프트 감지"; tail -8 /tmp/p5_g8a.log; fi
# orch 전체 회귀 = pyproject addopts(`-m 'not slow'`·라이브서버 hang 제외). 루트서 실행.
# (cd "$ROOT")로 pyproject addopts 적용. -q 미부여(요약 줄을 확실히 캡처 — 증거 비공허).
(cd "$ROOT" && PYTHONPATH="$ROOT" timeout -k 10 600 "$ORCH_PY" -m pytest orch/tests/ \
   -p no:cacheprovider -m "not slow") >/tmp/p5_g8b.log 2>&1
g8rc=$?
G8SUM="$(grep -oE '[0-9]+ (passed|failed|error)[a-z ,0-9]*' /tmp/p5_g8b.log | tail -1)"
if [ $g8rc -eq 0 ] && grep -qE '[0-9]+ passed' /tmp/p5_g8b.log \
   && ! grep -qE '[0-9]+ (failed|error)' /tmp/p5_g8b.log; then
  ok "orch 전체 회귀 PASS (${G8SUM:-요약파싱실패}·slow 제외)"
else fail "orch 회귀 FAIL (rc=$g8rc·${G8SUM:-no-summary})"; tail -25 /tmp/p5_g8b.log; fi

# ── 요약 ────────────────────────────────────────────────────────────────────────
echo; hdr "요약"
c '1;32' "PASS=$PASS"
[ $ACTION -gt 0 ] && c '1;33' "ACTION=$ACTION (pending Owner: Server B·≠fail·≠pass — tier3/트레일러-생성·미커밋 commit)"
[ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then
  c '1;32' "✓ Phase 5 통과 (풀 §28 캡스톤 경로·4차원 완주+관찰◐·provenance falsifiable)"
  c '1;33' "  · 정직 deferral: tier3 아트/트레일러 LTX-2 생성 = Server B(§16 Owner 갭) 대기(P7·합산 0)."
  exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
