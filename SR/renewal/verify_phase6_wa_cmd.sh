#!/usr/bin/env bash
# AIGS Phase 6 부분검증 — P6d(WA 6브릭 배선) + P6e(Discord `/cmd`) [writer 레인].
# ★범위: 이 스크립트는 P6d+P6e 만 검증한다(P6a API·P6b 8상태·P6c 패널은 별도 레인/별도 게이트).
#   전체 verify_phase6.sh 는 오케스트레이터가 이 게이트들을 합성한다.
# verify_phase5.sh 골격 계승(ok/fail/act). ✔=실제 실행만·green-fake 0·AGPL 비수정 단언.
#   1  P6e `/cmd` 단위(파서·매핑·미지거부·401 재인증) + non-primary sync 미호출 PASS
#   2  P6e NL 무회귀(classify_intent stage/ambiguous/chat 불변 + on_message byte 보존)
#   3  P6e 전체 discord 회귀(bounded·hang0) — bot/commands/mapping/cursor/auth/rest/sse/nick
#   4  P6d WA 유닛 AGPL 비수정 단언(upstream 이미지 핀 grep + 로컬 build 컨텍스트 0)
#   5  P6d WA Quadlet 정적 파싱(podman quadlet -dryrun EXIT0·9유닛) — 가능 시
#   6  P6d §9.2 포트모델 env 템플릿(내부↔외부 분리·PUSHER_URL 끝슬래시 제거·traefik/PathPrefix 0)
#   7  P6d WA 프로브 정적(이미지 핀 + 라이브 PENDING) — pull 은 [ACTION](별도·바운드)
# 정직(green-theater 차단): 라이브 아바타 입장 = [ACTION] pending Owner(START_ROOM_URL+도메인+TLS·≠pass).
# 사용: SR/renewal/verify_phase6_wa_cmd.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DISCORD_PY="${DISCORD_PY:-/opt/conda/envs/one_man_corp-discord/bin/python}"
[ -x "$DISCORD_PY" ] || { echo "[WARN] discord python 미발견 → orch"; DISCORD_PY="/opt/conda/envs/orch/bin/python"; }
[ -x "$DISCORD_PY" ] || DISCORD_PY="python"
QUADLET="${QUADLET:-/usr/libexec/podman/quadlet}"
WA_DIR="$ROOT/deploy/systemd/wa"
PASS=0; FAIL=0; ACTION=0
c(){ printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok(){     c '1;32' "[ OK ] $*"; PASS=$((PASS+1)); }
fail(){   c '1;31' "[FAIL] $*"; FAIL=$((FAIL+1)); }
act(){    c '1;33' "[ACTION] $*"; ACTION=$((ACTION+1)); }
hdr(){    c '1;34' "── $* ─────────────────────────────"; }

# ── 1. P6e /cmd 단위 + non-primary sync 미호출 ──────────────────────────────────
hdr "1) P6e /cmd 단위(파서·매핑·미지거부·401) + non-primary sync 미호출"
if PYTHONPATH="$ROOT" timeout -k 5 120 "$DISCORD_PY" -m pytest \
     "$ROOT/tests/test_discord_slash_cmd.py" \
     -p no:cacheprovider -q >/tmp/p6_g1.log 2>&1; then
  ok "/cmd 단위 PASS ($(grep -oE '[0-9]+ passed' /tmp/p6_g1.log | tail -1))"
else fail "/cmd 단위 FAIL"; tail -25 /tmp/p6_g1.log; fi

# ── 2. P6e NL 무회귀 ────────────────────────────────────────────────────────────
hdr "2) P6e NL 무회귀(classify_intent 불변 + on_message byte 보존)"
if PYTHONPATH="$ROOT" "$DISCORD_PY" - >/tmp/p6_g2.log 2>&1 <<'PY'
import inspect
from adapters.discord.commands import classify_intent
from adapters.discord.bot import DiscordBot
# NL 분류 회귀 불변.
assert classify_intent("게임 밸런스 작업 시작해줘").kind == "stage"
assert classify_intent("게임 만들어줘").kind == "ambiguous"
assert classify_intent("오늘 날씨 어때?").kind == "chat"
assert classify_intent("게임 디자인 어떻게 할까?").kind == "chat"
# on_message 본문에 슬래시 침투 0(NL 경로 byte 보존).
src = inspect.getsource(DiscordBot.on_message)
assert "classify_intent" in src, "on_message NL 분류기 사라짐"
for forbidden in ("tree", "/cmd", "app_command"):
    assert forbidden not in src, f"on_message 에 {forbidden} 침투(NL 경로 오염)"
print("NL 무회귀 OK (분류 불변 + on_message byte 보존)")
PY
then ok "NL 무회귀(classify_intent stage/ambiguous/chat + on_message 슬래시 침투 0)"
else fail "NL 무회귀 FAIL"; cat /tmp/p6_g2.log; fi

# ── 3. P6e 전체 discord 회귀(bounded·hang0) ─────────────────────────────────────
hdr "3) P6e 전체 discord 회귀(bounded·hang0)"
if PYTHONPATH="$ROOT" timeout -k 5 120 "$DISCORD_PY" -m pytest \
     "$ROOT/tests/test_discord_bot.py" "$ROOT/tests/test_discord_commands.py" \
     "$ROOT/tests/test_discord_mapping.py" "$ROOT/tests/test_discord_cursor.py" \
     "$ROOT/tests/test_discord_auth.py" "$ROOT/tests/test_discord_rest_client.py" \
     "$ROOT/tests/test_discord_sse_client.py" "$ROOT/tests/test_set_bot_nicknames.py" \
     "$ROOT/tests/test_discord_slash_cmd.py" \
     -p no:cacheprovider -q >/tmp/p6_g3.log 2>&1; then
  ok "discord 전체 회귀 PASS ($(grep -oE '[0-9]+ passed' /tmp/p6_g3.log | tail -1))·hang 0"
else fail "discord 회귀 FAIL(또는 hang)"; tail -25 /tmp/p6_g3.log; fi

# ── 4. P6d WA 유닛 AGPL 비수정 단언 ─────────────────────────────────────────────
hdr "4) P6d WA AGPL 비수정(upstream 이미지 핀 grep + 로컬 build 컨텍스트 0)"
r4=0
# (a) workadventure-*:<tag> 이미지 핀이 .container 에 존재.
if grep -rqE 'Image=docker\.io/thecodingmachine/workadventure-[a-z-]+:v[0-9]' "$WA_DIR"/*.container 2>/dev/null; then
  :
else fail "4 WA upstream 이미지 핀(workadventure-*:v…) 미발견"; r4=1; fi
# (b) 로컬 build 컨텍스트(소스 빌드) 부재 — AGPL §13 비수정.
if grep -rniE '^\s*Build\s*=|build:' "$WA_DIR"/*.container 2>/dev/null; then
  fail "4 ★AGPL 위반: WA .container 에 로컬 build 컨텍스트 발견(소스 빌드=§13 트리거)"; r4=1
fi
# (c) AGPL_COMPLIANCE.md 존재 + 결정①(트리거 미발생) 기록.
if [ -f "$ROOT/SR/renewal/AGPL_COMPLIANCE.md" ] && \
   grep -q "트리거.*미발생\|미발생.*트리거" "$ROOT/SR/renewal/AGPL_COMPLIANCE.md" 2>/dev/null; then
  :
else fail "4 AGPL_COMPLIANCE.md 결정①(트리거 미발생) 기록 없음"; r4=1; fi
[ "$r4" = 0 ] && ok "WA AGPL 비수정(upstream 핀·로컬 build 0·AGPL_COMPLIANCE 결정① 기록)"

# ── 5. P6d WA Quadlet 정적 파싱 ─────────────────────────────────────────────────
hdr "5) P6d WA Quadlet 정적 파싱(podman quadlet -dryrun)"
if [ -x "$QUADLET" ]; then
  TMPQ="$(mktemp -d)"; cp "$WA_DIR"/*.container "$WA_DIR"/*.network "$WA_DIR"/*.volume "$TMPQ"/ 2>/dev/null
  if QUADLET_UNIT_DIRS="$TMPQ" timeout -k 5 30 "$QUADLET" -dryrun -user >/tmp/p6_g5.log 2>&1 \
       && ! grep -iqE 'error|invalid|unknown key' /tmp/p6_g5.log; then
    n=$(grep -cE '^---.*\.service---' /tmp/p6_g5.log)
    ok "Quadlet -dryrun EXIT0·에러 0 ($n 서비스 생성)"
  else fail "Quadlet 파싱 실패/에러"; tail -20 /tmp/p6_g5.log; fi
  rm -rf "$TMPQ"
else
  act "podman quadlet 제너레이터 미발견 — code 100%/runtime pending: podman quadlet"
fi

# ── 6. P6d §9.2 포트모델 env 템플릿 ─────────────────────────────────────────────
hdr "6) P6d §9.2 포트모델 env(내부↔외부 분리·끝슬래시 제거·traefik/PathPrefix 0)"
ENVP="$WA_DIR/wa-play.env.example"
r6=0
[ -f "$ENVP" ] || { fail "6 wa-play.env.example 부재"; r6=1; }
if [ -f "$ENVP" ]; then
  # 내부 URL(컨테이너 DNS) 존재.
  grep -q '^API_URL=back:50051' "$ENVP" || { fail "6 내부 API_URL=back:50051 누락"; r6=1; }
  grep -q '^INTERNAL_MAP_STORAGE_URL=http://map-storage:3000' "$ENVP" || { fail "6 INTERNAL_MAP_STORAGE_URL 누락"; r6=1; }
  # 외부 URL(절대·포트별)이 존재하고 ★끝슬래시 없음.
  if grep -qE '^PUSHER_URL=https://[^/]+:[0-9]+$' "$ENVP"; then :; else fail "6 PUSHER_URL 포트절대URL·끝슬래시 제거 위반"; r6=1; fi
  # traefik/PathPrefix/stripprefix 활성 설정 0(포트모델 전환·결정③). ★주석(# …·"제거" 설명)은
  # 제외 — 활성 설정 라인(Label/labels/traefik.* 지시)만 위반으로 본다.
  if grep -rhE 'traefik|PathPrefix|stripprefix' "$WA_DIR"/*.container "$WA_DIR"/*.network "$WA_DIR"/*.env.example 2>/dev/null \
       | grep -vE '^\s*#' | grep -qE 'traefik|PathPrefix|stripprefix'; then
    fail "6 ★traefik/PathPrefix/stripprefix 활성 설정 발견(결정③ 포트모델 위반)"; r6=1
  fi
fi
[ "$r6" = 0 ] && ok "§9.2 포트모델(내부 back:50051·외부 끝슬래시 제거·traefik/PathPrefix 0)"

# ── 7. P6d WA 프로브 정적 + 라이브 PENDING ──────────────────────────────────────
hdr "7) P6d WA 프로브 정적(이미지 핀) + 라이브 입장 ACTION"
if PYTHONPATH="$ROOT" timeout -k 5 30 "$DISCORD_PY" "$ROOT/tools/p6_wa_probe.py" >/tmp/p6_g7.log 2>&1; then
  ok "p6_wa_probe 정적 PASS(이미지 핀 OK·라이브 pending 정직표기)"
else fail "p6_wa_probe 정적 FAIL"; tail -20 /tmp/p6_g7.log; fi
# 라이브 아바타 입장 = Owner-gated(≠pass).
act "라이브 아바타 입장 e2e — code 100% / runtime pending Owner: START_ROOM_URL+공개도메인+TLS"
act "이미지 pull + 브릭 gRPC/pub-sub 핸드셰이크 — 바운드 별도 실행(p6_wa_probe.py --pull·결정④)"

# ── 요약 ────────────────────────────────────────────────────────────────────────
echo
hdr "요약 (P6d+P6e·writer 레인)"
c '1;36' "PASS=$PASS  FAIL=$FAIL  ACTION(pending Owner)=$ACTION"
echo "출력 규약: [OK]/[FAIL]=집계·exit code / [ACTION]=pending Owner(≠fail·≠pass·미합산)."
[ "$FAIL" -eq 0 ] && { c '1;32' "exit0 = P6d+P6e 좌열 전부 PASS (라이브 입장은 Owner-gated ACTION)"; exit 0; }
c '1;31' "FAIL>0 — 위 [FAIL] 확인"; exit 1
