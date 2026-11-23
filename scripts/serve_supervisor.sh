#!/usr/bin/env bash
# scripts/serve_supervisor.sh — P1 step3: 멀티 OS 프로세스 워커풀 감독자 기동.
#
# ★★라이브 게이트(자율 발사 금지): concurrency:2 승격·supervisor production 기동은 반드시
#   사용자 세션 승인. 이 스크립트는 그 승인 후에만 실행한다. endpoint.yaml worker.concurrency
#   기본 1이면 단일 워커 동치(기존 serve_worker.sh와 사실상 동일·claimed_by_slot NULL 경로).
#
# 하는 일:
#   1) start_guard.sh로 이 셸(claude/세션) 트리에 자원 워치독을 디태치 기동한다 — supervisor가
#      프로세스를 스폰하므로 폭주 시 자기종료 가드 필수(시스템다운 전례 2회).
#   2) supervisor를 conda env python 직접 exec(conda run 회피·제약 #3)로 기동한다.
#      supervisor가 endpoint.yaml worker.concurrency만큼 슬롯 워커를 fork→exec로 감독한다
#      (각 슬롯 state/worker.lock.{i} advisory 락·죽은slot LOCK_NB 게이팅 청소·crash-loop 상한).
#
# 사용법(★사용자 승인 후):
#   scripts/serve_supervisor.sh                  # endpoint.yaml worker.concurrency
#   scripts/serve_supervisor.sh --concurrency 2  # 명시 오버라이드
#
# 롤백: endpoint.yaml worker.concurrency=1(또는 --concurrency 1) → 단일 워커 동치.
#
# ★런북(supervisor 사망 시·리뷰 F4): supervisor가 SIGKILL되면 자식 슬롯 워커들은 init로
#   reparent돼 계속 실행된다(각자 worker.lock.{i} 보유). 안전성:
#   (a) 새 supervisor를 띄워도 단일 인스턴스 락(state/supervisor.lock)이 이중 supervisor를
#       막고, 새 supervisor가 띄운 자식은 고아가 보유한 worker.lock.{i} 획득 실패→즉시
#       exit→_cleanup LOCK_NB도 실패→재기동 보류 = 이중실행 0(안전 재수렴).
#   (b) 권장: systemd 등 외부 감독으로 supervisor를 Restart=on-failure 결속(전 슬롯 crash-loop
#       포기 시 supervisor가 비-0 exit하므로 자동 재기동). 고아 자식 정리는 SIGTERM 전파 또는
#       다음 supervisor 수렴에 맡긴다(★pkill -f 금지 — 타 프로세스 불침해).
#
# 환경: 백엔드 conda env one_man_corp-engine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="/opt/conda/envs/one_man_corp-engine/bin/python"

if [ ! -x "$PY" ]; then
  echo "[serve_supervisor] ERROR: conda env python 없음: $PY" >&2
  exit 2
fi

# 1) 자원 워치독(폭주 자기종료) — supervisor가 프로세스 스폰이라 필수. 실패해도 진행은 막지 않음.
bash "$ROOT/.omc/guards/start_guard.sh" || echo "[serve_supervisor] 경고: start_guard 기동 실패(계속)." >&2

# 2) supervisor 직접 exec(conda run 회피).
echo "[serve_supervisor] 워커풀 감독자 기동(conda env python 직접·worker.concurrency=endpoint.yaml)."
exec "$PY" -m office.supervisor "$@"
