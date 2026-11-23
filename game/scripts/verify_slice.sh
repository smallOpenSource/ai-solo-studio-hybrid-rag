#!/usr/bin/env bash
# verify_slice.sh — 게임 슬라이스 재현가능 검증 (P2).
#
# "게임 슬라이스 완주" 주장을 재현가능하게 뒷받침한다(game/SLICE.md):
#   ① 결정론 테스트(playable 9/9 + schema) — LLM 양산 데이터가 실제로 플레이가능한지.
#   ② build(dist를 현 데이터로 재생성 — stale dist 해소).
#   ③ 데이터 정합 manifest(현 데이터 sha — dist가 이 데이터로 빌드됨).
# ★game_qa는 schema validate_all만 본다. 플레이가능성은 이 게이트(dev vitest)가 메운다.
set -uo pipefail
ROOT="/opt/studio/game"
cd "$ROOT" || exit 2

echo "[verify_slice] ① 결정론 테스트(playable + schema)..."
if ! npm run test -- --run; then
  echo "[verify_slice] ✗ 테스트 실패 — playable 미통과면 game/data 보정 필요(SLICE.md 참조)." >&2
  exit 1
fi

echo "[verify_slice] ② build(dist 현데이터 재생성·stale 해소)..."
if ! npm run build; then
  echo "[verify_slice] ✗ build 실패." >&2
  exit 1
fi

echo "[verify_slice] ③ 데이터 정합 manifest(현 data sha):"
sha256sum data/*.json | sort

echo "[verify_slice] ✅ 슬라이스 GREEN(playable 통과·dist=현데이터·데이터 정합)."
