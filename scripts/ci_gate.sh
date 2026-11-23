#!/usr/bin/env bash
# ci_gate.sh — 회귀방지 단일 진실원(SSOT) 게이트 (P4).
#
# 사람 규율(커밋 전 수동 체크)을 기계로 강제한다. preflight(httpx 린트 + 동결 무결성) →
# 결정론 테스트(env별 not-slow·3중 가드) → 웹 build+vitest를 1본으로 묶어 fail-fast로 돈다.
# ★slow(실서버·플래키 실LLM)는 제외(pyproject addopts + run_tests.sh -m "not slow").
# ★GitHub Actions(.github/workflows/ci.yml)는 best-effort 안전망이고 이 스크립트가 SSOT다
#   (GH 러너엔 conda env·로컬 모델이 없어 100% 재현 불가).
#
# 각 파이썬 테스트는 run_tests.sh(ulimit -v + OS timeout + pytest --timeout 3중 가드)로 돈다.
# 사용: bash scripts/ci_gate.sh            # 전체(파이썬 env들 + 웹)
#       bash scripts/ci_gate.sh --no-web   # 파이썬만(웹 build/vitest 건너뜀)
set -uo pipefail
ROOT="/opt/studio"
cd "$ROOT" || exit 2

NO_WEB=0
for arg in "$@"; do
  case "$arg" in
    --no-web) NO_WEB=1 ;;
    *) echo "[ci_gate] 알 수 없는 인자: $arg" >&2; exit 2 ;;
  esac
done

fail() { echo "[ci_gate] ✗ 게이트 실패: $1" >&2; exit 1; }

echo "[ci_gate] ① preflight(httpx 무타임아웃 린트 + 동결 무결성)..."
bash "$ROOT/scripts/preflight.sh" || fail "preflight"

# engine은 필수. 나머지(discord/knowledge)는 env 있을 때만(best-effort·의존성 격리).
ENGINE_PY="/opt/conda/envs/one_man_corp-engine/bin/python"
[ -x "$ENGINE_PY" ] || fail "engine env 필수(one_man_corp-engine 없음)"

run_env() {  # $1=env명  $2…=pytest 대상(env별)
  local env="$1"; shift
  local py="/opt/conda/envs/one_man_corp-${env}/bin/python"
  if [ ! -x "$py" ]; then
    echo "[ci_gate]   (env one_man_corp-${env} 없음 — skip·best-effort)"
    return 0
  fi
  echo "[ci_gate] ② tests[$env] not-slow(3중 가드)..."
  bash "$ROOT/scripts/run_tests.sh" "$env" "$@" -m "not slow" || fail "tests[$env]"
}

# ★env별 대상 분리(의존성 격리): 각 env는 자기 extra-deps만 갖는다 → 비-engine env에서
#   전체 tests/를 돌리면 타 env 의존 모듈이 수집에러난다(conftest collect_ignore가 못 잡는
#   game/crm 등). engine이 결정론 본체, discord/knowledge는 전용 글롭으로 분리.
run_env engine     tests/                     # 결정론 본체(core/office/web_server/game/workflow)
run_env discord    tests/test_discord_*.py    # discord.py 의존 어댑터 테스트
run_env knowledge  tests/test_knowledge_*.py  # qdrant-client 의존 지식 테스트

if [ "$NO_WEB" -eq 0 ]; then
  if [ -d "$ROOT/web" ] && command -v npm >/dev/null 2>&1; then
    echo "[ci_gate] ③ web build + vitest(--run)..."
    ( cd "$ROOT/web" && npm run build && npm run test -- --run ) || fail "web build/vitest"
  else
    echo "[ci_gate]   (web/ 또는 npm 없음 — skip)"
  fi
  if [ -x "$ROOT/game/scripts/verify_slice.sh" ]; then
    echo "[ci_gate] ④ game slice(playable + build·P2)..."
    bash "$ROOT/game/scripts/verify_slice.sh" || fail "game slice(verify_slice)"
  else
    echo "[ci_gate]   (game/scripts/verify_slice.sh 없음 — skip)"
  fi
fi

echo "[ci_gate] ✅ 전체 게이트 GREEN."
