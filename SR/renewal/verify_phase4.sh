#!/usr/bin/env bash
# AIGS Phase 4 검증 — §6.9 Evals & §21.4 연속 품질루프 (capture-then-batch·gemma-affinity).
#   1  골든셋(≥50·4타입·injection≥5·expects_tool·rag answer 메타·scorer 정합)
#   2  결정론 scorer bit-재현(고정 캡처 2회 byte-identical·LLM-0)
#   3  ★negative-mutation + N/A anti-abuse(실패캡처→FAIL·positive tool-anchor→FAIL not N/A)
#   4  judge 일치율 산식(stub 결정론·실 qwen 수치는 OBSERVE-ONLY)
#   5  ★회귀게이트 종료코드 우선순위(det=block exit≠0·judge=warn exit0·접합=block·subprocess)
#   6  ★runner 프로덕션 사이드이펙트 격리(정적 grep: app sink 미import/호출 + 런타임 부재)
#   7  gemma-affinity(judge 라이브 그래프/Redis 미접촉·정적 grep + 단위)
#   8  quality_loop 폐루프 e2e(fixture→handoff→교훈→Qdrant fake embedder→RPI 실회수)
#   9  ★§7 HARD(quality_loop Opus 자동호출 0: -p/Agent SDK/ANTHROPIC_API_KEY/anthropic 부재)
#  10  termination P0(quality_loop ROUND_CAP=3·자기개선 단발·무한루프 0)
#  11  자기개선 CLI(운영실패→라벨→골든 append·Phoenix 자동수집은 OBSERVE-ONLY)
#  12  빌드정합(import orch.evals.*·orch.quality_loop dangling 0) + 동결 sha256
#  13  orch 전체 회귀(동적 baseline·하드코딩 금지)
# 원칙: judge 실수치/실 bge 회수/Phoenix 자동수집 = OBSERVE-ONLY(완료게이트 아님·green 주장 금지).
#       green-theater 차단 = 상태플래그 아닌 파일/프레임/종료코드 레벨 falsifiable. ✔=실제 실행만.
#       OQ-2: 골든셋 ≥50 작성 AND Owner 검수 = P4a close-gate(Owner ACTION·아래 [1] 표기).
# 사용: SR/renewal/verify_phase4.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$OPY" ] || { echo "[WARN] orch python 미발견 → python"; OPY="python"; }
PASS=0; FAIL=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
obs(){  c '1;33' "[OBS ] $*"; }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

run_pytest() {  # <label> <pytest-args...>
  local label="$1"; shift
  if PYTHONPATH="$ROOT" timeout -k 10 300 "$OPY" -m pytest "$@" \
       -m "not slow" -p no:cacheprovider -p no:warnings >/tmp/p4_$label.log 2>&1; then
    ok "$label PASS ($(grep -oE '[0-9]+ passed' /tmp/p4_$label.log | tail -1))"
  else fail "$label FAIL"; tail -18 /tmp/p4_$label.log; fi
}

# ── P4a: 골든셋 + 결정론 scorer + 캡처 runner ──────────────────────────────────
hdr "1) 골든셋 (≥50·4타입·injection≥5·expects_tool·rag answer 메타·scorer 크로스파일 정합)"
run_pytest g1 "$ROOT/orch/tests/test_evals_golden.py"
obs "OQ-2: 골든셋 Owner 검수 = P4a close-gate(§6.9 '사람이 작성'·구현자 초안→Owner ACTION)"

hdr "2) 결정론 scorer bit-재현 (고정 캡처 2회 byte-identical·LLM-0·Pass@k fake embedder)"
run_pytest g2 "$ROOT/orch/tests/test_evals_scorers.py"

hdr "3) ★negative-mutation + N/A anti-abuse (실패캡처→FAIL·positive tool-anchor→FAIL not N/A)"
run_pytest g3 \
  "$ROOT/orch/tests/test_evals_scorers.py::test_scorers_fail_on_missing_signal" \
  "$ROOT/orch/tests/test_evals_scorers.py::test_tool_selection_positive_anchor_missing_expected_tool_is_FAIL"

# ── P4b: qwen judge + 회귀게이트 ────────────────────────────────────────────────
hdr "4) judge 일치율 산식 (stub 결정론·실 qwen 수치 OBSERVE-ONLY)"
run_pytest g4 "$ROOT/orch/tests/test_evals_judge.py"
obs "judge 실 qwen 일치율 수치 = OBSERVE-ONLY(slow·완료게이트 아님·결정론 주장 0)"

hdr "5) ★회귀게이트 종료코드 우선순위 (det=block exit≠0·judge=warn exit0·접합=block·실 subprocess)"
run_pytest g5 "$ROOT/orch/tests/test_evals_gate.py"

# ── ★정적 그린시어터 가드 (테스트 약화와 무관하게 불변식 강제) ──────────────────
hdr "6) ★runner 프로덕션 사이드이펙트 격리 (정적 grep + 런타임 단위)"
if grep -nE "from orch\.app import|import orch\.app|OutboundEmitter\(|blackboard_sink\(|_adr_sink\(|create_app\(" \
     "$ROOT/orch/evals/runner.py" >/tmp/p4_g6grep.log 2>&1; then
  fail "runner.py 에 프로덕션 sink import/호출 존재(격리 위반)"; cat /tmp/p4_g6grep.log
else ok "runner 격리 정적가드 (app sink import/호출 0·라이브 Discord/Redis/OTLP 오염 0)"; fi
run_pytest g6 "$ROOT/orch/tests/test_evals_runner.py"

hdr "7) gemma-affinity (judge 라이브 그래프/Redis/app 미접촉·정적 grep)"
# 정밀 매칭(import 문 + 호출 사이트만·docstring/주석 산문 제외·gate 6 동형).
if grep -nE "^[[:space:]]*(from|import)[[:space:]]+.*(orch\.app|redis)|build_cycle_graph\(|build_org_graph\(|create_app\(" \
     "$ROOT/orch/evals/judge.py" >/tmp/p4_g7grep.log 2>&1; then
  fail "judge.py 가 라이브 그래프/Redis/app 접촉(gemma-affinity 위반)"; cat /tmp/p4_g7grep.log
else ok "judge 배치 격리 (deliberation qwen seam 만·라이브 미접촉·per-anchor 스왑 0)"; fi

# ── P4c: quality_loop + 자기개선 ────────────────────────────────────────────────
hdr "8) quality_loop 폐루프 e2e (fixture→handoff→교훈→Qdrant fake embedder→RPI 실회수)"
run_pytest g8 "$ROOT/orch/tests/test_quality_loop.py"
obs "실 bge 회수 = slow/OBSERVE 별도(bge DEGRADED·배관은 fake embedder 결정론 검증)"

hdr "9) ★§7 HARD (quality_loop Opus 자동호출 0: -p/Agent SDK/ANTHROPIC_API_KEY/anthropic 부재)"
if grep -nE "anthropic|ANTHROPIC_API_KEY|claude_agent|Agent SDK|subprocess.*claude|\" -p \"|' -p '" \
     "$ROOT/orch/quality_loop.py" >/tmp/p4_g9grep.log 2>&1; then
  fail "quality_loop.py 에 Opus 자동호출 흔적(§7 HARD 위반)"; cat /tmp/p4_g9grep.log
else ok "§7 HARD (Opus 자동호출 0·정밀검수는 외부 사람 세션·코드는 배관만)"; fi

hdr "10) termination P0 (quality_loop ROUND_CAP=3·자기개선 단발·무한루프 0)"
if grep -qE "ROUND_CAP *= *3|_CAP *= *3|cap *= *3" "$ROOT/orch/quality_loop.py"; then
  ok "라운드캡 3 상수 (재검수 상한·termination P0·무한루프 구조적 0)"
else fail "quality_loop 라운드캡 3 미발견(termination 위반)"; fi

hdr "11) 자기개선 CLI (운영실패→라벨→골든 append·Phoenix 자동수집은 OBSERVE-ONLY)"
run_pytest g11 "$ROOT/orch/tests/test_evals_selfimprove.py"
obs "Phoenix 실시간 자동수집(trace→라벨) = OBSERVE-ONLY('code 100% / runtime pending: Phoenix query API'·BLOCKED≠pass·§6)"

# ── 공통 ───────────────────────────────────────────────────────────────────────
hdr "12) 빌드정합(import dangling 0) + 동결 sha256"
if PYTHONPATH="$ROOT" "$OPY" -c "import orch.evals.runner, orch.evals.scorers, orch.evals.judge, orch.evals.gate, orch.evals.selfimprove, orch.quality_loop" >/tmp/p4_g12.log 2>&1; then
  ok "import dangling 0 (orch.evals.* + quality_loop 배선 정합)"
else fail "import 실패(dangling)"; tail -8 /tmp/p4_g12.log; fi
if bash "$ROOT/scripts/verify_freeze.sh" >/tmp/p4_g12f.log 2>&1; then ok "동결 무결(드리프트 0·orch 비동결)"
else fail "동결 드리프트"; tail -5 /tmp/p4_g12f.log; fi

hdr "13) orch 전체 회귀 (동적 baseline·하드코딩 금지·P4 신규 포함)"
run_pytest g13 "$ROOT/orch/tests/"

# ── 요약 ───────────────────────────────────────────────────────────────────────
echo; hdr "요약"
c '1;32' "PASS=$PASS"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then
  c '1;32' "✓ Phase 4 통과 (§6.9 Evals·§21.4 품질루프: capture-then-batch·gemma-affinity·negative-mutation·종료코드 우선순위·§7 HARD·termination P0·judge수치/실bge/Phoenix는 OBSERVE-ONLY)"; exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
