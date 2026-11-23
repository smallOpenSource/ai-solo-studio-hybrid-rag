#!/usr/bin/env bash
# SR/renewal Phase 1 검증 (Path A·repo 루트) — 상태계층 스모크. 비파괴(임시키 즉시삭제·읽기 위주).
# 적응: 스펙 §23.10(/srv/office·redis-cli 가정)을 현 프로젝트 컴포넌트로 — state_store 프로브·
# Qdrant 서버(2811)·vault git·config/app.env. ① Redis ② Qdrant ③ Vault ④ app.env ⑤ 관찰성(옵션).
set -u
ROOT=/opt/studio
ENVPY=/opt/conda/envs/orch/bin/python
fail=0
ok() { echo "  ✓ $1"; }
no() {
  echo "  ✗ $1"
  fail=1
}

echo "== ① Redis Sentinel R/W (aigs:* · db 격리 · FLUSH 0) =="
if PYTHONPATH=$ROOT $ENVPY "$ROOT/tools/state_store_probe.py" >/dev/null 2>&1; then
  ok "state_store master/replica R/W roundtrip"
else
  no "state_store R/W"
fi

echo "== ② Qdrant 서버 (readyz + knowledge 컬렉션) =="
[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:2811/readyz)" = 200 ] && ok "qdrant readyz 200" || no "qdrant readyz"
curl -s --max-time 4 http://127.0.0.1:2811/collections | grep -q knowledge && ok "knowledge 컬렉션 존재" || no "knowledge 컬렉션"

echo "== ③ Vault git (SSoT · 12 폴더 · ≥1 commit) =="
[ "$(git -C "$ROOT/vault" log --oneline 2>/dev/null | wc -l)" -ge 1 ] && ok "vault ≥1 commit" || no "vault commit"
[ "$(ls -d "$ROOT"/vault/*/ 2>/dev/null | wc -l)" -ge 12 ] && ok "vault 12 폴더" || no "vault 폴더"

echo "== ④ app.env SoT (chmod 600 · gitignored) =="
[ "$(stat -c %a "$ROOT/config/app.env" 2>/dev/null)" = 600 ] && ok "app.env chmod 600" || no "app.env perms"
git -C "$ROOT" check-ignore config/app.env >/dev/null 2>&1 && ok "app.env gitignored" || no "app.env tracked!"
[ -f "$ROOT/config/ports.json" ] && $ENVPY -c "import json;json.load(open('$ROOT/config/ports.json'))" 2>/dev/null && ok "ports.json valid" || no "ports.json"

echo "== ⑤ (옵션) Phoenix · SearXNG 컨테이너 =="
[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:2813/)" = 200 ] && ok "phoenix UI 2813" || echo "  - phoenix(옵션) 미응답"
[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 'http://127.0.0.1:2815/search?q=t&format=json')" = 200 ] && ok "searxng json 2815" || echo "  - searxng(옵션) 미응답"

echo
if [ $fail -eq 0 ]; then
  echo "PHASE 1 검증 PASS ✓"
  exit 0
else
  echo "PHASE 1 검증 FAIL ✗"
  exit 1
fi
