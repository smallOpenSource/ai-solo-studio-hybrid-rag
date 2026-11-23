# WorkAdventure 셀프호스팅 — RHEL8 rootless podman 배포·netns 한계·slirp4netns 호스트라우팅·TLS 종단

P6([19_renewal_p6_interface])가 *설계*한 WorkAdventure(WA) 6-브릭을 이 호스트(`app-host.example.internal`·RHEL8
rootless podman 4.9)에 **실제 배포**하며 얻은 재사용 인프라 지식. wiki/19는 React 패널·AGPL·8-state 설계,
이 문서는 **컨테이너 배포·네트워킹·TLS 운영**(별개·범용). 셀프호스팅 WA는 이 호스트 제약에 여러 번 막혔고,
그 돌파법이 다른 멀티컨테이너 앱에도 그대로 쓰인다.

## ① ★rootless 커스텀 브리지 네트워크 불가 (이 호스트의 환경 제약·가장 큰 교훈)

- **증상**: `podman run --network <커스텀브리지>` → `Error: failed to mount runtime directory for rootless
  netns: no such file or directory`. 컨테이너 단위 netns는 만들어지나(`Made network namespace at …`),
  **커스텀 브리지가 요구하는 *공유* rootless-netns 마운트**에서 실패.
- **격리 확증**(추측 금지): ⓐ CNI·netavark **둘 다** 실패(`containers.conf network_backend` 전환해도 동일 →
  백엔드 문제 아님·netavark 설치는 헛수고였음) ⓑ 인터랙티브 셸에서도 실패(systemd 컨텍스트 아님) ⓒ
  **`podman unshare --rootless-netns` 자체가 실패** = 환경(샌드박스 mount/netns) 레벨. 반면 기본
  네트워크(slirp4netns)와 `podman pod`(공유 netns)는 **작동**.
- **왜 기존 인프라는 되나**: `aigs-qdrant/searxng/phoenix`는 전부 `NetworkMode=slirp4netns` **독립 컨테이너**
  (커스텀 네트워크 0)다. 정확히 이 한계를 피한 설계였다(`podman inspect <c> --format '{{.HostConfig.NetworkMode}}'`로 확인).
- **★워크어라운드 = slirp4netns 독립 + 호스트 경유 라우팅**(커스텀 네트워크 불요):
  - 각 컨테이너 `podman run`에 **`--network` 미지정**(기본 slirp4netns·작동) + **`--publish 0.0.0.0:<port>`**.
  - 컨테이너 간 통신은 호스트명(redis/back/…) DNS 대신 **`host.containers.internal:<게시포트>`**(실측 PONG).
    `studio.example.com:<포트>`도 됨. `10.0.2.2`는 **안 됨**(Network unreachable).
  - 즉 WA가 브릭 호스트명으로 서로를 부르던 것을 전부 `host.containers.internal:<게시포트>`로 재배선.
- **pod는 왜 안 썼나**: pod(공유 netns)는 작동하나 WA 브릭 내부 포트 충돌(play·map-storage 둘 다 3000·
  icon·uploader 둘 다 8080)·env 포트 오버라이드 없음 → 단일 pod 불가. 그래서 slirp4netns 독립+호스트라우팅.

## ② ★Quadlet `--cgroups=split` 실패 (RHEL8 cgroup v1 rootless)

- Quadlet(`.container`)은 `podman run`에 `--cgroups=split`를 **강제** → cgroup v1 rootless에서
  `mkdir /sys/fs/cgroup/memory/…: permission denied`(exit 126). 기본 `podman run`(플래그 없음)은 정상.
- **해법 = 플레인 systemd user 유닛**으로 `podman run`(--cgroups=split 없이): `deploy/systemd/wa/deploy_wa_plain.sh`
  (브릭 스펙→유닛 생성·`Type=simple`·`--sdnotify=ignore`·`Restart=on-failure`·`ExecStartPre=podman rm -f`).

## ③ ★TLS 종단 — 인증서 직접(프록시)·호스트 프록시 부재

- 이 호스트엔 nginx/caddy/socat/stunnel **전무**. WA 브릭은 **평문 HTTP만**(traefik 제거·포트모델). Owner가
  `/etc/studio/ssl/studio.example.com/`(fullchain/privkey·CN=studio.example.com) 제공 + "SSL 쓰라" 지시.
- **선례**: `web_server`(33006)는 uvicorn `ssl_certfile`/`ssl_keyfile`로 *같은 인증서* HTTPS 종단
  (`config/endpoint.yaml` web 블록).
- **해법 = `tools/wa_tls_proxy.py`**(경량 asyncio TLS 리버스 프록시): `ssl.SSLContext.load_cert_chain`으로
  공개 HTTPS 포트 종단 → 평문 백엔드로 **TLS→TCP 바이트 양방향 포워딩**(HTTP·WebSocket 투명·헤더 변형 0).
  브릭은 **내부 포트(43xxx)**로 옮기고 프록시가 공개 포트(33xxx) 종단. play는 `PUSHER_URL=https://…` env로
  자기 공개 스킴을 알므로 X-Forwarded-Proto 불요(바이트 프록시라 헤더 미추가여도 OK). 실측 `https://studio.example.com:33157 → 200`.
- ★앞선 `https→000`은 백엔드 미스가 아니라 **TLS 서버가 없어서**였음(프록시 세우자 200). VM 자체 https-egress가
  막혀 있어(COMFY 8190도 vm에선 `https→000`이나 실작동) 공개 https는 `127.0.0.1` 직접(`-k`)으로 검증.

## ④ WA 브릭 config 함정 (v1.31.7)

- **play는 `MAP_STORAGE_API_TOKEN` 필수**(map-storage와 동일값). 누락 시 `FATAL ERRORS FOUND IN
  ENVIRONMENT VARIABLES!!! For variable "MAP_STORAGE_API_TOKEN": Required`로 즉시 크래시.
- **Matrix admin 오버라이드가 무조건 실행**: `MatrixProvider.overrideRateLimitForAdminAccount`가
  ENABLE_CHAT/MATRIX 설정 무관하게 startup에 돌아 `ERR_INVALID_URL: undefined_matrix/client/r0/login`
  로그를 남김. **비치명**(백그라운드 retry·play는 listen 도달·restarts=0). `ENABLE_CHAT=false`도 이걸 막지 못함(무해).
- **play 부팅 느림**(`tsx ./src/server.ts` 런타임 컴파일·1–2분·healthcheck "(starting)"). HTTP 000을 실패로
  단정 말 것 — 컨테이너 내부 `:3000`(/proc/net/tcp `:0BB8`) 리스닝을 확인하고 긴 retry로 대기.
- **브라우저-페이싱 URL은 전부 같은 스킴**(mixed-content): play·map-storage·icon·uploader·맵 모두 HTTPS여야
  WA front(https)가 리소스를 로드한다.
- **★`START_ROOM_URL`은 룸 경로**(`/_/global/<host:port>/<path>`)지 전체 URL이 아니다. 전체 `https://…`를
  넣으면 front가 경로로 취급(`/https://…`)해 `UNSUPPORTED_URL_FORMAT`. 외부(Owner 호스팅) 맵은
  `/_/global/studio.example.com:33158/map-starter-kit/office.tmj`(Owner 정적호스트가 CORS `*` 제공 시 그대로 로드).
- **★play는 HTTP(컨테이너 3000)와 WebSocket(컨테이너 3001)을 별도 포트**로 서빙(로그 "Pusher web-server …3000"/
  "web-socket server …3001"). 프론트는 `PUSHER_URL` 하나로 둘 다 접속 → **3000만 게시하면 `/ws/room`→Express
  404 무한재연결**. 해법: 3001도 게시 + 리버스프록시가 `/ws*`→3001 경로분기(WA 레퍼런스의 traefik 라우팅 등가).
  `tools/wa_tls_proxy.py`가 33157 TLS 종단 후 첫 요청라인 경로로 분기(검증: `/ws/room`→101 Switching Protocols).

## ⑤ 포트 점유자 식별 (디버깅 교훈)

- `ss -ltnp`에 `users:()`가 안 보이면 **타 user 프로세스**(non-root는 자기 것만 보임). "터널"로 단정 말고
  **응답 본문으로 정체 식별**: 여기선 `curl http://127.0.0.1:33150`이 Python `http.server`의 "Directory listing
  for /"(map-starter-kit/office.tmj + agent_panel.html) → **Owner가 띄운 정적 맵/패널 호스트**(터널 아님).
  /proc inode 역추적도 보조.
- Owner가 메인 포트에 정적 맵(map-starter-kit/office.tmj)을 미리 띄워둠 → 그게 **START_ROOM_URL 소스**이지
  play를 올릴 자리가 아니다. play는 빈 포트로.

## ⑥ 최종 포트 레이아웃 (이 배포)

| 포트 | 역할 | 노출 |
|---|---|---|
| 33150 | 맵+패널 정적호스트 (Owner·office.tmj·agent_panel) | HTTP (Owner) |
| **33157** | **play (WA front+WS·진입점)** → `/ws*`→43159(c3001)·else→43157(c3000) | **HTTPS**(프록시·경로분기) |
| 33158 | 맵(office.tmj) → Owner 33150 | HTTPS(프록시) |
| 33151·33152·33153 | map-storage·icon·uploader → 43151·43152·43153 | HTTPS(프록시) |
| 33154·33155·33156 | redis·back·map-storage gRPC | 내부 평문(host.containers.internal) |

- 브릭 env: 내부=`host.containers.internal:<게시포트>` · 공개=`https://studio.example.com:<포트>`. 시크릿
  (SECRET_KEY 3브릭 동일·ROOM_API_SECRET_KEY·MAP_STORAGE_API_TOKEN)은 랜덤 생성·`~/.config/containers/systemd/wa-*.env`(gitignored).

## ⑦ 보안 하드닝 — rootless LAN 노출 차단 (배포 후 보안리뷰 적발·`42f4a5e`·`1b5e194`)

rootless podman 의 `--publish 0.0.0.0:<port>` 는 **firewalld OFF + LAN 인터페이스(예 `eth0 192.0.2.41`)가 있으면
LAN 전체에 노출**된다(보안리뷰 실측: `192.0.2.41:33154`→redis `+PONG` *무인증*·평문 백엔드가 TLS 우회로 LAN 도달).

- **★핵심 제약 — `host.containers.internal` = 호스트 LAN IP(여기선 192.0.2.41), loopback 아님**
  (`podman exec <c> getent hosts host.containers.internal` 로 확인). inter-brick(`REDIS_HOST`·`API_URL`·
  `INTERNAL_MAP_STORAGE_URL`)이 이 LAN IP 로 호스트에 도달하므로, **게시 포트를 `127.0.0.1` 단독으로 묶으면
  inter-brick 이 깨진다**(컨테이너가 192.0.2.41:port 로 가는데 리스닝이 없음). → "순수 localhost 바인딩" 불가가 출발점.
- **Redis(바인딩 못 바꿈·LAN 잔류) → `requirepass` 로 무인증 차단**(`42f4a5e`). ★rootless userns 함정:
  conf 를 마운트하면 in-container redis(uid999)가 **호스트 사용자 소유 600 conf 를 못 읽는다**(매핑 밖 uid라
  `--user 0`=컨테이너 root 로도 DAC 우회 불가·`Fatal error … Permission denied` 실측). → conf **644** + 디렉토리
  **700**(호스트측 타 유저 traverse 차단으로 644 노출 상쇄). 값은 gitignored `wa-redis.conf`·소비자(back/play/uploader) `REDIS_PASSWORD`.
- **HTTP/WS 백엔드 → `127.0.0.1` 게시 + inter-brick HTTPS 내부화**(`1b5e194`). 브릭끼리도 **TLS 프록시 경유**
  (brick → `https://studio.example.com:<공개포트>` → 프록시 → `127.0.0.1` brick)로 돌리고, 전 브릭에
  `--add-host studio.example.com:<LAN IP>` 로 도메인을 로컬 프록시로 해석시킨다(인증서 CN 일치). **CA서명 인증서(ZeroSSL)면
  Node 가 system CA 로 검증 통과** → `reject-unauthorized` 해제 불요(`tls.connect({servername}).authorized===true` 사전 확인).
  결과: play(43157/43159)·map-storage(43151)·icon(43152)·uploader(43153) 평문이 LAN 에서 사라지고 프록시(localhost)만 도달.
- **gRPC·redis 는 프록시로 못 감싼다**: WA 가 `API_URL`(back gRPC)·`REDIS_HOST` 에 **host:port 만**(TLS 스킴 미지원)
  쓰므로 바이트-파이프 TLS 프록시에 못 태운다. → gRPC(33155·33156)·redis(33154)는 LAN 잔류(redis 는 password 로 무인증만 차단).
  추가 차단은 **host firewall** 만 가능(전 서비스+SSH터널 영향이라 신중). 신뢰 LAN 이면 수용 가능(gRPC=내부 API·일반 RCE 경로 아님).
- 검증법: 포트별 `socket.connect((LAN_IP,port))` vs `(127.0.0.1,port)` 도달성 대비 + 변경 후 **아바타 입장 IN-OFFICE**
  로 inter-brick 무파손을 종단 확인. 시크릿 값은 전부 gitignored env/conf·repo 0.

> 검증: 7 서비스(6브릭+`aigs-wa-tlsproxy`) **active·restarts=0·enabled**·`https://studio.example.com:33157 → 200`
> (WA front·cert CN=studio.example.com)·맵 `:33158 → 200`·`/ws/room → 101`. ★**아바타 입장 검증 완료**(헤드리스 종단간·
> swiftshader + `--host-resolver-rules=MAP studio.example.com 127.0.0.1`로 로컬 TLS프록시 라우팅·`web/scripts/live_check_wa_enter.mjs`):
> 아바타 "AIGS-Bot" office.tmj 오피스 입장·맵 렌더·WS `OPEN` 1회(재연결 루프 0)·"MOVE AROUND" 튜토리얼. 비차단 잔여
> 1건(맵 정적호스트의 없는 선택적 자산 1개→작은 404 박스·입장/렌더/이동 영향 0). Matrix 채팅 off(homeserver 없음·비치명).
> 관련: [19_renewal_p6_interface] · [12_renewal_p1_foundation](rootless podman 인프라) · 코드:
> `tools/wa_tls_proxy.py`·`deploy/systemd/wa/deploy_wa_plain.sh`·`config/ports.json` wa_public.

## ⑧ 좌측 인월드 패널 복구 (위 §⑥ "작은 404 박스" 잔여 해소)

위에서 "비차단 작은 404 박스"로 남긴 것의 정체와 해결. 좌상단(8,104·260×330)의 `<iframe src=:33158/maps/agent_panel.html>`
가 **404**(Python http.server "Error response 404 File not found")를 표시하던 것 = **인월드 에이전트 상태판**이 깨진 상태.

**근본 원인(이중·traefik 제거 잔재)**: 맵 `office.tmj` 의 `script:"../office_panel.js"` 가 로드한 `office_panel.js` 가
`WA.ui.website.open({url: origin+"/maps/agent_panel.html"})` 로 패널을 임베드하는데, 이 `/maps` 경로는 **옛 traefik(:33100)
의 `/maps`→맵호스트 매핑**을 전제했다. renewal 포트모델이 traefik 을 제거해 `/maps` 가 안 풀리고(실파일은 :33150 **루트**
`/agent_panel.html`), 게다가 패널은 `/agents` 를 **same-origin** 폴링(옛 :33100 통합오리진 전제) → 데이터도 끊김. 추가로
**레거시 패널 택소노미**(researcher/writer/editor/game_designer…)가 renewal(ceo/coo/dev_lead/research/art/librarian/intake/
dev/qa)과 불일치.

**해결(Owner :33150·office.tmj·office_panel.js 비편집 → 우리 프록시에서)**: `tools/wa_tls_proxy.py` :33158 핸들러를 경로
라우팅으로 — `/agents`·`/agent_panel.html`·`/maps/agent_panel.html` → **orch(127.0.0.1:28202)**, 그 외 → 맵 정적호스트(:33150).
orch 가 **renewal 택소노미 패널 HTML**(data-driven·하드코딩 로스터 0) + `/agents` 피드(personas.yaml−secretary·라이브
8-state·`current_task` 비노출[공개표면]·auth/rate 면제·`orch/app.py _PANEL_OPEN_PATHS`)를 서빙.

> ★**keep-alive 풀공유 해저드**: :33158 은 origin 하나라 브라우저가 패널 iframe·맵 리소스를 **한 connection 풀**에서
> 공유 → 첫-요청 백엔드 라우팅은 keep-alive 재사용 시 깨진다(맵용 연결에 `/agents` 가 실려 :33150→404). 해법=**모든 :33158
> 요청에 `Connection: close` 강제**(요청라인 뒤 삽입·기존 Connection 헤더 제거) → 연결당 1요청 → 첫-요청 라우팅 항상 정확.

> 검증: 패널 connected(녹색)·**9 renewal 페르소나 라이브**(probe research=Researching/art=Coding 반영 후 정리·스크린샷
> 육안)·보안 무회귀(`/agents/status`·`/knowledge/search` 여전 401·`/agents`·`/agent_panel.html`만 공개). 패널 데이터원=
> orch canonical agent_status. 비핵심 잔여: `resources/objects/webrtc-in.mp3` 404(입장 알림음·선택 자산·패널 무관).
> 관련: [25_provider_primary_fallback](동일 orch·served_by 패턴) · 코드 `tools/wa_tls_proxy.py`(`_route_maphost`)·`orch/app.py`.

## ⑨ 접속 암호 게이트 (WA 무인증 → HTTP Basic·진입점 한정·2026-06-24)

배포된 WA 는 무인증이었다(누구나 입장). TLS 프록시(`tools/wa_tls_proxy.py`)가 모든 공개 HTTPS 포트를
종단하므로 **여기서 인증을 건다** — play 진입(:33157) 핸들러에 HTTP Basic 게이트(`_load_owner_basic`·
`_auth_ok`·`_make_play_handler`). 자격증명 = **`.env` 의 `HITL_OWNER_NAME`/`HITL_OWNER_PASSWORD`**
(값은 .env 에서만 READ·코드/로그 0·상수시간 `hmac.compare_digest`).

> ★**진입점만 게이트한다**: play(:33157)만 Basic 인증·맵/자산 포트(:33158 맵·:33151 map-storage·
> :33152 icon·:33153 uploader)는 **열어둔다**. 이유 = WA front(play origin)가 그 포트들을 **cross-origin
> fetch** 로 로드하는데 Basic 인증은 origin 별이라 cross-origin fetch 는 자동 인증 안 됨 → 전 포트 게이트
> 시 맵/자산 로드가 401 로 깨진다. 진입(play SPA+WS)이 막히면 입장 자체 불가하므로 play 게이트가 충분한
> 방벽(맵/자산은 읽혀도 "입장" 불가).

> ★**우리 헤드리스 클라이언트(아바타 ⑩)는 통과해야 한다**: playwright `httpCredentials:{username,password}`
> 로 Basic 헤더 자동 첨부. WS 핸드셰이크는 같은 origin(:33157)이라 브라우저가 캐시된 Basic 을 실어 통과.

> 검증(라이브): no-auth=401 · wrong-auth=401 · owner-auth=200. 첫 접속 시 브라우저 인증창 1회. creds
> 미설정 시 기동로그 `auth=OFF` 명시(무음 fail-open 아님). 코드 `tools/wa_tls_proxy.py`.

## ⑩ 직원 9명 상주 아바타 (단일 chromium·N context·systemd·2026-06-24)

스펙 §13 "맵 + 직원 아바타"를 라이브화. WA 는 실시간 멀티플레이어라 "아바타"=접속 클라이언트 → 직원 9명을
**헤드리스 WA 클라이언트**로 상주 입장. ★**"에이전트가 안 보인다"=상태판(⑧)이 아니라 월드 안 직원 아바타
부재**였다(Owner 확정 — 둘은 완전히 다른 수정).

- **리소스 안전 설계**: **단일 headless chromium + 페르소나당 1 browser context**(엔진 공유 → 9 분리
  브라우저보다 가벼움). 로스터 = orch `/agents`(canonical·personas.yaml−secretary·이름=label). 32GB 박스
  가용 16GB → 9 상주 안전. ★과거 시스템다운은 무한루프/무한이미지였지 *고정* N 이 아님 — **고정 9는 안전**.
- **암호 통과**: context `httpCredentials`(.env HITL_OWNER·⑨). **도메인 해석**: `--host-resolver-rules=MAP
  studio.example.com 127.0.0.1`(헤드리스→로컬 TLS 프록시·cert CN 일치)·`--use-angle=swiftshader`(WebGL)·fake media.
- **입장 흐름**(검증 시퀀스): goto(2-retry) → 이름 입력 → Continue/워카/카메라 클릭루프(≤12·tileset
  `office-chunk-1` 200 도착 시 조기탈출) → 게임씬. **스태거 입장**(thundering herd 방지) + **재입장
  헬스루프**(canvas 소실 시 재입장) + SIGTERM 정리.
- **종료성**: 페르소나 수 **고정**(무한 스폰 0)·각 입장 바운드·watchdog=systemd `Restart=on-failure`.

> ★함정: 초기 동시 입장 시 9번째가 `page.goto` 30s 타임아웃(WebGL 동시 init 경합) → **goto 45s + 1회
> 재시도** + 헬스루프 재입장으로 자가복구(8/9 → 9/9). 8 아바타 ≈ +2.7GB.

> 검증(라이브): **9/9 IN-OFFICE**(systemd `aigs-wa-avatars.service`·`상주 시작: 9/9` 로그)·가용 16GB.
>
> ★**in-world presence 발행(2026-06-24·B2)**: 헬스루프마다 `state/wa_avatars_status.json{in_world,total,ts}` 기록
> → `verify_phase6.sh` 가 `systemd active AND in_world≥1 AND ts 신선(≤120s)` 일 때만 "WA 라이브 아바타 입장"을 PASS
> (아니면 ACTION 폴백). ★**systemd active 만으론 입장 0 이어도 통과(green-fake)** 라 실 in-world 카운트로 falsifiable
> 하게 박았다([[23_renewal_verification_methodology]] ⑥). 이로써 verify_phase6 WA 입장 ACTION→조건부 라이브 OK 플립.
> 코드 `web/scripts/wa_avatar_presence.mjs`·유닛 `deploy/systemd/wa/aigs-wa-avatars.service`(`After=play/tlsproxy`·
> boot 자동). 관리 `systemctl --user {restart,status} aigs-wa-avatars`(XDG_RUNTIME_DIR=/run/user/$UID).
> 관련: ⑨(암호)·[19_renewal_p6_interface](§13 인터페이스).
