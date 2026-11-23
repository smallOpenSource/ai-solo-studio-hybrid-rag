# Renewal P6 — 관제/인터페이스 (§9 WA·§6.8 API·8상태·/cmd) · AGPL 비수정·callable 래핑·Owner-gated 경계

## 개요

SR/renewal P6(§9 WorkAdventure 인터페이스 + §6.8 백엔드 API + §9.4 8상태 + §9.5 Discord `/cmd`)를 구현하며 얻은
**재사용 패턴·함정**. 핵심은 ① **AGPL-3.0 §13을 트리거하지 않는 WA 통합**(upstream prebuilt 비수정 + 스크립팅 API),
② **graph 코어 byte를 보존하는 8상태 발행**(빌더-레벨 callable 래핑), ③ **로컬 검증가능 코어 vs Owner-gated 런타임
경계**(ACTION 제3범주), ④ **스펙 리터럴 워딩과 라이브러리 능력 경계의 충돌 해소**(아바타 배지→viewport 도크).

커밋: 코드 `<feat>` · wiki `<this>`(append-wiki). 합의계획 `.omc/plans/ralplan-p6-interface.md`(v2·Planner→Architect
SOUND-WITH-CONCERNS[HIGH-1 배지·HIGH-2 traefik·HIGH-3 래핑패턴]→Critic ITERATE[C-1 거버닝 로드맵 충돌·§27.4·페르소나수]→
v2→Architect SOUND + Critic APPROVE). 검증 `SR/renewal/verify_phase6.sh`(8게이트·exit0·PASS=15·ACTION=4·orch 336 passed·
freeze drift0). 구현 critic APPROVE(적대 grep-plant·byte-diff 검증). 관련: [[15_renewal_p3_authority]](authority_gate)·
[[17_renewal_p4_evals]](negative-mutation)·[[18_renewal_p5_capstone]](정직 deferral·ACTION 제3범주).

## 1. AGPL-3.0 §13 — 비수정 WA + 스크립팅 API = 트리거 미발생

§13(네트워크 사용 시 대응 소스 제공)은 **프로그램을 *수정*한 경우에만** 발동한다. 비수정 프로그램을 다운로드·서버
기동·포트 개방해 원격 상호작용시키는 것은 의무 미발생(GNU/opensource.com).

- **WA 6브릭 = upstream 공식 prebuilt 이미지 비수정**: `deploy/systemd/wa/aigs-wa-*.container`가
  `docker.io/thecodingmachine/workadventure-{play,back,map-storage,uploader}:v1.31.7`·`matthiasluedtke/iconserver`·
  `redis:6`만 참조(rootless Podman Quadlet). **로컬 `Build=`/build 컨텍스트 0**(소스 fork 0).
- **패널 = 별도 React 앱**(독립 프로세스·별도 origin·런타임 HTTP IPC·라이브러리 링크 아님) → AGPL "modified work" 아님.
- **가드(falsifiable)**: `verify_phase6.sh` 게이트7이 (i) `AGPL_COMPLIANCE.md` 존재 (ii) `grep '^[[:space:]]*Build='
  deploy/systemd/wa/` = 0건 단언. critic이 temp 복사본에 `Build=./fork` plant → grep DETECTED로 가드 falsify 확인.
- **정지 조건**: 패널이 스크립팅 API로 불가능해 브릭 fork가 필요하면 → 즉시 정지·Owner 소스-오퍼 재상신(자동 진행 금지).
- 결정 기록 = `SR/renewal/AGPL_COMPLIANCE.md`(P6-PRE 착수 게이트·4 결정).

## 2. 스펙 리터럴 워딩 ↔ 라이브러리 능력 경계 (배지=viewport 도크)

§9.4 line411은 "Agent Status Overlay = **아바타 위** 상태 배지"라 적으나, WA 스크립팅 API(`WA.ui`)에는 **아바타에
고정돼 함께 움직이는 per-avatar floating UI 프리미티브가 없다**(openPopup=타일·modal/website=viewport 도크·
actionBar/banner=고정·onRemotePlayerClicked=클릭 트리거). 글자 그대로 구현하려면 `play` 브릭 fork → AGPL 트리거.

- **해소**: 배지 = **viewport 도크 상태판**(아바타-추종 아님). 근거 = §9.4 line405가 "오버레이/iframe **또는 스크립팅
  API**로 연동" 재량을 명시 → 도크는 line411 워딩의 정당한 구현 재해석(§9.4 본질 = "직원 상태 가시화"). play 브릭 비침투.
- **교훈**: 스펙의 *리터럴 워딩*과 *구현 라이브러리 능력*이 충돌하면, (a) 워딩의 의도(§)를 재해석하거나 (b) 라이브러리를
  fork. fork가 AGPL/유지보수 비용을 부르면 (a)가 정공법이고, **그 재해석을 착수 *전* ADR로 박제**해 "리터럴 충족 압박"에
  떠밀린 fork 유혹을 사전 제거한다(사후 가드보다 강함). Architect HIGH-1·Critic M-4.

## 3. 8상태 발행 = 빌더-레벨 callable 래핑 (graph 코어 byte 보존)

§9.4 8상태(Idle·Researching·Coding·Reviewing·Meeting·Waiting·Blocked·Sleeping)를 노드 전이로 발행하되 graph 코어를
건드리지 않는 법:

- **노드 함수 본문 0수정**. 대신 **빌더에서 `g.add_node(name, _with_status(fn, agent_id, state))`로 add_node 인자만
  래핑**(`orch/agent_status.py:_with_status`). `_with_status`는 진입 시 `publish_status`(fire-and-forget·suppress) 후
  `return fn(*args)` — 입출력 계약 불변. → 컴파일 토폴로지 동형(노드/엣지 불변). **기존 graph 회귀 GREEN으로 동형 입증**
  (freeze drift 0). ★`deliberate`는 별도 reachable 노드(`Command(goto)` 반환)지 진입 hook 아님 — 래핑 무관(Architect HIGH-3).
- **§27.4 권위 스키마**: `publish_status`는 `{agent, state, current_task, since(iso)}` 발행(필드명 `state`·`current_task`·
  `since` 리터럴 — `status`/`ts`/`detail` 아님·Critic M-1). `since` clock은 주입 seam(결정론 테스트).
- **SSoT 단일화(deslop)**: 노드→(persona,state) 매핑을 `_NODE_STATUS` dict + `node_status()`로 단일 소싱(graph.py가
  리터럴 중복 대신 `*node_status("ceo_route")` 소싱). 두 곳 분산→드리프트 위험 제거.
- **반-green-theater**: `/agents/status`가 **실 invoke 후 그 전이를 반영**하는지 단언 + negative-mutation(전이 안 한
  직원이 active로 나오면 FAIL). 미전이 직원은 정직하게 Idle/current_task=null(날조 아님).
- **정직 갭(MINOR)**: 라이브 `current_task`는 노드-진입 wrapper가 task 컨텍스트(directive)를 미보유해 null(필드는 존재·
  정직 null). directive→current_task 스레딩은 후속(라이브 대시보드 승격 시).

## 4. §6.8 백엔드 API = app.py add-only Tier-2

기존 RO/RW x-api-key 게이트·`/tasks`·`/decisions`·SSE가 가동 중인 프로덕션 app.py에 잔여 표면을 **비파괴 추가**:

- `GET /health`(§6.8 리터럴·기존 `/healthz` alias)·`GET /knowledge/search`(RO·searcher 재사용)·CORSMiddleware(WA
  origin·`ports.json`/app.env 경유·**리터럴 금지**)·in-process rate-limit·`docs_url=None`/`redoc_url=None`.
- **★rate-limit SSE 면제**: `/events/stream`(long-lived `while True`)·`/health`·`/healthz`는 면제(`_RATE_EXEMPT_PATHS`).
  요청-카운트 rate-limit이 long-lived SSE 첫 요청에서 429를 던지면 스트림이 끊긴다 — 면제 또는 연결-당-1-카운트 필수
  (Architect MED-1·Critic M-5). 테스트: limit=1에서 SSE 반복 무429 + 비면제 경로 2번째 429.
- `create_app`은 docs_url/CORS origin/rate-limit를 **인자/플래그로 노출**(테스트 명시 검증·라이브는 app.env). 기존
  라우트/미들웨어/RO-RW 게이트 0수정. 프로덕션 `enable_docs` 기본 False → `/docs` 404(공격면 미노출).

## 5. Discord `/cmd` app_command = 순수 추가 (NL 무회귀)

기존 봇은 `on_message` 자연어(NL)만 처리·슬래시 트리 없음. `/cmd <agent> <지시>`는 **추가** 입력 경로:

- `discord.app_commands.CommandTree` + `setup_hook`에서 `tree.sync()` — **primary 봇만**(`consume_sse` 가드 재사용·
  non-primary 미호출 → N봇 중복 등록 차단). `/cmd` → `rest_client.post_run`/directives enqueue(persona id 검증).
- **NL byte 보존**: `bot.py` 변경이 **+100/-0(순수 additive·삭제 0)** → `on_message` 본문 불변. 회귀 가드 =
  `inspect.getsource(on_message)`에 `tree`/`/cmd`/`app_command` 토큰 0 단언(falsifiable). discord 234 passed.
- **순수 로직 분리**: `slash_commands.py`(agent→bot_role 매핑·discord 무의존)로 단위 테스트 용이.

## 6. 패널 데이터바인딩 falsifiability (렌더-트랩 차단)

WA 셸 없이 React 패널을 검증: `play_control_check.mjs`가 **실 `ControlPanels` 컴포넌트**(스텁 아님)를 mock API 응답으로
구동 → **2 시나리오 델타 단언**(A: research=Coding/qa=Reviewing, B: research=Reviewing/qa=Blocked → 배지/색/큐가
*응답값 따라 변함*). 고정 placeholder/하드코딩이면 FAIL. 독립 `EXPECT_COLOR`로 state→색 교차검증. WA-위-합성 렌더는
`data-wa-live=0` + "pending Owner" 명시(BLOCKED≠pass). [[18_renewal_p5_capstone]] C4 BFS와 동류 — "렌더+무에러"는 불충분.

## 7. 로컬 검증가능 코어 vs Owner-gated 런타임 (ACTION 제3범주)

P6은 외부 의존(공개 도메인·TLS·맵 URL)이 큰 페이즈. **분리선을 verify가 못박는다**:

- **로컬 code 100%(verify [OK] 집계)**: §6.8 API·8상태 발행·React 패널·`/cmd`·WA 유닛/env/정적 핀.
- **Owner-gated(verify `[ACTION] pending Owner`·≠fail·≠pass·미합산·exit code 비오염)**: WA-위-합성 렌더·라이브 아바타
  입장·브릭 핸드셰이크(도메인분)·2-액터 proximity·라이브 /cmd 디스패치 = `START_ROOM_URL`+공개 도메인+TLS(§9.3 PVE host).
- ACTION을 OK로 위장 0(green-fake 차단). exit0 = 좌열 전부 PASS + AGPL/§9.2 기록. [[18_renewal_p5_capstone]] Server B
  deferral과 동형(BLOCKED≠pass·weakest-criterion-wins).

## 8. ★함정 — recon compose ≠ 타깃 토폴로지 (traefik vs §9.2 포트모델)

`/tmp/wa-compose.yaml`(upstream recon)은 **traefik 리버스프록시 + ACME + PathPrefix 경로라우팅**을 토대로 한다 →
스펙 §9.2("경로라우팅·리버스프록시·Caddy **없음**·포트기반만")와 **정면 모순**. "compose 그대로 쓰면 됨"은 거짓 —
**§9.2 포트모델로의 능동 전환이 실작업**(traefik 제거·play env 포트별 절대 URL·PathPrefix→루트·`PUSHER_URL` 끝슬래시
제거[OIDC `Invalid playUri` 함정]). 교훈: **upstream recon 산출물은 *참고*지 타깃이 아니다** — 스펙 토폴로지와 대조해
일탈을 능동 정정하라(Architect HIGH-2·Critic 전면 동의·메인 검증토대도 1차 오판→리뷰가 교정).

## 재사용 체크리스트 (다음 인터페이스/통합 작업)

- [ ] AGPL/카피레프트 라이브러리: 비수정+IPC면 트리거 회피·수정 서빙이면 소스-오퍼 의무(착수 게이트로 이진 판정·grep 가드).
- [ ] 스펙 리터럴 ↔ 라이브러리 능력 충돌: 의도 재해석 ADR을 *착수 전* 박제(사후 가드보다 강함).
- [ ] graph/코어 코드에 횡단 부작용(상태발행 등): 빌더-레벨 callable 래핑(본문 byte 보존)·기존 회귀 GREEN으로 동형 입증.
- [ ] rate-limit: long-lived SSE/헬스 프로브는 **면제**(요청-카운트가 스트림을 끊는다).
- [ ] 기존 핸들러에 입력경로 추가: 순수 additive(+N/-0)·`inspect.getsource` 토큰 가드로 무회귀 falsifiable.
- [ ] UI 데이터바인딩: 2-시나리오 델타 단언(고정 placeholder FAIL·렌더-트랩 차단).
- [ ] Owner-deps: ACTION 제3범주(≠pass)·로컬 가능분은 실행·라이브는 정직 pending.
- [ ] 멀티-env 테스트: discord=`one_man_corp-discord`(dotenv/discord.py)·orch=`orch` env — conftest import 실패는 env 오선택 신호.
