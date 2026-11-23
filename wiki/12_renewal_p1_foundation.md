# Renewal P1: 풀스펙 토대 인프라 (Qdrant 서버 · Redis 상태계층 · app.env SoT · 관찰성)

## 개요

2026-06-21, [cutover+Redis 잠금해제 11](11_renewal_cutover_redis.md)에서 **dormant**로만 둔 상태계층을
실가동하고, SR/renewal 명세(§5·§6.7·§23)의 **데이터·관찰·설정 토대**를 풀스펙으로 구현·라이브 적용했다
(`/ralph --critic=critic` P1, 9 스토리). 임베디드 Qdrant→**서버 컨테이너**, Redis **상태계층 실배선**,
**Phoenix/SearXNG 컨테이너**, **Vault git tree**, 그리고 4+1 라이브 서비스의 **단일 `app.env` SoT 마이그레이션**.
Critic 적대검증 **ACCEPT**(재검증·3 MAJOR cleared·primary 라이브증거).

이 페이즈는 **거버닝 원칙이 HYBRID(재사용·이연)에서 "신규 설계안=절대 권위·100% 리터럴"로 전환된 직후**의
첫 실행이다(아래 §0). 토대(Qdrant 서버+Redis+Vault+app.env)는 하류 6 스펙섹션(§24.5 하이브리드검색·§18
지식생애주기·§27 Blackboard/Task큐·§15 Intake)을 동시에 unlock한다.

## 0. 거버닝 원칙 전환 (HYBRID → 풀스펙 권위) — 방법론

[09](09_renewal_langgraph_core.md)의 HYBRID 결정("코어만 교체·인프라 이연")은 2026-06-21 **3차에 걸친
사용자 교정**으로 풀스펙으로 격상됐다. 합의 계획(`/ralplan --deliberate`)이 v1→v5로 진화한 궤적:

1. **v1→v2**(적대검증): 효율-편향 계획이 "0-edit 비파괴 불변식"을 P3에서 못 지키는 허위로 드러남(아키텍트
   실측: `hitl_gate` 무조건 interrupt·빌더 주입 seam 無) → 신규 gated-graph 변종으로 정직화.
2. **v2→v3**(사용자 교정 #1): "기존 리소스 재활용 편향" 지적 → **신규 설계=SSoT·100% 리터럴·효율 후순위**.
   능력패리티→리터럴·`.env`→풀 app.env·web2D→WorkAdventure·외부의존→Owner 제공 하드요건(§16).
3. **v4→v5**(사용자 교정 #2): "무중단·롤백안전 불필요" → **기존 시스템 보존(가동시간·가역성)은 제약이 아님**.
   다운타임/teardown/구 인플라이트 폐기 허용. 유지되는 유일 안전 = **신규 시스템 *기능 정확성* + 타 데이터/
   시스템 불침해**.

★**일반화 교훈**: 기존 시스템의 어떤 속성(효율·토큰·자산·가동시간·가역성)도 신규 설계 완전구현의 제약으로
제안하지 말 것. 반복적으로 끼워넣은 보호/재사용 헤지가 모두 제거됨 → 박제: 사용자 메모리 `renewal-spec-is-authority`.

## 1. Qdrant 임베디드 → 서버 컨테이너 (§5.2)

- **컨테이너**: `docker.io/qdrant/qdrant` rootless Podman·REST `127.0.0.1:2811`/gRPC 2812·바인드
  `state/podman/data/qdrant:/qdrant/storage:Z`. 신규 컬렉션 `knowledge`(dim 1024·Cosine).
- **`knowledge/store.py` 서버 재작성**(`KnowledgeStore.__init__`): 프로덕션=`QdrantClient(url=…)`·테스트=
  `QdrantClient(location=":memory:")`. **디스크-임베디드 `path=` 경로와 H-13 락 처리는 제거**(END STATE=
  서버가 유일). `endpoint.yaml knowledge.qdrant_url`로 주입.
- **무손실 마이그레이션**(`tools/migrate_qdrant_to_server.py`): 구 임베디드 백업(`.omc/backups/`)의 벡터를
  **직접 복사(재임베딩 0)** → 서버 `knowledge`로 upsert. 동일 벡터로 백업 vs 서버 top-k ID **MATCH**(178pt
  무손실 실증). 라이브 knowledge 서비스 재시작 → 서버모드 → 실 `/search` 178pt 결과.

## 2. Redis 상태계층 (`orch/state_store.py` · §5.1·§27)

- [11](11_renewal_cutover_redis.md)의 dormant `redis_client`를 **첫 실소비자**로 배선. `make_state_store`
  (`orch/state_store.py`)가 Sentinel **master 쓰기·replica 읽기**(`slave_for`는 단일노드 시 master 폴백)로
  `StateStore` 생성. **전용 namespace `aigs:*`**: task 큐(§27.3)·agent status(§27.4)·blackboard(§27.5).
- **데이터 안전**: 빈 logical db(cutover-A에서 확인한 db 7)만 사용·`aigs:*` 키만·**FLUSHDB/FLUSHALL 0**·
  임시키 즉시 삭제(공유 인프라 타 키스페이스 무손상). 라이브 R/W 왕복 프로브 PASS·leftover 0.

## 3. 관찰성 (Phoenix + SearXNG + OTLP · §6.7·§8)

- **컨테이너**: `arizephoenix/phoenix`(UI 2813·OTLP 2814)·`searxng/searxng`(2815·`settings.yml` `formats:[json]`·
  `limiter:false`·`secret_key` 생성).
- **orch OTLP 배선**(`orch/app.py` `_init_tracing`): `PHOENIX_OTLP` 환경변수 설정 시 `BatchSpanProcessor`+
  `OTLPSpanExporter`를 추가(`ConsoleSpanExporter`는 span-count 증거·테스트 결정론용으로 **유지**). 미설정/미설치/
  연결불가 시 console만(비치명·핫패스 의존 아님). 의존 `opentelemetry-exporter-otlp-proto-grpc`(orch env).

## 4. app.env 단일 SoT 라이브 마이그레이션 (§23.4·§23.9)

- `tools/render_app_env.py`가 `SR/renewal/app.env.example` 템플릿 + 기존 `.env` 시크릿 + `endpoint.yaml` +
  게이트웨이를 통합해 `config/app.env`(chmod 600·gitignored) 렌더. **시크릿은 file→file 흐름·값 미출력**(키
  이름·카운트만 보고).
- **라이브 전환**: 5 서비스(web·knowledge·supervisor·discord·orch) systemd `--user` 유닛에 drop-in
  `EnvironmentFile=config/app.env`(orch는 `EnvironmentFile=` 빈줄로 기존 `.env` 리셋 후 app.env = .env 정본
  박탈). 직접 restart(**downtime OK·롤백 머신러리 불요**·v5). 전부 active.
- **신규 시스템 기능 acceptance**(§1.6): 실 directive → `route_affinity graph=cycle` → 풀 cycle
  (`ceo route:build → research[wikipedia·knowledge 3 hits] → design → content → hitl_gate interrupt`) ·
  model gemma-4-e4b-it · `spans_emitted=9`→Phoenix. **`/healthz`(readiness shim)만으로는 불충분 → 실 왕복으로
  검증**(아키텍트/Critic 강조).

## ★ 재사용 함정·교훈 (비자명·실측)

1. ★**Quadlet `--cgroups=split`가 RHEL8/cgroup-v1 rootless에서 실패**: 생성 유닛의 `podman run --cgroups=split`
   가 `Error: mkdir /sys/fs/cgroup/cpu,cpuacct/.../runtime: permission denied`(exit 126)로 죽고 Restart 루프.
   직접 `podman run`(split 없음)은 정상. → **스펙 §23.2가 명시 sanction한 폴백**: Quadlet 대신 **플레인
   `~/.config/systemd/user/aigs-<svc>.service`**(foreground `podman run`·`--rm`·`ExecStartPre=-podman rm -f`·
   `ExecStop=podman stop`). 동작 동일.
2. ★**Quadlet 서비스명 = *파일명*이지 `ContainerName`이 아니다**: `qdrant.container` → `qdrant.service`(생성),
   `aigs-qdrant`(ContainerName)와 무관. `systemctl --user start aigs-qdrant`가 "not found"의 원인.
3. ★**systemd `EnvironmentFile`은 인라인 주석을 제거하지 않는다**: `KEY=val  # 주석` → 값이 `val  # 주석`으로
   오염(systemd는 줄-시작 `#`만 주석). 따라서 app.env 렌더는 **값 라인에서 인라인 주석 제거 필수**(문서 주석은
   `app.env.example` 템플릿에 보존). 값에 `;`·공백 있으면 따옴표(systemd가 제거).
4. ★**`EnvironmentFile` 전환 안전 = app.env ⊇ .env (superset)**: 렌더가 **모든 `.env` 키를 verbatim 이월**해야
   `.env`→`app.env` 전환 시 서비스 의존 변수 누락 0. (superset 미보장 시 LLM 게이트웨이 키 등 누락→서비스
   런타임 실패.) 토큰 일관성: orch는 RO/RW를 `ORCH_API_KEY_RW/RO`(app.env의 `RO_TOKEN`과 *다른 이름*)로 읽어
   기본값 유지 → 전환 transparent.
5. ★**임베디드→서버 마이그는 *벡터 직접 복사*가 옳다**(재임베딩 아님): 백업을 `QdrantClient(path=)`로 열어
   scroll(with_vectors)→서버 upsert. 동일 벡터로 top-k ID 일치 = 무손실 증명(재임베딩은 드리프트 위험). 병렬
   컬렉션(구 임베디드 무삭제·백업)으로 검증 후 클라이언트 스위치(pre-mortem #1).
6. ★**`:memory:` Qdrant는 재오픈-영속을 테스트할 수 없다**: `del store; store2=QdrantClient(":memory:")`는
   *새 빈* in-memory db → 영속 smoke가 무음 0-hit. 영속 검증 smoke는 **서버 임시 컬렉션**(생성→재접속→검색→삭제)
   으로. (테스트 격리는 `:memory:`로 충분하나 *영속*은 불가.)
7. **web localhost curl 000 = HTTPS-only**(TLS 1.3·cert CN=PUBLIC_HOST)라 plain `http://127.0.0.1` 빈응답.
   `https://`/브라우저=200(journal 실서빙). "미들웨어 quirk"로 오진 금지 — 프로토콜 확인.
8. **레거시 포트(33006/8810/28202)의 2810대역 이전 = host 역터널 의존**(§9.2/§9.3·PVE host 책임·게스트 범위
   밖). 포트 레지스트리(`config/ports.json`)는 신규 컴포넌트(Qdrant/Phoenix/SearXNG/API)용으로 수립하되,
   레거시 band 이전은 host-coordinated 후속(Server B류 Owner-gated·efficiency-deferral 아님).

## 방법론

- ★**적대검증 ACCEPT-WITH-RESERVATIONS → ACCEPT**: Critic이 "인프라는 전부 실재·라이브·재현가능"을 독립
  재현하되 3 over-claim 포착(§1.6 추론·tracked 테스트의 실 비번 리터럴·AC4 포트 이연). 모두 primary 라이브
  증거(실 directive 왕복·`fake-test-pw` 치환·host-gated 정직 재범위)로 cleared. **헤드리스 PASS ≠ 진실**의
  반복 — §1.6은 *실 cycle 실행*으로만 검증.
- **시크릿 위생**: 게이트웨이 키·Redis 비번은 gitignored 저장만(`state/secrets/`·`config/app.env`)·tracked
  소스에 평문 0. (구 비번이 *이력*에 잔존 시 회전+scrub은 공유 push 시 별도 조치.)

## ★ 2026-06-24 후속 — Redis async 라이브 활성 + vault github 백업 (A2·A1)

§2 Redis 상태계층이 *코드로는 배선됐으나 라이브 orch 에선 비활성*이던 것을 실측·해소. vault git tree(§16) 외부 백업도 완료. 둘 다 **"권한/코드 문제로 보였으나 실은 env/remote 설정 불일치"** 클래스(silent).

### A2 — async Sentinel 소비자 dormant 진단 (env-file split)
- **증상**: `state_store.make_state_store`(async master/replica)는 app.py 에 배선(import·lazy `app.state`·blackboard sink·`GET /tasks`)됐고 `config/app.env` 에 `REDIS_ENABLED=true`+Sentinel 키 전부 존재인데 **라이브 orch 는 Redis disabled**(`/tasks`→`"redis":"disabled"`).
- **근인**: ★**orch 유닛은 `EnvironmentFile=.env` 만 로드**(config/app.env 아님·현 라이브 유닛 실측). `redis_client._load_env_local` 은 `.env`+`os.environ` overlay 를 읽는데 **`.env` 에는 `REDIS_ENABLED` 가 없다**(config/app.env 에만). → §4 의 app.env 이행이 orch 에 한해 현 라이브선 `.env` 로 되돌아와 있어, **config/app.env-only 키는 orch 에 도달하지 않는다**.
- **진단법(격리)**: `tools/a2_redis_probe.py` 를 (a) 그대로(=라이브 orch env)·(b) `REDIS_ENABLED=true` 주입 2회 실행(safe_run 래핑) → (a) `ok=False err=disabled` (b) `ok=True roundtrip=True residue=0`. "flag 만이 누락분"을 격리 실증(`aigs:_a2_probe` 즉시 삭제·시크릿 0).
- **수정(.env 무수정)**: orch 유닛에 `Environment=REDIS_ENABLED=true`(redis_client 의 os.environ overlay 가 픽업·롤백=줄 삭제). 라이브 검증=`/tasks`→`"redis":"ok"`. ★`.env`(read-only 시크릿)를 안 건드리고 유닛 `Environment=` 로 비-시크릿 플래그만 주입. orch 유닛은 deploy/ 미커밋(기존 갭).
- ★교훈: **"코드 배선 ≠ 라이브 활성"** — env-file 이 갈리면(.env vs config/app.env) 플래그가 소비자에 silent 미도달→disabled. 라이브 진실은 *실 소비자 경로*(여기선 `/tasks` redis 상태·또는 probe)로 확인.
- ★**2026-06-30 정정(A2 진단 보강·deploy 편입)**: 위 "orch 유닛은 `EnvironmentFile=.env` 만 로드(config/app.env 아님)" 는 **부정확**(본 페이지 §라이브 전환의 drop-in 서술과 모순). 실제로 orch 유닛엔 drop-in `one-man-corp-orch.service.d/app-env.conf`(빈 `EnvironmentFile=` 리셋 후 `config/app.env`)가 있어 **effective `EnvironmentFile`=config/app.env**(`systemctl --user show one-man-corp-orch -p EnvironmentFiles` 확인). → `REDIS_ENABLED=true`(config/app.env)는 **drop-in 경유로 이미 orch 에 도달**하고, 유닛 명시 `Environment=REDIS_ENABLED=true` 는 **중복·defense-in-depth**(drop-in 빠져도 강제). a2_probe baseline `disabled` 는 probe 가 **standalone 프로세스**(systemd env/drop-in 미상속·`.env` 직접 로드)였던 탓(≠ 라이브 orch env). ★**orch 유닛(메인+drop-in `app-env.conf`·`org-routing.conf`)을 `deploy/systemd/` 에 byte-동일 편입**(커밋 `aa6880f`·시블링 4 코어 유닛 drop-in 도 `b0b20d8`·`deploy/systemd/README.md`)·검증=재구성 트리==`systemctl --user cat` 5/5. 재배치 시 `cp -r deploy/systemd/*.service.d ~/...`(글롭 `*.service` 는 `.d/` 누락).

### A1 — vault github 백업: gh CLI HTTPS 인증 모델 (이 환경 push 규약)
- vault=**별도 git tree**(main repo gitignore·§16)·내부 산출물(decisions·evals·quarantine·meetings). 백업 시도서 `git -C vault push`=**`Permission denied (publickey)`**.
- **근인 2중**: `VAULT_REMOTE`(config/app.env)=`git@github.com:owner/vault.git` = ① **SSH 방식**(이 환경 github 인증은 SSH 키가 아님) + ② `owner`=**미설정 플레이스홀더**(실계정 아님). main repo push 가 되는 건 권한이 아니라 ★**`gh` CLI(<owner>) 가 HTTPS github.com 자격증명 헬퍼**로 작동하기 때문(`git config credential.helper`=none·`~/.git-credentials` 없음·토큰 env 없음·**오직 gh auth**).
- **해소**: `gh repo create <owner>/vault --private --source=vault --remote=origin --push`(HTTPS·gh 인증)·**visibility=PRIVATE 확인**(내부 데이터). `VAULT_REMOTE` 도 `https://github.com/<owner>/vault.git` 로 갱신(vault/.git/config 일치). Drive 백업(`gdrive:vault_backup`·`.git/.omc` 제외=토큰 누출 0)도 redundancy 로 유지.
- ★교훈: **이 환경 github push = gh CLI(HTTPS) 헬퍼**. remote 는 **HTTPS** 로 둘 것(SSH 는 키 없어 실패). 토큰 박힌 URL 출력 0(push -q·실패 stderr 는 카테고리만 redact). 새 repo 는 `gh repo create --private`(내부 데이터는 절대 public 금지).

## 관련 문서

- [11 renewal cutover + Redis](11_renewal_cutover_redis.md) — 본 P1이 실배선한 Redis의 잠금해제(dormant) +
  조직그래프 프로덕션 승격.
- [10 renewal 직원 레이어](10_renewal_org_layer.md) · [09 renewal LangGraph 코어](09_renewal_langgraph_core.md)
  — 본 토대 위 조직그래프·코어(gemma-affinity·checkpointer·seq 영속).
- [03 지식 레이어](03_knowledge_layer.md) — 임베디드 Qdrant 원형(본 P1이 서버로 전환).
- 상세 기록: `.omc/plans/ralplan-renewal-remaining.md`(v5 풀스펙 로드맵) · `.omc/research/p1-critic-review.md`
  (P1 적대검증).
