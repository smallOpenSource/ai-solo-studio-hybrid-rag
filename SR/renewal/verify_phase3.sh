#!/usr/bin/env bash
# AIGS Phase 3 검증 — §20 자율·의사결정 권한 (OD-E=ii 직접 재작성·C2a cycle → C2b org).
#  [C2a] 1 분류기 단위(classify 진리표·assess·RPI/보수 demote·brief·digest) · 2 가역 무차단 +
#        ★프레임 레벨 emit(run_id 매칭·non-empty BLUF·Critic MAJOR#2) · 3 비가역 escalate interrupt ·
#        4 2-tier 동시발화(green-theater 차단) · 5 digest 누적
#  [C2b] 6 org τ 격상(escalate 키워드 구동·qwen override·goto=finalize 가드)
#  [공통] 7 빌드정합(import dangling 0) + 동결 sha256 · 8 orch 전체 회귀(not slow)
# 원칙: τ gradient/캘리브레이션은 OBSERVE-ONLY(완료게이트 아님·green 주장 금지). 라이브 Discord 왕복은
#       §1.6 acceptance(slow·별도)·본 게이트는 결정론(stub·프레임 레벨). ✔=실제 실행만.
# 사용: SR/renewal/verify_phase3.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$OPY" ] || { echo "[WARN] orch python 미발견 → python"; OPY="python"; }
PASS=0; FAIL=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

run_pytest() {  # <label> <files...>
  local label="$1"; shift
  if PYTHONPATH="$ROOT" timeout -k 10 300 "$OPY" -m pytest "$@" \
       -m "not slow" -p no:cacheprovider -p no:warnings >/tmp/p3_$label.log 2>&1; then
    ok "$label PASS ($(grep -oE '[0-9]+ passed' /tmp/p3_$label.log | tail -1))"
  else fail "$label FAIL"; tail -15 /tmp/p3_$label.log; fi
}

# ── C2a (cycle authority — org 착수 전 GREEN) ──────────────────────────────────
hdr "1) C2a 분류기 단위 (§20.5 진리표·assess·RPI/보수 demote·brief·digest)"
run_pytest g1 "$ROOT/orch/tests/test_authority.py" "$ROOT/orch/tests/test_digest.py"

hdr "2-4) C2a 가역 무차단(프레임 emit) + 비가역 escalate + 2-tier 발화"
run_pytest g2 "$ROOT/orch/tests/test_authority_app.py" "$ROOT/orch/tests/test_graph_unit.py"

hdr "5) C2a digest 라이브 배선 (🟢 autonomous + 🔴 escalate dead-path 폐쇄·Critic MAJOR#2)"
run_pytest g5 "$ROOT/orch/tests/test_authority_app.py::test_digest_wired_in_live_run_paths"

# ── C2b (org τ escalation — C2a GREEN 후) ─────────────────────────────────────
hdr "6) C2b org τ 격상 (escalate 키워드 구동·qwen override·goto=finalize 가드)"
run_pytest g6 "$ROOT/orch/tests/test_org_authority.py" "$ROOT/orch/tests/test_org_graph.py"

# ── 공통 ───────────────────────────────────────────────────────────────────────
hdr "7) 빌드정합(import dangling 0) + 동결 sha256"
if PYTHONPATH="$ROOT" "$OPY" -c "import orch.app, orch.graph, orch.org_graph, orch.authority, orch.digest" >/tmp/p3_g7.log 2>&1; then
  ok "import dangling 0 (authority_gate 재배선 정합)"
else fail "import 실패(dangling)"; tail -8 /tmp/p3_g7.log; fi
if bash "$ROOT/scripts/verify_freeze.sh" >/tmp/p3_g7f.log 2>&1; then ok "동결 무결(드리프트 0·orch 비동결)"
else fail "동결 드리프트"; tail -5 /tmp/p3_g7f.log; fi

hdr "8) orch 전체 회귀 (not slow)"
run_pytest g8 "$ROOT/orch/tests/"

# ── 요약 ───────────────────────────────────────────────────────────────────────
echo; hdr "요약"
c '1;32' "PASS=$PASS"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then
  c '1;32' "✓ Phase 3 통과 (§20 자율: cycle authority_gate + org τ 격상·τ는 OBSERVE-ONLY)"; exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
