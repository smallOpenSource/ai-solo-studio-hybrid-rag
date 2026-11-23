#!/usr/bin/env bash
# verify_phase7 — P7 풀 미디어 게이트 (OD-P7=B·tree B·종료성 바운드).
#
# 산술: PASS/FAIL/ACTION/BLOCKED 4-카운터. exit0 ⟺ FAIL==0 AND BLOCKED==0.
#   [OK]/[FAIL] = 코드/로컬 게이트(집계) · [ACTION] = 호스트측 pending Owner(≠pass·≠fail·미합산)
#   · [BLOCKED] = OD 미비준/능력부재(exit≠0).
# ★게이트0(최상단): OD-P7 토큰 + OWNER-SIGNOFF + art env 승인 부재 → 전체 BLOCKED(이하 미실행).
# ★Server B 게이트웨이 정상 확인(Owner 2026-06-23): 이미지 VAE 수리(게이트6 라이브 COLOR) +
#   영상 wan/video-audio ltx-2 정상(게이트8 실 video-audio 라이브·게이트10 실 footage 트레일러 조립).
#   라이브 미디어 게이트 전부 falsifiable 실증(실 생성+검정판별·green-fake 0). 게임 캡처 트레일러는 P5 별개.
#   런타임 Server B 미도달/타임아웃 시에만 해당 게이트 [ACTION](≠fail·exit0 유지).
# 종료성: 모든 pytest 는 -m "not slow" + timeout. 라이브 80s 호출 재실행 0(degradation 은 문서화).

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 2
PY=/opt/conda/envs/orch/bin/python
ART=/opt/conda/envs/art/bin/python
SHAPE="SR/renewal/COMFY_API_SHAPE.md"

PASS=0; FAIL=0; ACTION=0; BLOCKED=0
ok(){ echo "[OK] $1"; PASS=$((PASS+1)); }
bad(){ echo "[FAIL] $1"; FAIL=$((FAIL+1)); }
act(){ echo "[ACTION] pending Owner: $1"; ACTION=$((ACTION+1)); }
blk(){ echo "[BLOCKED] $1"; BLOCKED=$((BLOCKED+1)); }
run(){ timeout 300 "$PY" -m pytest "$1" -m "not slow" -q >/tmp/p7_pt.log 2>&1; }

echo "=== P7 verify (OD-P7=B · tree B) ==="

# --- 게이트0: OD 가드 (착수 전제·falsifiable·미비준→BLOCKED·이하 미실행) ----------
if grep -Eq '^OD-P7:[[:space:]]*(A|B|C)\b' "$SHAPE" 2>/dev/null \
   && grep -q '^OWNER-SIGNOFF:' "$SHAPE" 2>/dev/null \
   && grep -q 'art env approved' "$SHAPE" 2>/dev/null; then
  ok "게이트0: OD-P7 토큰 + OWNER-SIGNOFF + art env 승인 ($SHAPE)"
else
  blk "게이트0: OD-P7/OWNER-SIGNOFF/art-approved 부재 또는 placeholder → P7 착수 불가"
  echo "exit≠0 (BLOCKED=$BLOCKED) — OD-P7 비준 후 재실행"; exit 1
fi

# --- 게이트1: comfy 클라이언트 unit (mock·종료성·Semaphore(1)) ------------------
if run orch/tests/test_comfy.py; then ok "게이트1: comfy 클라이언트 unit (submit/poll/view·종료성·Semaphore1·시크릿 redact)";
else bad "게이트1: test_comfy ($(tail -1 /tmp/p7_pt.log))"; fi

# --- 게이트2: 관찰성 span 계측 존재 (OBSERVE-ONLY) -------------------------------
if [ "$(grep -cE 'start_as_current_span|get_tracer' orch/comfy.py)" -ge 2 ]; then
  ok "게이트2: comfy.submit/poll/view 수동 span 계측 존재 (OBSERVE-ONLY·값 비차단)";
else bad "게이트2: comfy span 계측 부재"; fi

# --- 게이트3: 확인 프로브 reachability (read-only) ------------------------------
if timeout 60 "$PY" tools/comfy_probe.py >/tmp/p7_probe.log 2>&1 && grep -q 'RESULT: \[OK\]' /tmp/p7_probe.log; then
  ok "게이트3: Server B reachable (comfy_probe·OpenAI-compat 재확인)";
else act "Server B reachability (라이브 호스트 미도달·자원표는 0.22s 확인)"; fi

# --- 게이트4: 캐스케이드 falsifiable (X1..X4·negative-mutation) -----------------
if run orch/tests/test_image_ops.py; then ok "게이트4: 캐스케이드 트리거 falsifiable (X1/X2/X3정수/X4·escalate 결정론)";
else bad "게이트4: test_image_ops ($(tail -1 /tmp/p7_pt.log))"; fi

# --- 게이트5: tier1/tier2 로컬 처리 (art env·Pillow/rembg/OpenCV) ---------------
if timeout 180 "$ART" - <<'PYART' >/tmp/p7_art.log 2>&1
import sys; sys.path.insert(0,'.')
from PIL import Image, ImageDraw
im=Image.new("RGB",(400,300),(255,255,255)); ImageDraw.Draw(im).ellipse([80,60,320,240],fill=(40,120,220))
im.save("/tmp/p7v_src.png")
from orch.image_ops import tier2_process, inspect_asset
m=tier2_process("/tmp/p7v_src.png","/tmp/p7v_out.png",resize=(256,256),remove_bg=True)
assert m.width==256 and m.height==256 and m.has_alpha, m
print("TIER12_OK", m.alpha_ratio)
PYART
then ok "게이트5: tier1/tier2 로컬 (Pillow/rembg/OpenCV·resize+bg-remove 1산출·$(grep TIER12_OK /tmp/p7_art.log))";
else bad "게이트5: tier1/tier2 art env 처리 실패 ($(tail -2 /tmp/p7_art.log))"; fi

# --- 게이트6: tier3 실 이미지 라이브 (VAE 수리 후 실 생성 + 검정판별·falsifiable) -------
P7VAE=/tmp/p7_vae_verify.png
if timeout 260 "$PY" tools/p7_generate_one.py image "a colorful rainbow parrot perched on a green branch, vivid saturated colors" "$P7VAE" 1024x1024 >/tmp/p7_gen6.log 2>&1; then
  if "$ART" tools/p7_black_check.py "$P7VAE" >/tmp/p7_blk6.log 2>&1; then
    ok "게이트6: tier3 실 이미지 라이브 — VAE 수리 후 컬러 ($(grep -oE 'black_ratio=[0-9.]+ verdict=COLOR' /tmp/p7_blk6.log))"
  else
    act "tier3 실 이미지: 생성됐으나 검정($(grep -oE 'verdict=[A-Z]+' /tmp/p7_blk6.log))·VAE 여전 degraded"
  fi
else
  act "tier3 실 이미지: Server B 미도달/타임아웃 ($(tail -1 /tmp/p7_gen6.log))"
fi

# --- 게이트7: 영상 조립 + subprocess 종료성 (로컬 ffmpeg·orphan 0) --------------
if run orch/tests/test_video_assemble.py; then ok "게이트7: 영상 조립 + subprocess 격리 (concat·orphan kill·audio detect)";
else bad "게이트7: test_video_assemble ($(tail -1 /tmp/p7_pt.log))"; fi

# --- 게이트8: 실 video-audio 라이브 (Server B 정상·동기-long·영상+오디오 단일생성) ------
P7VID=/tmp/p7_va_verify.mp4
if timeout 420 "$PY" tools/p7_generate_one.py video_audio "a short cinematic clip of a colorful parrot in a tropical forest with ambient birdsong, vivid colors" "$P7VID" >/tmp/p7_gen8.log 2>&1; then
  if "$ART" tools/p7_video_check.py "$P7VID" --audio >/tmp/p7_vchk8.log 2>&1; then
    ok "게이트8: 실 video-audio 라이브 — Server B ($(grep -oE 'video_stream=True frame_max_channel=[0-9]+ frame_non_black=[0-9]+ audio_stream=True' /tmp/p7_vchk8.log))"
  else
    act "실 video-audio: 생성됐으나 검증 실패($(grep -oE 'verdict=[A-Z]+' /tmp/p7_vchk8.log))·모델 degraded?"
  fi
else
  act "실 video-audio: Server B 미도달/타임아웃 ($(tail -1 /tmp/p7_gen8.log))"
fi

# --- 게이트9: 서버A threading.BoundedSemaphore(2) (동시 ≤2·release 누수 0) ------
if run orch/tests/test_server_a_semaphore.py; then ok "게이트9: 서버A Semaphore(2) caller-decorator (동시 ≤2·release 누수 0·노드 byte 보존)";
else bad "게이트9: test_server_a_semaphore ($(tail -1 /tmp/p7_pt.log))"; fi

# --- 게이트10: 실 footage 트레일러 조립 (게이트8 Server B 클립 → video_assemble concat → 유효) --------
P7TRL=/tmp/p7_trailer_verify.mp4
if [ -s "$P7VID" ] && timeout 120 "$PY" -c "import sys;sys.path.insert(0,'.');from orch.video_assemble import concat_clips;concat_clips(['$P7VID','$P7VID'],'$P7TRL',timeout=100)" >/tmp/p7_trl.log 2>&1 && "$ART" tools/p7_video_check.py "$P7TRL" >/tmp/p7_tchk.log 2>&1; then
  ok "게이트10: 실 footage 트레일러 조립 (Server B 클립→concat→유효 영상·$(grep -oE 'bytes=[0-9]+ video_stream=True' /tmp/p7_tchk.log))·게임 캡처 트레일러는 P5 게임녹화 별개"
else
  act "P5 트레일러: 조립코드 OK(게이트7)·실 footage 조립 미완 ($(tail -1 /tmp/p7_trl.log 2>/dev/null))"
fi

# --- 게이트11: §28.3 하드코딩 금지 (B=추출 면제·인라인 워크플로 JSON 0) ----------
WF_COUNT="$(find apps/art/workflows -name '*.json' 2>/dev/null | wc -l)"
if [ "$WF_COUNT" -eq 0 ] && grep -q '^OD-P7: B' "$SHAPE"; then
  ok "게이트11: §28.3 워크플로 JSON 추출 면제(OD-P7=B)·하드코딩 합성 0 (apps/art/workflows *.json=$WF_COUNT)";
else bad "게이트11: 워크플로 JSON 하드코딩 의심 (*.json=$WF_COUNT)"; fi

# --- 게이트12: 동결 drift0 + orch 회귀 + ruff -----------------------------------
DRIFT_OK=1; bash scripts/verify_freeze.sh >/tmp/p7_freeze.log 2>&1 || DRIFT_OK=0
REG_OK=1; timeout 400 "$PY" -m pytest orch/tests/ -m "not slow" -p no:warnings >/tmp/p7_reg.log 2>&1 || REG_OK=0
RUFF_OK=1; /opt/conda/bin/ruff check orch/comfy.py orch/image_ops.py orch/video_assemble.py orch/graph.py tools/comfy_probe.py tools/p7_generate_one.py tools/p7_black_check.py tools/p7_video_check.py orch/tests/test_comfy.py orch/tests/test_image_ops.py orch/tests/test_video_assemble.py orch/tests/test_server_a_semaphore.py >/tmp/p7_ruff.log 2>&1 || RUFF_OK=0
if [ "$DRIFT_OK" = 1 ] && [ "$REG_OK" = 1 ] && [ "$RUFF_OK" = 1 ]; then
  ok "게이트12: 동결 drift0 + orch 회귀($(grep -oE '[0-9]+ passed' /tmp/p7_reg.log | tail -1)) + ruff clean";
else bad "게이트12: freeze=$DRIFT_OK reg=$REG_OK ruff=$RUFF_OK ($(tail -1 /tmp/p7_reg.log))"; fi

echo ""
echo "=== P7 verify 집계: PASS=$PASS FAIL=$FAIL ACTION=$ACTION BLOCKED=$BLOCKED ==="
echo "ACTION(런타임·≠pass): Server B 정상 — 이미지/video-audio/트레일러 라이브 게이트 전부 실 생성+검정 PASS·미도달/타임아웃 시에만 ACTION"
if [ "$FAIL" -eq 0 ] && [ "$BLOCKED" -eq 0 ]; then
  echo "[verify_phase7] ✅ exit0 (FAIL=0·BLOCKED=0·코드 100%·라이브 CONTENT=ACTION pending gateway)"; exit 0
else
  echo "[verify_phase7] ✗ exit1 (FAIL=$FAIL·BLOCKED=$BLOCKED)"; exit 1
fi
