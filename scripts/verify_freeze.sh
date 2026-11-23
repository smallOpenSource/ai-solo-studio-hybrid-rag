#!/usr/bin/env bash
# verify_freeze — 동결 파일(엔진 계약) 무결성 검증 (P3 강제장치).
#
# 컨벤션-only 동결을 기술적 가드로 승격한다. .omc/state/protected_pre.sha의 각 파일
# sha256을 1회 대조하고(루프 0·hang 0), 불일치 시 드리프트 파일을 출력하고 exit 1.
# 동결 목록은 protected_pre.sha 단일 출처에서 읽으므로, P9가 동결을 9→7로 정정해도
# 이 스크립트는 무수정으로 추종한다(목록 하드코딩 0).
#
# 사용: bash scripts/verify_freeze.sh   (불일치 exit 1 · 매니페스트 없음 exit 2)
set -u
ROOT="/opt/studio"
SHA_FILE="$ROOT/.omc/state/protected_pre.sha"

if [ ! -f "$SHA_FILE" ]; then
  echo "[verify_freeze] ✗ 동결 매니페스트 없음: $SHA_FILE" >&2
  exit 2
fi

out="$(cd "$ROOT" && sha256sum -c "$SHA_FILE" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  n="$(grep -c . "$SHA_FILE")"
  echo "[verify_freeze] ✅ 동결 ${n}파일 무결(드리프트 0)."
  exit 0
fi

echo "[verify_freeze] ✗ 동결 드리프트 감지 — 변조(또는 누락)된 파일:" >&2
printf '%s\n' "$out" | grep -vE ': OK$' >&2
echo "[verify_freeze] 동결 파일은 엔진 계약입니다. 의도된 해동이면 .omc/state/FREEZE.md" >&2
echo "  비준 절차를 따르고 sha를 재생성하세요(code-review 승인 필수)." >&2
exit 1
