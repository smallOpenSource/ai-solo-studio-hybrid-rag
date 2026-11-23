# Renewal P4 — Evals & 연속 품질 루프 (§6.9 + §21.4) · capture-then-batch·종료코드 우선순위

## 개요

SR/renewal P4(§6.9 측정형 Evals + §21.4 연속 품질 루프)를 구현하며 얻은 **재사용 패턴·함정**.
핵심은 ① **gemma-affinity 를 깨지 않고 qwen judge 를 쓰는 capture-then-batch 아키텍처**, ② **eval 이
프로덕션을 오염시키지 않는 격리**, ③ **회귀게이트가 거짓-통과/거짓-차단하지 않게 하는 종료코드 우선순위**,
④ ★**"단위 synthetic 통과·실 데이터 실패" 통합버그 클래스와 그 유일한 차단법**(본 페이즈 최대 교훈).

커밋: 코드 `84421f7`(16파일·+3679) · vault `2366ccd`(골든셋 68앵커 + 교훈 템플릿·aigs 로컬). 합의계획
`.omc/plans/ralplan-p4-evals.md`(Planner→Architect SOUND-WITH-CONCERNS[7봉합]→Critic ITERATE[3 MAJOR]→
v3→APPROVE·구현 critic ACCEPT). 검증 `SR/renewal/verify_phase4.sh`(13게이트·exit0·orch 289 passed·freeze
drift0). 관련: [[06_phase2_evaluation]] · [[07_quality_engine]] · [[14_renewal_p2_knowledge_transfer]]
(handoff/knowledge_lifecycle 재사용) · [[15_renewal_p3_authority]] · [[16_renewal_p3b_deliberation]]
(judge=qwen seam 공유).

## 1. capture-then-batch-judge — 다른 judge 모델을 핫패스 스왑 없이 쓰는 법 (F5a)

문제: §6.9 LLM-judge=로컬 qwen 인데, 핫패스는 gemma 전용(gemma-affinity·모델 스왑 16.5s+6.15s). eval 이
앵커마다 gemma invoke 후 qwen judge 를 부르면 **per-anchor 스왑**이 폭발한다.

- 해법 = **2-단계 분리**: `orch/evals/runner.py` 가 그래프를 **gemma 로만** invoke 해 출력/도구호출/검색결과/
  라우팅을 캡처 레코드(JSONL)로 *전량* 적재 → `orch/evals/judge.py` 가 그 캡처만 입력받아 **qwen 1회 스왑**으로
  배치 채점. judge 는 **라이브 그래프/Redis/app 미접촉**(judge.py:33 `use_judge_caller/_ACTIVE_JUDGE 만 import`).
  per-anchor 스왑 0·스왑은 배치 경계 1회(judge.py:10).
- judge=qwen seam 은 **[[16_renewal_p3b_deliberation]] 의 `orch.deliberation.use_judge_caller`/`_ACTIVE_JUDGE`
  를 그대로 재사용**(judge.py:9·신규 qwen 클라이언트 0). 숙의와 evals 가 동일 qwen seam 공유.
- ★일반화: "핫패스와 다른 모델을 써야 하는 오프라인 작업"은 *캡처를 트레이스화*해 모델별로 단계 분리하면
  스왑을 작업 경계 1회로 누른다. F5a Owner 결정 = (ii) 정적창 오프라인 배치(co-resident 소형 judge 대신).

## 2. eval 러너의 프로덕션 사이드이펙트 격리 (그래프를 "구경만" 하기)

eval 은 실 그래프를 돌리지만 **프로덕션을 건드리면 안 된다**(라이브 Discord emit·Redis `aigs:*`·OTLP
스팸·[[13_discord_replay_and_demo_gates]] 재방송 인시던트 이력).

- ★**핵심 사실**: orch 그래프의 부수효과(`_adr_sink`·`blackboard_sink`·`_digest_append`·outbound emit)는
  **전부 `app._run` 에 있고 그래프 *노드* 는 I/O-0**([[09_renewal_langgraph_core]] graph.py 노드 순수). ∴
  `build_cycle_graph(checkpointer=None)`/`build_org_graph(checkpointer=None)` 를 **`app._run` 우회로 직접
  invoke** 하면(runner.py:9,16) outbound emitter·Redis store·OTLP exporter 를 *주입하지 않는* 한 오염 0.
- **checkpointer=None + `__interrupt__` 키**: escalate tier 가 `interrupt()` 에 닿으면 checkpointer=None 은
  보통 에러지만, langgraph 는 반환 dict 에 `__interrupt__`(Interrupt 목록) 키를 실어 돌려준다(runner.py:19
  실측). 러너는 이를 감지해 route="escalate" 캡처(에러 0). MemorySaver 도 ephemeral 격리지만 checkpointer=None
  + `__interrupt__` 감지가 더 단순하고 durable/Redis/outbound 를 *구조적으로* 미접촉(runner.py:22).
- **정적 격리 가드(falsifiable)**: `verify_phase4.sh` [6] 이 runner.py 소스에 `app.create_app`/`OutboundEmitter`/
  `blackboard_sink`/`_adr_sink` 의 *import 문·호출 사이트*(괄호 포함)가 없음을 grep. ★**docstring 산문 오탐
  주의**: 가드를 *설명하는* 주석("…를 import 하지 않는다")이 단순 단어 grep 에 걸린다 → **import 문(`^from…import`)
  + 호출(`X(`)만** 매칭해 산문을 배제(gate 6/7 둘 다 이 방식). [[15_renewal_p3_authority]] 와 동류 교훈.

## 3. ★회귀게이트 종료코드 우선순위 — 거짓-통과/거짓-차단 동시 차단 (MAJOR-1)

회귀게이트의 가치는 "임계 미달 시 *실제로* 머지를 막음"(DR-2). 그런데 judge 는 비결정적이라 게이트를
플래키화한다. 해법 = **권한 분리**(gate.py:7-16):

- **deterministic scorer verdict = 종료코드의 *유일* 권한.** 임계 미달 → `sys.exit(비-제로)`.
- **qwen judge 다수결 = warn-only(비-블로킹).** 임계 미달이어도 **exit code 변이 0** — stderr `[JUDGE-WARN]`
  + 리포트만. **judge 경로는 `sys.exit` 호출/exit code 변이 *금지*.** §6.9 line325 "평가 점수 게이트 포함"을
  *비-블로킹 포함*으로 리터럴 충족(line324 "프로그램 채점 가능한 건 코드로"가 "블로킹은 결정론에만"을 뒷받침).
- ★**안티패턴(green-theater 급소)**: 구현자가 게이트 끝에 `if judge_below: print("[JUDGE-WARN]"); sys.exit(0)`
  를 쓰면 **직전 비-제로를 리셋해 실 회귀를 가린다.** "implied by mechanism"(deterministic 이 먼저 exit) 은
  함정 — 명시 AC 가 없으면 그 경로가 합법으로 작성된다.
- **falsifiable 가드 = 실 subprocess returncode 3케이스**(test_evals_gate.py): (1)det-FAIL→exit≠0 (2)det-PASS
  ∧ judge-WARN→exit0+`[JUDGE-WARN]` (3)**det-FAIL ∧ judge-WARN 접합→exit≠0**(judge warn 이 block 못 가림).
  상태플래그 아닌 *프로세스 종료코드*.
- ★**MINOR(의도적 선택)**: judge 배치는 deterministic 차단이 확정된 뒤에도 *일부러* 돈다(gate.py judge-on-block
  주석) — 차단 런 진단 가시성 + 접합 케이스의 `[JUDGE-WARN]` 실증 때문. 단락(`not det_blocked`)은 접합 테스트를
  깬다. 비용은 F5a=(ii) 오프라인 배치라 수용. "낭비처럼 보이는 코드"는 *의식적 선택*임을 주석으로 박을 것.

## 4. N/A anti-abuse — N/A 를 탈출구 아닌 타입된 분기로 (MAJOR-2)

scorer 가 적용 불가 차원에 `N/A` 를 반환하면(분모 제외) **"전부 N/A 로 도망쳐 항상-PASS"** 탈출구가 열린다.

- 규칙(scorers.py:7-9·`score_tool_selection`): N/A 는 **`expects_tool==False`(no-tool 앵커)에서만 합법**.
  `expects_tool==True` 인데 도구가 틀리거나 기대 도구 미선언이면 **반드시 FAIL(절대 N/A 아님)**.
- 골든 스키마가 `expects_tool` 플래그로 N/A 합법성을 *타입*한다. negative-mutation 에 **positive tool-anchor
  (도구호출 있고 인자 틀림)→FAIL** 를 포함해 탈출구를 닫음(test_evals_scorers.py).
- ★일반화: scorer/검증기가 "적용 안 됨"을 반환할 수 있으면, 그 합법성을 **데이터 스키마로 타입**하고
  negative-mutation 으로 *오분류→PASS* 경로를 잡아라. 안 그러면 N/A 가 vacuous-PASS 가 된다.

## 5. ★★"단위 synthetic 통과·실 데이터 실패" 통합버그 클래스 (본 페이즈 최대 교훈)

P4 에서 **동일 형태의 버그 2건**이 났고 둘 다 단위 스위트(synthetic 픽스처)는 GREEN 인데 *실 golden* 에선
깨졌다. 병렬 executor 가 **계약(데이터 스키마의 *위치*)을 모호하게 합의**해 갈라진 게 공통 원인:

- **C-3**: `score_pass_at_k` 가 `anchor["answer_doc"]`(top-level)를 읽었는데 골든은 `anchor["expected"]
  ["answer_doc"]` 에 둠 → 실 rag 앵커 전부 FAIL. 단위 테스트는 자기 synthetic 앵커를 top-level 로 만들어 통과.
- **MAJOR**(critic 포착): rag 앵커가 `expects_tool:true` 인데 `expected.tool` 미선언 → 게이트가 모든
  expects_tool 앵커에 `score_tool_selection` 적용(gate.py) → §4 규칙으로 무조건 FAIL → **완벽한 캡처로도 실
  golden 게이트 exit=1**(결함 0 시스템을 거짓-차단). 단위 픽스처엔 그런 앵커가 없어 미검출.
- ★**유일한 차단 = 실 데이터 위 cross-file 통합 테스트**(unit synthetic 은 구조적으로 못 잡음):
  - `test_gate_satisfiable_on_real_golden`(test_evals_gate.py:256): 실 golden 전 앵커에 *type 별 완벽 캡처*를
    구성→`score_deterministic` `pass_rate==1.0`/`failed==0` 단언("결함0 시스템이면 게이트 통과 가능"=구조적
    un-passable 앵커 0).
  - `test_rag_anchor_scorable_by_pass_at_k`(test_evals_golden.py:266): 실 rag 앵커가 scorer 로 채점 가능
    (정답 메타 위치 정합).
  - `test_expects_tool_requires_expected_tool`(test_evals_golden.py:293): `expects_tool:true→expected.tool`
    필수 스키마 불변식(갭이 게이트 아닌 *골든 검증* 시점에 시끄럽게 드러남).
  - 두 가드 모두 **비공허**(pre-fix 재구성서 FAIL·post PASS 실증)=green-theater 아님.
- ★**규칙**: *데이터 파일 + 그걸 소비하는 코드* 가 분리되면(특히 병렬 에이전트가 각각 만들면), **실 데이터 위
  end-to-end 테스트 1개**가 계약 드리프트의 유일한 방어선이다. 단위 synthetic 은 양쪽이 *같은 착각*을 공유하면
  같이 통과한다. 적대 critic 이 "실 golden 으로 돌려봤나?"를 물어 MAJOR 를 실증으로 잡았다(=`ralph --critic=critic`
  의 가치).

## 6. fake embedder — bge 없이 Qdrant 배관을 결정론 검증

bge 엔드포인트는 DEGRADED(12~41s/콜·[[14_renewal_p2_knowledge_transfer]] 형식 C2 이연 원인). eval/품질루프
테스트가 실 bge 를 때리면 slow/flaky/green-fake(타임아웃을 회수실패로 오판).

- ★**함정**: `KnowledgeStore(qdrant_url=":memory:")` 는 **Qdrant 클라이언트만** 인메모리화한다 — 임베딩은
  *별개*다. embedder 미주입 시 `_get_embedder()` 가 **실 BGEClient 를 지연 생성**(store.py). 즉 `:memory:` 만
  쓰면 여전히 실 bge 를 때린다.
- 해법: `KnowledgeStore(__init__(embedder=...))` 주입 seam 에 **토큰해시→고정벡터 fake embedder**(dep-0·
  결정론) 주입. 색인/검색 *배관* + scorer *로직* 을 실 bge 미접촉으로 결정론 검증.
- ★**정직(M-1)**: fake-embedder Pass@k/RPI 회수는 *배관·scorer 로직* 검증이지 *검색 관련성/의미 회수* 검증이
  **아니다**(고정벡터 ⇒ dense 랭킹 임의 ⇒ hit 은 구조적 우연). 실 검색품질은 실 bge/OBSERVE 경로(과대주장 금지).
- 공유 헬퍼는 `orch/tests/conftest.py::FakeEmbedder`(deslop 시 test_evals_scorers·test_quality_loop 중복
  통합·기존 `stub_caller`/`stub_searcher` 모듈레벨 관례 정합).

## 7. §7 HARD — Opus 정밀검수는 *사람*·코드는 배관만

§21.4 ① 정밀검수는 "최강 모델(Claude Code Opus 4.8)"이지만 §7 과금 제약상 **대화형 사람 운용만**
(`-p`/Agent SDK/벤더 API 자동호출 금지).

- `orch/quality_loop.py` 는 Opus 를 **자동호출하지 않는다**(quality_loop.py:8·anthropic/SDK/API 키 환경변수
  부재·`verify_phase4.sh` [9] 정적 grep). 코드는 *배관*: §29 감사리포트(사람 산출) 파서 → defect → handoff
  (`orch.handoff`) → 교훈 `vault/quality/`(`capture_and_index`) → RPI 선조회(`search_hybrid`). human-trigger
  hook = 파일경로 인터페이스(테스트는 fixture 리포트 주입).
- §21.4 루프 = **상태기반 프로세스 루프**(git/Qdrant/작업큐)지 Claude Code `/loop` 크론이 아니다. 연속성은
  vault·Qdrant 가 보장(세션은 짧은 바운드 반복). 재검수 라운드캡=3(quality_loop.py:82 `ROUND_CAP=3`·
  termination P0·상한 도달 시 Owner 에스컬레이션/범위축소).

## 8. 정직 OBSERVE-ONLY 경계 (green 주장 금지)

코드 100%·런타임은 외부의존이라 *관측전용*으로 정직 스코핑(BLOCKED≠pass·green-fake 0):

- **judge 실 qwen 일치율 *수치*** = OBSERVE(비결정·slow). 산식의 결정론 검증(흔들리는 stub→≥M/K)만 ✔ 게이트
  (C-5 이원화). 진리표/단일런을 "결정론 judge"로 오판매 금지.
- **실 bge 회수** = slow/OBSERVE(DEGRADED·§6). **Phoenix 자기개선 자동수집**(운영 트레이스→라벨→골든) =
  쿼리 API 의존 → "code 100% / runtime pending: Phoenix query API". 자기개선의 *사람 라벨→골든 append→재실행*
  경로만 falsifiable 구현(단발·루프0).
- **OQ-2**: 골든셋 ≥50 은 구현자 초안이나 §6.9 "핵심 시나리오를 사람이 작성" → **Owner 검수 = P4a close-gate**
  (순수 구현자생성은 리터럴 미스). `verify_phase4.sh` [1] 에 ACTION 표기.

## 9. 합의·프로세스 메모

- **F5a precondition**: P4 착수 *전* Owner 가 qwen-judge 배치를 결정해야 한다(gemma-affinity 최상위). 신규설계
  권위([[renewal-spec-is-authority 메모리]])상 §6.9 line321 "로컬 Qwen3.6"이 자체 qwen judge 를 리터럴
  라이선스 → arize-phoenix 라이브러리(신규 의존·핫패스 스왑 통제권 상실)보다 자체 judge+seam 재사용이 *더* 정합.
- **합의 패턴**: Architect 가 *계획*에서 격리/falsifiability 갭을 선포착(SOUND-WITH-CONCERNS 7봉합)→Critic 이
  *구현*에서 실 golden MAJOR 를 적대 실증(ACCEPT-WITH-RESERVATIONS)→봉합 후 재검증 ACCEPT. ★리뷰어 ACCEPT-
  WITH-RESERVATIONS = 비차단이나 reservation 은 실제 갭 → 닫고 재검증(self-approve 금지·리뷰어가 grep 으로
  파일사실 재확인).
- OMC 리뷰어 터스 반환(VERDICT-only)→트랜스크립트 JSONL 본문 추출 or SendMessage 재개([[14]][[15]][[16]] §7
  동일 교훈).
