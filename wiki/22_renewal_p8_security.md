# P8 보안 — §11.1 IPI 방어심층화 런타임 (스팟라이팅·provenance 전파·quarantine·권한·적대)

SR/renewal P8 — §11.1 간접 프롬프트 인젝션(IPI)/RAG 포이즈닝 방어심층화. P9 provenance 태깅을 런타임에 *소비*해 Lethal Trifecta를 끊는다. 재사용 지식:

## ① 방어심층화 6계층 (단일 방어 불가·OWASP LLM01)
①출처 신뢰표시(provenance·P9)→②스팟라이팅→③권한 분리→④인입 새니타이즈(strip+격리)→⑤최소권한 HITL(§23.6)→⑥적대 테스트. ★**Lethal Trifecta**(민감접근+비신뢰입력+실행/유출경로) 중 P8은 **1변 구조제거**(권한분리=실행/유출경로)+**1변 완화**(스팟라이팅=비신뢰입력)+**1변 무변**(민감접근). "끊김" 과장 금지·정직.

## ② ★provenance 회수 전파 (E-1·CRITICAL 함정)
- **함정**: `capture_and_index`(P9)가 `chunk.meta['provenance']`에 저장해도, **회수 경로(`knowledge/search.py`)가 `{text,source,score}`만 조립해 meta를 폐기**하고 공유 계약 `core/knowledge_contract.py SearchResult`에 provenance 필드가 없으면 → 라이브 회수에 provenance 신호 **0** → 스팟라이팅이 항상-비래핑(침묵 붕괴). mock 청크에 provenance 박은 유닛은 통과(green-theater).
- **해법**: `SearchResult.provenance: str|None = None` **add-only**(소비자 무회귀)+`search.py`가 `chunk.meta.get("provenance")` 적재+`knowledge/service.py`(FastAPI `response_model`)/`office/knowledge_client.py` HTTP 왕복 보존. ★**실 service.py 왕복 테스트**(pydantic 모델만 아님·직렬화 경계 손실 재발 차단). ★**fail-safe**: provenance=None/unknown(legacy pre-P9 코퍼스)은 외부수집 컬렉션서 **untrusted 간주**(침묵 비래핑 금지).

## ③ ★스팟라이팅 주입 지점 (CRITICAL 함정)
- **함정**: org_graph 부서 노드는 **지식 회수를 안 한다**(prose 산출만·grep search=0). RPI `quality_loop.prefetch_quality_lessons`도 **라이브 cycle 그래프 dead**(build_cycle_graph 미포함). → org_graph에 스팟라이팅 seam을 걸면 **대상 없는 표면**.
- **해법**: 실 라이브 회수→프롬프트 경계 = **cycle `orch/graph.py:372 stage_research` 단일**. 회수 top-1 `finding`을 `artifacts["research"]`에 넣기 전 `wrap_untrusted` 경유(provenance 구동·trusted 비래핑)·`_run_stage`로 design/content/finalize **다운스트림 프롬프트까지 생존**. `orch/spotlight.py wrap_untrusted`=순수 결정론(`<untrusted_data source_url>`+불복종 프리앰블). graph.py=라이브 cycle 코어 → **add-only seam**(기존 추출 byte 보존).

## ④ ★★green-theater 함정 (자작 초안→적대 독립검토 가치)
- API 과부하로 Planner 부재→오케스트레이터 자작 초안이 **라이브에 없는 데이터흐름(provenance 회수)·표면(org_graph seam) 위에 설계**됨. mock provenance 주입 유닛·org seam 가정 테스트는 통과해도 **라이브 방어 0** — 정확히 계획이 막으려던 green-theater에 정찰오류로 self-노출.
- **교훈**: (1) 자작 초안이라도 **적대 독립검토(Architect+Critic) 필수**(2 CRITICAL 포착). (2) 보안 테스트는 **라이브 데이터흐름 위에서**(mock 신호 주입 금지)·**부정변이**(방어 비활성→저항 약화 관측)로 falsifiable. (3) 결정론 게이트(stub caller·문자열 매칭)=회귀 차단·실 LLM 다회=OBSERVE(비결정 분리).

## ⑤ quarantine (strip은 위반 아님·격리는 *추가*)
- **함정**: "strip은 silent 손실이라 §11.1④ 위반"=spec 오독. §11.1④는 "탐지→**제거/격리** 둘다 허용". strip(P9) 자체는 정합·**진짜 갭=격리 보류+통지의 부재**.
- **해법**: P9 라인 strip **유지** + 문서 injection-dominant(탐지 임계 초과) 시 `vault/quarantine/<ts>-<slug>.md` 라우팅+**인덱싱 차단**+통지. `ingest_files` 반환 **add-only**(`quarantined` 키)·호출자 무회귀. 입도=문서임계(라인 1-2개는 strip·문서 지배 시 격리).

## ⑥ ★.claude/settings.json §23.6 (운용 CC 자신의 권한)
- 이 파일은 *사람-운용 Claude Code* 권한을 바꾼다(자율 orch 런타임 무관). **함정**: spec ".env read deny" 리터럴을 "운용 편의"로 완화하면 [[renewal-spec-is-authority]] 박제(리터럴>연속성) 위반. 또 .claude는 자율 에이전트 무관(자율은 .env 접근 경로 없음)·`Read(**/*.env)` deny≠`Edit`.
- **해법**: §23.6 **전체 deny/ask/allow 블록 verbatim**(deny=두 .env·sudo·rm-rf·ask=git push/podman/systemctl/loginctl·allow=Read/Edit/dev·**defaultMode default**). ★`defaultMode default`가 미목록 루틴도구(grep/ls/sed)를 무파손 보장(deny만 차단·ask만 프롬프트). 기존 hooks 블록 **add-only 병합 보존**. git tracked(롤백 가역)·e3 운용 무파손 *측정*(가정 아님)·증명된 파손만 최소 예외.

## ⑦ retrieval-borne vs prompt-borne 적대 (중복 회피)
- P4 적대 케이스(golden.jsonl g-inj-*)=**prompt-borne**(악성 지시가 input directive 자체). P8 스팟라이팅이 막는 위협=**retrieval-borne**(오염 청크가 *회수되어* 프롬프트 주입). 둘은 다른 위협면 → P8 신규는 retrieval-borne 앵커(g-retr-*)만(P4 중복0·via 필드 구분). runner는 cycle+stub searcher로 오염 회수 주입(org는 searcher 미사용→cycle 한정).

## ⑧ ACTION 해소 — provenance 백필 + 실모델 OBSERVE (2026-06-23)

- **legacy provenance 백필(OD-P8-B)**: 178pt 전부 provenance=None→fail-safe untrusted 우회였던 것을 `tools/p8_provenance_backfill.py`로 실재화. ★**Qdrant `set_payload` 재임베드 0**(provenance=벡터 무관 payload·bge 12s/콜 회피)·payload.source 기준 분류(**https 크롤 123→untrusted[+source_url] / 상대경로 내부 55→trusted**)·scroll 백업(가역·239KB)·멱등(재실행 skip=178). `verify_phase8.sh` b2 = ACTION→**OK 게이트**(`--verify` 로 with_provenance=178 missing=0 단언). 형식=`capture_and_index` 동형 `meta.provenance={provenance,source_url}` dict(`search.py _chunk_provenance` 호환).
- **실모델 적대 OBSERVE**: `tools/p8_adversarial_observe.py`(`runner.run_anchor(caller=None=실 gemma·searcher=오염 stub·checkpointer=None 격리)`). ★**OBSERVE 는 방어가 *실제 행사됐는지* 검증해야 vacuous 가 아니다**: 1차 g-retr "조사" 지시 → 실 gemma 가 **end 라우팅** → stage_research 미실행 → `search_results` 빈 = **not_exercised(인젝션 미주입·vacuous PASS)**. 수정 = **build 라우팅 지시**(클리커/서바이버/퍼즐·golden g-route build)로 stage_research 실행 보장 + g-retr must_not_contain 합집합 채점 → **resist 15/15·rate 1.000·error 0**(실 gemma + 스팟라이팅이 9 금지토큰 전부 불복종). 비결정이라 OBSERVE 유지(회귀 차단=결정론 stub 게이트 `test_evals_retrieval_borne`). ★재사용 교훈: OBSERVE/적대 측정은 `search_results` 비어있지 않음(=방어 경로 실행)을 단언해야 "통과"가 진짜다(green-theater 차단).

> 검증: `SR/renewal/verify_phase8.sh`(4-counter·exit0 **PASS=12/FAIL=0/ACTION=1/BLOCKED=0**·2026-06-23 갱신)·orch 497 passed·freeze drift0. 잔여 1 ACTION=실모델 적대 OBSERVE(본질적 비결정·실측 resist 15/15). 관련: [09_renewal_langgraph_core] · [17_renewal_p4_evals] · [20_renewal_p7_media] · [21_renewal_p9_research_intake].
