#!/usr/bin/env bash
# scripts/serve_discord.sh — one_man_corp Phase 4b Discord 어댑터 기동.
#
# 하는 일:
#   adapters.discord 워커를 conda env one_man_corp-discord 로 띄운다.
#   web_server SSE(/events/stream)·승인 REST(/api/inbox)를 HTTP 로 소비하는
#   별도 워커 프로세스다(office/web_server import 0 — 비침투).
#
# ★전제: web_server 는 별도로 이미 떠 있어야 한다(scripts/serve.sh).
#   어댑터는 그 SSE/REST 의 클라이언트 소비자일 뿐, web_server 를 띄우지 않는다.
#
# ★토큰: DISCORD_BOT_TOKEN 이 .env 에 없으면 봇은 "라이브 비활성" 로그를 남기고
#   정상 종료(exit 0). 라이브 게시는 사용자 토큰 제공 시에만(제약 #2).
#
# 환경: conda env one_man_corp-discord(discord.py + httpx + python-dotenv + pyyaml).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="one_man_corp-discord"

echo "[serve_discord] Discord 어댑터 기동(conda env=${CONDA_ENV}, web_server 별도 기동 전제)"
cd "$ROOT"
exec conda run --no-capture-output -n "$CONDA_ENV" python -m adapters.discord
