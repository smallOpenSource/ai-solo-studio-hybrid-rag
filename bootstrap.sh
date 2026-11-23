#!/usr/bin/env bash
# SR/renewal Phase 0 부트스트랩 (Path A·repo 루트) — 멱등·비파괴.
# 적응: 스펙 §23.8(/srv/office·conda create·Quadlet)을 현 프로젝트로 — 디렉토리·ports.json·
# app.env 렌더·플레인 systemd 유닛(RHEL8 cgroup-v1 폴백·§23.2). 시크릿은 .env/state/secrets에서 렌더.
set -u
ROOT=/opt/studio
RENDER_PY=/opt/conda/envs/one_man_corp-discord/bin/python

echo "[0-A] 디렉토리 골격(멱등)"
mkdir -p "$ROOT"/state/podman/data/{qdrant,phoenix,searxng} "$ROOT"/state/secrets "$ROOT"/config "$ROOT"/vault

echo "[0-B] ports.json (2810-2829 레지스트리)"
[ -f "$ROOT/config/ports.json" ] && echo "  config/ports.json ✓" || echo "  ⚠ config/ports.json 없음(수동 생성)"

echo "[0-C] app.env 렌더 (.env + endpoint.yaml + state/secrets/comfy.env → config/app.env·chmod600·superset)"
"$RENDER_PY" "$ROOT/tools/render_app_env.py" | grep -E 'WROTE' || echo "  ⚠ 렌더 실패"

echo "[0-D] 컨테이너 유닛 (플레인 systemd·cgroup-v1 폴백)"
for u in aigs-qdrant aigs-phoenix aigs-searxng; do
  [ -f "$HOME/.config/systemd/user/$u.service" ] && echo "  $u.service ✓" || echo "  ⚠ $u.service 없음"
done

echo "[0-E] daemon-reload"
systemctl --user daemon-reload

echo "부트스트랩 완료(멱등)."
echo "다음: systemctl --user enable --now aigs-qdrant aigs-phoenix aigs-searxng && bash $ROOT/verify_phase1.sh"
