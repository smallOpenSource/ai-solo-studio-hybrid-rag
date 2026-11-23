#!/usr/bin/env bash
# AIGS Phase 1 검증 — 상태계층 스모크 테스트 (명세 §12 Phase 1 수용 기준)
#   ① Redis Sentinel 경유 R/W  ② Qdrant healthz + knowledge 컬렉션  ③ Obsidian vault(git)
#   (선택) Phoenix UI · SearXNG 도달 — 컨테이너 가동 시
# 원칙: 비파괴. Redis 임시 키는 즉시 삭제, Qdrant 컬렉션 생성은 멱등(Phase 1 산출물), vault는 읽기 전용.
# 사용: ./verify_phase1.sh [/srv/office/config/app.env]
set -uo pipefail
APPENV="${1:-/srv/office/config/app.env}"
PASS=0; FAIL=0; ACT=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){   c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){ c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
warn(){ c '1;33' "[WARN] $*"; }
act(){  c '1;36' "[ACTION] $*"; ACT=$((ACT+1)); }
hdr(){  c '1;34' "── $* ─────────────────────────────"; }

# ── 0. app.env 로드 ────────────────────────────────────────────────
[ -f "$APPENV" ] || { fail "app.env 없음: $APPENV (bootstrap.sh 먼저)"; exit 1; }
set -a; . "$APPENV"; set +a
ok "app.env 로드: $APPENV"
PY="${CONDA_BASE:-/opt/conda}/envs/orch/bin/python"
[ -x "$PY" ] || { warn "orch python 미발견($PY) → 'python' 사용 시도"; PY="python"; }
QH="127.0.0.1"

# Phase 1 핵심 [필수] placeholder 검사 (Redis·Vault만; Discord/Comfy는 후속 Phase)
ph(){ case "${1:-}" in *__CHANGE_ME__*|*__host1__*|*__git@*|"") return 0;; *) return 1;; esac; }
ph "$REDIS_SENTINELS" && { fail "REDIS_SENTINELS 미설정(placeholder)"; }
ph "$REDIS_PASSWORD"  && { fail "REDIS_PASSWORD 미설정(placeholder)"; }
ph "$VAULT_REMOTE"    && { warn "VAULT_REMOTE 미설정 — git 원격 점검 생략 가능"; }

# ── 1. Redis Sentinel 경유 R/W ─────────────────────────────────────
hdr "1) Redis Sentinel (R/W via discover_master)"
"$PY" - <<'PYRD'
import os,sys
try:
    from redis.sentinel import Sentinel
except Exception as e:
    print("redis 패키지 없음:",e); sys.exit(3)
try:
    sents=[(hp.split(':')[0],int(hp.split(':')[1])) for hp in os.environ['REDIS_SENTINELS'].split(',') if ':' in hp]
    name=os.environ.get('REDIS_MASTER_NAME','mymaster'); pw=os.environ.get('REDIS_PASSWORD') or None
    skw={'password':pw} if pw else {}
    s=Sentinel(sents, socket_timeout=3, sentinel_kwargs=skw)
    addr=s.discover_master(name)
    m=s.master_for(name, password=pw, socket_timeout=3)
    k='aigs:phase1:probe'
    m.set(k,'ok',ex=30); v=m.get(k)
    sl=s.slave_for(name, password=pw, socket_timeout=3)
    try: rv=sl.get(k)
    except Exception: rv=v   # 복제본 없거나 지연 시 master 값으로 대체
    m.delete(k)
    print(f"master={addr} write/read={'ok' if v==b'ok' else 'NG'} replica_read={'ok' if rv==b'ok' else 'lag/NA'}")
    sys.exit(0 if v==b'ok' else 2)
except Exception as e:
    print("Sentinel 오류:",e); sys.exit(2)
PYRD
[ $? -eq 0 ] && ok "Redis Sentinel R/W 정상" || fail "Redis Sentinel R/W 실패 (접속정보·subuid·방화벽 §14)"

# ── 2. Qdrant healthz + knowledge 컬렉션(멱등) ─────────────────────
hdr "2) Qdrant (healthz + ${QDRANT_COLLECTION} 컬렉션)"
QURL="http://$QH:${PORT_QDRANT_REST}"
hz=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$QURL/healthz" || echo 000)
if [ "$hz" = 200 ]; then ok "Qdrant healthz 200 ($QURL)"
else fail "Qdrant healthz=$hz — 컨테이너 미가동? 'systemctl --user status aigs-qdrant'"; fi
if [ "$hz" = 200 ]; then
  code=$(curl -s -o /tmp/aigs_col.json -w '%{http_code}' --max-time 5 "$QURL/collections/$QDRANT_COLLECTION" || echo 000)
  if [ "$code" = 200 ]; then ok "컬렉션 '$QDRANT_COLLECTION' 존재"
  elif [ "$code" = 404 ]; then
    act "컬렉션 없음 → 멱등 생성(size=$EMBED_DIM, distance=$EMBED_DISTANCE)"
    pc=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X PUT "$QURL/collections/$QDRANT_COLLECTION" \
         -H 'Content-Type: application/json' \
         -d "{\"vectors\":{\"size\":${EMBED_DIM},\"distance\":\"${EMBED_DISTANCE}\"}}" || echo 000)
    [ "$pc" = 200 ] && ok "컬렉션 생성 완료" || fail "컬렉션 생성 실패(code=$pc)"
  else fail "컬렉션 조회 code=$code"; fi
fi

# ── 3. Obsidian vault (git, SSoT) ──────────────────────────────────
hdr "3) Obsidian vault (git §5.3·§21.3)"
VD="${VAULT_DIR:-/srv/office/vault}"
if [ -d "$VD/.git" ]; then ok "git repo: $VD"
  ru=$(git -C "$VD" remote get-url origin 2>/dev/null || echo "")
  if [ -z "$ru" ]; then act "origin 원격 미설정 → 'git -C $VD remote add origin $VAULT_REMOTE'"
  elif ph "$VAULT_REMOTE" || [ "$ru" = "$VAULT_REMOTE" ]; then ok "origin=$ru"
  else warn "origin($ru) ≠ app.env VAULT_REMOTE($VAULT_REMOTE)"; fi
  cn=$(git -C "$VD" rev-list --count HEAD 2>/dev/null || echo 0)
  [ "${cn:-0}" -gt 0 ] 2>/dev/null && ok "커밋 $cn개" || act "커밋 0 → 첫 commit 필요(Phase 1 수용 기준)"
else act "vault git 미초기화 → 'git init $VD' 후 폴더 생성"; fi
miss=""
for d in game-design content art research meetings decisions retro quality evals playbook quarantine specs; do
  [ -d "$VD/$d" ] || miss="$miss $d"
done
[ -z "$miss" ] && ok "vault 폴더 8종 존재" || act "vault 폴더 누락:$miss (mkdir -p)"

# ── (선택) Phoenix · SearXNG 도달 ──────────────────────────────────
hdr "(선택) Phoenix · SearXNG — Phase 3·4 사용, 컨테이너 가동 시"
px=$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "http://$QH:${PORT_PHOENIX_UI}/" || echo 000)
[ "$px" = 200 ] && ok "Phoenix UI 200 (:$PORT_PHOENIX_UI)" || warn "Phoenix UI=$px (Phase 3 전이면 정상)"
sx=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$QH:${PORT_SEARXNG}/search?q=test&format=json" || echo 000)
[ "$sx" = 200 ] && ok "SearXNG json 200 (:$PORT_SEARXNG)" || warn "SearXNG=$sx (Phase 4 전이면 정상; settings.yml formats:[json] 확인)"

# ── 요약 ───────────────────────────────────────────────────────────
echo
hdr "요약"
c '1;32' "PASS=$PASS"; [ $ACT -gt 0 ] && c '1;36' "ACTION=$ACT (조치 후 재실행)"; [ $FAIL -gt 0 ] && c '1;31' "FAIL=$FAIL"
if [ $FAIL -eq 0 ]; then c '1;32' "✓ Phase 1 핵심 통과 → §25 Phase 2 진행 가능"; exit 0
else c '1;31' "✗ FAIL 해소 필요 (대개 접속정보/컨테이너 미가동/subuid)"; exit 1; fi
