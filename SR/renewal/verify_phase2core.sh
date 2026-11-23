#!/usr/bin/env bash
# AIGS Phase 2-core 검증 — 무손실 전파 최소셋(핸드오프 스키마 + vault 템플릿 + DoD).
#   명세 §18.3/§27.1 핸드오프 브리프 · §18.4 ADR/Worklog 템플릿 · §18.5 지식적재 DoD.
# 게이트: ① orch handoff 단위(round-trip·검증·DoD) GREEN
#         ② vault/_templates/{adr,worklog}.md 존재
#         ③ 라이브 라운드트립 1회(render→write→parse 동등) + DoD reject 1회
# 원칙: 비파괴(임시 디렉토리만 기록). OS timeout 가드(orch env엔 pytest-timeout 미설치).
# 사용: SR/renewal/verify_phase2core.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # repo root (= SR/renewal/../..)
PY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$PY" ] || { echo "[WARN] orch python 미발견($PY) → 'python' 시도"; PY="python"; }
PASS=0; FAIL=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

# ── 1. orch handoff 단위 테스트 ────────────────────────────────────────────
hdr "1) orch handoff 단위 (§27.1 round-trip · 검증 · §18.5 DoD)"
LOG=/tmp/aigs_p2core_pytest.log
if PYTHONPATH="$ROOT" timeout -k 5 180 "$PY" -m pytest "$ROOT/orch/tests/test_handoff.py" \
     -p no:cacheprovider >"$LOG" 2>&1; then
  ok "test_handoff.py PASS ($(grep -oE '[0-9]+ passed' "$LOG" | tail -1))"
else
  fail "test_handoff.py FAIL — 로그: $LOG"; tail -25 "$LOG"
fi

# ── 2. vault 템플릿 (§18.4) ────────────────────────────────────────────────
hdr "2) vault 템플릿 (§18.4 ADR / Work Log)"
for t in adr worklog; do
  if [ -f "$ROOT/vault/_templates/$t.md" ]; then ok "vault/_templates/$t.md 존재"
  else fail "vault/_templates/$t.md 없음"; fi
done

# ── 3. 라이브 라운드트립 + DoD ─────────────────────────────────────────────
hdr "3) 라이브 라운드트립 (§27.1 render→write→parse) + DoD reject"
if PYTHONPATH="$ROOT" timeout -k 5 60 "$PY" - <<'PYRT'
import tempfile, pathlib
from orch.handoff import HandoffBrief, render_brief, parse_brief, write_brief, check_dod
b = HandoffBrief(
    task_id="verify-p2core", from_="CEO", to="RESEARCH", objective="검증용 브리프",
    context_refs=["vault/x.md"], acceptance_criteria=["AC1"], constraints=["c1"],
    reversibility="two-way", confidence=0.8, priority="high",
    artifacts_expected=["vault/y.md"],
)
assert parse_brief(render_brief(b)) == b, "메모리 round-trip 불일치"
with tempfile.TemporaryDirectory() as d:
    p = write_brief(b, d)
    assert p.exists() and p.name == "verify-p2core.md", "write 경로 오류"
    assert parse_brief(p.read_text(encoding="utf-8")) == b, "파일 round-trip 불일치"
    a = pathlib.Path(d) / "a.md"; a.write_text("x", encoding="utf-8")
    assert check_dod(a, a, a, p).ok, "DoD all-present 가 reject"
    r = check_dod(a, None, a, p)
    assert (not r.ok) and "research_evidence" in r.missing, "DoD 누락 reject 실패"
print("round-trip + write + DoD OK")
PYRT
then ok "라이브 라운드트립 + DoD"; else fail "라이브 라운드트립/DoD 실패"; fi

# ── 요약 ───────────────────────────────────────────────────────────────────
echo
hdr "요약"
c '1;32' "PASS=$PASS"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then c '1;32' "✓ Phase 2-core 통과 (무손실 전파 최소셋)"; exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
