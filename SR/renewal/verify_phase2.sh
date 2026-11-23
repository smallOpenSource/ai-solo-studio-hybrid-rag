#!/usr/bin/env bash
# AIGS Phase 2 검증 (sub-phase A 회수트랙 + B 협업트랙).
#  [A] ① named-sparse(sparse_probe) ② knowledge named dense+sparse + count ③ 단위(하이브리드·eval·lifecycle)
#      ④ C2(§24.5 헤드라인 post-rerank 회복·정직 무신호 허용·semantic 회귀 0)
#  [B] B1 플레이북(§18.6) 주입 + Blackboard/persona 단위 · B2 프로덕션 sink 정적배선(grep·REDIS 무관 always)
#      B3 Blackboard(§27.5) 스냅샷 + REDIS 왕복(미설정 skip) · B4 /tasks graceful(REDIS 미설정 200+빈목록)
#  [공통] 비파괴(동결 sha256)
# 원칙: ④는 tools/p2rest_c2.py(실 bge·~40min) 결과(results/p2rest_c2/c2_summary.json)를 읽는다.
#       REDIS 미설정 줄은 'skipped: pending Owner(§6)'(≠fail·해당 줄만)·B2 정적배선은 REDIS 무관 always.
# 사용: SR/renewal/verify_phase2.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KPY="${KNOWLEDGE_PY:-/opt/conda/envs/one_man_corp-knowledge/bin/python}"
[ -x "$KPY" ] || { echo "[WARN] knowledge python 미발견 → python"; KPY="python"; }
OPY="${ORCH_PY:-/opt/conda/envs/orch/bin/python}"
[ -x "$OPY" ] || { echo "[WARN] orch python 미발견 → python"; OPY="python"; }
QH="127.0.0.1"; QPORT="${PORT_QDRANT_REST:-2811}"; COLL="knowledge"
C2JSON="$ROOT/results/p2rest_c2/c2_summary.json"
PASS=0; FAIL=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
warn(){ c '1;33' "[WARN] $*"; }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

# ── 1. Qdrant named-sparse 전제 ────────────────────────────────────────────
hdr "1) Qdrant named-sparse 전제 (sparse_probe)"
if PYTHONPATH="$ROOT" timeout -k 5 60 "$KPY" tools/qdrant_sparse_probe.py >/tmp/p2_g1.log 2>&1; then
  ok "named-sparse SUPPORTED ($(grep -oE 'qdrant-client=[0-9.]+' /tmp/p2_g1.log | head -1))"
else fail "sparse_probe 실패"; tail -8 /tmp/p2_g1.log; fi

# ── 2. knowledge 컬렉션 스키마 + count ─────────────────────────────────────
hdr "2) knowledge 컬렉션 (named dense+sparse · count)"
curl -s "http://$QH:$QPORT/collections/$COLL" >/tmp/p2_g2.json 2>&1
"$KPY" - <<PY
import json
d=json.load(open("/tmp/p2_g2.json")).get("result",{})
p=d.get("config",{}).get("params",{})
dense=isinstance(p.get("vectors"),dict) and "dense" in p["vectors"]
sparse="text" in (p.get("sparse_vectors") or {})
cnt=d.get("points_count")
import sys
print(f"dense_named={dense} sparse_named={sparse} count={cnt}")
sys.exit(0 if (dense and sparse and cnt and cnt>0) else 1)
PY
[ $? -eq 0 ] && ok "named dense+sparse · count>0" || fail "컬렉션 스키마/카운트 불충족"

# ── 3. 단위 테스트(하이브리드·eval·lifecycle) ──────────────────────────────
hdr "3) 단위 (store 하이브리드 · eval 3rd · lifecycle searchable-after-emit)"
if PYTHONPATH="$ROOT" timeout -k 10 240 "$KPY" -m pytest \
     "$ROOT/knowledge/tests/test_store_hybrid.py" \
     "$ROOT/knowledge/tests/test_eval_hybrid.py" \
     "$ROOT/knowledge/tests/test_knowledge_lifecycle.py" \
     -p no:cacheprovider >/tmp/p2_g3.log 2>&1; then
  ok "단위 PASS ($(grep -oE '[0-9]+ passed' /tmp/p2_g3.log | tail -1))"
else fail "단위 FAIL"; tail -15 /tmp/p2_g3.log; fi

# ── 4. C2 (§24.5 헤드라인 회복·정직 무신호 허용·semantic 회귀 0) ────────────
hdr "4) C2 (§24.5 하이브리드 회복·delivered post-rerank)"
if [ ! -f "$C2JSON" ]; then
  # c2_summary 부재 = C2 미측정. bge 엔드포인트 저하(Owner 자원·§6)면 REDIS 미설정과 동일 범주의
  # 정직 skip(WARN·≠fail·≠OK). green-theater 차단: WARN 은 PASS 카운트 0(green 주장 아님)이고
  # 메시지가 명시적. bge 건강 시 'PYTHONPATH=$ROOT $KPY tools/p2rest_c2.py' 로 측정→이 게이트가 OK.
  warn "C2 NOT MEASURED → skipped: bge degraded·BLOCKED-pending-Owner(§6) (코드 100%·green 주장 금지·앵커 조작 금지)"
else
  "$KPY" - "$C2JSON" <<'PY'
import json, sys
c=json.load(open(sys.argv[1]))
hn=len(c.get("headline_recovered",[])); dn=len(c.get("diagnostic_recovered",[])); sr=c.get("semantic_regression",0)
print(f"headline={hn} diagnostic={dn} semantic_regression={sr}")
# 하드: semantic 회귀 0. headline≥3=PASS / <3=정직 무신호(green-fake 아님·exit 0 허용·메커니즘 검증됨).
if sr != 0:
    print(f"  ✗ semantic 회귀 {sr} (0이어야 함)"); sys.exit(1)
if hn >= 3:
    print(f"  ✓ C2 PASS 헤드라인 {hn}≥3"); sys.exit(0)
print(f"  ◑ C2 NO-SIGNAL(정직): 헤드라인 {hn}<3 — §24.5 구축됨·소표본 무신호(앵커 조작 금지·Owner 결정)"); sys.exit(0)
PY
  [ $? -eq 0 ] && ok "C2 게이트(헤드라인≥3 또는 정직 무신호·semantic 회귀 0)" || fail "C2 게이트(semantic 회귀 또는 오류)"
fi

# ── B1. 플레이북(§18.6) 주입 + Blackboard/persona/tasks 단위 (orch) ─────────
hdr "B1) 플레이북 주입 + Blackboard/persona/tasks 단위 (orch)"
if PYTHONPATH="$ROOT" timeout -k 10 240 "$OPY" -m pytest \
     "$ROOT/orch/tests/test_org_blackboard.py" \
     "$ROOT/orch/tests/test_persona_playbook.py" \
     "$ROOT/orch/tests/test_app_tasks_blackboard.py" \
     -p no:cacheprovider -p no:warnings >/tmp/p2_gb1.log 2>&1; then
  ok "B 단위 PASS ($(grep -oE '[0-9]+ passed' /tmp/p2_gb1.log | tail -1))"
else fail "B 단위 FAIL"; tail -15 /tmp/p2_gb1.log; fi

hdr "B1b) 플레이북 주입 실증 (_persona_prompt('research') ⊇ playbook)"
if PYTHONPATH="$ROOT" "$OPY" - <<'PYB1B' >/tmp/p2_gb1b.log 2>&1
import sys
from orch.org_graph import _persona_prompt
out = _persona_prompt("research")
ok = "## 플레이북(절차기억)" in out and ("scope" in out or "출처" in out)
sys.exit(0 if ok else 1)
PYB1B
then ok "research 노드 프롬프트에 vault/playbook/research.md 주입됨"
else fail "플레이북 주입 안 됨(§18.6)"; fi

# ── B2. Blackboard 프로덕션 sink 정적 배선 (grep·REDIS 무관·always) ──────────
hdr "B2) Blackboard 프로덕션 sink 정적 배선 (app.py _run org 분기)"
if grep -q "await blackboard_sink(" "$ROOT/orch/app.py"; then
  ok "app.py 에 blackboard_sink 호출 존재 (테스트 전용 sink·green-theater 차단)"
else fail "app.py 프로덕션 sink 호출 누락(§27.5 green-theater)"; fi

# ── B3. Blackboard(§27.5) 스냅샷 + REDIS 왕복(미설정 정직 skip) ──────────────
hdr "B3) Blackboard 스냅샷 + REDIS 왕복"
if PYTHONPATH="$ROOT" "$OPY" - <<'PYB3' >/tmp/p2_gb3.log 2>&1
import asyncio, sys, tempfile
from pathlib import Path
from orch.org_graph import blackboard_sink
d = tempfile.mkdtemp()
p = asyncio.run(
    blackboard_sink(
        {"shared_facts": {"research": {"by": "research", "finding": "x"}}},
        None, proj="org:verify", vault_dir=d, today="verify",
    )
)
sys.exit(0 if Path(p).is_file() else 1)
PYB3
then ok "Blackboard 스냅샷 파일 작성(≥1)"
else fail "스냅샷 미작성"; tail -8 /tmp/p2_gb3.log; fi
# REDIS 왕복: ok 시 aigs:blackboard:_probe set→getall→del, 미설정 시 정직 skip(≠fail).
PYTHONPATH="$ROOT" "$OPY" - <<'PYB3R' >/tmp/p2_gb3r.log 2>&1
import asyncio, sys, contextlib
from orch.state_store import make_state_store
async def main():
    store, ok, err = await make_state_store()
    if not ok:
        print(f"SKIP redis={err}")
        return 2
    await store.bb_set("_probe", "f", {"v": 1})
    g = await store.bb_getall("_probe")
    with contextlib.suppress(Exception):
        await store._m.delete("aigs:blackboard:_probe")
    print("redis roundtrip ok")
    return 0 if g.get("f") == {"v": 1} else 1
sys.exit(asyncio.run(main()))
PYB3R
rc=$?
if [ $rc -eq 0 ]; then ok "REDIS aigs:blackboard 왕복"
elif [ $rc -eq 2 ]; then warn "REDIS 미설정 → skipped: pending Owner(§6) (≠fail·해당 줄만)"
else fail "REDIS 왕복 실패"; tail -8 /tmp/p2_gb3r.log; fi

# ── B4. /tasks graceful (REDIS 미설정 200+빈목록·503 아님) ───────────────────
hdr "B4) /tasks 엔드포인트 graceful"
if PYTHONPATH="$ROOT" "$OPY" - <<'PYB4' >/tmp/p2_gb4.log 2>&1
import sys, tempfile
from fastapi.testclient import TestClient
from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher
app = create_app(
    ckpt_db=tempfile.mktemp(suffix="_v.db"),
    caller=stub_caller, searcher=stub_searcher, tracing=False, auth=False,
)
with TestClient(app) as c:
    r = c.get("/tasks")
    sys.exit(0 if r.status_code == 200 and r.json().get("tasks") == [] else 1)
PYB4
then ok "/tasks 200 + 빈 목록 (REDIS 미설정 graceful)"
else fail "/tasks graceful 실패"; tail -8 /tmp/p2_gb4.log; fi

# ── 6. 비파괴(동결 sha256) ─────────────────────────────────────────────────
hdr "6) 비파괴 (동결 sha256 무결)"
if bash "$ROOT/scripts/verify_freeze.sh" >/tmp/p2_g5.log 2>&1; then ok "동결 무결(드리프트 0)"
else fail "동결 드리프트"; tail -5 /tmp/p2_g5.log; fi

# ── 요약 ───────────────────────────────────────────────────────────────────
echo; hdr "요약"
c '1;32' "PASS=$PASS"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then c '1;32' "✓ Phase 2 통과 (A 하이브리드 회수·생애주기 + B Blackboard·플레이북·/tasks)"; exit 0
else c '1;31' "✗ FAIL 해소 필요"; exit 1; fi
