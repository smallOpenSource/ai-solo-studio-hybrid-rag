#!/usr/bin/env bash
# WorkAdventure 6브릭 배포 — slirp4netns 독립 컨테이너 + 호스트 경유 라우팅 (커스텀 네트워크 0).
#
# ★왜 커스텀 브리지 네트워크가 아닌가 (실측 2026-06-23):
#   이 RHEL8 rootless podman 은 **커스텀 브리지의 공유 rootless-netns 를 마운트하지 못한다**
#   (`failed to mount runtime directory for rootless netns: no such file or directory`·
#    CNI·netavark 무관·`podman unshare --rootless-netns` 자체 실패=샌드박스 mount 제약).
#   기존 라이브 인프라(qdrant/searxng/phoenix)도 같은 이유로 slirp4netns 독립 컨테이너를 쓴다.
#   → 각 브릭을 slirp4netns(기본·작동)로 **0.0.0.0:<허용포트>** 게시하고, 브릭 간 통신은
#   **host.containers.internal:<게시포트>**(내부·검증: PONG), 브라우저는 **studio.example.com:<포트>**
#   (SSH 터널·공개)로 라우팅한다. 커스텀 네트워크/DNS 불요 → rootless-netns 블로커 회피.
#
# 공개 포트(config/ports.json wa_public·studio.example.com:<port> HTTPS=tools/wa_tls_proxy.py 종단):
#   play 33157(컨테이너 3000 HTTP + 3001 WS·프록시가 /ws→3001 경로분기) · map 33158→Owner 정적호스트 33150 ·
#   map-storage 33151 · icon 33152 · uploader 33153 · redis 33154 · back 33155 · back-gRPC 33156.
#   내부 평문 게시: play-http 43157 · play-ws 43159 · map-storage 43151 · icon 43152 · uploader 43153.
#   ★33150=Owner 정적 맵/패널 호스트(office.tmj·건드리지 말 것).
# env: ~/.config/containers/systemd/wa-*.env (gitignored·시크릿·평문0). 브릭별 필수 변수(실값은 디스크·wiki/24 ⑥):
#   공통: SECRET_KEY(play=back=map-storage 동일값) · ROOM_API_SECRET_KEY.
#   play: PUSHER_URL=FRONT_URL=https://studio.example.com:33157 · PUBLIC_MAP_STORAGE_URL=https://studio.example.com:33151/map-storage ·
#         ICON_URL=…:33152 · UPLOADER_URL=…:33153 · API_URL=host.containers.internal:33155 ·
#         INTERNAL_MAP_STORAGE_URL=https://studio.example.com:33151 (★프록시 경유 HTTPS·--add-host로 LAN IP 해석·평문 LAN 제거) · MAP_STORAGE_API_TOKEN(필수·map-storage와 동일) ·
#         START_ROOM_URL=/_/global/studio.example.com:33158/map-starter-kit/office.tmj  (★룸 경로·전체 URL 아님) · ENABLE_CHAT=false.
#   map-storage: PUSHER_URL=https://studio.example.com:33157 · MAP_STORAGE_API_TOKEN(play와 동일).  uploader/back: 각 SECRET_KEY/URL.
#   redis: requirepass(wa-redis.conf·gitignored·아래 자동 마운트) · 소비자 back/play/uploader 는 REDIS_PASSWORD(동일값).
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
PODMAN=/usr/bin/podman
UDIR="$HOME/.config/systemd/user"
ENVDIR="$HOME/.config/containers/systemd"
mkdir -p "$UDIR"

# brick|image|envfile(빈=없음)|publishes(쉼표구분·0.0.0.0:host:cont)|volume(빈=없음)
SPEC="
redis|docker.io/library/redis:6||0.0.0.0:33154:6379|aigs-wa-redisdata:/data
back|docker.io/thecodingmachine/workadventure-back:v1.31.7|wa-back.env|0.0.0.0:33155:50051|
map-storage|docker.io/thecodingmachine/workadventure-map-storage:v1.31.7|wa-map-storage.env|127.0.0.1:43151:3000,0.0.0.0:33156:50053|aigs-wa-maps:/maps
icon|docker.io/matthiasluedtke/iconserver:v3.21.0||127.0.0.1:43152:8080|
uploader|docker.io/thecodingmachine/workadventure-uploader:v1.31.7|wa-uploader.env|127.0.0.1:43153:8080|
play|docker.io/thecodingmachine/workadventure-play:v1.31.7|wa-play.env|127.0.0.1:43157:3000,127.0.0.1:43159:3001|
"

while IFS='|' read -r brick image envfile publishes volume; do
  [ -z "${brick// /}" ] && continue
  # --add-host: inter-brick HTTPS 내부화 — 브릭이 https://studio.example.com:<프록시포트>로 서로를 부를 때
  #   도메인을 로컬 TLS프록시(LAN IP)로 해석시킨다(인증서 CN=studio.example.com·ZeroSSL 체인이라 Node 검증 통과).
  #   → HTTP/WS 백엔드(43xxx)는 127.0.0.1 단독 게시로 LAN 평문 제거. gRPC(33155/56)·redis(33154·password)는 LAN 잔류
  #   (WA 가 gRPC/redis 클라이언트에 TLS 스킴 미지원 → 프록시 경유 불가).
  args="run --rm --name aigs-wa-$brick --sdnotify=ignore --add-host studio.example.com:192.0.2.41"
  IFS=',' read -ra PUBS <<< "$publishes"
  for p in "${PUBS[@]}"; do [ -n "${p// /}" ] && args="$args --publish $p"; done
  [ -n "$envfile" ] && args="$args --env-file $ENVDIR/$envfile"
  [ -n "$volume" ] && args="$args --volume $volume"
  # ★redis 인증: requirepass 를 gitignored conf 마운트로 주입(값은 repo 밖·systemd/shell $ 확장 회피).
  #   redis 는 inter-brick(host.containers.internal=호스트 LAN IP 192.0.2.41) 때문에 LAN 도달이 필요 →
  #   127.0.0.1 단독 바인딩 불가 → 대신 password 로 *무인증* 노출을 차단. wa-redis.conf 부재 시 무인증(생성 권장).
  extra_cmd=""
  if [ "$brick" = redis ] && [ -f "$ENVDIR/wa-redis.conf" ]; then
    # conf 는 644 여야 in-container redis(uid999)가 읽는다 — rootless userns 에서 호스트 사용자 소유 파일을
    #   매핑되는 in-container uid 가 없어 --user 0(컨테이너 root)으로도 DAC 우회 불가(Permission denied 실측).
    #   비밀 노출은 ENVDIR 700(타 로컬 유저 traverse 차단)으로 막는다. 둘 다 아래에서 보장(멱등).
    chmod 644 "$ENVDIR/wa-redis.conf" 2>/dev/null || true
    chmod 700 "$ENVDIR" 2>/dev/null || true
    args="$args --volume $ENVDIR/wa-redis.conf:/usr/local/etc/redis/redis.conf:ro"
    extra_cmd="redis-server /usr/local/etc/redis/redis.conf"
  fi
  cat > "$UDIR/aigs-wa-$brick.service" <<UNIT
[Unit]
Description=AIGS WorkAdventure $brick (slirp4netns·host-routing·no custom bridge·RHEL8 rootless)
After=network-online.target
[Service]
Type=simple
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
ExecStartPre=-$PODMAN rm -f aigs-wa-$brick
ExecStart=$PODMAN $args $image $extra_cmd
ExecStop=$PODMAN stop -t 10 aigs-wa-$brick
[Install]
WantedBy=default.target
UNIT
  echo "  unit: aigs-wa-$brick.service"
done <<< "$SPEC"

systemctl --user daemon-reload
echo "daemon-reload OK — 기동순서 redis→back→map-storage→icon→uploader→play"
