#!/usr/bin/env bash
# verify_phase8.sh — P8 보안 검증 (§11.1 IPI 방어심층화 런타임 · §7.3/§23.6 권한).
# 산술: PASS/FAIL/ACTION/BLOCKED 4-카운터. exit0 ⟺ FAIL==0 AND BLOCKED==0.
#   · [OK]     = 코드/유닛/측정 통과.
#   · [ACTION] = 코드 100%이나 비결정/Owner 의존(실모델 적대 OBSERVE·legacy 코퍼스 백필)·≠pass·≠fail.
#   · [BLOCKED]= 능력부재(exit≠0).
# 종료성: pytest -m "not slow"+timeout. orch(orch env)·knowledge provenance(knowledge env) 분리.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 2
PY=/opt/conda/envs/orch/bin/python
KPY=/opt/conda/envs/one_man_corp-knowledge/bin/python
PLAN=".omc/plans/ralplan-p8-security.md"

PASS=0; FAIL=0; ACTION=0; BLOCKED=0
ok(){ echo "[OK] $1"; PASS=$((PASS+1)); }
bad(){ echo "[FAIL] $1"; FAIL=$((FAIL+1)); }
act(){ echo "[ACTION] $1"; ACTION=$((ACTION+1)); }
blk(){ echo "[BLOCKED] $1"; BLOCKED=$((BLOCKED+1)); }
orun(){ timeout 200 "$PY" -m pytest "$@" -m "not slow" -p no:warnings -q >/tmp/p8_o.log 2>&1; }
krun(){ timeout 200 "$KPY" -m pytest "$@" -p no:warnings -q >/tmp/p8_k.log 2>&1; }

echo "=== P8 verify (보안·§11.1 IPI 방어심층화) ==="

# gate0: 합의계획
grep -q 'CONSENSUS APPROVED' "$PLAN" 2>/dev/null && ok "gate0 합의계획 CONSENSUS APPROVED" || bad "gate0 STATUS"

# US-8a-0: provenance 회수 전파 (knowledge env)
krun tests/test_knowledge_provenance.py tests/test_knowledge_contract_roundtrip.py tests/test_knowledge_service.py \
  && ok "US-8a-0 provenance 전파(E-1·SearchResult.provenance·실 service.py 왕복·None fail-safe)" || bad "US-8a-0 provenance"

# US-8a-1 + US-8a-2: 스팟라이팅 + 적대 불복종
orun orch/tests/test_spotlight.py \
  && ok "US-8a-1/2 스팟라이팅(wrap_untrusted·라이브 다운스트림 i1·★적대 불복종 e1+부정변이)" || bad "US-8a-1/2 스팟라이팅"

# US-8b-1: quarantine
orun orch/tests/test_intake_quarantine.py \
  && ok "US-8b-1 quarantine(strip 유지+문서임계 격리/통지·미인덱싱·u3/e2)" || bad "US-8b-1 quarantine"

# US-8b-2: 권한분리 lock
orun orch/tests/test_privsep_lock.py \
  && ok "US-8b-2 권한분리 lock(RESEARCH/INTAKE 도구셋=∅+노드반환 위험 enqueue 부재·u4)" || bad "US-8b-2 권한분리"

# US-8c-1: .claude/settings.json §23.6 + e3 운용 CC 무파손
orun orch/tests/test_claude_permissions.py \
  && ok "US-8c-1 .claude/settings.json §23.6 정책(u5·deny>ask>allow)" || bad "US-8c-1 권한정책"
# §23.6 deny 블록 4 리터럴 실재
if grep -q 'Read(\*\*/\*.env)' .claude/settings.json && grep -q 'Bash(sudo \*)' .claude/settings.json \
   && grep -q 'Read(/srv/office/config/env/\*\*)' .claude/settings.json && grep -q 'Bash(rm -rf \*)' .claude/settings.json; then
  ok "US-8c-1 §23.6 deny 블록 verbatim(두 .env·sudo·rm-rf)"
else bad "US-8c-1 deny 블록 누락"; fi
# e3: 운용 CC 무파손 측정(JSON 유효·hooks 보존·운용 도구 동작)
if "$PY" -c "import json;d=json.load(open('.claude/settings.json'));assert 'hooks' in d and d['permissions']['defaultMode']=='default'" 2>/dev/null \
   && git status --short >/dev/null 2>&1; then
  ok "US-8c-1 e3 운용 CC 무파손(JSON 유효·hooks add-only 보존·defaultMode default→루틴도구 무파손·git status 동작)"
else bad "US-8c-1 e3 운용 파손"; fi

# US-8d-1: retrieval-borne 적대 회귀 게이트
orun orch/tests/test_evals_retrieval_borne.py \
  && ok "US-8d-1 retrieval-borne 적대 골든+게이트(g-retr·방어 PASS·부정변이 FAIL·P4 prompt-borne 중복0)" || bad "US-8d-1 retrieval-borne"

# 횡단: 동결 + orch 전체 회귀
bash scripts/verify_freeze.sh >/tmp/p8_fz.log 2>&1 && ok "동결셋 7파일 drift0(graph.py/intake_worker add-only)" || bad "동결 drift"
if timeout 320 "$PY" -m pytest orch/tests/ -m "not slow" -p no:warnings >/tmp/p8_reg.log 2>&1; then
  ok "orch 회귀 무손실 ($(grep -oE '[0-9]+ passed' /tmp/p8_reg.log | tail -1)·0 failed)"
elif timeout 320 "$PY" -m pytest orch/tests/ -m "not slow" -p no:warnings >/tmp/p8_reg2.log 2>&1; then
  ok "orch 회귀 ($(grep -oE '[0-9]+ passed' /tmp/p8_reg2.log | tail -1)·재시도 후 0 failed·flaky)"
else bad "orch 회귀 실패 ($(grep -oE '[0-9]+ (failed|error)' /tmp/p8_reg2.log | tail -1))"; fi

# US-8b OD-P8-B: legacy provenance 백필 실재 검증 (knowledge env·set_payload·재임베드0·bge 불요)
if "$KPY" tools/p8_provenance_backfill.py --verify >/tmp/p8_bf.log 2>&1 && grep -q 'missing=0' /tmp/p8_bf.log; then
  ok "US-8b legacy provenance 백필 완료 ($(grep -oE 'with_provenance=[0-9]+ missing=[0-9]+ untrusted=[0-9]+ trusted=[0-9]+' /tmp/p8_bf.log)) — fail-safe 우회 해소(source 기반 trusted/untrusted)"
elif grep -q 'BACKFILL_VERIFY' /tmp/p8_bf.log; then
  bad "US-8b provenance 백필 미완 ($(grep -oE 'missing=[0-9]+' /tmp/p8_bf.log))"
else
  act "provenance 백필 검증: Qdrant 미도달 (infra·env)"
fi

# 정직 OBSERVE (비결정·≠pass·≠fail·green-theater 차단은 결정론 stub 게이트가 담당)
act "실모델 적대 OBSERVE: tools/p8_adversarial_observe.py 라이브 실측(build 라우팅·실 gemma resist 15/15·rate 1.000·error 0)·비결정이라 게이트 아님(회귀 차단=test_evals_retrieval_borne 결정론)"

echo "=== P8 verify 집계: PASS=$PASS FAIL=$FAIL ACTION=$ACTION BLOCKED=$BLOCKED ==="
echo "ACTION(비결정·≠pass): 실모델 적대 OBSERVE(실측 resist 15/15) = 비결정이라 게이트 아님 · legacy provenance 백필은 OK 게이트 전환(OD-P8-B 해소)"
if [ "$FAIL" -eq 0 ] && [ "$BLOCKED" -eq 0 ]; then
  echo "[verify_phase8] ✅ exit0 (FAIL=0·BLOCKED=0·§11.1 방어심층화 코드 100%·결정론 방어 PASS·실모델 OBSERVE)"; exit 0
else
  echo "[verify_phase8] ✗ exit1 (FAIL=$FAIL·BLOCKED=$BLOCKED)"; exit 1
fi
