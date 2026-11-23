#!/usr/bin/env bash
# start_guard.sh — 이 셸의 'claude' 조상 PID를 자동탐지해 self_resource_guard 를 디태치 기동.
# 중복 기동 방지. setsid 로 claude 트리에서 분리(워치독이 자기 트리에 포함되지 않도록).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"

# $$ 에서 위로 올라가며 comm==claude 탐색(최대 12단계)
p=$$; target=""
for _ in $(seq 1 12); do
  pp=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  [ -z "$pp" ] || [ "$pp" = "0" ] || [ "$pp" = "1" ] && break
  comm=$(ps -o comm= -p "$pp" 2>/dev/null | tr -d ' ')
  if [ "$comm" = "claude" ]; then target="$pp"; break; fi
  p="$pp"
done
# 명시 인자/환경 우선
[ -n "${1:-}" ] && target="$1"
[ -n "${TARGET_PID:-}" ] && target="$TARGET_PID"
[ -z "$target" ] && { echo "ERROR: claude 조상 PID 탐지 실패 — TARGET_PID=<pid> 로 명시"; exit 1; }

PIDFILE="$DIR/guard.pid"
TGTFILE="$DIR/guard.target"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  if [ "$(cat "$TGTFILE" 2>/dev/null)" = "$target" ]; then
    echo "guard already running pid=$(cat "$PIDFILE") target=$target"; exit 0
  fi
  # 실행중 워치독이 다른(이전 세션) PID를 보고 있음 → 그건 곧 self-exit. 현 세션용 새 워치독 기동.
  echo "stale guard (target=$(cat "$TGTFILE" 2>/dev/null) != current $target); launching fresh"
fi

TARGET_PID="$target" \
SOFT_KB="${SOFT_KB:-10000000}" HARD_KB="${HARD_KB:-16000000}" \
MEM_FLOOR_KB="${MEM_FLOOR_KB:-1200000}" PROC_CAP="${PROC_CAP:-250}" INTERVAL="${INTERVAL:-8}" \
DRY_RUN="${DRY_RUN:-0}" \
setsid nohup bash "$DIR/self_resource_guard.sh" >>"$DIR/guard.log" 2>&1 &
echo $! >"$PIDFILE"; echo "$target" >"$TGTFILE"
echo "guard launched watchdog_pid=$(cat "$PIDFILE") target=$target dry_run=${DRY_RUN:-0}"
