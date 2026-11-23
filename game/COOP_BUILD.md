# 협업 게임 빌드 파이프라인 — AI 직원들이 게임을 만든다

AI 에이전트 직원들이 **협업해 게임 빌드를 산출**하는 방법. 신규 이벤트/노드/워크플로 0·동결
7파일(`office/{events,event_store,engine,nodes,gating}.py`·`web_server/runner.py`·
`web/src/types/events.ts`) 미접촉으로, 기존 엔진 **위에 얹은 비동결 코디네이터**로 구현한다.
Windows 빌드는 [`native/README.md`](native/README.md) 참조.

## 협업 모델 (정직)

**"협업"의 검증가능 정의 = 상호기여 + 교차영향**(한 역할 산출이 다른 역할의 함수).

- **직렬 파이프라인 = 진짜 협업(단, 순차)**: `game_design→game_balance→game_content→game_assemble→game_qa`.
  각 단계가 이전 단계의 `game/data/*.json` 산출 위에 기여한다(예: `game_content`가 `game_balance`의
  enemies에 flavor 병합·`game_assemble`이 5파일 조립). ②교차영향 실재.
- **N=2 독립잡 병렬 = 처리량(throughput)·협업 아님**: 서로 다른 워크플로 2개가 *서로 다른 일*을
  동시에. 상호기여 0이므로 "협업"이 아니라 "분업"으로 **정직 라벨**(label ⊥ semantics).
- **같은 산출물 동시 공동편집**(한 run_id에 다역할 동시 기여)은 **엔진 동결 7파일 해제가 필요한
  USER-GATE**다 — 본 파이프라인은 트리거하지 않는다.

> ★교훈: "16명 실시간 오피스"를 동시 협업으로 연출하지 말 것. 직렬은 직렬, 병렬은 처리량으로 표기.
> 오피스에 보이는 직원 수 = 실제 in-flight 런 수(presence ⊥ throughput).

## 자동 코디네이터 (`office/coordinator.py` · 비동결)

5단계를 자동 체이닝한다. 핵심은 **run_id 상관 2축 바운드 폴링**:

1. `task_queue.enqueue(stage, payload)` 는 **`task_id`를 반환**(run_id 아님).
2. **run_id는 워커가 claim한 뒤 생성**되어, RUN_STARTED 발행 **전** payload에 기록된다
   (`office/run.py` `_record_run_id_in_payload`). 그래서 코디네이터는 `load_run_id(task_id)`
   (`office/run.py` `load_run_id`)를 **바운드 폴링 `T_claim`**으로 상관시킨다.
3. 그 run_id로 `EventStore.read(run_id)`의 `RUN_FINISHED`를 **바운드 폴링 `T_run`** → status
   (success → 다음 / failed·rejected → 체인 중단).
4. ★`T_claim`·`T_run`은 **독립 타임아웃**(단일 T 금지 — 5단계 직렬 실LLM은 단계당 수십 초라
   한 단계 지연이 전체를 거짓실패시킨다). 두 폴링 모두 deadline 검사 후 sleep = 무한대기 0.

진입 라우터 `web_server/coop_router.py`: `POST /api/coop/build` → **즉시 202 + build_id**, `run_build`는
**백그라운드 데몬 스레드**(web-inline 금지 — 폴링이 uvicorn/SSE를 점유하지 않게). `GET /api/coop/status/{id}`.
동결 `runner.py` 미접촉(`cancel_all_router` 선례 동형·enqueue는 `task_queue` 직접).

## 라이브 구동 (web 재시작 없이)

`scripts/run_coop_build_live.py`가 `BuildCoordinator`를 **라이브 `state/office.db`에 직접 구동**한다 →
가동 중 supervisor 워커풀(slot0/1)이 큐를 드레인해 실 gemma로 5단계를 실행한다. 첫 라이브 실측:
~2분에 5단계 done·`assembly{ready:true}`+`qa_report{pass:true·errors:[]}`(실 gemma는 worker-mock 캔드와
달리 내부정합 데이터 생성). ★`game/data`를 덮으므로 사전 백업은 호출측 책임.

> web 서비스 UI에서 `/api/coop/build`를 트리거하려면 web 서비스 재기동(=사용자 게이트)이 필요하다
> — 라우터가 가동 중 프로세스에 아직 로드되지 않았기 때문. 스크립트 구동은 이를 우회한다.

## ★GOTCHA — N=2 슬롯 증거는 in-flight일 때만 보인다

`claimed_by_slot`은 **task가 running일 때만 각인**되고, **완료 시 `release_slot`이 NULL로 반납**한다
(`office/task_queue.py` `release_slot`의 `claimed_by_slot=NULL`·`office/worker.py` `_release_slot`).
설계 불변식 = `claimed_by_slot ≠ NULL ⟺ 살아있는 in-flight`.

→ **"슬롯 경로가 실가동됐다"(claimed_by_slot 0·1)는 반드시 running 중 폴링해야 보인다.** 완료 후
DB를 조회하면 전부 None이다 — 감사가 "60태스크 전부 None = 슬롯경로 한 번도 미실가동"으로 **오인한
원인**이 바로 이것. `scripts/run_parallel_demo.py`가 1초 간격으로 running 슬롯을 폴링해 0·1 동시 점유
스냅샷 + wall-clock 오버랩을 포착한다(실측: research(slot0) ∥ game(slot1) ~81초 동시).

## 검증 패턴

- **mock e2e**(`tests/test_coop_e2e.py`): temp `office.db` + game tools `REAL_TOOLS` monkeypatch로
  **temp game/data 격리**(라이브 미접촉) + in-process 워커가 `worker.run(max_ticks=None)` 연속 드레인 +
  `worker.request_stop()`+`join(timeout)` 이중 바운드 → 5단계 자동 체이닝 결정론 검증.
  ★워커를 고정 `max_ticks`로 돌리면 부하 시 코디네이터가 마지막 단계를 enqueue하기 전에 워커가
  예산을 소진해 거짓실패한다(연속 가용성이 결정론의 핵심).
- **격리 단언은 절대-부재 금지**: "실 game/data에 assembly.json 없어야 함"은 라이브 빌드가 정당하게
  남긴 산출물 때문에 환경의존 거짓실패한다. 대신 **테스트 시작 시점 스냅샷(파일명→mtime) 대비
  무변화**를 검사하라(=이 테스트가 실 game/data를 *건드렸는가*만 결정론적으로 본다).
- **worker-mock 캔드 한계**: frozen `run.py` `_FakeSmokeLLM` 캔드는 cross-file 정합을 보장하지 않으므로
  `validate_all ok:true`는 단언하지 않는다(체인 완주 게이트 = assembly.ready+qa.pass+done). 정합
  데이터 검증은 별 메커니즘인 `tests/test_game_pipeline_e2e.py`(slow)가 담당.
