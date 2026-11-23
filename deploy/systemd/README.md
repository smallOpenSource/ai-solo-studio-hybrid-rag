# systemd user 서비스 — one_man_corp 라이브 서빙

on-prem 라이브 서빙을 관리하는 **systemd user 서비스** 유닛(루트 불필요).
2026-06-17 수동 detached 기동 → systemd 관리로 전환(SPOF 해소·부팅 자동기동·자동재기동).

## 유닛 (5 핵심 서비스 · renewal 코어 포함)

| 유닛 | 역할 | 포트 | conda env |
|---|---|---|---|
| `one-man-corp-knowledge.service` | 지식 검색(qdrant 단일소유) | 8810 | one_man_corp-knowledge |
| `one-man-corp-orch.service` | renewal LangGraph+OTEL 권위 코어(FastAPI) | 28202(127.0.0.1) | orch |
| `one-man-corp-web.service` | 운영 인터페이스(FastAPI SSE/WS/API) | 33006 | one_man_corp-engine |
| `one-man-corp-supervisor.service` | 워커풀 감독(concurrency=2 멀티프로세스) | — | one_man_corp-engine |
| `one-man-corp-discord.service` | Discord N봇 @멘션 어댑터(아웃바운드) | — | one_man_corp-discord |

부팅 순서: knowledge → {orch, web} → {supervisor, discord} (`After=`). orch 는 `After=knowledge`(stage_research 가 knowledge :8810 프록시 의존).

보조 유닛(선택): `aigs-intake.service` — P9 §15 인입 워커 데몬(`aigs:intake:queue` 바운드 폴 소비 →
vault/research → `capture_and_index(untrusted)`·env `intake`·`After=knowledge`·종료성 P0 바운드 루프).
설치: `cp deploy/systemd/aigs-intake.service ~/.config/systemd/user/ && systemctl --user daemon-reload &&
systemctl --user enable --now aigs-intake.service`. REDIS_ENABLED/Drive(rclone OAuth·§16) 부재 시
데몬은 graceful no-op 종료(코드 완성·라이브 인입은 Owner ACTION).

## 설치

```sh
# 1) 유닛 배치
cp deploy/systemd/*.service ~/.config/systemd/user/
# ★drop-in(.service.d/) — *.service 글롭은 .d/ 를 안 옮긴다. 전 코어 서비스가 drop-in `app-env.conf`
#   로 config/app.env 를 로드하고(orch=+REDIS_ENABLED/ORCH_ORG_ROUTING·discord=+WEB_BASE_URL→orch:28202)
#   → 반드시 함께 복사해야 완전 재현.
cp -r deploy/systemd/*.service.d ~/.config/systemd/user/
systemctl --user daemon-reload

# 2) Linger(로그아웃/재부팅에도 가동) — 이미 켜져 있으면 생략
loginctl enable-linger "$USER"

# 3) enable + 기동
systemctl --user enable --now one-man-corp-knowledge.service
systemctl --user enable --now one-man-corp-orch.service
systemctl --user enable --now one-man-corp-web.service
systemctl --user enable --now one-man-corp-supervisor.service
systemctl --user enable --now one-man-corp-discord.service
```

> SSH/스크립트에서 `systemctl --user`를 쓸 땐 `export XDG_RUNTIME_DIR=/run/user/$(id -u)` 선행.

## 관리

```sh
systemctl --user status  one-man-corp-<svc>.service
systemctl --user restart one-man-corp-<svc>.service
# PID는 재기동마다 변동:
systemctl --user show one-man-corp-<svc>.service -p MainPID
```

## 환경 의존(이 박스 고정값)

- 프로젝트 루트 `/opt/studio` — 유닛 `WorkingDirectory`·`ExecStart` 절대경로.
- conda env python을 직접 exec(`conda run` 회피). `.env`는 각 앱이 앱 레벨에서 자체 로드. **추가로 전 코어 서비스는 drop-in `app-env.conf`(`<svc>.service.d/`)로 systemd 가 `config/app.env` 를 주입**하고, 서비스별 drop-in 이 더 있다(orch=`org-routing.conf` ORCH_ORG_ROUTING·discord=`orch-cutover.conf` WEB_BASE_URL→orch:28202). → 유닛 복원 시 `.service.d/` 까지 배치 필수(설치 `cp -r *.service.d`).
- ★**orch env 모델**(drop-in 포함): orch 의 **effective `EnvironmentFile` 은 `config/app.env`** 다 — 메인 유닛은 `EnvironmentFile=.env` 를 두지만 drop-in `app-env.conf`(`one-man-corp-orch.service.d/`)가 빈 `EnvironmentFile=` 로 그 목록을 리셋한 뒤 `config/app.env` 로 재지정한다(`systemctl --user show one-man-corp-orch -p EnvironmentFiles` 로 확인). `.env` 는 이와 별개로 `orch/graph.py` 자체 stdlib 로더가 앱 레벨에서 읽는다. 그래서 `config/app.env` 의 `REDIS_ENABLED=true`(A2 async Sentinel)·기타 키가 systemd 로 주입되고, drop-in `org-routing.conf` 가 `Environment=ORCH_ORG_ROUTING=coexist` 를 더한다. 메인 유닛의 `Environment=REDIS_ENABLED=true` 는 **defense-in-depth(중복·무해)** — drop-in 이 빠져도 async Sentinel 을 강제한다. Redis off 롤백=메인 유닛의 그 줄 삭제 **및** `config/app.env` 의 `REDIS_ENABLED=false` → restart. ★유닛 복원 시 `.service.d/` drop-in 까지 함께 배치해야 완전 재현(아래 설치 참조).
- 다른 호스트/경로면 유닛의 절대경로를 수정할 것.

## 서비스별 gotcha

- **web**: 장수명 SSE/WS로 graceful drain이 사실상 무한 지연(소켓은 SIGTERM 즉시 닫힘=미서빙) →
  `TimeoutStopSec=10` 후 systemd가 cgroup SIGKILL. 클라는 자동 재연결·이벤트는 office.db
  영속+커서 리플레이라 무손실.
- **knowledge**: `state/qdrant` 단일 소유자(H-13) → 재기동 시 구 프로세스 완전 사망 후 새 인스턴스가
  락 오픈(systemd 정지가 보장). 가동 중 ingest 금지.
  ★**외부에서 `state/qdrant`를 변경(크롤·ingest)한 뒤에는 knowledge 서비스를 반드시 재기동하라.**
  임베디드 qdrant는 시작 시 컬렉션을 로드하므로 **런타임 외부 변경을 자동 반영하지 않는다** — 미반영
  시 `/search`가 빈 바디로 HTTP 500을 낸다(동일 코드를 수동 기동하면 200 정상 = 코드 결함 아님). 절차:
  서비스 stop → 크롤/ingest → 서비스 start. (세션 시작 시 관찰되는 첫 500도 보통 이 원인.)
- **discord**: 아웃바운드 봇(리슨 포트 없음). 헬스 = 프로세스 생존 + 연결
  (`ss -tnp | grep pid=<MainPID>` → 16봇 Discord:443 + web SSE:33006). stderr 로깅을 journald가
  미수집하는 환경이라 `StandardOutput/Error=append:.omc/logs/discord_bot_live.log`로 파일 기록.
  재기동 시 16봇 재IDENTIFY(~수십초).
- **supervisor**: 자식 슬롯 crash-loop 상한 + systemd `Restart=on-failure` 2중. 자원한도
  (MemoryMax/High/TasksMax)는 cgroup v1에선 무효일 수 있음(자동재기동 주목적은 무관).
- **orch**: renewal LangGraph+OTEL 권위 코어(127.0.0.1:28202·`After=knowledge`). `Restart=on-failure`+자체 ckpt db로
  비정상 종료 시 무손실 재수렴. ★메인 유닛 + drop-in 2개(`app-env.conf`→config/app.env · `org-routing.conf`→
  `ORCH_ORG_ROUTING=coexist`)로 구성 — 위 "환경 의존" 예외 참조. 헬스=`systemctl --user is-active` 또는
  effective env `systemctl --user show one-man-corp-orch -p Environment,EnvironmentFiles`.

## 롤백

- 단일 워커(legacy)로: `systemctl --user disable --now one-man-corp-supervisor.service` +
  `scripts/serve_worker.sh`.
- 수동 런처 `scripts/serve_{,knowledge,supervisor,worker,discord}.sh`는 비-systemd 폴백으로 보존.

## 마이그레이션 절차 (라이브 detached 프로세스 → systemd·무중단 인계)

이미 떠 있는 수동 detached 프로세스를 거의 무중단으로 systemd에 인계한 절차(2026-06-17 실적용):

1. **유닛 생성 + enable**(비파괴): `cp` → `daemon-reload` → `systemctl --user enable`(시작은 안 함).
   라이브 프로세스 미접촉이라 안전. 부팅 순서는 `After=`로 표현.
2. **핸드오버**(서비스별): 구 프로세스를 **정확 PID로 graceful 종료**(`kill -TERM <pid>` · ★`pkill -f`
   금지 — 자기/타 프로세스 오살) → **완전 사망 대기**(`timeout 20 tail --pid=<pid> -f /dev/null` ·
   foreground `sleep`은 이 환경에서 차단됨) → **죽은 것 확인 후에만** `systemctl --user start`
   (이중 인스턴스·락 충돌 방지) → 헬스 검증.
3. ★**SSE/장수명 연결 소비/제공 서비스(web·discord)는 graceful drain이 사실상 무한 지연**(소켓은
   SIGTERM 즉시 닫혀 미서빙이나 프로세스가 안 죽음). 대기 타임아웃 후 **`kill -KILL <pid>`로 확정**
   (클라는 자동 재연결이라 무손실). systemd 정지 시엔 `TimeoutStopSec` 후 자동 SIGKILL이 같은 역할.
4. **knowledge는 qdrant 단일소유** → 새 인스턴스 기동 전 구 프로세스 완전 사망 필수(파일락 해제).
   위 2번 순서가 이를 보장("already accessed" 회피).
5. **검증**(서비스별): web=`curl -ks https://127.0.0.1:33006/`(HTTP 응답) · knowledge=`curl
   http://127.0.0.1:8810/health` · supervisor=`ps --ppid <MainPID>`(슬롯 N개) · discord=`ss -tnp |
   grep pid=<MainPID>`(16봇 Discord:443 + web SSE:33006). 구 detached PID 전부 소멸 확인.
6. **자동재기동 실증**(선택·안전): `systemctl --user kill -s KILL <svc>` → systemd가 `Restart=on-failure`로
   ~5s 내 새 MainPID로 부활(`NRestarts` 증가). cgroup 전체 SIGKILL이라 고아 없이 깨끗이 재수렴.

## WorkAdventure 6브릭 (P6d · `wa/` · rootless Podman Quadlet)

SR/renewal §9.1 공간 인터페이스. **upstream 공식 prebuilt 이미지를 비수정 사용**(AGPL §13 트리거
미발생·`SR/renewal/AGPL_COMPLIANCE.md` 결정①). 위 4 핵심 서비스(conda·systemd-user)와 달리 WA는
**rootless Podman Quadlet `.container`**로 관리한다(`deploy/systemd/wa/`).

> ★**실배포 주의(2026-06-23·이 호스트=RHEL8 cgroup v1 rootless)**: 아래 Quadlet 절차는 이 호스트에서
> `--cgroups=split` 실패(`mkdir /sys/fs/cgroup/memory…: permission denied`)로 **작동하지 않는다**. 실제 라이브
> 배포는 **플레인 systemd**(`deploy/systemd/wa/deploy_wa_plain.sh`) + **TLS 프록시·`/ws` 경로분기**
> (`tools/wa_tls_proxy.py`·`/etc/studio/ssl` 종단)로 수행했고 **아바타 입장까지 검증**됨. 상세·돌파법(커스텀 브리지
> 불가→slirp4netns+host경유·HTTP 3000/WS 3001 분리 등)=`wiki/24_renewal_wa_deployment.md`. 아래 Quadlet
> 표/절차는 cgroup v2 rootless 호스트용 **대안**으로 보존.

| 유닛(`.container`) | 이미지(핀·비수정) | 외부 포트(`config/ports.json`) | 내부 |
|---|---|---|---|
| `aigs-wa-play` | `workadventure-play:v1.31.7` | 2817→3000 | gRPC 50051·WS 3001 비노출 |
| `aigs-wa-back` | `workadventure-back:v1.31.7` | — (내부) | gRPC 50051 |
| `aigs-wa-map-storage` | `workadventure-map-storage:v1.31.7` | 2818→3000 | gRPC 50053 비노출 |
| `aigs-wa-icon` | `matthiasluedtke/iconserver:v3.21.0` | 2819→8080 | — |
| `aigs-wa-uploader` | `workadventure-uploader:v1.31.7` | 2820→8080 | — |
| `aigs-wa-redis` | `redis:6` | — (내부) | 6379 |

보조 유닛: `aigs-wa.network`(내부 DNS `aigs-wa`) · `aigs-wa-maps.volume` · `aigs-wa-redisdata.volume`.

★**§9.2 포트모델(결정③)**: traefik 리버스프록시·PathPrefix 경로라우팅 **없음**. 각 외부 브릭은
포트로 직접 노출되고 절대 URL 로 배선된다. `play` env(`wa-play.env.example`)는 **내부 URL**
(`API_URL=back:50051`·`INTERNAL_MAP_STORAGE_URL=http://map-storage:3000`)과 **외부 URL**
(`PUSHER_URL=https://studio.example.com:2817` 등·**끝슬래시 제거**=`Invalid playUri` 함정 회피)을 분리한다.
브릭마다 별도 env 파일(`wa-{play,back,map-storage,uploader}.env.example`)을 두어 한 브릭 변수가 다른
브릭으로 새지 않게 한다(클린-env·`NODE_ENV` 미설정).

```sh
# 설치(Quadlet은 ~/.config/containers/systemd/ 에 배치 — user 서비스와 디렉토리 다름)
mkdir -p ~/.config/containers/systemd
cp deploy/systemd/wa/aigs-wa-*.container deploy/systemd/wa/aigs-wa.network \
   deploy/systemd/wa/aigs-wa-*.volume     ~/.config/containers/systemd/
# env 템플릿을 복사해 시크릿(SECRET_KEY/ROOM_API_SECRET_KEY)·도메인 채움(★example 에 시크릿 금지)
for b in play back map-storage uploader; do
  cp deploy/systemd/wa/wa-$b.env.example ~/.config/containers/systemd/wa-$b.env
done
systemctl --user daemon-reload           # Quadlet 제너레이터가 aigs-wa-*.service 생성
systemctl --user start aigs-wa-redis aigs-wa-back aigs-wa-map-storage \
                       aigs-wa-icon aigs-wa-uploader aigs-wa-play
```

★**라이브 아바타 입장 = pending Owner**: 외부 입장은 `START_ROOM_URL`(GitHub Pages 맵)·공개 도메인
(`studio.example.com:<port>`)·TLS 종단(§9.3 PVE host)이 **전부 Owner 제공**이라야 성립한다. 부재 시
유닛/env 는 code-complete 이나 입장 e2e 는 "code 100% / runtime pending Owner"(BLOCKED ≠ pass).

검증: `tools/p6_wa_probe.py [--pull]`(이미지 핀 + 바운드 pull + ExposedPorts·라이브는 정직 pending) ·
`SR/renewal/verify_phase6_wa_cmd.sh`(P6d+P6e 게이트·Quadlet -dryrun·AGPL 비수정 grep·포트모델). ★브릭
소스 **수정 금지**(AGPL §13) — 수정 필요 발견 시 즉시 정지·소스-오퍼 결정 재상신(`AGPL_COMPLIANCE.md` 단서 b).

## 부록: `workflow` 스코프 없는 PAT로 master 푸시

이 repo의 PAT에는 `workflow` 스코프가 없어, **`.github/workflows/*`를 건드리는 커밋이 push 범위에 들어가면
GitHub가 거부**한다(커밋 diff 기준이지 HEAD 트리 기준이 아님 — 과거 커밋이 ci.yml을 추가했으면 그 커밋이
범위에 들어가는 순간 막힘). 우회(2026-06-17 적용): 새 변경을 **`origin/master` 기반 커밋**으로 만들어
**fast-forward push**하면 push 범위에 workflow 파일 변경이 없어 스코프 불요로 통과한다.

```sh
git checkout -b release origin/master                 # 현 master 위에서 시작(과거 workflow 커밋 미포함)
git add <비-workflow 변경> && git commit -m "..."
git push "https://<TOKEN>@github.com/<owner>/<repo>.git" release:master   # ff·스코프 불요
git update-ref refs/remotes/origin/master release     # 로컬 추적 ref 동기화(토큰 없이)
git checkout -B <작업브랜치> release && git branch -D release             # 로컬 정리(분기 해소)
```

근본 해결은 `workflow` 스코프 토큰으로 ci.yml 포함 재push지만, 본 프로젝트는 CI/CD 미운용이라 불요
(ci.yml은 제거됨). ★토큰은 인라인 1회만 사용·`.git/config`/파일에 미저장.
