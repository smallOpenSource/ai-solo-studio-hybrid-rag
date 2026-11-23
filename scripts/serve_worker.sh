#!/usr/bin/env bash
# scripts/serve_worker.sh — F2 워커 프로세스 기동(Phase 6 6a Chunk 2).
#
# 하는 일:
#   task_queue(office.db)를 소비하는 별도 워커 프로세스(office.worker)를 기동한다.
#   웹(scripts/serve.sh)은 enqueue + SSE/Inbox/api만, 이 워커가 claim+run을 담당한다
#   (장애 격리: 워크플로 크래시가 웹을 안 죽임). 같은 office.db(WAL)를 공유하므로 웹의
#   FanoutPoller가 워커가 쓴 이벤트를 그대로 SSE→Discord로 흘린다(계약 보존).
#
# ★단일 인스턴스(동시성 1 — M2): office.worker가 state/worker.lock에 fcntl.flock
#   (advisory·non-blocking)으로 단일 인스턴스를 강제한다. 이미 떠 있으면 즉시 exit(1).
#   프로세스가 죽으면 OS가 락을 자동 해제(좀비 잔재 0).
#
# 사용법:
#   scripts/serve_worker.sh                 # 무한 루프(프로덕션·기본 poll 2.0s)
#   scripts/serve_worker.sh --poll-interval 0.5
#
# ★2프로세스 기동(웹 + 워커): 별 셸/슈퍼바이저로 각각 띄운다.
#   터미널 1:  scripts/serve.sh           # 웹(uvicorn 단일 워커)
#   터미널 2:  scripts/serve_worker.sh    # 워커(task_queue 소비)
#
# ★런북 권고(사용자 영역 — 강결합 회피): systemd/supervisor로 두 프로세스를 감독하라.
#   예) systemd unit 2개(omc-web.service·omc-worker.service, Restart=on-failure)로
#   각각 ExecStart에 위 두 스크립트를 건다. 워커는 SIGTERM 수신 시 현재 tick 마치고
#   graceful 종료한다(진행 중 워크플로 보호). 배포 자동화·unit 파일은 이 계획 비범위.
#
# 환경: 백엔드 conda env one_man_corp-engine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="one_man_corp-engine"

echo "[serve_worker] 워커 기동(office.worker, advisory 락·단일 인스턴스)."
exec conda run --no-capture-output -n "$CONDA_ENV" python -m office.worker "$@"
