# Renewal: LangGraph 오케스트레이션 코어 (HYBRID 코어 스왑)

## 개요

2026-06-20~21, `SR/renewal/` 새 설계(AI GAME STUDIO 명세 §0~§29)를 두고 **현 프로젝트에 적용 vs 바닥부터 재구축**을 딥다이브(3레인 적대검증)로 면밀검토한 결과 **HYBRID**로 결정·구현·프로덕션 적용했다. 오케스트레이션 코어만 **커스텀 엔진(`office/`, [04](04_workflow_engine.md) 참조) → LangGraph**로 교체하고, 검증된 주변(Discord 멀티봇·knowledge·모델 게이트웨이·event 계약)은 재사용, 무거운 인프라는 이연했다.

신규 코어는 `orch/` 패키지로 산다. 구 `office/` 엔진과 4개 라이브 서비스는 **0수정**으로 보존되고, 컷오버는 **1줄 역전 가역**이다.

## 결정: 왜 HYBRID인가 (재사용 가능한 방법론)

"새 설계를 적용 vs 재구축" 류 질문의 일반해:

1. **비호환 코어는 교체 강제**: 커스텀 엔진의 영속/HITL 이음매(무상태 재방송·별도 워커프로세스)와 LangGraph(살아있는 컴파일그래프·동일프로세스 `Command(resume=)`)는 **구조적 비호환** → 한 오케스트레이터로 공존 불가. "기존 엔진 위에 새 설계를 얹기"는 불가능 — 적용=엔진 신규 재작성이 강제된다.
2. **수렴 자산은 재사용**: 인터페이스/툴/데이터 링은 URL·계약 수준 결합이라 대부분 무수정~경미 어댑트로 이식된다.
3. **인프라는 이연**: 코어 증명에 비필수인 HA·서버모드·미디어 등은 데모 후 백필.
4. ★ **아키텍처는 2차 변수**: 완성도의 지배변수는 종종 공유 천장(GPU/모델 수·동시성·사람-게이트·로컬모델 품질)이지 프레임워크 선택이 아니다. 재구축이 천장을 올리지 않으면 *적용(코어만 교체)*이 효율적이다.

상세: `.omc/specs/deep-dive-renewal-apply-vs-rebuild.md` · 3레인 `.omc/research/renewal-decision-lane{1,2,3}-*.md`.

## orch/ 아키텍처

- **`orch/graph.py`**: LangGraph `StateGraph`. 노드 `ceo_route → (research →) design → content → hitl_gate(interrupt) → qa → finalize(BLUF)`. 최소 페르소나셋(CEO·Research·ART/DEV-spec·QA). `Command(goto/update)` 라우팅. gemma-affinity(전 노드 gemma·사이클 중 qwen 금지).
- **`orch/app.py`**: FastAPI lifespan이 컴파일 그래프 + `AsyncSqliteSaver`를 라이브 보유. 라우트: `/api/run`·`/directives`·`/threads/by_task`·`/decisions/pending`·`/decisions/{tid}/resolve`·`/api/login`·`/api/inbox/{seq}/approve|reject`·`/api/inbound`·`/events/stream`. **dual-auth**(omc_session 쿠키 OR RO/RW x-api-key).
- **`orch/outbound.py`**: AG-UI 형상 이벤트 emitter(기존 11종 계약 유지·신규 타입 0). `chat-{role}-{id}` run_id·monotonic `seq`·**seq 영속**(재시작 내성, `seed_last_seq` + atomic).
- **`orch/tools.py`·`orch/session.py`**: 순수 툴 노드 래핑(`office.business` 무수정 등) + 쿠키 세션.
- 관찰성: openinference + OpenTelemetry 자동 스팬(노드별 CHAIN + LLM + interrupt=exception 이벤트). **콘솔 exporter**(Phoenix 서버는 이연).
- 테스트: `orch/tests/` 결정론 **56 passed**(모델 stub).

## ★ 재사용 가능한 함정·교훈 (비자명·실측)

1. **모델 스왑 스래싱**: 단일 GPU에서 gemma↔qwen 교대 = 콜드로드 **6–21s**(qwen 35B 11–21s·gemma ~6s), 동일모델 정상상태 ~0.27s. → **gemma-affinity 배칭 필수**: 거의 모든 노드를 gemma 고정, qwen은 사이클 *밖* 희귀 하드결정만. 순진한 노드별 qwen↔gemma 교대는 사이클을 비현실로 키운다. 진짜 병목 = 동시성 자체가 아니라 **모델 스와핑**.
2. **langgraph 1.x 실 API**(명세의 0.x 가정과 다름·구현 직전 재확인): `Command`/`interrupt` ← `langgraph.types`. `AsyncSqliteSaver` ← `langgraph.checkpoint.sqlite.aio`, **async 컨텍스트매니저** → FastAPI lifespan에서 `__aenter__`로 프로세스 수명 동안 보유. pending interrupt = `aget_state(cfg).interrupts` **점읽기**; **전수 `alist()` 스캔 금지**(단일커넥션 saver 데드락). 체크포인트 serde(ormsgpack)는 **state 안의 callable 직렬화 불가** → 결정성 주입은 state가 아니라 **`ContextVar`** seam(use_caller/use_searcher)으로.
3. **sqlite-with-discipline로 충분**(부하시험): K=2 실 gemma·N=10에서 데드락 0·saver 쓰기오류 0(2128 동시 점읽기 0오류). 병목은 saver 아니라 gemma 엔드포인트(천장=2). Postgres saver 불요(K>2 처리량 필요 시 백필).
4. **Discord 어댑터 풀 계약 ≠ `/api/run` 하나**: 드롭인 백엔드는 `/api/run`+`/events/stream`뿐 아니라 **`/api/login`(omc_session 쿠키)·`/api/inbox/{seq}/approve|reject`(HITL 버튼·seq→thread 브리지)·`/api/inbound`(채팅)** + **쿠키 인증**까지 전부 replicate해야 어댑터가 *무수정*으로 붙는다.
5. **Discord REST 직접 호출엔 proper `User-Agent` 필수**: 없으면 Cloudflare **1010(403)**으로 엣지 차단(토큰·권한 무관). `User-Agent: DiscordBot (url, ver)` 필요. discord.py는 자동 처리(게이트웨이·REST).
6. **비파괴 컷오버 = 소비자 유닛 env 오버라이드**: 어댑터가 `WEB_BASE_URL`을 `load_dotenv(override=False)`로 읽으므로, discord systemd 유닛에 drop-in `Environment=WEB_BASE_URL=http://127.0.0.1:<orch포트>`를 두면 **그 서비스만** orch로 향하고 .env 전역은 무변. 역전 = drop-in `rm` + daemon-reload + restart(**1줄**). 롤백 리허설(플립→역전 정상)을 컷오버 *전* 하드게이트로.
7. ★ **컷오버 seq-space 함정(무음드롭)**: 소비자(봇)가 **영속 커서**(구 소스의 높은 seq)를 갖는데 새 emitter가 **인메모리 0부터** 시작하면, `frames_after(cursor)=∅` + 봇의 `should_post(seq>cursor)` 이중 스킵으로 **메시지가 조용히 사라진다**(에러 0). 컷오버 시 **커서 리셋**(이벤트소스 전환의 본질 단계) 또는 **emitter seq 영속**(재시작 내성)이 필요. 본 코어는 후자 채택. 영속 배포 시 새 seq 파일을 **현재 커서 이상으로 시드**해야 첫 재시작 회귀를 막는다.

## 운영 (라이브)

- `one-man-corp-orch` systemd user 서비스(orch env python·`-m orch.app`·`127.0.0.1:28202`·`Restart=on-failure`·enabled). 구 `one-man-corp-web`/`-supervisor`는 **standby**(트래픽만 전환·즉시 역전용).
- discord 서비스가 orch `/events/stream`을 소비 → 비서봇이 실 채널에 BLUF 게시(라이브 실증, orch 2회 재시작 내성).
- **역전(1줄)**: `rm ~/.config/systemd/user/one-man-corp-discord.service.d/orch-cutover.conf && systemctl --user daemon-reload && systemctl --user restart one-man-corp-discord`.

## ★ 구현 범위 (정직: 명세 전부 아님)

HYBRID는 **명세의 코어+인터페이스만** 구현하고 나머지는 **의도적 이연**이다. SR/renewal 명세 대비:

- **구현·라이브**: LangGraph 오케 코어 §6 · FastAPI+풀 어댑터계약 §6.8 · Discord 주채널 §9.5 · 1사이클 데모 §21(컨셉~QA) · 모델 소비 §4.1 · OTEL 자동스팬.
- **부분**: 페르소나 §6.4(최소셋·9 아님) · 검색 §5.2(dense 임베디드·하이브리드 아님) · 연구 §8(crawl4ai만·SearXNG/browser-use 없음) · 관찰성(콘솔 exporter·Phoenix 서버 아님).
- **미구현(이연·Phase E backfill)**: Redis Sentinel §5.1 · Qdrant 서버+BM25/RRF §5.2 · **WorkAdventure §9** · **ComfyUI 이미지/영상 §4.2·§17** · **에이전트 보안 IPI §11.1** · **Evals/골든셋 §6.9** · **Obsidian vault §5.3·§18** · Opal 인입 §15 · 구조화 숙의 §19 · 연속품질루프 §21.4 · **실제 게임 1편 출시 §28** · Podman/Quadlet 인프라 §2 · 9 페르소나 전체 §26.

즉 "동작하는 회사의 뼈대(오케+인터페이스)"는 섰지만 "명세 전부 구현"은 아니다.

> **후속(2026-06·Path A)**: 9 페르소나 정의·orch 조직그래프(9노드)·다부서 협업(map-reduce)+CEO qwen 은 [10 직원 레이어](10_renewal_org_layer.md)에서 **비파괴 구현**(라이브 코어 0수정·미승격). 이후 [11 cutover+Redis](11_renewal_cutover_redis.md)에서 조직그래프를 **coexist flag로 프로덕션 승격**(durable thread-id 마커)하고 **Redis Sentinel 잠금해제**(클라이언트·dormant·빈 db 게이트). 나머지(WorkAdventure·미디어·evals·vault·IPI·실게임 등)는 여전히 이연.

## 관련 문서

- [10 renewal 직원 레이어](10_renewal_org_layer.md) — **본 코어 위에** 쌓은 9 페르소나·조직그래프·다부서 협업(비파괴·미승격).
- [04 워크플로 엔진](04_workflow_engine.md) — **본 코어가 대체한** 구 커스텀 엔진(`office/`). AG-UI 11이벤트·SSE·위험기반 게이팅 개념은 계승.
- [05 HITL·게이팅](05_hitl_and_gating.md) — HITL 개념(orch는 LangGraph `interrupt()`로 재구현).
- [01 프로젝트 개요](01_project_overview.md).
- 상세 기록(`.omc/`·커밋 `0d158be`): 결정 `.omc/specs/deep-dive-renewal-*` · 3레인/스파이크/Phase A~E `.omc/research/renewal-*` · 계획 `.omc/plans/ralplan-renewal-core-swap.md`.
