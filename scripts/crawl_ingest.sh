#!/usr/bin/env bash
# scripts/crawl_ingest.sh — 웹 URL 크롤 → 지식 ingest CLI 래퍼(Phase 9, synthesis).
#
# 하는 일:
#   knowledge env에서 `python -m knowledge.crawl <url...> [--staging <dir>]`를 실행해
#   웹 URL을 crawl4ai로 크롤하고, chunk_markdown(source=url)→store.upsert로 같은
#   Qdrant 컬렉션(state/qdrant)에 직접 ingest한다(크롤+upsert 한 프로세스).
#
# ★라이브(사용자 동석 세션 전용): 실 brave 브라우저·실 네트워크·실 LLM 임베딩을
#   부른다. 에이전트/CI 결정론 게이트는 이 스크립트를 쓰지 않고 mock crawler를
#   주입한 단위/통합 테스트(scripts/run_tests.sh knowledge -m 'not slow')로 검증한다.
#
# ★H-13 락 경계(serve_knowledge.sh:18 계승): 이 CLI는 state/qdrant를 직접 오픈
#   (ingest와 동일)하므로, knowledge 검색 서비스가 떠 있는 동안 실행하면 임베디드
#   qdrant 디스크를 둘이 열어 "already accessed" 충돌이 난다(store.py:88-101).
#   ★반드시 knowledge 서비스 stop 중에 실행할 것(또는 후속 ingest 엔드포인트로).
#
# 사용법:
#   scripts/crawl_ingest.sh <url...> [--staging <dir>]
#   예: scripts/crawl_ingest.sh https://example.com/docs https://example.org/guide
#
# 환경: 백엔드 conda env one_man_corp-knowledge(qdrant_client + crawl4ai 보유).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="one_man_corp-knowledge"

if [ "$#" -eq 0 ]; then
  echo "사용법: scripts/crawl_ingest.sh <url...> [--staging <dir>]" >&2
  exit 2
fi

echo "[crawl_ingest] 웹 크롤→지식 ingest (env=${CONDA_ENV})."
echo "[crawl_ingest] ★state/qdrant 직접 오픈 — knowledge 서비스 stop 중 실행할 것(H-13)."
exec env PYTHONPATH="$ROOT" conda run --no-capture-output -n "$CONDA_ENV" \
  python -m knowledge.crawl "$@"
