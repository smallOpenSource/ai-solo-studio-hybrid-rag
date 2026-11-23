# 검증 방법론 — 적대 합의·green-theater 차단·falsifiable 테스트 (전 페이즈 공통)

renewal P1~P9 전반에서 반복된 **검증 방법론**(개별 페이즈 docs는 *instance*·이 문서는 재사용 *method*). green-theater(통과해도 라이브 방어 0)를 막는 구체 기법.

## ① 반복 실패모드: "가정된-그러나-없는 데이터흐름/표면 위 설계"
구현·계획이 **라이브에 존재하지 않는 데이터흐름이나 코드 표면을 가정**하면, mock 신호 주입·격리 유닛 테스트는 **통과**하지만 라이브 방어/기능은 **0**이다(silent). 이번 세션 2회 실증:
- **P9 `intake_request` 채널 drop**([[21_renewal_p9_research_intake]] ④): 노드가 set한 필드가 `OrgState` TypedDict 채널 *미선언* → LangGraph가 체크포인트서 드롭 → sink dead code. i3 유닛은 (a)노드 직접호출 (b)sink에 필드 *수동 주입* = 양쪽 격리라 통합 seam 누락 → 라이브 enqueue 0인데 green.
- **P8 provenance 회수 소실**([[22_renewal_p8_security]] ②): `capture_and_index`가 meta에 저장해도 `search.py`가 회수 시 폐기·공유 계약에 필드 부재 → 스팟라이팅이 항상-비래핑(침묵 붕괴). mock 청크에 provenance 박은 유닛은 통과.
- **P8 주입지점 오지정**([[22]] ③): org_graph 노드는 회수를 *안 함*(prose만)인데 거기 seam을 가정 → 대상 없는 표면.

## ② 차단 기법 (비협상)
1. **적대 독립검토 (Architect steelman + Critic)** — ★*자작 초안에도* 필수. P8은 API 529로 Planner 부재→오케스트레이터 자작 초안이었으나, 독립검토가 green-theater 2 CRITICAL을 포착(자기가 못 보는 가정을 적대자가 본다). self-approve 금지·writer/reviewer 분리.
2. **라이브 데이터흐름 테스트** — 방어를 **실제 컴파일 그래프/실 서비스 출력**에 단언(mock 신호 주입 금지). 예: `build_org_graph(MemorySaver()).ainvoke()→aget_state()`로 채널 생존 확인(P9)·`stage_research` 다운스트림 프롬프트에 `<untrusted_data>` 단언(P8)·실 `service.py` response_model 왕복(pydantic 모델만 아님·P8).
3. **부정변이(negative mutation)** — 방어를 *비활성*하면 테스트가 **FAIL**해야 진짜(falsifiable). 예: 스팟라이팅 우회 시 오염 지시 노출 관측(P8 e1)·trusted 누출 시 validator FAIL(P9 u8).
4. **결정론 게이트 + 실모델 OBSERVE 분리** — 회귀 차단은 결정론(stub caller·문자열 매칭 scorer)·LLM 비결정 저항은 OBSERVE(다회). green-theater는 결정론 게이트가 막고 실모델은 관측.

## ③ 안티패턴 (green-theater 원천)
- **격리-절반 테스트**: 통합 seam의 양쪽(노드 / sink)을 따로 검증(통합 지점은 미검증). → 컴파일 그래프 e2e 필수.
- **mock 신호 주입**: 테스트가 라이브엔 없는 신호(provenance·필드)를 직접 박아 통과. → 실 계약/그래프 경유.
- **부재증명 누락**: "위험 도구 미보유"를 grep만으로 단언. → 화이트리스트 lock(추가 시 FAIL·P8 권한분리 u4).
- **자기-검토**: 작성자가 자기 산출을 승인. → 적대 독립 critic.

## ④ OMC 운영 패턴 (이번 세션)
- **529-resume**: 합의 에이전트(Planner/Architect/Critic)가 API 529(과부하)로 실패 시 — 일시적이므로 재시도·지속 시 오케스트레이터가 *초안 작성*은 가능하나 **적대 독립검토는 비협상**(green-theater 자기노출 차단).
- **에이전트 터스-반환 → JSONL 추출**: architect/critic/planner가 최종 텍스트를 터스하게 반환해도 실작업(tool_uses)은 함 → 트랜스크립트 `subagents/agent-<id>.jsonl`에서 최장 assistant 텍스트 블록을 python으로 추출(판정 회수).
- **cross-env pytest**: orch 테스트=`envs/orch`·knowledge=`envs/one_man_corp-knowledge`(dotenv)·intake=`envs/intake`(pypdf)·각 테스트는 *제 env*로(루트 `pytest`는 slow/의존 hang). pre-existing 스테일 테스트(메시지 drift)는 회귀와 구분(코드 무수정 확인).

## ⑤ 비파괴 검증 (라이브 권위 코어)
라이브 프로덕션 코어(`orch/graph.py` cycle·`orch/org_graph.py`·`orch/app.py`·`.claude/settings.json`) 수정 시: **add-only seam**(기존 로직 byte 보존)·`git diff --numstat`로 삭제 라인 검토(불가피한 1줄=wrap 주입 같은 최소 변경만 허용)·동결셋 7파일(`.omc/state/protected_pre.sha`) 0수정·`scripts/verify_freeze.sh` drift0·orch 회귀 무손실. `.claude/settings.json`은 git tracked(롤백 가역)·`defaultMode: default`가 미목록 루틴도구 무파손 보장.

## ⑥ stale 게이트 재정렬 + 조건부-라이브 게이트 (2026-06-24·green-fake 회피)
- ★**stale 게이트(수용 베이스라인 미반영)**: Owner 가 `.claude/settings.json` §23.6 을 *완화*(수용·박제)하면 `test_claude_permissions`(strict 설정을 단언) 3건이 실패하는데 이는 **정상**(완화는 Owner 결정). canonical 회귀는 `--ignore=orch/tests/test_claude_permissions.py` 로 제외한다. 그런데 **full orch suite 를 도는 verify 게이트**(`verify_phase6.sh` 8.2·`verify_phase9.sh` reg)가 이를 미제외→FAIL=1(빨간불). → 해법: 게이트를 canonical 과 동일하게 `--ignore` 정렬 + **가시 노트**(테스트 파일 존속·그 외 실패는 정상 FAIL). green-fake 아님(완화는 박제 결정·노트로 투명). ★격리 확인: 변경분 회귀(claude_permissions 제외)=exit0 → 그 FAIL 이 베이스라인임을 입증(변경 무관). 다른 verify_phase 스크립트도 같은 베이스라인 영향 가능(sweep 후보·silent 누락 금지).
- ★**조건부-라이브 게이트는 `systemd is-active` 만으론 green-fake**: "WA 라이브 아바타 입장"을 서비스 active 만으로 PASS 처리하면 **서비스가 떠 있어도 입장 0**일 때 거짓 통과. → 실 **in-world 증거**를 요구: `wa_avatar_presence.mjs` 가 헬스루프마다 `state/wa_avatars_status.json{in_world,total,ts}` 발행 → 게이트가 `in_world≥1 AND fresh(ts≤120s)` 검사([[24_renewal_wa_deployment]]). falsifiable(서비스 stop→stale→ACTION 폴백). = [[21_renewal_p9_research_intake]] ⑧ 의 "조건부 라이브(rclone lsf 도달성)" 패턴을 *presence* 로 확장. ★ACTION→PASS 플립은 *실측 증거*가 게이트에 falsifiable 하게 박혀야(억측·systemd-only 금지).

## ⑦ hollow-vector + "headless 그린 ≠ live" (창발/내러티브/오디오 게임·2026-06-30)
차기작 프로토타입(memoir-room·rhythm-tale·[27_web_games_client](27_web_games_client.md)·[28_game_market_genre_gap](28_game_market_genre_gap.md))서 반복된 green-theater 변종 2종:
- ★**hollow-vector(메커니즘만 계산·실 출력 미게이트)**: 기능이 형식상 존재하나 *실 효과로 연결 안 됨*. rhythm-tale 의 "연주 정확도→엔딩 변경"이 AC 상 *허용*만 되고 실제 분기 무관(ralplan Architect C-3 적발), R2 다층편곡(③)이 **레이어 계산만·실 합성 미발화**(vestigial cutoff/detune 재발 구조). → 차단 = **결합 불변식**(계획값==실발화값 + 실 호출 카운터를 ctx 가드 *앞*에): rhythm `__plannedVoices`===`__activeLayers` + 성부 호출수(`BASS_COMBO`/`PERC_COMBO` 경계·`escalateLiveLayers`). 내러티브는 **창발 강제**(엔딩을 `judge()` 연주판정 경유로만 산출=hollow 면 분기 무관이 드러남).
- ★**headless 그린 ≠ live**: 결정론 헤드리스 게이트(`A.now()=0`·강제 combo·autoPlay clock-비의존)는 **시간/재진입/실클럭 경로를 구조적으로 못 본다**. 하네스 사각의 MAJOR 실증: memoir `advanceChapter` branch 미리셋→챕터 분기 영구소실 / rhythm `retry()` songStart 미재설정→재연주 전곡 auto-miss·`scheduleMelody` combo=0 1회만→베이스/퍼커션 **live 영영 미발화**. → **live-flow 리뷰 + 실클럭 스모크 필수**(독립 retry/재진입 스모크·실 ctx 시각). 헤드리스 PASS 후에도 "사람이 끝까지 실제 플레이"하는 경로를 별도 검토.
- ★**ralplan Architect 의 *구현 전* hollow 적발이 핵심 가치**(C-3·AC7③→결합불변식 교정 후 APPROVE). OMC read-only 리뷰 에이전트(architect/critic) 빈반환 고질 → general-purpose 가 판정을 `.omc/plans/review-*.md` 로 기록해 회수([[improvement-program-progress]] 동일).

> 각 페이즈 verify_phase*.sh = 4-counter(PASS/FAIL/ACTION/BLOCKED·exit0 ⟺ FAIL==0 AND BLOCKED==0). ACTION=코드 100%·env/Owner 의존(green-fake 금지·BLOCKED≠pass). 관련: 전 [NN_renewal_*] 페이즈 docs.
