# Renewal P3 — 자율·의사결정 권한 (§20) · 라이브 코어 직접 재작성

## 개요

SR/renewal P3(§20 자율성)를 **OD-E=ii 직접 재작성**으로 구현하며 얻은 **재사용 패턴·함정**.
"매 빌드 하드-interrupt"(`hitl_gate` 무조건 `interrupt()`·confidence 0.55 하드코딩)를 **3-tier 권한
게이트**로 교체 — 가역 결정은 무차단 진행, 비가역만 escalate. 핵심 주제 6개: ① 결정론 §20.5 분류기
(LLM 0), ② **라이브 langgraph 노드 본문 직접 재작성**(N1 함정), ③ ★**게이트 발화조건 변경 → 완료-방출
갭**(green-theater 급소), ④ confidence 정직(τ OBSERVE-ONLY), ⑤ digest dead-path + 게이트-미실행,
⑥ 발화조건이 바뀐 테스트의 *의미* 이관.

커밋: `7de0ff1`(P3 코드 14파일·+1038/-78). 합의계획 `.omc/plans/ralplan-p3-authority.md`(Architect
SOUND-WITH-CONCERNS 7우려 반영 + Critic APPROVE)·Architect 리뷰 `.omc/research/p3-architect-review.md`.
관련: [[09_renewal_langgraph_core]] · [[10_renewal_org_layer]] · [[14_renewal_p2_knowledge_transfer]] · [[16_renewal_p3b_deliberation]](§19 숙의는 P3 τ 트리거 위에 얹힘).

## 1. 결정론 §20.5 권한 분류기 (LLM 0) — `orch/authority.py`

§20.5 가역성 τ 표는 **순수 결정표**라 LLM 0으로 충실 구현 가능(핫패스 gemma-affinity 보존).

- `classify(reversibility, reversal_cost, confidence, scope)`(authority.py:87): **4-입력**. 규칙(§20.5:773
  "one-way OR 되돌림비용=높음 OR Owner-reserved → Escalate" = **셋 다 co-equal 트리거**):
  owner_reserved → ESCALATE · one_way → ESCALATE · **reversal_cost=="high" → ESCALATE**(authority.py:110·
  ★Architect 포착: 되돌림비용은 two/one-way와 *독립* 축 — "비싼 가역"을 under-escalate 하지 않게) ·
  그 외 two_way: conf≥τ_auto(0.75)→AUTONOMOUS · ≥τ_notify(0.60)→NOTIFY · 미만→NOTIFY(가역이라 진행).
  tie-break = `>=`(경계 명시).
- `assess(directive, state)`(authority.py:132): 키워드 derivation(owner_reserved/one_way/high_cost) +
  confidence(state emit 우선·proxy 폴백). `decide()`(authority.py:155) = assess→classify→**§20.2 RPI demote**
  (AUTONOMOUS인데 citation 없으면 NOTIFY — "자율은 근거가 전제")→**보수 demote**(미escalate 고위험동사 →
  NOTIFY·§20.5 "애매하면 Notify").
- ★**substring 매칭 = SAFE-direction accepted limitation**: 키워드는 부분일치라 "brand-new"⊃"brand"
  같은 false-positive 과(過)escalate 가능하나 §20.5 보수 방향(차단 과다 > 위험 통과)이라 수용. 승인
  피로 관측 시 word-boundary(출시후 튜닝).

## 2. 라이브 langgraph 노드 본문 직접 재작성 (OD-E=ii) — N1 함정

레거시 노드(`hitl_gate`)를 **노드 본문 직접 교체**(→`authority_gate`·graph.py:407)로 재작성. 신규 변종+
플래그(코드스워프)가 아니라 직접 교체(downtime 허용·spec END STATE=신규 코어 유일).

- ★**N1 "하류 전체 재배선 필요"는 과장 — 노드 본문 교체엔 불요(실증)**: langgraph 1.2.6 에서
  `Command(goto="X")` 는 **정적 엣지 선언 없이** 등록된 노드로 동적 라우팅한다. 기존 `ceo_route`/
  `hitl_gate`/org `escalate` 가 전부 `add_edge` 없이 goto 로 동작(증거). ∴ `authority_gate` 가 모든 tier
  에서 `Command(goto="stage_qa")` 를 반환하면 토폴로지 동일·하류 재배선 0. 빌더는 노드명만 교체
  (graph.py:508/516).
- ★**불변식: 전 tier 가 단일 노드(`stage_qa`)로 수렴**해야 토폴로지가 보존된다. 그리고 **goto 타깃은
  런타임 문자열**(typo → 컴파일 에러 아닌 무음 mis-route) → 빌드정합 게이트(`import` dangling 0)+라이브
  왕복이 load-bearing(선택 아님).
- escalate 는 여전히 `interrupt()` 호출 → 기존 resume/`_resolve_thread`/Discord 버튼 경로 **무손상**
  (버튼은 pending interrupt 시만 발화·Autonomous/Notify 는 interrupt 미생성).

## 3. ★게이트 발화조건 변경 → 완료-방출 갭 (green-theater 급소)

P3 최대 함정(Architect HIGH). **"항상 정지하던 노드를 *가끔* 정지 안 하게" 만들면, 정지 전제로 짜인
완료 후처리(dispatcher)에 *새 비정지 완료 경로*에 대한 분기가 없어 무음이 된다.**

- 증상: `app.py _run` post-invoke 가 `if snap.interrupts: ... elif kind=="org": ...`(else 없음)이었다.
  레거시 cycle 은 **항상** hitl_gate 에서 정지 → 항상 첫 분기 → BLUF 는 resume 시 `_resolve_thread` 가
  방출. P3 Autonomous cycle 은 **인터럽트 없이 finalize 도달** → 두 분기 어디에도 안 걸려 **BLUF 가
  state 에만 있고 Discord 엔 무음**(=§20 자율 happy-path 가 프로덕션서 침묵). 단위/스텁 테스트는
  `final["bluf"]`(ainvoke 반환)로 통과해 갭을 못 잡는다(green-theater).
- 폐쇄: `_run` 에 **`else:` 분기 추가**(app.py:456·비-인터럽트 cycle 완료 → `emit_text_message(bluf)+
  emit_run_finished`·org 분기 미러·add-only Tier-2). route=end(빌드 아님)은 bluf 없어 무방출(레거시 보존).
- ★**falsifiable 게이트 = 프레임 레벨**(state 아님): TestClient POST → `/outbound/frames` bounded-poll →
  `TEXT_MESSAGE` 프레임이 **run_id 매칭 + content 비어있지 않음 + RUN_FINISHED 후행**(RUN_FINISHED-only/
  빈 content = FAIL). `ainvoke` 반환 state 만 보면 fire-and-forget `_run` 의 emit 경로를 안 거친다.
- **일반 교훈**: 자율/게이팅 변경 시 *완료·방출·후처리 경로*를 반드시 동반 점검. 분기 dispatcher
  (if/elif·switch)는 새 상태가 생기면 빠진 else 가 무음 버그.

## 4. confidence 정직 — τ OBSERVE-ONLY · proxy = 배관증명

- confidence 출처: `ceo_route` 프롬프트 확장("ROUTE: ... | conf 0.NN | ...")+`_parse_confidence`
  정규식(graph.py:222)→state. 추가 LLM 콜 0(기존 라우팅 콜 재사용). 미산출 시 **proxy**(citation→0.75/
  else→0.55).
- ★**proxy 만이면 confidence 가 2값이라 τ gradient(AUTO-by-고conf vs NOTIFY-by-중conf) 가 *구조적으로
  미도달***. ∴ 진리표 8케이스 PASS 는 **함수(배관) 증명이지 라이브 gradient 증명이 아니다**. ADR 에
  정직 박제: "τ gradient/캘리브레이션은 OBSERVE-ONLY(override-rate 순환의존·출시후 튜닝)". green-fake
  방지의 핵심 — 진리표 통과를 "라이브 τ 작동"으로 오판매 금지.
- 계산 confidence 가 기존 하드코딩(0.55 cycle·0.5 org)을 대체(`escalate_payload`·§24.2 rich 페이로드).

## 5. digest dead-path + 게이트-미실행 (Critic 검증정직)

§20.3/§20.4b 일일 다이제스트(`orch/digest.py`·🟢N/🟡M/🔴K·REDIS+vault 폴백·per-decision 핑 0).

- ★**dead-path 함정**: `render_digest` 가 🔴(escalate) 분기를 *렌더*하는데 어디서도 escalate 를
  **누적**하지 않으면 🔴 카운트는 영원히 0 = dead code(§20.4b 요구 위반). 폐쇄: escalate 가 경유하는
  `_run` `if snap.interrupts:` 분기에서 `append_decision("escalate", ...)` 누적(cycle+org 공통).
- ★**게이트-주장-미실행 함정**: verify 스크립트가 "digest 누적 ≥1" 게이트를 *주석으론* 주장하나 실제론
  unit(primitive 격리)만 돌리고 **라이브 _run 배선은 미실행** → 배선 회귀가 게이트를 통과(green-theater).
  폐쇄: **라이브 경로 통합 테스트**(monkeypatch spy 로 `append_decision` 가 autonomous+escalate 양쪽서
  실호출됨을 단언) + verify 게이트가 그 테스트를 *실제로 by-name 실행*.
- 발송 스케줄러(다이제스트를 Discord 로 push)는 P6 — 본 페이즈는 *누적+렌더*까지(정직 경계).

## 6. 발화조건이 바뀐 테스트의 *의미* 이관 (rename 아님)

게이트 발화조건을 바꾸면(여기선 "build=항상 interrupt"→"가역이면 무차단") **기존 테스트의 전제가
무효**가 된다. 단순 노드명 rename 이 아니라 *의미 재작성*.

- ★**canonical 지시의 분류가 이관 규모를 좌우**: `assess("make a clicker game")`=AUTONOMOUS(가역·범위내·
  인용된 빌드) → interrupt-전제 테스트(pending/resolve/inbox-approve/coexist)는 **새 ESCALATE 픽스처**
  ("스팀 출시·실금전 지출" — owner-reserved 키워드)로 이관해야 interrupt 가 발생. 이 한 결정이 ~5개
  테스트가 "rename"인지 "재작성"인지 가른다(착수 전 확정).
- ★**stub 라우팅 ≠ tier 분류**: 본 코드 stub 은 directive 무관 "build" 라우팅이고, escalate 여부는
  `authority_gate` 가 *directive 키워드*(assess)로 결정 → ESCALATE 픽스처는 게임 키워드(빌드/게임)로
  cycle 라우팅되면서 owner-reserved 키워드로 escalate(둘 다 만족 가능). 단, org 라우팅 키워드(조사/시장/
  전략)는 피해야 cycle 로 감.
- **정직 원칙**: 이관은 단언을 *강화*(하드코딩 0.55→계산 confidence·`authority_tier` 단언·escalate
  픽스처가 실제 escalate 유발)하지 *약화*하지 않는다. 통과시키려 단언을 무르게 하면 green-theater.

## 7. 합의·검토 프로세스 메모

- **OD-E(메커니즘) = Owner 결정**: 라이브 코어 재작성은 최고위험 → 착수 전 사용자 확정(직접재작성 ii vs
  플래그변종 i). spec=권위+downtime 허용([[renewal-spec-is-authority]] 식)이면 ii(레거시 은퇴)가 정합.
- **ralplan 합의 먼저**: Planner 초안→Architect(SOUND-WITH-CONCERNS·7우려)→반영→Critic(APPROVE). Architect
  가 *계획*에서 app.py 완료-방출 갭(§3)을 선포착 → 구현 전 강화. Critic 이 *구현*에서 digest dead-path
  (§5)를 적대탐색으로 포착 → 수정 후 재검증 APPROVE.
- ★**OMC 리뷰어 에이전트 터스 반환**: critic/architect 가 "VERDICT: APPROVE" 만 반환하고 본문 우려를
  누락하는 패턴 — 트랜스크립트 JSONL(또는 SendMessage 재개)에서 본문 추출해 반영([[14_renewal_p2_knowledge_transfer]]
  §7 와 동일 교훈). ACCEPT-WITH-RESERVATIONS = 비차단이나 reservations 는 실제 갭이니 닫고 재검증.
