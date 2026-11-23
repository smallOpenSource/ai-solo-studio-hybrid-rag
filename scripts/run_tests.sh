#!/usr/bin/env bash
# 안전 테스트 러너 — 모든 테스트를 3중 가드로 실행해 무한 루프 hang을 원천 차단한다.
#
# Phase 4b(2026-06-08)에 SSE 무한 재연결 루프 테스트가 종료 조건 없이 돌며 CPU 스핀 +
# 메모리 폭주로 시스템을 hang시킨 사고가 있었다(memory/discord-adapter-test-hang-guard.md,
# .omc/wiki/hang-timeout.md). 어댑터는 의도적 무한 루프(재연결·gateway·이벤트 소비)가
# 본질이라, 테스트가 이를 bound하지 못하면 시스템이 멈춘다. 이 러너는 사람·에이전트의
# 기억에 의존하지 않고 **항상** 아래 3중 가드를 강제한다:
#   1) ulimit -v       : 가상메모리 캡 → 런어웨이 폭주는 MemoryError로 실패(시스템 OOM 방지)
#   2) timeout(OS)     : 전체 프로세스 벽시계 상한 → 초과 시 SIGTERM→SIGKILL(확실한 종료)
#   3) pytest --timeout: 개별 테스트 타임아웃(+ conftest가 비-slow 전 테스트에 기본 30s 자동 적용)
#
# 사용:
#   scripts/run_tests.sh <env> [pytest 인자...]
#     env = engine | bench | knowledge | discord
#   예:
#     scripts/run_tests.sh discord tests/test_discord_*.py
#     scripts/run_tests.sh engine tests/ -m "not slow"
#   환경변수 오버라이드: OMC_TEST_MEM_KB · OMC_TEST_WALL_SEC · OMC_TEST_PYTEST_TIMEOUT
set -u

ENV_NAME="${1:?사용: run_tests.sh <env: engine|bench|knowledge|discord> [pytest args...]}"
shift

ROOT="/opt/studio"
PY="/opt/conda/envs/one_man_corp-${ENV_NAME}/bin/python"
if [ ! -x "$PY" ]; then
  echo "ERROR: env python을 찾을 수 없음: $PY" >&2
  exit 2
fi

MEM_KB="${OMC_TEST_MEM_KB:-10000000}"      # ~10GB 가상메모리 캡(시스템 31GB OOM 방지)
WALL_SEC="${OMC_TEST_WALL_SEC:-600}"       # OS 벽시계 상한(초) — 전체 프로세스 강제 종료
PYTEST_TIMEOUT="${OMC_TEST_PYTEST_TIMEOUT:-30}"  # pytest 개별 테스트 타임아웃(초)

echo "[run_tests] env=$ENV_NAME mem=${MEM_KB}KB wall=${WALL_SEC}s pytest-timeout=${PYTEST_TIMEOUT}s"

# 서브셸에서 ulimit 적용 후 exec — 가드가 테스트 프로세스에 확실히 걸린다.
(
  ulimit -v "$MEM_KB" 2>/dev/null || echo "[run_tests] 경고: ulimit -v 미적용(메모리캡 없이 진행)" >&2
  exec env PYTHONPATH="$ROOT" timeout -k 10 "$WALL_SEC" \
    "$PY" -m pytest "$@" --timeout="$PYTEST_TIMEOUT" -p no:cacheprovider
)
