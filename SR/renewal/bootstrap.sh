#!/usr/bin/env bash
# AIGS Phase 0 부트스트랩 — 환경 탐지 + 안전 스캐폴딩 + 단일 app.env 구성·렌더링
# 원칙: 비파괴·멱등. 시크릿/접속정보는 app.env 의 [필수] 항목을 사람이 채운다.
# 참조: 명세 §2·§23. app.env.example 를 이 스크립트와 같은 디렉터리에 두고 실행.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OFFICE=/srv/office; PODMAN_DATA=/srv/podman/data; U="$(id -un)"
CFG="$OFFICE/config"; APPENV="$CFG/app.env"; QUAD="$HOME/.config/containers/systemd"
log(){ printf '\033[1;34m[AIGS]\033[0m %s\n' "$*"; }
ok(){  printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }

# ── 0-A 환경 실태 탐지 (추정 금지) ──────────────────────────────────
log "환경 실태 탐지"
echo " user: $U (uid=$(id -u))"
[ "$(id -u)" -ne 0 ] && ok "non-root" || warn "root 실행 — 비권장"
CONDA_BASE_DET="$(conda info --base 2>/dev/null || true)"
[ -n "$CONDA_BASE_DET" ] && ok "conda base: $CONDA_BASE_DET" || warn "conda 미발견 — 경로 확인"
PV="$(podman --version 2>/dev/null | awk '{print $3}' || true)"
if [ -n "$PV" ]; then
  if [ "$(printf '%s\n4.4\n' "$PV" | sort -V | head -1)" = "4.4" ]; then ok "podman $PV (Quadlet 가능)"
  else warn "podman $PV < 4.4 — Quadlet 불가, 'podman generate systemd' 폴백(§23.2)"; fi
else warn "podman 미발견 — Qdrant/Phoenix/SearXNG 불가, 서버 담당자 확인"; fi
loginctl show-user "$U" 2>/dev/null | grep -q 'Linger=yes' && ok "linger=yes" \
  || warn "linger 미설정 → 'loginctl enable-linger $U' 또는 담당자"
if grep -q "^$U:" /etc/subuid 2>/dev/null && grep -q "^$U:" /etc/subgid 2>/dev/null; then
  ok "subuid/subgid 매핑 존재"
else warn "subuid/subgid 미설정 → rootless Podman 불가, 서버 담당자(§2④)"; fi
command -v nvidia-smi >/dev/null 2>&1 && warn "GPU 감지(앱서버는 GPU 無 가정)" || ok "GPU 없음(정상)"
echo " 사용 중 포트: $(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -oE '[0-9]+$' | sort -un | tr '\n' ' ')"
[ -w /app ] && ok "/app 쓰기 가능" || { warn "/app 쓰기 불가 — 중단"; exit 1; }

# ── 0-B 디렉터리 골격 (멱등) ────────────────────────────────────────
mkdir -p "$OFFICE"/{apps,bin,config,vault,state,intake} "$PODMAN_DATA"/{qdrant,phoenix,searxng}
mkdir -p "$HOME/.config/systemd/user" "$QUAD"
ok "디렉터리 골격: $OFFICE, $PODMAN_DATA"

# ── 0-C 단일 app.env 구성 (example 복사 + RO/RW 토큰 자동 주입) ──────
if [ -f "$APPENV" ]; then ok "app.env 존재(건너뜀): $APPENV"
elif [ -f "$HERE/app.env.example" ]; then
  cp "$HERE/app.env.example" "$APPENV"
  RO="$(python -c 'import secrets;print(secrets.token_urlsafe(32))' 2>/dev/null || echo __CHANGE_ME__)"
  RW="$(python -c 'import secrets;print(secrets.token_urlsafe(32))' 2>/dev/null || echo __CHANGE_ME__)"
  sed -i "s|^RO_TOKEN=__AUTO__|RO_TOKEN=$RO|; s|^RW_TOKEN=__AUTO__|RW_TOKEN=$RW|" "$APPENV"
  chmod 600 "$APPENV"
  ok "app.env 생성(토큰 자동): $APPENV"
  warn "app.env 의 [필수] 항목(COMFY_BASE·REDIS_*·DISCORD_*·VAULT_REMOTE·START_ROOM_URL)을 채우세요"
else warn "app.env.example 미발견 → app.env 수동 작성 필요"; fi

# ── app.env 로드(렌더링에 사용) ─────────────────────────────────────
if [ -f "$APPENV" ]; then set -a; . "$APPENV"; set +a; ok "app.env 로드"; fi

# ── 0-D conda 환경 4종 (기존 베이스 재사용 — 신규 설치 금지) ─────────
if [ -n "$CONDA_BASE_DET" ]; then
  log "conda 환경(orch·discord·art·intake)"
  # shellcheck disable=SC1090
  source "$CONDA_BASE_DET/etc/profile.d/conda.sh"
  for E in orch discord art intake; do
    if conda env list | awk '{print $1}' | grep -qx "$E"; then ok "env $E 존재(건너뜀)"
    else conda create -y -n "$E" python=3.12 >/dev/null 2>&1 && ok "env $E 생성"; fi
  done
else warn "conda base 미확인 — env 생성 건너뜀"; fi

# ── 0-E ports.json 렌더 (app.env → 참조용 레지스트리) ───────────────
if [ -n "${PORT_API:-}" ]; then
  cat > "$CFG/ports.json" <<JSON
{
  "_band": "2810-2829",
  "api": ${PORT_API},
  "qdrant_rest": ${PORT_QDRANT_REST}, "qdrant_grpc": ${PORT_QDRANT_GRPC},
  "phoenix_ui": ${PORT_PHOENIX_UI}, "phoenix_otlp": ${PORT_PHOENIX_OTLP},
  "searxng": ${PORT_SEARXNG},
  "wa_play": ${PORT_WA_PLAY}, "wa_mapstorage": ${PORT_WA_MAPSTORAGE},
  "wa_icon": ${PORT_WA_ICON}, "wa_uploader": ${PORT_WA_UPLOADER}
}
JSON
  ok "ports.json 렌더(app.env 기준)"
else warn "PORT_* 미설정 — ports.json·Quadlet 렌더 생략"; fi

# ── 0-F Quadlet .container 렌더 (Quadlet은 env 치환 불가 → app.env에서 렌더) ──
if [ -n "${PODMAN_BIND:-}" ] && [ -n "${PORT_QDRANT_REST:-}" ]; then
  cat > "$QUAD/aigs-qdrant.container" <<Q
[Unit]
Description=Qdrant (rootless Podman, AIGS)
After=network-online.target
Wants=network-online.target
[Container]
Image=docker.io/qdrant/qdrant:latest
ContainerName=aigs-qdrant
PublishPort=${PODMAN_BIND}:${PORT_QDRANT_REST}:6333
PublishPort=${PODMAN_BIND}:${PORT_QDRANT_GRPC}:6334
Volume=${PODMAN_DATA}/qdrant:/qdrant/storage:Z
[Service]
Restart=always
RestartSec=5
TimeoutStartSec=900
[Install]
WantedBy=default.target
Q
  cat > "$QUAD/aigs-phoenix.container" <<Q
[Unit]
Description=Arize Phoenix (rootless Podman, AIGS)
After=network-online.target
Wants=network-online.target
[Container]
Image=docker.io/arizephoenix/phoenix:latest
ContainerName=aigs-phoenix
PublishPort=${PODMAN_BIND}:${PORT_PHOENIX_UI}:6006
PublishPort=${PODMAN_BIND}:${PORT_PHOENIX_OTLP}:4317
Volume=${PODMAN_DATA}/phoenix:/mnt/data:Z
Environment=PHOENIX_WORKING_DIR=/mnt/data
[Service]
Restart=always
RestartSec=5
TimeoutStartSec=900
[Install]
WantedBy=default.target
Q
  cat > "$QUAD/aigs-searxng.container" <<Q
[Unit]
Description=SearXNG (rootless Podman, AIGS)
After=network-online.target
Wants=network-online.target
[Container]
Image=docker.io/searxng/searxng:latest
ContainerName=aigs-searxng
PublishPort=${PODMAN_BIND}:${PORT_SEARXNG}:8080
Volume=${PODMAN_DATA}/searxng:/etc/searxng:Z
Environment=SEARXNG_BASE_URL=${SEARXNG_URL:-http://127.0.0.1:${PORT_SEARXNG}/}
[Service]
Restart=always
RestartSec=5
TimeoutStartSec=900
[Install]
WantedBy=default.target
Q
  ok "Quadlet 3종 렌더(app.env 기준): $QUAD"
  warn "SearXNG는 settings.yml 도 필요 → $PODMAN_DATA/searxng/settings.yml(§23.5)"
fi

# ── 안내 ────────────────────────────────────────────────────────────
log "다음: app.env [필수] 채움 → 'systemctl --user daemon-reload'"
log "      → 'systemctl --user enable --now aigs-qdrant aigs-phoenix aigs-searxng' (Phase 1)"
ok "Phase 0 부트스트랩 완료. 명세 §25 Phase 1 진행."
