#!/usr/bin/env bash
# checkpoint.sh — 커밋 카덴스 기계강제 (P5).
#
# "테스트 통과" 자기보고 대신, 빠른 게이트(preflight: httpx 무타임아웃 린트 + 동결 무결성)를
# 실제로 돌리고 그 결과를 **기계 푸터**로 커밋에 박는다. 작은 단위 잦은 atomic 커밋을 돕고,
# 커밋이 무엇을 검증했는지 기계가 증언한다(자기보고 게이트 문제 해소).
# ★전체 테스트 게이트는 scripts/ci_gate.sh(SSOT) — checkpoint는 빠른 동결/정적 가드만 박는다.
#
# 사용: scripts/checkpoint.sh "<커밋 메시지>" [파일...]
#   파일 인자가 있으면 그것만 명시 stage(git add -A 안 함 — 의도단위 atomic).
#   없으면 이미 staged된 변경만 커밋한다.
set -uo pipefail
ROOT="/opt/studio"
cd "$ROOT" || exit 2

MSG="${1:-}"
if [ -z "$MSG" ]; then
  echo "사용: scripts/checkpoint.sh \"<커밋 메시지>\" [파일...]" >&2
  exit 2
fi
shift || true

# 파일 인자가 있으면 명시 stage(git add -A 금지 — 의도단위 atomic).
if [ "$#" -gt 0 ]; then
  git add -- "$@"
fi

if git diff --cached --quiet; then
  echo "[checkpoint] staged 변경 없음 — 파일을 인자로 주거나 먼저 git add 하세요." >&2
  exit 1
fi

# 빠른 게이트(동결 무결성 + httpx 무타임아웃 린트). 전체 테스트는 ci_gate.sh.
gate_log="$(mktemp)"
if ! bash "$ROOT/scripts/preflight.sh" >"$gate_log" 2>&1; then
  cat "$gate_log" >&2; rm -f "$gate_log"
  echo "[checkpoint] ✗ preflight 실패 — 커밋 중단(동결 드리프트 또는 httpx 위반)." >&2
  exit 1
fi
rm -f "$gate_log"

# 기계 푸터(자기보고 아님 — 방금 실제 실행한 결과).
total="$(grep -c . "$ROOT/.omc/state/protected_pre.sha")"
ok="$(sha256sum -c "$ROOT/.omc/state/protected_pre.sha" 2>/dev/null | grep -c ': OK$')"
nfiles="$(git diff --cached --name-only | wc -l | tr -d ' ')"
footer="checkpoint-gate: preflight=PASS freeze=${ok}/${total} staged=${nfiles}file(s)"

git commit -m "$MSG" -m "$footer"
echo "[checkpoint] ✅ 커밋 완료 — $footer"
