#!/usr/bin/env bash
# preflight — 커밋/머지 전 hang·안전 게이트 (CI 부재 환경의 수동 게이트).
#
# hang 감사 RP-1 배선: 프로덕션 코드에 httpx 무타임아웃 호출이 들어오면 차단한다.
# 새 httpx 호출이 timeout을 빠뜨리면(서버 hang 시 무한대기) 여기서 막힌다.
# 테스트 게이트는 별도(scripts/run_tests.sh <env>)이며, 이 스크립트는 빠른 정적 체크만.
#
# 사용: bash scripts/preflight.sh   (위반 시 exit 1)
set -u
ROOT="/opt/studio"

echo "[preflight] ① httpx 무타임아웃 린트(RP-1)..."
if ! bash "$ROOT/scripts/lint_http_timeout.sh"; then
  echo "[preflight] ✗ httpx 무타임아웃 위반 — 커밋 차단. 새 httpx 호출에 timeout= 추가(스트리밍은 read=None)." >&2
  exit 1
fi

echo "[preflight] ② 동결 파일 무결성(P3·엔진 계약 드리프트 차단)..."
if ! bash "$ROOT/scripts/verify_freeze.sh"; then
  echo "[preflight] ✗ 동결 드리프트 — 커밋 차단. .omc/state/FREEZE.md 비준 절차를 확인하세요." >&2
  exit 1
fi

echo "[preflight] ✅ 정적 게이트 통과."
echo "[preflight] (테스트는 별도: scripts/run_tests.sh {engine|bench|knowledge|discord} -- 3중 가드 자동)"
