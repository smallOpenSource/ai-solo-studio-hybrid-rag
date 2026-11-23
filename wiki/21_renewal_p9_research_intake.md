# P9 연구·인입 (§8 SearXNG·browser-use · §15 rclone 인입 워커 · §10 Store · §11.1 provenance)

SR/renewal P9 — *외부세계* 연구·인입 파이프라인(웹 메타검색·에이전틱 브라우징·Drive 동기화). P2-rest의 *내부* 지식 생애주기(§18)와 구분. 재사용 지식:

## ① SearXNG 메타검색 클라이언트 (`orch/searxng.py`)
- 컨테이너는 **P1 토대가 이미 배포**(`aigs-searxng`·`127.0.0.1:2815`·plain systemd `--user` 폴백=RHEL8 cgroup-v1 rootless에서 Quadlet `--cgroups=split` 실패 회피·§23.2 인가). P9 신규 = **검색 클라이언트 코드만**. → 인프라 재배포 전 **실 라이브 확인**(`GET /search?format=json` 200 JSON)으로 스코프 정직 축소.
- `search(query) -> list[SearxResult]`: httpx **바운드 타임아웃**(connect+read·무한 0)·`httpx.MockTransport` 주입 seam(실 네트워크 없이 파싱 테스트)·로컬 무인증(쿼리/값 로깅 0).

## ② ★browser-use 워커 프로세스 격리 (MAJOR-1·termination P0)
- **핵심 토폴로지 교훈**: `.omc/guards/safe_run.sh`는 `exec timeout`+`ulimit -v` = **서브프로세스 가드**. browser-use는 §8 리터럴상 "라이브러리"이나 **orch 노드가 in-process `agent.run()`를 부르면 safe_run이 토폴로지상 닿지 않는다**(과거 시스템다운 2회=정확히 *in-process* 무한루프 클래스).
- 해법: browser-use를 **별도 워커 *프로세스*로 격리**(`browser_worker.py`·라이브러리 lazy import·`MAX_STEPS=10`+벽시계 timeout)하고 그 프로세스를 `safe_run.sh`로 래핑(`browser_client.py`·`subprocess`+`start_new_session`+`os.killpg`). 노드는 enqueue만(in-process 호출 금지). = §8 리터럴(라이브러리 import) 충족 + 프로세스 격리로 safe_run **실재화**(진짜 이중가드).
- 종료성 테스트(u6)는 *워커 프로세스* 비-제로 종료를 입증(in-process 종료 아님). **라이브 실증**: 실 browser-use가 example.com 내비 중 LLM(qwen)이 `done` 미선언 루프→**safe_run이 exit124로 실 runaway 종료**(가드 작동 강증거).

## ③ ★§11.1 비신뢰 인입 provenance 라우팅 (MAJOR-A·보안경계)
- 스펙 §11.1은 **RESEARCH/INTAKE를 IPI/RAG-포이즌 위협벡터로 명시 지목**(웹·Drive=비신뢰→지식DB→검색·실행→간접 프롬프트 인젝션·Lethal Trifecta). 외부 적재물은 frontmatter `provenance: untrusted` + `source_url` 필수.
- **함정**: 크롤/인입 색인을 raw `knowledge/ingest.py`(provenance 무처리·mtime만)나 `store.upsert` 직접호출로 하면 §11.1 위반. → P9 비신뢰 인입(SearXNG→crawl·browser-use·Drive)은 **`knowledge_lifecycle.capture_and_index(store, text, source, provenance=ArtifactProvenance(provenance="untrusted", source_url=url))` 단일경로**로 라우팅(raw upsert/ingest 금지). validator가 untrusted→source_url 필수 강제.
- **경계**: provenance *태깅*=P9(인입 경로를 *짓는* 우리 책임·P8 스팟라이팅의 선결 토대). 스팟라이팅(`<untrusted_data>` 래핑)·egress 방어=P8 보안 페이즈. 정규화 단계 새니타이즈(§11.1:451·활성/숨김 콘텐츠 strip)는 인입 위생으로 P9.
- falsifiable: u8 부정변이(trusted 누출/source_url 공백→validator FAIL).

## ④ ★★LangGraph declared-channel drop 함정 (적대검증이 잡은 silent break)
- **증상**: 노드가 `return` dict에 필드(`intake_request`)를 set해도, 그 키가 **`OrgState` TypedDict 채널로 미선언**이면 **LangGraph가 체크포인트 상태에 보존하지 않는다** → `aget_state().values`에서 사라짐 → 후속 sink의 `snap.values.get(...)`=항상 None → **dead code·라이브 그래프서 enqueue 0**.
- **왜 유닛이 못 잡나**: 노드를 직접 호출(raw 반환 검사) + sink에 필드 수동 주입 = 양쪽 절반을 *격리* 테스트하면 통합 seam(컴파일 그래프→aget_state)을 건너뛴다 = **green-theater**. `blackboard_sink`가 작동하는 건 그 소스(`shared_facts`)가 *declared 채널*이기 때문(동형 주장이 거짓일 수 있음).
- **해법**: 노드 방출 필드를 **declared 채널**로 선언. 동시 fan-out(예 research+intake) 가능하면 **reducer 필수**: `intake_requests: Annotated[list[dict], operator.add]`(bare key는 concurrent-write 드롭/오류). 노드는 1-원소 리스트 append·sink는 iterate. **회귀 테스트는 컴파일 그래프**(`build_org_graph(MemorySaver()).ainvoke()`→`aget_state`→채널 present 단언·단일+동시)로 — 두 절반 격리 아님.
- 선례: P3b `adr_payload`를 채널로 선언("snap.values에 있음→무음유실 0")한 것과 동일 패턴. 새 sink 추가 시 **소스 키가 declared 채널인지 먼저 확인**.

## ⑤ rclone 인입 워커 (`orch/intake_worker.py`·conda -n intake)
- `sync_source` **Protocol 추상화**: `LocalFixtureSource`(테스트·라이브) + `RcloneSource`(argv 조립·시크릿0)가 동일 시그니처 `sync(dest)->list[Path]`. → 라이브 Drive(rclone OAuth·§16 Owner) 없이도 **로컬 fixture로 정규화 이후 파이프라인 전량 falsifiable**(정규화·dedup·vault commit·capture_and_index·search_hybrid 회수). 라이브 Drive 동기화만 ACTION.
- PDF(pypdf)/CSV(stdlib)→MD 정규화 + dedup 멱등(`store._point_id` 결정론·동일 입력 2회→포인트 불변). 워커 데몬(`run_worker_daemon`·Redis `aigs:intake:*` rpop **바운드** 폴)+`deploy/systemd/aigs-intake.service`.
- ★verify 4-counter는 **fixture-표면(✔)** vs **rclone-Drive 표면(argv 단위/라이브 ACTION)**을 분리(혼합 합산 금지·"전량 falsifiable" 과대주장 차단).

## ⑥ env 정직 분류 (ACTION 사유 정밀화)
- 라이브 미달을 ACTION으로 둘 때 **근본 원인을 실측으로 좁혀라**: 외부 egress(`curl example.com`→200)·Xvfb(`:99` 가동)·browser revision 확인 → crawl4ai `CrawlFetchError`는 *네트워크 아님*(egress 정상)·**버전 정합**(기존 §8 도구 config)으로 정직 분류. green-fake 0·BLOCKED≠pass.
- INTAKE 노드(판단·orch env) vs 인입 워커(실행·intake env) **경계 분리**(§15)·노드는 enqueue만(in-process 워커 실행 금지).

## ⑦ ✅ 후속 — 라이브 외부연구 복구 (2026-06-23)

verify_phase9 의 SearXNG/crawl4ai ACTION 은 **stale** 였다(P9 구현 시 env degraded). 재측정:
`aigs-searxng` Up·HTTP 200·orch `searxng.search` 3 실결과 / crawl4ai **0.8.9** 정상·**search→crawl
핸드오프 라이브**(검색 top URL→crawl→markdown ~11–20KB). → US-9a-1/US-9a-2 **라이브 PASS**
(US-9a-2 무조건 `act` → 라이브 search→crawl 게이트로 플립·crawl env·비오염=색인은 i1 단위검증).
★교훈: **ACTION 라인은 재측정으로 stale 여부를 확인하라** — 한때 degraded 였어도 인프라 복구 시
라이브 검증으로 즉시 OK 전환(억측으로 ACTION 유지 금지·green-fake 0). 잔여 ACTION=browser-use clean
nav(env·OBSERVE)·라이브 Drive(§16 Owner·`SR/renewal/OWNER_RESOURCES_NEEDED.md`).

> 검증: `SR/renewal/verify_phase9.sh`(4-counter·exit0 **PASS=14·ACTION=2**·FAIL=0·BLOCKED=0)·orch 435+ passed·freeze drift0.
> ★라이브 복구 후 SearXNG+crawl4ai search→crawl=**라이브 PASS**·잔여 ACTION=browser-use clean nav·라이브 Drive OAuth.
> 관련: [09_renewal_langgraph_core] · [12_renewal_p1_foundation] · [20_renewal_p7_media].

## ⑧ ✅ 라이브 Drive 인입 — rclone 헤드리스 OAuth (2026-06-24·US-9c-2 ACTION→PASS)

§16 Owner 의존이던 라이브 Drive 가 구성 완료. ⑤의 `RcloneSource`(`orch/intake_worker.py`·argv `rclone
copy <remote> <dest>`)가 실제 `gdrive:office_intake` 에서 파일을 회수함을 **프로덕션 코드로 왕복 실증**.

- **설치(sudo 0)**: rclone v1.74.3 공식 바이너리를 `~/bin`(PATH·쓰기가능)에 설치 — conda base 오염 0.
- **★헤드리스 OAuth 함정·해법**: 서버는 디스플레이가 없는데 rclone OAuth 는 브라우저 콜백(`127.0.0.1:53682`)이
  필요하다. **포트 53682 포워딩**(VS Code Ports / `ssh -L 53682:localhost:53682`)하면 — rclone 이 *서버
  루프백* 53682 에 임시 웹서버를 띄우고, **로컬 PC 브라우저**가 그 주소를 열면 요청이 터널 타고 서버 rclone
  으로 도달한다. 로그인 화면은 구글 공개사이트(로컬 브라우저가 직접 렌더·서버 무관) → 승인 후 구글이 53682
  로 redirect → 터널 경유 서버가 콜백 수신 → 토큰 저장. **서버는 화면을 그린 적이 없다**(루프백 포트만
  빌려줌). 포워딩 누락 시 로컬 127.0.0.1:53682=자기자신→연결거부.
- **remote 토큰 주입(비대화)**: `rclone.conf` 에 stub(`[gdrive] type=drive scope=drive`·토큰 비움)을 먼저
  쓰고 `rclone config reconnect gdrive:`(★**콜론 필수** — 없으면 로컬 경로로 해석돼 "backend doesn't support
  reconnect" 에러)로 토큰만 받는다. 토큰=`~/.config/rclone/rclone.conf`(0600·repo 밖·gitignored 위치).
- **폴더/검증**: `rclone mkdir gdrive:office_intake` → `config/app.env DRIVE_INTAKE_FOLDER=office_intake`
  (gitignored) → 프로브 업로드 → `RcloneSource.sync()`(프로덕션 코드)로 라이브 풀+내용 마커 검증+정리(잔여 0).

> ★게이트 정직성: `verify_phase9.sh` US-9c-2 가 정적 `act`(ACTION) 고정이었다 → **조건부 라이브 체크로
> 교체**: 읽기전용 `rclone lsf "${RCLONE_REMOTE}:${DRIVE_INTAKE_FOLDER}"` 도달 시 PASS·미설정(app.env
> 부재/`__CHANGE_ME__`/rclone 미설치) 시 ACTION 폴백. falsifiable(토큰만료/폴더부재→nonzero→ACTION)·
> 부작용 0·CI 안전(app.env gitignored 부재→자동 ACTION). 이로써 잔여 ACTION=browser-use clean nav 1건.
> 코드 `SR/renewal/verify_phase9.sh`·`orch/intake_worker.py`. 관련: ⑤(rclone 워커)·⑥·⑦(ACTION 재측정 교훈).
