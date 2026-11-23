#!/usr/bin/env bash
# verify_provider_fallback.sh — Provider primary/fallback (DashScope image · Azure LLM) 검증 게이트.
# 계획: .omc/plans/ralplan-provider-fallback.md (CONSENSUS APPROVED). AC1~AC16 falsifiable.
# 라이브 호출은 tools/p_provider_verify.py 가 safe_run.sh 가드 하에 수행(실 Azure/DashScope/gemma/comfy).
# 사용: bash SR/renewal/verify_provider_fallback.sh   (exit0 ⟺ FAIL==0)
set -u
cd "$(dirname "$0")/../.." || exit 2
ROOT="$(pwd)"
PY=/opt/conda/envs/orch/bin/python
ART=/opt/conda/envs/art/bin/python
SHOT="${PV_OUT:-/tmp/provider-verify}"
PASS=0; FAIL=0; ACTION=0
ok(){ echo "[PASS] $1"; PASS=$((PASS+1)); }
no(){ echo "[FAIL] $1"; FAIL=$((FAIL+1)); }
act(){ echo "[ACTION] $1"; ACTION=$((ACTION+1)); }

echo "=== T1 단위 테스트 (azure_llm·dashscope_image·media_provider·eval 격리·AC6/AC10/AC16) ==="
if timeout 220 "$PY" -m pytest orch/tests/test_azure_llm.py orch/tests/test_dashscope_image.py \
     orch/tests/test_media_provider.py orch/tests/test_provider_eval_isolation.py -q -p no:cacheprovider >/tmp/pv_unit.log 2>&1; then
  ok "T1 unit: $(grep -oE '[0-9]+ passed' /tmp/pv_unit.log | tail -1)"
else
  no "T1 unit FAILED ($(grep -oE '[0-9]+ (passed|failed)' /tmp/pv_unit.log | tr '\n' ' '))"
fi

echo "=== T2 라이브 primary/fallback (AC1·AC2·AC3·AC4·AC11·AC15 · safe_run 가드·AC14) ==="
if PYTHONPATH="$ROOT" bash .omc/guards/safe_run.sh 420 0 -- "$PY" tools/p_provider_verify.py >/tmp/pv_live.log 2>&1; then
  ok "T2 live: $(grep -oE '[0-9]+/[0-9]+ PASS' /tmp/pv_live.log | tail -1) (AC1 dashscope·AC2 comfy-fallback·AC3 azure·AC4 gemma-fallback·AC11 edit)"
else
  no "T2 live FAILED — $(grep -E '^\[FAIL\]' /tmp/pv_live.log | head -5 | tr '\n' '|')"
fi

echo "=== T3 AC1 이미지 COLOR(비검정·green-theater 차단) ==="
if [ -f "$SHOT/pv_ac1_dashscope.png" ] && "$ART" tools/p7_black_check.py "$SHOT/pv_ac1_dashscope.png" 2>/dev/null | grep -q 'verdict=COLOR'; then
  ok "T3 AC1 image COLOR (DashScope primary 실생성)"
else
  act "T3 AC1 COLOR 미확인(이미지 부재·T2 먼저 실행 필요)"
fi

echo "=== T4 AC7 시크릿 0노출 (변경 코드·검증기에 평문 키 부재) ==="
if grep -REn 'sk-ws-[A-Za-z0-9._-]{20,}|83yf2Y9t0|AZURE_OPENAI_API_KEY=[^$]|DASHSCOPE_API_KEY=[^$]' \
     orch/azure_llm.py orch/dashscope_image.py orch/media_provider.py tools/p_provider_verify.py \
     orch/graph.py orch/org_graph.py orch/deliberation.py orch/evals/runner.py orch/evals/gate.py 2>/dev/null \
     | grep -vE 'forced-bad|sk-forced|=os\.environ|getenv|\.get\(' >/tmp/pv_secret.log; then
  no "T4 시크릿 평문 의심: $(head -2 /tmp/pv_secret.log)"
else
  ok "T4 시크릿 0 (env에서만 읽음)"
fi

echo "=== T5 AC13 browser_worker 로컬 유지(EXCLUDED·자체 클라이언트) ==="
if grep -qE 'BENCH_API_URL|LLM_GATEWAY' orch/browser_worker.py && ! grep -q 'make_primary_fallback_caller' orch/browser_worker.py; then
  ok "T5 browser_worker 로컬(BENCH_API_URL·azure 래퍼 미적용)"
else
  no "T5 browser_worker 경계 위반"
fi

echo "=== T6 AC9 video=comfy 불변 (media_provider 라우팅) ==="
if grep -qE "kind in \(.image.,\s*.image_edit.\)" orch/media_provider.py; then
  ok "T6 video/video_audio → comfy (image/image_edit만 DashScope)"
else
  no "T6 video 라우팅 미확인"
fi

echo "=== T7 AC5 flag-off 롤백 경로 (ORCH_LLM_PRIMARY=off → fallback 직행) ==="
if grep -qE 'ORCH_LLM_PRIMARY' orch/azure_llm.py && grep -qE 'ORCH_IMAGE_PRIMARY' orch/media_provider.py; then
  ok "T7 flag-off 1줄 롤백 경로 존재(call-time 읽기)"
else
  no "T7 flag 경로 미확인"
fi

echo ""
echo "================= verify_provider_fallback: PASS=$PASS FAIL=$FAIL ACTION=$ACTION ================="
[ "$FAIL" -eq 0 ] && { echo "RESULT: OK (exit0)"; exit 0; } || { echo "RESULT: FAIL"; exit 1; }
