#!/usr/bin/env bash
# self_resource_guard.sh — 이 Claude Code 프로세스(TARGET) "트리만" 감시·자기종료.
#
# 안전 불변(★):
#   - TARGET(이 claude PID)의 **자손 PID만** BFS로 수집(tree_pids). 다른 claude(다른 트리)는 구조적으로 포함 불가.
#   - pkill/-f/패턴매칭 **절대 미사용**(자기매칭·타프로세스 오살 방지).
#   - 워치독 자신($$)은 kill 대상에서 제외.
#   - 죽이는 대상 = {TARGET 자손} (소프트) 또는 {TARGET 자손 + TARGET} (하드). 그 외 PID 불가침.
#
# 동작:
#   soft breach(트리 RSS>SOFT_KB)  -> 1GB 초과 무거운 자손만 종료(claude 생존).
#   hard breach(트리 RSS>HARD_KB | MemAvailable<floor & 트리 큼 | 자손수>PROC_CAP)
#                                  -> 자손 전체 + TARGET(claude) KILL = 자기종료.
#
# 사용: TARGET_PID=<claude_pid> [DRY_RUN=1] [SOFT_KB=] [HARD_KB=] [MEM_FLOOR_KB=] [PROC_CAP=] [INTERVAL=] \
#        bash self_resource_guard.sh
set -u
TARGET="${TARGET_PID:?need TARGET_PID}"
SELF=$$
DRY_RUN="${DRY_RUN:-0}"
SOFT_KB="${SOFT_KB:-10000000}"          # 10 GB: 무거운 자손 정리
HARD_KB="${HARD_KB:-16000000}"          # 16 GB: 전체 자기종료
MEM_FLOOR_KB="${MEM_FLOOR_KB:-1200000}" # 시스템 MemAvailable 바닥 1.2 GB
LEAF_KB="${LEAF_KB:-1000000}"           # 자손 1개가 1 GB 초과 = '무거움'
PROC_CAP="${PROC_CAP:-250}"             # fork storm 상한(자손 수)
INTERVAL="${INTERVAL:-8}"
LOG="${GUARD_LOG:-/opt/studio/.omc/guards/guard.log}"

log(){ printf '%s [guard pid=%s target=%s] %s\n' "$(date '+%F %T')" "$SELF" "$TARGET" "$*" >>"$LOG" 2>&1; }

# TARGET의 모든 자손 PID 출력(BFS·깊이 bound 64·워치독 자신 제외). 다른 트리 미포함.
tree_pids(){
  local frontier="$TARGET" next pid k kids out="" depth=0
  while [ -n "$frontier" ] && [ "$depth" -lt 64 ]; do
    next=""
    for pid in $frontier; do
      kids=$(ps -o pid= --ppid "$pid" 2>/dev/null)
      for k in $kids; do
        [ "$k" = "$SELF" ] && continue
        out="$out $k"; next="$next $k"
      done
    done
    frontier="$next"; depth=$((depth+1))
  done
  printf '%s' "$out"
}
rss_of(){ ps -o rss= -p "$1" 2>/dev/null | tr -d ' '; }

log "started DRY_RUN=$DRY_RUN SOFT=${SOFT_KB}KB HARD=${HARD_KB}KB floor=${MEM_FLOOR_KB}KB cap=$PROC_CAP int=${INTERVAL}s"
while :; do
  if ! kill -0 "$TARGET" 2>/dev/null; then log "target gone; guard exiting"; exit 0; fi
  kids="$(tree_pids)"
  total=$(rss_of "$TARGET"); total=${total:-0}; count=0
  for p in $kids; do r=$(rss_of "$p"); [ -n "$r" ] && { total=$((total+r)); count=$((count+1)); }; done
  avail=$(awk '/^MemAvailable/{print $2; exit}' /proc/meminfo 2>/dev/null); avail=${avail:-99999999}

  reason=""
  [ "$total" -gt "$HARD_KB" ] && reason="HARD tree_rss=${total}KB>${HARD_KB}KB"
  { [ "$avail" -lt "$MEM_FLOOR_KB" ] && [ "$total" -gt 3000000 ]; } && reason="HARD memavail=${avail}KB<${MEM_FLOOR_KB}KB tree=${total}KB"
  [ "$count" -gt "$PROC_CAP" ] && reason="HARD descendants=$count>$PROC_CAP"

  if [ -n "$reason" ]; then
    log "BREACH(hard): $reason -> self-terminate {descendants+TARGET}. kids=[$kids]"
    if [ "$DRY_RUN" = "1" ]; then log "DRY_RUN: would TERM/KILL kids then KILL $TARGET"; exit 1; fi
    for p in $kids; do kill -TERM "$p" 2>/dev/null; done
    sleep 2
    for p in $kids; do kill -KILL "$p" 2>/dev/null; done
    kill -TERM "$TARGET" 2>/dev/null; sleep 1; kill -KILL "$TARGET" 2>/dev/null
    log "self-terminated."
    exit 1
  fi

  if [ "$total" -gt "$SOFT_KB" ]; then
    heavy=""
    for p in $kids; do r=$(rss_of "$p"); [ -n "$r" ] && [ "$r" -gt "$LEAF_KB" ] && heavy="$heavy $p"; done
    if [ -n "$heavy" ]; then
      log "BREACH(soft): tree_rss=${total}KB>${SOFT_KB}KB -> kill heavy leaves [$heavy] (keep claude)"
      if [ "$DRY_RUN" != "1" ]; then
        for p in $heavy; do kill -TERM "$p" 2>/dev/null; done
        sleep 2
        for p in $heavy; do kill -KILL "$p" 2>/dev/null; done
      fi
    fi
  fi
  sleep "$INTERVAL"
done
