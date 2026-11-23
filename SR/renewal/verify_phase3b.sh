#!/usr/bin/env bash
# AIGS Phase 3b 검증 — §19 구조화된 숙의 서브그래프(찬반·Premortem·ADR·소수의견·보정정지).
#  [P0]  1 종료성(서브그래프 라운드캡 + 게이트 one-shot 2-레벨·무한루프 0·★시스템 다운 차단)
#  [C3a] 2 단위(needs_deliberation 진리표·classify byte-identical·독립제안 격리·render_adr 합집합·ADR번호)
#        3 숙의 라운드트립 프레임/ADR 레벨 + 재평가 3분기(AUTONOMOUS/NOTIFY/ESCALATE·HIGH-1 양쪽 분기)
#        4 트리거 정밀(<τ+고위험만 진입·일상 가역 미진입)
#        5 ★부정변이(adr_is_substantive 게이트: 껍데기 ADR→FAIL·green-theater 차단)
#        6 보정정지 2경로(가역 commit&proceed + ADR · escalate interrupt + ADR·무음유실 0)
#  [C3b] 7 org 숙의 격상(P3b-5 — org_graph §19 미러: 진입/재진입 qwen 1콜/escalate goto=finalize/one-shot/MINOR-2 + org 회귀)
#  [공통] 8 빌드정합(import dangling 0) + 동결 sha256 · 9 orch 전체 회귀(동적 baseline·classify 불변)
# 원칙: τ_deliberate/trigger-rate/Judge분기/폭 N = OBSERVE-ONLY(완료게이트 아님·green 주장 금지).
#       green-theater 차단 = 상태플래그 아닌 ADR 파일/프레임 레벨 falsifiable. ✔=실제 실행만.
# 사용: SR/renewal/verify_phase3b.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$OPY" ] || { echo "[WARN] orch python 미발견 → python"; OPY="python"; }
PASS=0; FAIL=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

run_pytest() {  # <label> <pytest-args...>
  local label="$1"; shift
  if PYTHONPATH="$ROOT" timeout -k 10 300 "$OPY" -m pytest "$@" \
       -m "not slow" -p no:cacheprovider -p no:warnings >/tmp/p3b_$label.log 2>&1; then
    ok "$label PASS ($(grep -oE '[0-9]+ passed' /tmp/p3b_$label.log | tail -1))"
  else fail "$label FAIL"; tail -18 /tmp/p3b_$label.log; fi
}

# ── P0 종료성(★시스템 다운 이력·불변식·먼저 실행·2-레벨) ─────────────────────────
hdr "1) 종료성 P0 (서브그래프 CAP 종료 + 게이트 one-shot·무한루프 0)"
run_pytest g1 \
  "$ROOT/orch/tests/test_deliberation.py::test_terminates_even_when_never_agree" \
  "$ROOT/orch/tests/test_deliberation.py::test_no_consensus_shortcut_branch" \
  "$ROOT/orch/tests/test_deliberation_cycle.py::test_deliberation_one_shot_terminates_even_when_judge_always_low"

# ── C3a (cycle 숙의 — org 착수 전 GREEN 필수) ──────────────────────────────────
hdr "2) C3a 단위 (needs_deliberation 진리표·classify byte-identical·격리·render_adr·ADR번호)"
run_pytest g2 "$ROOT/orch/tests/test_deliberation.py" "$ROOT/orch/tests/test_authority.py"

hdr "3) C3a 숙의 라운드트립 프레임/ADR 레벨 + 재평가 3분기 (HIGH-1 양쪽 분기)"
run_pytest g3 "$ROOT/orch/tests/test_deliberation_app.py"

hdr "4) C3a 트리거 정밀 (cycle 재진입·일상 가역 미진입)"
run_pytest g4 "$ROOT/orch/tests/test_deliberation_cycle.py"

hdr "5) ★부정변이 (adr_is_substantive: 껍데기 ADR→FAIL·green-theater 차단)"
run_pytest g5 "$ROOT/orch/tests/test_deliberation.py::test_adr_substantive_gate_rejects_degenerate"

hdr "6) C3a 보정정지 2경로 (가역 commit&proceed+ADR · escalate interrupt+ADR)"
run_pytest g6 \
  "$ROOT/orch/tests/test_deliberation_app.py::test_reversible_deliberation_emits_bluf_and_writes_adr" \
  "$ROOT/orch/tests/test_deliberation_app.py::test_deliberation_escalate_emits_interrupt_and_writes_adr"

# ── C3b (org 숙의 격상 — P3b-5: org_graph 에 §19 숙의 미러) ──────────────────────
hdr "7) C3b org 숙의 (진입·재진입 qwen 1콜·escalate goto=finalize·one-shot·가역무차단·MINOR-2 + org 회귀)"
run_pytest g7 \
  "$ROOT/orch/tests/test_org_deliberation.py" \
  "$ROOT/orch/tests/test_org_authority.py" \
  "$ROOT/orch/tests/test_org_graph.py"

# ── 공통 ───────────────────────────────────────────────────────────────────────
hdr "8) 빌드정합(import dangling 0) + 동결 sha256"
if PYTHONPATH="$ROOT" "$OPY" -c "import orch.app, orch.graph, orch.org_graph, orch.authority, orch.digest, orch.deliberation, orch.state_store" >/tmp/p3b_g8.log 2>&1; then
  ok "import dangling 0 (deliberation 배선 정합)"
else fail "import 실패(dangling)"; tail -8 /tmp/p3b_g8.log; fi
if bash "$ROOT/scripts/verify_freeze.sh" >/tmp/p3b_g8f.log 2>&1; then ok "동결 무결(드리프트 0·orch 비동결)"
else fail "동결 드리프트"; tail -5 /tmp/p3b_g8f.log; fi

hdr "9) orch 전체 회귀 (동적 baseline·169 하드코딩 금지·classify 불변)"
run_pytest g9 "$ROOT/orch/tests/"

# ── 요약 ───────────────────────────────────────────────────────────────────────
echo; hdr "요약"
c '1;32' "PASS=$PASS"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then
  c '1;32' "✓ Phase 3b C3a 통과 (§19 숙의: 종료성 P0·ADR falsifiable·재진입·보정정지·τ는 OBSERVE-ONLY)"; exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
