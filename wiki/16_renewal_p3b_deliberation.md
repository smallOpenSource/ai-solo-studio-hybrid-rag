# Renewal P3b — 구조화된 숙의 서브그래프 (§19) · 찬반·Premortem·ADR·소수의견

## 개요

SR/renewal P3b(§19 구조화된 숙의)를 구현하며 얻은 **재사용 패턴·함정**. 고위험·저확신 결정의
*품질*을 ① 독립 제안(상호 비가시) ② Proponent/Challenger(Devil's Advocate) ③ 라운드캡+강제 합성
④ Premortem ⑤ ADR(소수의견 disagree&commit) ⑥ 보정된 정지로 끌어올린다. P3(§20 authority·[[15_renewal_p3_authority]])
가 깔아둔 τ 트리거 위에 얹는 **별도 표면**이다. 핵심 주제 7개: ① **nested DAG 서브그래프로 구조적 종료성**,
② **2-레벨 종료성**(서브그래프 라운드캡 + 게이트 one-shot·재진입 무한루프 급소), ③ **MAJOR-1 재진입
토폴로지**(Command(goto) 재진입·2-in-edge·래퍼 노드), ④ ★**escalate 도달성**(needs_deliberation↔ESCALATE
상호배타 → dead-code 위험 → recommend_escalate), ⑤ **green-theater 차단**(adr_is_substantive·부정변이),
⑥ **Tension B**(합의 아닌 카운터가 종료를 구동), ⑦ 합의·검토 메타.

커밋: `a1f80b1`(P3b 코드 11파일·+1996/−9). 합의계획 `.omc/plans/ralplan-p3b-deliberation.md`(CONSENSUS
APPROVED·Planner→Architect SOUND-WITH-CONCERNS[6우려]→Critic ITERATE[2M+2m+3]→Architect 재검토 SOUND→Critic
APPROVE). 검증 `SR/renewal/verify_phase3b.sh` PASS=10·orch 201 passed. 관련: [[09_renewal_langgraph_core]] ·
[[10_renewal_org_layer]] · [[14_renewal_p2_knowledge_transfer]] · [[15_renewal_p3_authority]] ·
[[17_renewal_p4_evals]](§6.9 evals 가 judge=qwen seam 재사용).

## 1. §19 숙의를 nested DAG 서브그래프로 — 구조적 종료성 (`orch/deliberation.py`)

숙의는 본질적으로 다단계·다역할이라 종료성이 최우선 제약(★프로젝트 무한루프/무한생성으로 2회 다운).
**그래프 레벨 사이클을 두지 않는 DAG**로 설계해 종료를 *구조적으로* 보장한다.

- `build_deliberation_graph()` 토폴로지: `dispatch -Send×N-> propose -join-> debate -> premortem_dispatch
  -Send×N-> premortem -join-> synthesize -> END`. **그래프 사이클 0**(조건부 back-edge·self-loop 없음).
- ★**라운드캡은 그래프 루프가 아니라 `_debate` 노드 *내부* bounded `for _r in range(cap)`**. "라운드 2-3 +
  강제 합성"(§19.2③)을 노드내 for-range 로 표현 → 그래프엔 루프가 없어 무한루프가 *구조적으로 불가능*.
- `recursion_limit`(invoke config·`run_deliberation` 의 `_RECURSION_LIMIT=50`)은 DAG라 자연종료하나
  **defense-in-depth**(off-by-one·미래 토폴로지 변경 시 GraphRecursionError 로 폭주 차단). Architect 실측:
  가드 제거 시 실제로 GraphRecursionError 발생 → 가드가 load-bearing 임을 입증.
- `checkpointer=None`(stateless)·`run_deliberation(directive)` 공유 헬퍼가 cycle/org 래퍼의 build+invoke
  중복과 magic `recursion_limit` 를 단일 출처로(deslop).

## 2. 2-레벨 종료성 + 게이트 one-shot 재진입 (★급소)

서브그래프 *내부* 종료(§1)만으로는 부족하다. 숙의를 **게이트에서 호출**하는 구조라, 게이트가 숙의 결과로
**재평가→다시 숙의**를 반복하면 *게이트 레벨*에서 무한루프가 난다(서브그래프 내부 캡은 부모 재진입을 못 막음).

- 증상: §19.2⑥ "숙의 후 §20 재평가"를 위해 숙의 노드가 게이트로 **재진입**한다. 그런데 재진입한 게이트가
  `needs_deliberation` 을 다시 True 로 평가하면(confidence 가 그대로면) → 또 숙의 → 무한 재-디스패치.
- 폐쇄(**HIGH-2 one-shot 가드**): `needs_deliberation(directive, state, inputs)` 내부에 `not state.get("deliberated")`
  를 AND 조건으로 둔다. 숙의 노드(`_synthesize`)가 `deliberated=True` 를 set → **게이트 방문당 정확히 1회**.
  2회차 게이트는 `needs_deliberation==False` 라 우회하지 않고 *갱신된* confidence 로 §20 tier 라우팅.
- 실증(연극 아님): `test_deliberation_one_shot_terminates_even_when_judge_always_low` 가 `deliberate` 노드를
  **spy 로 감싸 호출 횟수==1** 을 단언. judge stub 이 *항상* 저confidence 를 반환해도(종료가 합의/고conf 에
  의존하면 무한루프가 나야 하는 입력) 그래프가 자연종료. cycle·org 양쪽 동일.
- ★**불변식**: 종료성은 (서브그래프 라운드캡) + (게이트 one-shot) **2-레벨**. 둘 중 하나만 있으면 한쪽
  레벨에서 폭주 가능. 자율/게이팅에 서브그래프를 끼울 땐 *부모 재진입*도 반드시 바운드하라.

## 3. MAJOR-1 재진입 토폴로지 — Command(goto) 재진입 · 2-in-edge · 래퍼 노드

"숙의 후 재평가"를 *어떻게* 배선하느냐는 모호할 수 있다(신규 게이트 노드 vs 기존 게이트 재진입). **기존
게이트 재진입(A해석)** 으로 박제 — 신규 post-deliberation 노드를 만들지 않는다(분기 dispatcher 증식 =
무음 갭 원천·[[15_renewal_p3_authority]] §3).

- cycle: `authority_gate` 가 `needs_deliberation` 시 `Command(goto="deliberate")`. `deliberate` 래퍼 노드는
  서브그래프 1회 실행 후 `Command(goto="authority_gate")` 로 **재진입**. 빌더는 정적 엣지
  `stage_content→authority_gate` 에 더해 `deliberate→authority_gate` in-edge 추가(**2-in-edge**·langgraph
  1.2.6 허용·실측 PASS). **interrupt 는 재진입한 authority_gate 본문이 호출**(신규 노드 아님).
- org: `ceo_route` 가 `deliberate_org` 로 우회→재진입(`START→ceo_route` + `deliberate_org→ceo_route` 2-in-edge).
- ★**래퍼 노드(Opt 2A 실용형)**: 컴파일 서브그래프를 `add_node` 로 직접 꽂으면 DeliberationState↔
  CycleState/OrgState **키정렬 마찰**(예 서브그래프 `confidence` ↔ 부모 `decision_confidence`). 얇은 래퍼
  함수가 `sub.invoke(...)` 결과를 부모 state 키로 *명시 매핑* → 마찰 0·서브그래프 캡슐화(nested compiled) 보존.
- ★**sequencing(escalate·HIGH-1)**: 래퍼는 `adr_payload`·갱신 conf·`deliberated` 를 **재진입 *전* `update` 에
  실어 기록**. 재진입 게이트가 escalate 로 `interrupt()` 하면 `app._run` 이 `aget_state` 로 읽는 snapshot 에
  `adr_payload` 가 이미 머지돼 있어 if-interrupt 의 ADR sink 가 집어낸다(기록 전 재진입 금지).
- ★**순환 import 회피**: `deliberation` 은 `graph._active_caller` 만 import(graph 는 `build_cycle_graph` 안에서
  `deliberation` 을 **lazy** import). `org_graph` 는 `deliberation` 을 lazy import(역방향 금지). Judge(qwen)는
  `deliberation` 자체 ContextVar seam(`_ACTIVE_JUDGE`/`use_judge_caller`)으로 둬 org 의존 0.

## 4. ★escalate 도달성 — 상호배타 dead-code 위험과 recommend_escalate (§19.2.6)

구현 중 발견한 핵심 함정. **숙의→escalate 가 키워드 분류기로는 도달 불가**해서, HIGH-1 의 "escalate 분기
ADR sink"가 *dead code* 가 될 뻔했다.

- 모순: `needs_deliberation` = **가역(two_way)** ∧ conf<τ ∧ 고위험신호. 그런데 `classify` 의 ESCALATE 는
  **비가역(one_way) / owner_reserved / 되돌림비용=높음**일 때만. 두 조건이 **상호배타** → 숙의에 들어간
  (가역) 결정은 재진입 후 `decide()` 가 절대 ESCALATE 를 못 낸다(conf 만 바뀌고 rev/scope 는 directive
  키워드라 불변). ∴ "숙의→escalate" 경로가 코드상 도달 불가 → if-interrupt ADR sink = 영원히 미발화.
- 폐쇄(**§19.2.6 충실 구현**): 숙의의 `_synthesize`(Judge=qwen)가 고위험을 *surface* 하면 escalate 를 권고.
  `recommend_escalate = (judge "ESCALATE: yes") OR (conf < 0.35 하드 floor·§20.1 "비가역&저확신" 정신)` 을
  `adr_payload` 에 실어 반환. 재진입 게이트가 `state["deliberated"] and adr_payload["recommend_escalate"]`
  이면 `interrupt()`. 이로써 §19.2.6("숙의 후 commit&proceed 기본·escalate 는 예외")이 *실제로* 동작.
- 실증: `test_deliberation_escalate_emits_interrupt_and_writes_adr` 가 judge `ESCALATE:yes` →
  **INTERRUPT_REQUESTED 프레임 AND 실제 `vault/decisions/ADR-*.md` 파일** 둘 다를 단언(dead code 아님).
- **일반 교훈**: 두 트리거 조건이 상호배타면, 그 둘을 잇는 분기는 *도달 불가 = dead code* 다. 분기를
  추가하기 전에 "이 경로가 실제로 도달 가능한가"를 falsify 하라. 도달 불가면 메커니즘을 마련하거나 분기를
  지워라(껍데기 분기를 ✔ 으로 두지 말 것).

## 5. green-theater 차단 — adr_is_substantive 부정변이 게이트

"숙의했다"는 *상태 플래그*(`deliberated=True`)로는 검증이 안 된다(연극). 성공을 **ADR 파일/프레임 레벨**로
falsify 한다.

- `_synthesize`/`render_adr` 는 실데이터 결핍 시 placeholder 로 채운다("(대안 미달…", "(합성 실패…",
  "(소수의견 없음)"). 따라서 **빈 숙의도 형식상 ≥2 옵션·소수의견 섹션을 갖는다** → 단순 "섹션 존재" 검사는
  껍데기를 통과시킨다.
- `adr_is_substantive(payload)`: **패딩 마커가 아닌 옵션 ≥2 + premortem 리스크 ≥1**(실 proposals·실 premortem
  이 있었다는 증거)일 때만 True. `_PAD_MARKERS` startswith 로 placeholder 옵션을 제외한다.
- ★**부정변이 테스트가 게이트의 판별력을 falsify**: `test_adr_substantive_gate_rejects_degenerate` 가 빈
  proposals/premortem 으로 만든 껍데기 ADR → `adr_is_substantive==False`(게이트 FAIL) 를 단언. 이 함수가
  *항상 True* 면 게이트는 연극이므로, degenerate→False 가 판별력을 증명한다.
- 프레임 레벨(HIGH-1): `app._run` 의 **3분기 모두**(if-interrupt / else / elif-org)에 `_adr_sink` 를 둔다.
  통합 테스트는 TestClient → `/outbound/frames` bounded-poll 로 TEXT_MESSAGE(BLUF·run_id 매칭·non-empty·
  RUN_FINISHED 후행) 또는 INTERRUPT_REQUESTED **+ 실제 ADR 파일**을 단언(state 아님). "RUN_FINISHED-only/빈
  content"는 FAIL.

## 6. Tension B — 종료는 합의가 아니라 카운터가 구동한다

Devil's Advocate(Challenger)는 §19.2② 상 **반드시 이의**를 제기한다 → 합의는 *구조적으로* 미달. 그러면 무엇이
숙의를 끝내는가?

- 답: **라운드캡 카운터**. `_debate` for-loop 이 cap 회 돌면 무조건 `_synthesize`(강제 합성). 종료는
  *합의 감지*가 아니라 *카운터*가 구동한다.
- ★**영구 금지**: "합의 시 조기종료" 최적화를 절대 넣지 말 것. 넣으면 종료가 Challenger 의 동의에 의존하게
  되는데 Challenger 는 영원히 동의하지 않으므로 → 무한루프(다운 3회째). `test_no_consensus_shortcut_branch`
  가 합의 조기종료 분기 부재를 회귀 가드한다.
- 비용 정직: 트리거된 숙의 1회 = **N(3) propose + 2·CAP(6) debate + N(3) premortem = 12 gemma 콜 + 1 qwen(Judge)**.
  단일 직렬 워커·모델 스왑 6-21s 에서 의미 있는 지연 → §19.1 보수적 AND 트리거(가역∧저확신∧고위험신호)로
  바운드하고 **trigger-rate/폭 N 은 OBSERVE-ONLY**(출시후 캘리브레이션·완료게이트 아님). gemma-affinity:
  org 재진입은 ceo(qwen)를 **재호출 0**(2회차는 저장된 route_reason + 갱신 conf 재사용).

## 7. 독립 제안 격리 + 합의·검토 메타

- ★**§19.2① 상호 비가시는 설계 대상**(가정 금지): langgraph `Send(node, payload)` 는 payload 만 전달(부모
  state 전체 비노출·실측). 하지만 proposals 를 `operator.add` 로 누적하면 *라운드 간* herding 가능. 폐쇄:
  propose 를 **단일 병렬 wave**(누적 proposals 읽는 반복 라운드 아님)·각 Send payload = `{directive, role_prompt}`
  만(**`proposals` 키 부재**). `test_propose_payloads_have_no_proposals_key` 가 격리를 falsify. §19.2①(독립
  제안)과 ②(Proponent/Challenger 토론·서로 봄)는 *아키텍처적으로 분리*된 단계다(한 fan-out 혼동 금지).
- **1B(별도 술어) > 1A(4번째 tier)**: 트리거를 `classify` 안에 넣으면(DELIBERATE tier 추가) 진리표 테스트·
  digest/`authority_tier` 소비처가 전부 깨진다. 별도 술어 `needs_deliberation` 은 `classify` 를 **byte-identical**
  로 보존(`test_classify_truth_table_unchanged`). 관측 사각(숙의 발생이 digest 에 안 남음)은 *직교 필드*
  `deliberation_path` + digest "(숙의)" 마커로 해소(tier 도메인 불변).
- **langgraph 1.2.6 실측 probe 가 합의를 앵커**: Architect 가 ContextVar 서브그래프 전파·Send payload 격리·
  2-in-edge 재진입+interrupt·하드캡 종료를 *실제 코드 실행*으로 검증(주장 아님). 고위험 토폴로지 결정은
  설치된 런타임에 probe 하라.
- **OMC 리뷰어 터스 반환**(VERDICT 만·본문 누락): 트랜스크립트 JSONL 의 최장 assistant 텍스트 블록을 파싱해
  본문 findings 를 추출·반영([[14_renewal_p2_knowledge_transfer]] §7·[[15_renewal_p3_authority]] §7 동일 교훈).
- ★**에이전트가 종단 API 오류(529 Overloaded)로 죽으면 재스폰 말고 SendMessage 로 재개**: P3b Architect 가
  계획·스펙·코드·langgraph 문서를 13 tool 정독한 *직후* 최종 응답에서 529 로 0토큰 반환했다. 새 에이전트로
  재스폰하면 그 정독/probe 가 전부 날아간다 → `SendMessage(to=<agentId>)` 로 **웜 트랜스크립트에서 재개**(이미
  한 분석 위에서 결론만 산출). 일시 과부하·rate-limit 은 재시도성 → 컨텍스트 보존이 토큰·시간 절약.
- **합의 iter2 재검토 루프가 dead-code 를 잡았다**: Critic 이 ITERATE(2M+2m+3) 로 §4 escalate 도달성 결함을
  지목 → Planner 반영 → Architect 재검토(실측) → Critic 재검증 APPROVE. *단발* 리뷰였다면 if-interrupt ADR
  sink 가 dead code 로 통과했을 것. 고위험 표면은 ITERATE→재검토 닫힌 루프(non-APPROVE 시 full loop)가 값을 한다.
- ADR 번호 N=2 동시성: `StateStore.incr("adr:seq")`(원자적 Redis INCR) 우선·폴백 glob max+1(anchored regex
  `ADR-(\d{4})-` 로 NNNN 만 추출·uuid 접미 무시) + uuid 접미. "단일워커라 경쟁 0" 가정은 천장=동시성 2 에서 거짓.
