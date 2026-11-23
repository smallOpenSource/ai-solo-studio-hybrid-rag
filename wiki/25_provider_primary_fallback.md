# 25 · Provider primary/fallback 라우팅 (Azure LLM · DashScope 이미지)

orch 코어에 **LLM·이미지 provider 의 primary/fallback 라우팅**을 도입한 작업의 재사용 지식.
Owner 지시: 이미지 생성/편집 primary=**DashScope Qwen-image**(현 Server B comfy=fallback)·LLM
primary=**Azure OpenAI Responses gpt-5.4-mini**(현 gemma/qwen=fallback)·Driver=TODO. seam-레벨
래퍼 + 플래그 게이트로 **비파괴·1줄 롤백**. 합의(Architect SOUND+Critic APPROVE)→구현→라이브 6/6.

관련: [09_renewal_langgraph_core] · [17_renewal_p4_evals](eval 격리 함정의 무대) · [20_renewal_p7_media](comfy 게이트웨이) · [23_renewal_verification_methodology].

---

## 설계 — seam-레벨 래퍼 (Option A)

기존 시스템이 두 개의 *결정론 seam*을 이미 갖고 있어 호출측(노드/그래프) 본문 0수정으로 흡수했다:

- **LLM**: `ModelCaller = (prompt, temperature, max_tokens) -> str` 를 `contextvars.ContextVar` + `use_caller()`
  로 바인딩(graph·org_graph·deliberation 각 1개). **기본값(default)** 만 `make_primary_fallback_caller(로컬)`
  로 교체 → `orch/graph.py _ACTIVE_CALLER`·`org_graph.py _ACTIVE_STRATEGY`·`deliberation.py _ACTIVE_JUDGE`.
- **이미지**: `orch/comfy.py` 의 `ComfyTransport` Protocol(`generate(kind,prompt,**kw)->MediaResult`).
  신규 `DashScopeImageTransport` 가 같은 Protocol 을 구현 → `generate_media` 라우터가 교환 가능하게 사용.

> ★**graph-agnostic 헬퍼(순환 회피)**: `orch/azure_llm.py make_primary_fallback_caller(fallback)` 는
> fallback 을 **인자로** 받는다 — `orch.graph` 를 import 하지 않는다. graph/org_graph/deliberation 이
> 헬퍼를 import(단방향) → `graph ↔ azure_llm` 순환 0. 이미지도 동일: `media_provider`→`comfy`(top),
> `comfy.generate_sync`→`media_provider`(lazy)로 단방향.

> ★**call-time 플래그 읽기**(import-time 캡처 금지): `ORCH_LLM_PRIMARY`/`ORCH_IMAGE_PRIMARY` 를 래퍼
> *호출 시점*에 `os.environ.get(...)` 로 읽는다. 이래야 eval 드라이버/테스트가 프로세스 env 로 강제 off 가능.
> 미설정→primary ON(Owner default)·`off`→fallback 직행(byte-identical 롤백).

---

## Azure OpenAI Responses API (≠ chat/completions)

현 LLM 클라이언트(`core/llm_client.py`)는 `/chat/completions` 인데 Azure primary 는 **Responses API** 다 —
요청/응답 형태가 다르므로 어댑터로 흡수:

```python
client = OpenAI(api_key=AZURE_OPENAI_API_KEY,
                base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1",
                default_headers={"api-version": AZURE_OPENAI_RESPONSES_API_VERSION})
resp = client.responses.create(model=AZURE_OPENAI_MODEL_NAME, input=prompt,
                               max_output_tokens=max_tokens)   # ★temperature 미전달
text = resp.output_text
```

> ★**temperature OMIT**: gpt-5.4-mini 는 추론모델 — `temperature` 를 거부/무시할 수 있다. `ModelCaller`
> 의 temperature 인자는 **버리고**, `max_tokens → max_output_tokens` 만 매핑. (spike 가 temperature 없이 성공.)
> 에러는 **예외 타입명만**(`type(exc).__name__`·`from None`) — 응답 본문/url/헤더 0노출(`core/llm_client.py`
> 의 body-append 패턴을 **복제하지 말 것**·시크릿 누출 표면).

---

## DashScope Qwen-image 네이티브 (≠ OpenAI /images/generations)

```python
POST {DASHSCOPE_BASE}/services/aigc/multimodal-generation/generation
body = {"model": <qwen-image-2.0-pro | qwen-image-edit-max>,
        "input": {"messages": [{"role":"user","content":[{"image": <편집입력·data-uri|url>}, {"text": prompt}]}]},
        "parameters": {"size":"1024*1024","n":1,"watermark":False}}
# 응답(동기): {"output":{"choices":[{"message":{"content":[{"image": <url·24h만료>}]}}]}, "usage":{...}}
```

> ★qwen-image-2.0-pro 는 **동기**(task_id 폴링 아님) — 응답에 이미지 **URL**(24h 만료)이 직접 옴 →
> **즉시 download → bytes**. `image_edit` 는 `qwen-image-edit-max` + 입력이미지를 message content 에 포함.
> 영상은 DashScope 미지원 → 라우터가 comfy 로(이미지/편집만 DashScope·video/video_audio=comfy 유지).

`generate_media(kind,prompt,**kw)`(`orch/media_provider.py`): image/image_edit & primary ON & 구성됨 →
DashScope → 실패 시 comfy fallback. `comfy.OpenAICompatTransport.generate` 에 `image=None` 인자를 추가해
fallback `TypeError` 를 방지(comfy 는 미사용·시그니처 호환만). `comfy.generate_sync`→`generate_media` 재지정.

---

## ★★ 함정 — 기본 caller 플립이 evals 를 침묵 오측시킨다 (적대검증이 잡은 CRITICAL)

**기본(default) 모델 caller 를 새 primary 로 바꾸면, "기본값에 의존하던" 경로가 전부 조용히 새 모델로 바뀐다.**
P4 Evals(`orch/evals/runner.py run_anchor/run_golden`·`gate.py score_judge_majority`)는 `caller=None` 일 때
ContextVar **기본값**으로 폴백한다 → 기본을 Azure 로 래핑하면 **골든셋 캡처·qwen judge 가 침묵으로 Azure 가 되어
회귀게이트가 Azure-vs-Azure** 가 된다(F5a 정적창 전제 무효·수 주간 미탐지 가능). 노드가 *깨지지 않고* 측정만 틀린다.

> ★**해법 = eval seam 자기격리**: eval 진입점이 `caller is None` 이면 **로컬 caller 를 명시 바인딩**한다 —
> `eff_caller = caller if caller is not None else graph._gemma_caller`(runner)·`deliberation._judge_caller`(gate)
> 후 `use_caller(eff_caller)`. `use_caller` override 가 Azure-wrapped 기본을 덮으므로 evals 는 플래그 무관 항상 로컬.
> **falsifier**(`test_provider_eval_isolation.py`): `ORCH_LLM_PRIMARY=azure` 에서 default 경로의 azure 호출 수==0
> 단언 → 격리 라인을 지우면 테스트가 깨진다(negative-mutation·비공허).

> ★**결정론 보존**: `use_caller`/`use_strategy_caller`/`use_judge_caller(stub)` 은 ContextVar **전체** override →
> 테스트·evals 가 어떤 caller 든 강제 가능. 래퍼는 *기본값* 에만 작용(기존 테스트 explicit caller= 주입 → 무회귀).

---

## ★ 함정 — 새 LLM 의 *형식 준수* 가 authority 게이트를 침묵 drift 시킨다

`ceo_route` 는 `conf 0.NN`(`graph._parse_confidence`)·`_synthesize` 는 `ESCALATE: yes|no` 를 모델 출력에서
파싱한다. **파싱 실패 시 crash 가 아니라 proxy 기본값**(conf=0.6·escalate=False)으로 폴백 → 새 모델이 형식을
안 지키면 autonomy/HITL 게이트가 조용히 "autonomous" 로 drift(안전관련 제어). "비공 출력" 단언(AC3)만으로는
불충분.

> ★**검증(AC3')**: 라이브 Azure 에 라우팅-포맷 프롬프트를 주고 `_parse_confidence(out)` 가 **non-None**(emit≠proxy)
> 인지 단언(실측 `conf=0.93`). 콜마다 confidence source(emit|proxy) 로깅으로 drift 관측.

---

## 검증 (라이브·mock 0·safe_run 가드)

`SR/renewal/verify_provider_fallback.sh`(PASS=7 exit0) + `tools/p_provider_verify.py`(실호출·`served_by=` 로그
캡처로 primary/fallback 경로 falsifiable 구분·전부 `.omc/guards/safe_run.sh` 래핑):

> 라이브 6/6 — AC3 Azure 서빙 · AC3' 파싱가능 conf=0.93 · AC1 DashScope 1024² **COLOR**(black_ratio=0·빨강원)
> · AC11 edit(빨강원→**파랑원**·qwen-image-edit-max) · AC4 Azure **강제실패→gemma fallback** · AC2 DashScope
> **강제실패→comfy fallback**. 강제실패=잘못된 키를 임시 env 주입(실제 primary 실패 후 fallback 실증·green-theater 0).
> 단위 24 passed·orch 회귀 신규실패 0·ruff clean·동결 drift 0.

**롤백**: `ORCH_LLM_PRIMARY=off` 또는 `ORCH_IMAGE_PRIMARY=off` + orch 재시작 → 현 gemma/qwen·comfy 단독(byte-identical).
**범위 경계**: "Azure primary"=**orch 그래프(cycle/org/deliberation) 한정**. `browser_worker`(tool-calling 필요·로컬
유지)·`core/llm_client.py`(office/bench/knowledge)·evals(로컬)는 의도적 EXCLUDED.

---

## 이번 세션 기타 운영 교훈 (재사용)

- **Redis dormant 함정**: `.env`/app.env 에 Sentinel 접속정보가 있어도 `REDIS_ENABLED` 미설정이면 state_store 가
  **dormant**(미사용). "왜 redis 컨테이너를 따로 띄웠나"의 진짜 답은 *별도 컨테이너가 아니라 플래그 미활성* — `REDIS_ENABLED=true`
  한 줄로 기존 Sentinel 활성(새 컨테이너 0). (WA 전용 `aigs-wa-redis` 는 스펙 §9.1 필수 브릭·별개.)
- **Discord 슬래시 등록 0**: `tree.sync()`(글로벌)이 0개 등록이면(전파 지연 아님·API는 즉시 반영) **guild-scoped
  sync** 로 즉시 확정 — `copy_global_to(guild)` + `sync(guild=guild)`(GUILD_ID 설정 시). 길드 ID 는 채널에서
  `GET /channels/{id}.guild_id` 로 도출.
- **★deep-research × 라이브 키 = 유출**: 구현 과제(엔드포인트·키·샘플코드 제공)를 `/deep-research` 로 돌리면 WebSearch
  에이전트가 **API 키를 외부 검색엔진에 fan-out** → 절대 금지. 키가 섞인 요청은 연구 하네스가 아니라 구현(`/all-in-one`)으로.

코드: `orch/azure_llm.py`·`orch/dashscope_image.py`·`orch/media_provider.py`·`orch/comfy.py`(image=·generate_sync)·
`orch/graph.py`·`orch/org_graph.py`·`orch/deliberation.py`(_ACTIVE_* 기본 래핑)·`orch/evals/{runner,gate}.py`(격리)·
`tools/p_provider_verify.py`·`SR/renewal/verify_provider_fallback.sh`. 키=`config/app.env`(gitignored:
DASHSCOPE_*·AZURE_OPENAI_*·ORCH_*_PRIMARY). 견본=`tests/test_qwen-image.py`·`tests/test_azure.py`(Owner 제공).
