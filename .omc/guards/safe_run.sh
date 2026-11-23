#!/usr/bin/env bash
# safe_run.sh — 무거운/루프 가능 명령을 **시간·메모리 상한** 하에서 실행.
# 폭주(무한루프·메모리 풍선) 시 **해당 명령만** 죽음 → 시스템·타프로세스(타 claude 포함) 무영향.
# self_resource_guard(워치독)의 1차 방어선: 명령 단위 커널 강제.
#
# 사용:
#   bash .omc/guards/safe_run.sh <timeout_sec> <vmem_kb> -- <command ...>
#   예) bash .omc/guards/safe_run.sh 180 8000000 -- /opt/conda/envs/one_man_corp-crawl/bin/python tools/lpc_pipeline/build_us1.py
#       → 180초 초과 시 SIGTERM(+5s 후 SIGKILL), 가상메모리 8GB 초과 시 커널이 그 프로세스만 종료.
#
# 권장값:
#   - PIL/numpy 이미지 파이프라인: timeout 120~300s·vmem 6~8GB(가상≈실사용이라 vmem 유의미).
#   - ★Node/Vite/빌드/테스트(npm·esbuild·WASM): **vmem=0**(타임아웃 전용). ulimit -v는
#     V8/WASM의 대용량 가상주소 예약을 깨뜨려 거짓 OOM(WebAssembly.instantiate OOM)을
#     유발한다 → 이런 도구는 timeout만 쓰고 메모리 보호는 워치독(RSS)이 담당. timeout 600s.
set -u
T="${1:?usage: safe_run.sh <timeout_sec> <vmem_kb|0> -- <cmd...>}"; shift
M="${1:?need vmem_kb (0=타임아웃 전용·Node/WASM용)}"; shift
[ "${1:-}" = "--" ] && shift
[ -z "${1:-}" ] && { echo "ERROR: 실행할 명령이 없음"; exit 2; }
# timeout=런타임 상한(무한루프 차단·필수). vmem>0이면 ulimit -v 주소공간 상한(PIL/numpy 풍선
# 차단). vmem=0이면 ulimit 생략(Node/WASM 호환·메모리는 워치독 RSS가 보호). 해당 프로세스
# 트리에만 적용 → 시스템/타프로세스 무영향.
exec timeout --kill-after=5 --signal=TERM "$T" \
  bash -c 'if [ "$0" -gt 0 ]; then ulimit -v "$0" 2>/dev/null || true; fi; exec "$@"' "$M" "$@"
