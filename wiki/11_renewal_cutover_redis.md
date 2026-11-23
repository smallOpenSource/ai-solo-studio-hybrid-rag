# Renewal: 승격 cutover (coexist) + Redis 잠금해제

## 개요

[직원 레이어 10](10_renewal_org_layer.md)에서 **비파괴·미승격**으로 검증만 했던 조직그래프를, 2026-06
**프로덕션 권위로 승격**(cutover A)하고 동시에 **Redis Sentinel을 잠금해제**했다. 핵심 설계 = `coexist`
**flag-gated 라우터** + **durable thread-id 마커**. 라이브 동작은 flag flip 전까지 byte-identical, flip은
**1줄 가역**. 커밋 `b891a23`(`orch/app.py`만 코어 수정·나머지 신규). 합의(2R Architect SOUND·Critic
APPROVE)·구현검증 APPROVED-WITH-NITS. 계획 `.omc/plans/ralplan-redis-unlock-cutoverA.md`.

## ★★ 핵심 함정: 두 그래프가 한 checkpointer 공유 → 교차 resume 무음손상 (재사용·실증)

조직그래프(9노드)와 게임빌드 사이클(7노드)을 **하나의 `AsyncSqliteSaver`**(`orch_ckpt.db`)에 공컴파일하면:

- 체크포인트 PK = `(thread_id, checkpoint_ns, checkpoint_id)`. 최상위 invoke는 `checkpoint_ns=""`(빈문자열).
  두 컴파일 그래프 모두 내부 `name="LangGraph"` → **thread_id가 유일 식별자**.
- **cycle에서 시작한 thread를 org 그래프로 resume하면**: `ainvoke(Command(resume=))`가 **예외 없이** 반환,
  **BLUF 없음**, org 그래프는 thread를 **완료(`next=()`)로 표시**, 그런데 cycle의 `aget_state`는 **여전히
  인터럽트 살아있음**. → 사용자 승인 → 빈 응답 → 작업 무음 소실 + 체크포인트 모순 오염.

★이건 이론이 아니라 **orch env에서 실측 재현**됐다(적대검증 Architect). 두 그래프 공존 = 반드시 thread→그래프
디스패치가 필요하다.

### 봉합: durable thread-id 접두사 마커 (인메모리 사이드테이블 금지)

- 마커는 **thread_id 접두사**(`cyc:`/`org:`)로 새긴다(`orch/intent.py` `make_thread_id`/`graph_kind_of`·
  `CYC_PREFIX`/`ORG_PREFIX` 인접 — 리터럴 드리프트 차단). 무접두 = 레거시 → cycle 폴백, 미지 접두사 → **raise**.
- ★**왜 사이드테이블이 아니라 thread_id인가**: app의 라우팅 사이드테이블(`task_to_thread`·`threads`·
  `inbound_seen`)은 **전부 인메모리** → **컷오버가 요구하는 재시작에 소실**. 정지중 org thread가 재시작 후 마커를
  잃고 cycle로 폴백 = 바로 그 손상. thread_id 접두사는 체크포인트에 담겨 **재시작 무료 생존**, resume config에
  그대로 실려 온다.
- ★**마커가 그래프를 *선택***(detect-then-reject 아님): `_resolve_thread`가 `graph_kind_of(thread_id)`로
  그래프를 고른다(`orch/app.py` `_resolve_thread`·`graph_kind_of(thread_id)`). 잘못된 그래프를 *애초에 호출
  안 함* → mismatch 경로가 구조적으로 제거된다(Architect: reject보다 안전·구멍 없음).

## coexist 아키텍처 (orch/app.py — 유일 수정 코어)

- **flag** `ORCH_ORG_ROUTING ∈ {off(기본)·coexist·replace}`(lifespan에서 `os.environ.get(..., "off")`).
  lifespan이 **두 그래프 공컴파일**(`cycle_graph`/`org_graph`·단일 saver) + `state.graph = cycle_graph`.
- **off = byte-identical**: `_route_kind`→`cycle`·thread_id=무접두 bare uuid·outcome dict·`workflow=
  "game_build"` 라벨 전부 무변. 기존 테스트가 default-off로 도는 게 회귀 증거(66 byte-identical).
- **신규 시작**(`_start_cycle`): `coexist`면 `classify_directive`(결정론·**LLM 0**·핫패스 gemma-affinity)로
  game/org 분류 → `cyc:`/`org:` thread_id + 해당 그래프. `off`면 bare uuid·cycle.
- **★단일 resume chokepoint** `_resolve_thread`: `/decisions/{tid}/resolve` **와** `/api/inbox/{seq}/
  approve|reject`(어댑터 버튼·`_decide_seq` 경유)가 **둘 다** 이 함수를 탄다. 그래프 선택을 여기 1곳에 두면 양
  경로가 커버된다. **회귀가드 = AST 불변식**(`Command(resume=` 호출이 전부 `_resolve_thread` 내부인지·naive
  grep은 docstring false-match라 AST 필수).
- **marker-wins-over-flag**: `_resolve_thread`는 현 `ORCH_ORG_ROUTING`을 **읽지 않고** thread 접두사로만
  선택. flag는 *신규 시작*만 좌우 → 정지중 thread는 시작 그래프로 안전 재개(flip 중에도). 구조 테스트로 단언.
- **graph-aware outcome/label**: org은 `workflow="org"`(game_build 아님)·outcome에서 cycle 전용키
  (`stage_timings`/`knowledge_source` 등) 생략. 안 하면 org resume이 **phantom None 키 + 오라벨**(critic Major).
- **★cycle는 항상 interrupt(hitl_gate)·org는 비-escalate면 인터럽트 없이 즉시 finalize**. 그래서 `_start_cycle`은
  org가 인터럽트 없이 완료되면 **BLUF를 직접 방출**해야 한다(`elif kind == "org"` 분기). cycle만 가정한 "resume에서만
  BLUF" 로직을 그대로 두면 org 비-escalate 결과가 무음 소실.
- **테스트 seam**: `create_app(strategy_caller=...)`로 org CEO(qwen 전략 seam)를 stub 주입(프로덕션 기본=실 qwen).
  org 경로를 앱 레벨에서 결정론 테스트하려면 `use_caller`(부서 gemma)+`use_strategy_caller`(CEO) **둘 다** 바인드.
- `/api/inbound`는 one-shot `chat_reply`(graph thread 아님) → 마커 불요(범위 명확화).

## Redis 잠금해제 (inert·라이브 미배선)

- **`orch/redis_client.py`**: `redis.asyncio.sentinel.Sentinel` async 클라이언트. 공개심볼 4종
  (`_load_env_local`·`_parse_sentinel_hosts`·`make_sentinel_client`→`(client,ok,err)`·`redis_healthcheck`).
  ★**`REDIS_ENABLED` 기본 false → dormant**(접속 0·`(None,False,"disabled")`) — 그래프 코어 미배선 상태에서
  안전. 비밀은 **로그/예외 평문 0**(`err`=`type(exc).__name__` 카테고리명만).
- **`tools/redis_db_probe.py`**: logical-db 비어있음 점검 = **순수 읽기**(`DBSIZE`·`CONFIG GET databases`·
  `discover_master`만·`SET/DEL/FLUSHDB/FLUSHALL` **0**·테스트가 소스 단언). 빈 db 선택 → `.env REDIS_DB`를
  **atomic temp+rename**(해당 1줄·시크릿/타줄 byte 무변).
- ★**공유 인프라 안전**: 이 Redis는 공유(db 0/1/8/9/11에 타 서비스 키). **빈 logical db만** 써서 타 키스페이스
  무손상. 설정 db가 비었으면 그대로, 아니면 범위 내 첫 빈 db, 전부 비-빈이면 **중단(clobber 금지)**.

## ★ 재사용 함정·교훈 (비자명·실측)

1. **`master_for(name, db=N, password=…)`** — db는 `master_for` **kwargs**(connection_kwargs 머지), Sentinel
   ctor 아님(redis-py 5.x). async = `redis.asyncio.sentinel.Sentinel`.
2. **orch 테스트는 `asyncio.run` 패턴**(orch env에 **pytest-asyncio 미설치**) — async 테스트 함수 금지·동기
   함수에서 `asyncio.run(coro)`. (root `tests/`는 `asyncio_mode=auto`지만 그건 별도 env.)
3. **`tests/conftest.py`가 python-dotenv 의존** → 미니멀 orch env로 `tests/` 실행 시 import 에러. 자족적
   테스트는 `pytest --noconftest`로 우회(orch는 자체 .env 로더라 dotenv 불요).
4. **redis-py를 orch **프로덕션** env에 설치**(런타임 의존성·테스트전용 아님). 설치 후 `import orch.app` 스모크
   + 기존 테스트 그린 **확인 후에만** 라이브 단일 재시작(systemctl --user·pkill 금지).
5. ★**.env 인라인 주석 파싱 함정**: `DISCORD_CHANNEL_ID='…'  # ← 주석` 처럼 값 뒤 인라인 주석이 있으면 naive
   `KEY=VALUE` 파서가 주석/따옴표를 값에 끌고 와 Discord **400 NUMBER_TYPE_COERCE**(snowflake 아님). 해법 =
   snowflake를 `\d{17,20}` 정규식으로 추출. (committed redis 코드는 `REDIS_*` 키만 파싱·그 줄들은 인라인 주석
   없음 → 무영향. 함정은 일회성 진단 스크립트였다.)
6. **Discord 봇은 자기 메시지만 삭제 가능**(MANAGE_MESSAGES 없으면) — 검증 부산물 정리 시 author 봇 토큰 필요·
   날짜+내용 가드로 타 메시지 보호.

## 라이브 cutover 운영 (1줄 가역)

- **flip**: discord cutover와 동형 — systemd **drop-in** `~/.config/systemd/user/
  one-man-corp-orch.service.d/org-routing.conf`에 `[Service]\nEnvironment=ORCH_ORG_ROUTING=coexist`,
  그리고 `systemctl --user daemon-reload && systemctl --user restart one-man-corp-orch`(**단일 재시작**·
  PID는 `systemctl --user show -p MainPID`).
- **관측**: `_resolve_thread`/시작이 `route_affinity thread=<id> graph=<cycle|org>` **비밀 없는** 로그 1줄
  방출(journalctl grep). 라이브 검증 = game→`cyc:`/`graph=cycle`·org→`org:`/`graph=org`.
- **롤백(1줄)**: drop-in `rm` + daemon-reload + restart → off(cycle-only·bare uuid). ★컷오버 *전* 하드게이트로
  **flip→롤백 리허설**(off에서 bare uuid 확인 → 복원). 재시작 내성(seq 영속·[09](09_renewal_langgraph_core.md)
  seq 함정)도 확인.

## 방법론 (재사용)

- ★**비파괴 완화는 의도적·flag-gated**: 종전 `app.py 0수정` 불변식은 미승격 단계용. 승격은 정의상 app.py를
  수정하지만 **default-off flag**로 코드 변경 후에도 라이브 동작은 flip 전까지 불변 → 안전한 승격.
- ★**적대검증이 verification-method/구조 결함을 잡는다**: 교차그래프 resume 무음손상은 리뷰가 **orch env서 실측
  재현**해 발견(헤드리스 PASS ≠ 진실). [10](10_renewal_org_layer.md)의 Discord 닉 read-back 함정과 같은 계열.
- **deslop**: 라우팅 리팩터 후 dead 헬퍼(`_graph`) 잔존 → 변경파일 한정 deslop으로 제거·회귀 재실행.

## 관련 문서

- [12 renewal P1 풀스펙 토대](12_renewal_p1_foundation.md) — 본 cutover가 잠금해제한 Redis를 **실배선** + Qdrant 서버화·app.env SoT·Phoenix/SearXNG 관찰성(풀스펙 토대 P1).
- [10 renewal 직원 레이어](10_renewal_org_layer.md) — 본 cutover가 **승격한** 조직그래프(9노드·다부서·CEO qwen).
- [09 renewal LangGraph 코어](09_renewal_langgraph_core.md) — HYBRID 코어·checkpointer·seq 영속·gemma-affinity.
- [05 HITL·게이팅](05_hitl_and_gating.md) · [04 워크플로 엔진](04_workflow_engine.md).
- 상세: `.omc/plans/ralplan-redis-unlock-cutoverA.md` · 리뷰 `.omc/research/redis-cutover-{architect,critic}-review.md` · 커밋 `b891a23`.
