#!/usr/bin/env bash
# scripts/smoke_run.sh — 라이브 스모크: smoke_email 워크플로 1건 enqueue.
#
# 웹 게이트웨이(POST /api/run)에 enqueue → 워커가 claim+run. ★딱 1회만 실행
# (재실행 금지 — 새 run_id로 새 발송 = 2통 위험). 라이브 SMTP 트랙은 사용자가 .env에
# SMTP 4종을 넣고 C-SMOKE-1을 시드(smoke_seed.py)한 뒤 실행한다.
#
# 사용법:
#   scripts/smoke_run.sh             # 실 LLM(mock=false) — SMTP/통합 트랙(C-SMOKE-1 발송)
#   scripts/smoke_run.sh --mock      # 결정론 fake LLM(메일0·C-2041) — Discord 파이프라인 트랙
#   scripts/smoke_run.sh --dry-run   # 큐 미변경·payload만 출력
#   scripts/smoke_run.sh --force     # 실 SMTP sentinel 무시·강제 재실행(2통 책임 하에)
#
# 전제: web(serve.sh) + worker(serve_worker.sh) 기동. WEB_BASE_URL(.env, 기본 127.0.0.1:8800).
set -euo pipefail

MOCK="false"
DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --mock) MOCK="true" ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    *) echo "[smoke_run] 알 수 없는 인자: $arg" >&2; exit 2 ;;
  esac
done

BASE_URL="${WEB_BASE_URL:-http://127.0.0.1:8800}"
PAYLOAD="{\"workflow\":\"smoke_email\",\"mock\":${MOCK}}"
# ★실 SMTP 스모크 run-once sentinel(P4): mock=false 재실행=새 run_id=2통 위험 → 1회만.
SMOKE_SENTINEL="/opt/studio/state/.smoke_email_sent.sentinel"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[smoke_run] DRY-RUN — enqueue 안 함."
  echo "[smoke_run] payload: ${PAYLOAD}  →  POST ${BASE_URL}/api/run"
  exit 0
fi

# ★sentinel 가드: 실 SMTP 트랙(mock=false)은 1회만. mock=true는 메일 0이라 무가드.
if [[ "$MOCK" == "false" && -e "$SMOKE_SENTINEL" && "$FORCE" -eq 0 ]]; then
  echo "[smoke_run] ✗ 실 SMTP 스모크는 이미 1회 실행됨:" >&2
  echo "[smoke_run]   sentinel=$SMOKE_SENTINEL ($(cat "$SMOKE_SENTINEL" 2>/dev/null))" >&2
  echo "[smoke_run]   2통 발송 방지 가드. 의도적 재실행이면 --force(또는 sentinel 삭제)." >&2
  exit 3
fi

echo "[smoke_run] enqueue: ${PAYLOAD}  →  ${BASE_URL}/api/run (mock=${MOCK})"
echo "[smoke_run] ★딱 1회만 — 결과(inbox 승인·메일 도착) 확인 전 재실행 금지(2통 방지)."
# connect/max-time 타임아웃 필수(hang 차단). -f: HTTP 에러 시 실패 종료.
curl -fsS --connect-timeout 5 --max-time 15 \
  -X POST "${BASE_URL}/api/run" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}"
echo ""
echo "[smoke_run] enqueue 완료 — 워커가 claim+run. 승인은 GET /api/inbox 또는 Discord 버튼."
# 실 SMTP 트랙만 sentinel 박제(재실행 차단). mock/dry-run은 무영향.
if [[ "$MOCK" == "false" ]]; then
  mkdir -p "$(dirname "$SMOKE_SENTINEL")"
  printf 'enqueued %s (run-once 가드·재실행은 --force)\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$SMOKE_SENTINEL"
  echo "[smoke_run] sentinel 기록: $SMOKE_SENTINEL (재실행은 --force)."
fi
