#!/usr/bin/env bash
# httpx 무타임아웃 린트 — `httpx.Client()` / `httpx.AsyncClient()` 를 timeout 인자
# 없이 호출하는 것을 금지한다(RP-1, hang 재발방지 구조강제).
#
# 배경: 동시성 1 엔진에서 무타임아웃 HTTP 호출 한 건이 hang하면 전체가 정지한다
# (.omc/plans/ralplan-hang-audit.md H-01/H-02/H-08). 새 httpx 호출이 타임아웃을
# 빠뜨리면 이 린트가 빌드를 실패시킨다.
#
# ★ 규약: 신규 httpx 호출은 timeout= 필수(스트리밍은 read=None 으로 명시).
#
# 사용: bash scripts/lint_http_timeout.sh
#   위반 0 → "✅ httpx 무타임아웃 0" + exit 0
#   위반   → 위반 라인 출력 + exit 1
set -u

ROOT="/opt/studio"

# 무인자 httpx.(Async)Client() 호출만 매칭(timeout= 등 인자 있으면 통과).
# tests/는 transport 주입 + timeout= 명시라 스캔 대상 외(프로덕션 코드만).
matches="$(grep -rnE 'httpx\.(Async)?Client\(\s*\)' --include=*.py \
  "$ROOT/office" "$ROOT/web_server" "$ROOT/adapters" "$ROOT/knowledge" "$ROOT/core" \
  2>/dev/null)"

if [ -n "$matches" ]; then
  echo "❌ httpx 무타임아웃 호출 발견(timeout= 필수, 스트리밍은 read=None):" >&2
  echo "$matches" >&2
  exit 1
fi

echo "✅ httpx 무타임아웃 0"
exit 0
