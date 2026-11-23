#!/usr/bin/env bash
# scripts/serve.sh — one_man_corp Phase 4a 통합 서빙 헬퍼(US-008 / T8).
#
# 하는 일:
#   1) web/ 프론트를 빌드(npm run build → web/dist)한다(--build 또는 dist 없을 때).
#   2) web_server를 uvicorn 단일 워커(--workers 1)로 기동한다.
#      → FastAPI 단일 포트로 프론트(web/dist 정적) + API(SSE·inbox·run)를 함께 서빙.
#
# ★단일 워커(--workers 1) 고정(M2, 수용 20): 멀티워커면 runner의 전역 단일 실행
#   락·전용 executor·sse 인메모리 fan-out 큐가 프로세스별로 쪼개져 동시성 1·구독이
#   깨진다. python -m web_server 진입점이 workers=1을 코드로 못 박는다(web_server/__main__.py).
#
# host/port는 config/endpoint.yaml의 web 블록에서 읽는다(코드 하드코딩 0, 수용 18).
#
# 사용법:
#   scripts/serve.sh            # web/dist 있으면 그대로, 없으면 빌드 후 서빙
#   scripts/serve.sh --build    # 항상 프론트를 새로 빌드한 뒤 서빙
#
# 환경: 백엔드는 conda env one_man_corp-engine, 프론트 빌드는 npm(node).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="one_man_corp-engine"

BUILD=0
if [[ "${1:-}" == "--build" ]]; then
  BUILD=1
fi

# 1) 프론트 빌드(필요 시) — web/dist 없거나 --build면.
if [[ "$BUILD" -eq 1 || ! -f "$ROOT/web/dist/index.html" ]]; then
  echo "[serve] 프론트 빌드: npm run build (web/dist)"
  (cd "$ROOT/web" && npm run build)
fi

# 2) uvicorn 단일 워커로 기동 — host/port는 endpoint.yaml web 블록에서.
echo "[serve] web_server 기동(uvicorn --workers 1, host/port=endpoint.yaml web 블록)"
exec conda run --no-capture-output -n "$CONDA_ENV" python -m web_server
