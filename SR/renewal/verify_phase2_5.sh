#!/usr/bin/env bash
# AIGS Phase 2.5 검증 — 파이프라인-품질(provenance) 슬라이스.
#   ①handoff 단위(P2-core 토대) ②브리프 통합(parse+AC 계약) ③C4 e2e(브리프-구동·결합 불변식)
#   ④provenance 3 HARD(시계열 brief⊰game·task_id 역참조·AC-구동 부정변이) ⑤DoD(check_dod 4구성)
# 원칙: 비파괴(임시 파일만). cross-repo(vault↔main) provenance는 commit 타임스탬프 선행으로 강제.
# 사용: SR/renewal/verify_phase2_5.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORCH_PY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$ORCH_PY" ] || { echo "[WARN] orch python 미발견 → python"; ORCH_PY="python"; }
TASK_ID="p25-clicker-mvp"
BRIEF="$ROOT/vault/specs/$TASK_ID.md"
GAME="$ROOT/games/clicker-mvp/index.html"
PROV="$ROOT/games/clicker-mvp/PROVENANCE"
WORKLOG="$ROOT/vault/quality/worklog-$TASK_ID.md"
RESEARCH="$ROOT/vault/research/clicker-mvp-notes.md"
PASS=0; FAIL=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

# ── 1. handoff 단위 (P2-core 토대 재사용) ──────────────────────────────────────
hdr "1) handoff 단위 (round-trip·DoD)"
if PYTHONPATH="$ROOT" timeout -k 5 180 "$ORCH_PY" -m pytest "$ROOT/orch/tests/test_handoff.py" \
     -p no:cacheprovider >/tmp/p25_g1.log 2>&1; then
  ok "test_handoff.py PASS ($(grep -oE '[0-9]+ passed' /tmp/p25_g1.log | tail -1))"
else fail "test_handoff.py FAIL"; tail -15 /tmp/p25_g1.log; fi

# ── 2. 브리프 통합 (parse_brief + 기계파싱 계약 존재) ───────────────────────────
hdr "2) 브리프 통합 (parse_brief + AC 계약)"
if PYTHONPATH="$ROOT" "$ORCH_PY" - "$BRIEF" >/tmp/p25_g2.log 2>&1 <<'PY'
import sys
from orch.handoff import parse_brief
b = parse_brief(open(sys.argv[1], encoding="utf-8").read())
ac = "\n".join(b.acceptance_criteria).lower()
miss = [k for k in ("init:", "click:", "upgrade:", "coupling:") if k not in ac]
assert b.task_id == "p25-clicker-mvp", f"task_id={b.task_id}"
assert not miss, f"AC 계약 누락: {miss}"
print("brief OK")
PY
then ok "브리프 parse + AC 계약(init/click/upgrade/coupling)"; else fail "브리프 통합 FAIL"; cat /tmp/p25_g2.log; fi

# ── 3. C4 e2e (브리프-구동·결합 불변식) ────────────────────────────────────────
hdr "3) C4 e2e (play_clicker_check.mjs)"
if PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 timeout -k 5 150 \
     node "$ROOT/web/scripts/play_clicker_check.mjs" >/tmp/p25_g3.log 2>&1; then
  ok "C4 PASS (결합 불변식·stage 전이·무에러)"
else fail "C4 FAIL"; tail -20 /tmp/p25_g3.log; fi

# ── 4. provenance 3 HARD ───────────────────────────────────────────────────────
hdr "4) provenance 3 HARD (시계열·task_id 역참조·AC-구동)"
# 4.1 시계열: brief_sha ⊰ game_sha (cross-repo → commit 타임스탬프 선행)
BRIEF_SHA="$(grep -oE 'brief_sha: *[0-9a-f]+' "$PROV" | awk '{print $2}')"
GAME_SHA="$(git -C "$ROOT" log -1 --format=%H -- games/clicker-mvp/index.html 2>/dev/null)"
if [ -n "$BRIEF_SHA" ] && git -C "$ROOT/vault" cat-file -e "$BRIEF_SHA" 2>/dev/null && [ -n "$GAME_SHA" ]; then
  BT="$(git -C "$ROOT/vault" show -s --format=%ct "$BRIEF_SHA")"
  GT="$(git -C "$ROOT" show -s --format=%ct "$GAME_SHA")"
  if [ "$BT" -lt "$GT" ]; then ok "4.1 시계열 brief($BT) ⊰ game($GT)"
  else fail "4.1 brief가 game보다 선행 아님 ($BT ≥ $GT)"; fi
else fail "4.1 brief_sha($BRIEF_SHA) vault 부재 또는 game_sha 없음"; fi
# 4.2 task_id 역참조: 게임커밋 메시지 + PROVENANCE + worklog
r42=0
git -C "$ROOT" log -1 --format=%B "$GAME_SHA" 2>/dev/null | grep -q "$TASK_ID" || { fail "4.2 게임커밋 메시지 task_id 없음"; r42=1; }
grep -q "$TASK_ID" "$PROV" 2>/dev/null || { fail "4.2 PROVENANCE task_id 없음"; r42=1; }
grep -q "$TASK_ID" "$WORKLOG" 2>/dev/null || { fail "4.2 worklog task_id 없음"; r42=1; }
[ $r42 -eq 0 ] && ok "4.2 task_id 역참조(게임커밋+PROVENANCE+worklog)"
# 4.3 AC-구동: 부정 변이(AC 비움)본은 FAIL해야 정상(단언이 브리프에서 파생됨 증명)
MUT="$(mktemp /tmp/p25_mut_brief.XXXXXX.md)"
PYTHONPATH="$ROOT" "$ORCH_PY" - "$BRIEF" "$MUT" <<'PY'
import sys
from orch.handoff import parse_brief, render_brief
b = parse_brief(open(sys.argv[1], encoding="utf-8").read())
b.acceptance_criteria = []
open(sys.argv[2], "w", encoding="utf-8").write(render_brief(b))
PY
if PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 timeout -k 5 120 \
     node "$ROOT/web/scripts/play_clicker_check.mjs" "$MUT" >/tmp/p25_g43.log 2>&1; then
  fail "4.3 부정 변이본이 PASS → 단언 하드코딩(provenance 위반)"
else ok "4.3 AC-구동: 부정 변이본 FAIL(단언이 브리프에서 파생됨)"; fi
rm -f "$MUT"

# ── 5. DoD (§18.5 4구성) ───────────────────────────────────────────────────────
hdr "5) DoD (check_dod: artifact·research·worklog·handoff)"
if PYTHONPATH="$ROOT" "$ORCH_PY" - "$GAME" "$RESEARCH" "$WORKLOG" "$BRIEF" >/tmp/p25_g5.log 2>&1 <<'PY'
import sys
from orch.handoff import check_dod
r = check_dod(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
assert r.ok, f"DoD 미충족: {r.missing}"
print("DoD ok")
PY
then ok "check_dod ok (4구성 적재)"; else fail "DoD FAIL"; cat /tmp/p25_g5.log; fi

# ── 6. 비파괴: 동결파일 무결성 (sha256 매니페스트·계획 follow-up) ────────────────
hdr "6) 비파괴 (동결파일 sha256 무결)"
if bash "$ROOT/scripts/verify_freeze.sh" >/tmp/p25_g6.log 2>&1; then
  ok "동결파일 무결(드리프트 0)"
else fail "동결파일 드리프트 감지"; tail -8 /tmp/p25_g6.log; fi

# ── 요약 ───────────────────────────────────────────────────────────────────────
echo; hdr "요약"
c '1;32' "PASS=$PASS"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then c '1;32' "✓ Phase 2.5 통과 (파이프라인-품질·provenance 구성적 입증)"; exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
