# Renewal P5 — 풀 §28 제품 캡스톤 (spec-queue 사람브리지 · C4 BFS falsifiability · 정직 deferral)

## 개요

SR/renewal P5(§28 완전한 HTML5 게임 1편 7산출물 + §0.5 4차원 완주+관찰◐)를 구현하며 얻은 **재사용 패턴·함정**.
핵심은 ① **§7.2/§7.5 spec-queue = 사람-비동기 브리지**(LangGraph 가 Claude Code 를 *프로그램 호출하지 않는다*·
§7.1 HARD), ② **C4 "플레이가능"을 BFS 실풀이로 falsifiable 하게 입증**(렌더-only 차단), ③ ★**cycle graph 에
DEV-핸드오프 산출 노드가 없어 app.py 자동 sink 는 죽은 분기**(→ DEV 도구 명시 enqueue·app.py 무수정), ④ **정직
deferral 의 제3범주(ACTION≠pass≠fail)**, ⑤ ★**`orch/pytest.ini` vs `pyproject` slow-lane 게이트 함정**(본 페이즈
디버깅 최대 교훈).

커밋: 코드 `9a43332`(13파일·feat) · vault `3c22be1`(7산출물+클립·aigs 로컬). 합의계획 `.omc/plans/ralplan-p5-capstone.md`
(v2·Planner→Architect SOUND-WITH-CONCERNS→Critic ITERATE[CRITICAL-1 자동sink 죽은분기·MAJOR-2 state_store task키 부재]→
v2→Architect SOUND+Critic APPROVE). 검증 `SR/renewal/verify_phase5.sh`(8게이트·exit0·PASS=12·ACTION=2·orch 312 passed·
freeze drift0). 구현 critic VERDICT APPROVE(적대 변이 입증). 관련: [[14_renewal_p2_knowledge_transfer]](handoff/check_dod
재사용) · [[15_renewal_p3_authority]](reversibility-gated 머지) · [[17_renewal_p4_evals]](quality_loop·runner 재사용).

## 1. §7.2/§7.5 spec-queue — 사람=비동기 브리지 (자동호출 0)

§7.1 HARD: `-p`/Agent SDK/API 자동호출 금지 → **Claude Code 는 LangGraph 가 프로그램 호출하는 도구가 아니다.**
DEV 에이전트가 "pending Claude Code work"를 **큐잉**하고, Owner 가 대화형 Opus 세션으로 처리한 산출(commit/diff)을
에이전트가 **수확(harvest)**하는 비동기 브리지.

- **`orch/spec_queue.py` = 순수 큐 연산만**: `enqueue_spec`/`peek_pending`/`harvest` + `make_claude_signal`(BLUF 문자열).
  능동 실행 표면 0 — harvest 가 다음 액션을 트리거하지 않는다(재큐·emit·구현 호출 0·spy 테스트로 양성 입증).
- **§7.1 HARD 정적 grep 가드**: spec_queue 소스에 `-p `/`subprocess.*claude`/`anthropic`/`ANTHROPIC_API_KEY`
  리터럴 0. 금지 리터럴은 *테스트에만*(quality_loop.py 선례). 유일한 `subprocess` = 읽기전용 `git log --grep`.
- **harvest = falsifiable 4-AND**: (a) `git log --all --grep=<task_id>` 매치 (b) `brief.artifacts_expected` 경로
  전부 실파일 (c) `check_dod` 4구성 (d) 시계열 `brief_sha ⊰ commit_sha`. 4 AND 면 status=done. 주관적 "완료" 불가.
- **★set_task RMW(LOW-1)**: harvest 가 status 만 바꿀 때 `get_task→status 변경→set_task` (read-modify-write)
  로 기존 payload(handoff_ref/assignee/trigger) 보존. full 덮어쓰기는 추적성 유실 → 음성 대조 테스트로 강제.
- **state_store `set_task`/`get_task` add-only**: §27.3 `aigs:task:<id>` Hash/JSON 상태레코드. 기존 List 큐
  (`push_task`)와 **역할 분리**(개별 키 set/get 부재였음). `set_status` 동형 add-only(`aigs:` 접두 유지·기존 byte 보존).

## 2. ★cycle graph 에 DEV-핸드오프 산출 노드가 없다 → 자동 sink 는 죽은 분기 (CRITICAL-1)

합의 iter1 에서 Critic 이 잡은 최대 결함. 초안은 "cycle 산출이 `to='Claude Code'` 면 app.py post-invoke 에서
자동 enqueue"를 전제했으나:

- cycle graph 노드 = `ceo_route→stage_research→stage_design→stage_content→authority_gate→[deliberate]→stage_qa→finalize`.
  design/content 는 **텍스트 아티팩트만** 생성 — `HandoffBrief` 를 만드는 노드가 **없다**. `grep "Claude Code" orch/`=0·
  CycleState/OrgState 에 `to`/`assignee`/`needs_impl` 필드 부재.
- → "to=Claude Code 면 enqueue" 자동 sink 는 **영구 0회 죽은 분기**(integration 테스트는 skip 또는 인위 신호주입 green-fake).
- **해법**: enqueue 를 **DEV 도구(`tools/p5_make_brief.py`)의 명시적 호출**로 이동. **app.py 무수정**(라이브 프로덕션
  권위 byte 보존). 자동 부착은 "graph 에 DEV-핸드오프 신호 필드 추가" 선행 P6+ follow-up.
- 교훈: **"기존 패턴대로 sink 부착"이 식별가능성 부재를 가린다.** 자동 트리거를 설계하기 전, *그 트리거가 표현하려는
  사건이 graph state 에 실제로 존재하는지* grep 으로 확인하라.

## 3. C4 "플레이가능" falsifiability — 렌더-only 를 BFS 실풀이로 차단

P2.5 [C4] 계승·강화. "canvas 렌더+무에러"는 플레이가능이 아니다.

- **하네스가 브리프에서 AC 파생**(`orch.handoff.parse_brief`·하드코딩 금지) → playwright 로 게임 구동 → **관찰가능
  상태전이 단언**: `window.__gameState.moves` 엄격증가 + `tiles` 배열 실변경 + **결합 불변식**(이동 타일=빈칸 인접·
  비인접 이동 no-op).
- **solved S0→S1 도달 = BFS 실풀이**(3×3 결정론 보드를 하네스가 실제로 19수로 풀어 `solved false→true` 1회 관찰).
  렌더 단독 FAIL.
- **AC-구동 부정변이(falsifiability 입증)**: 빈 acceptance_criteria 브리프를 넘기면 하네스가 `exit≠0`. 단언이
  브리프에서 파생됨을 증명(green-theater 차단). critic 이 게임 직접 변이(`moves+=1` 주석)→C4 가 잡는지 적대 입증.
- **결합 불변식 N차원**: P2.5 교훈("score++ 단일 카운터 stub 위장")을 이식 → "moves++ AND tiles 변경 AND 빈칸인접만"
  3차원 동시 충족으로 설계. 단일 카운터 위장이 *구조적으로* 불가능.

## 4. 정직 deferral — 제3범주 ACTION (≠pass·≠fail)

Server B(§16 Owner) 의존 항목은 부당축소가 아니라 외부경계.

- **트레일러 LTX-2 *생성*** = ✅**라이브(2026-06-23·Server B 정상)**: 과거 "pending Owner: Server B" 였으나
  VAE 수리 + 영상모델(ltx-2) 정상 확인 → 실 LTX-2 b-roll(P7 gate8) + 실 게임 footage 캡처
  (`tools/capture_puzzle_footage.mjs`·playwright recordVideo·BFS solve 20수) → `tools/p5_trailer_compose.py`
  (정규화→`video_assemble.concat_clips`) 합성 → 실 트레일러(h264 720p·13.8s·verdict=OK·`vault/art/video/trailer.mp4`).
  verify_phase5 트레일러 게이트 = ACTION→**라이브 캡처+합성 PASS**. ★`concat_clips`는 `-c copy`(스트림 복사)라
  코덱 다른 클립(게임 webm/vp9 + ltx-2 mp4/h264)은 **공통 규격 정규화(h264 720p) 선행** 필수.
- **tier3 아트(ComfyUI)** = Server B 정상 후 라이브 가능(P7 캐스케이드)·green-fake 금지(실 생성·가짜 0).
- **ffmpeg *조립*은 로컬(Server B 무관) → P5 에서 실산출**: 생성(LTX-2)과 조립(ffmpeg concat)을 분리해, 조립
  파이프라인은 검증하고 생성만 deferral. `verify` 가 ACTION 을 ✔도 ✗도 아닌 **제3범주**로 출력.
- **task_id 커밋 = 사람-브리지 정상동작**(green-fake 아님): 게임 미커밋 시 harvest 의 commit 게이트는 ACTION,
  Owner 가 task_id 로 commit 하면 충족. verify 에 "사람-브리지 정상" 주석으로 미래 감사자 오인 차단.
- **관찰 차원 ◐**: P5 시점 orch trace+SSE 로 부분 충족·풀 관찰(WA 8-상태)=P6. BLUF 를 "5차원 완주"로 과대명명하지
  말 것 → "4차원 완주+관찰◐"(HIGH-2 교정).

## 5. ★함정 — `orch/pytest.ini` vs `pyproject` slow-lane 게이트

**증상**: 루트에서 `pytest orch/tests/` 직접 실행 → cutover/dropin 라이브-서버 테스트 4종이 **deterministic hang**
(TimeoutError). 격리 재실행해도 hang.

**원인**: pytest 는 *가장 가까운* config 를 쓴다. `orch/tests/` 에는 `orch/pytest.ini`(slow 미제외·`--timeout` 없음)가
루트 `pyproject.toml`(`addopts="-m 'not slow' --timeout=30"`)보다 가까워 채택됨 → `@pytest.mark.slow`(실서버 부팅
`live_orch` 픽스처) 테스트가 수집되어 hang(이 환경엔 라이브 orch 서버 가동 중).

**표준 게이트**: `-m "not slow"`(pyproject·`run_tests.sh`/`ci_gate.sh` 동일). 이걸로 돌리면 0 실패. slow=별도
라이브-서버 레인(드롭인 계약 증명)·표준 회귀에서 제외. **add-only 변경이 무관 테스트를 깨뜨린 것처럼 보이면, 먼저
config 채택 경로(pytest.ini 근접성)부터 의심하라.**

## 6. imageio-ffmpeg — 시스템 ffmpeg 없이 로컬 조립

이 환경엔 시스템 ffmpeg·conda `art` env 부재. `pip install imageio-ffmpeg`(orch env) → 정적 ffmpeg v7.0.2 바이너리
획득(`imageio_ffmpeg.get_ffmpeg_exe()`)·`.omc/bin/ffmpeg` 심링크로 PATH 노출. 프레임 생성용 PIL 은 기존
`one_man_corp-crawl` env 사용(새 의존 0). **헬퍼는 `shutil.which("ffmpeg")` 로 경로-불가지론** 유지 — imageio 는
바이너리 *소싱* 방법일 뿐이고 코드 의존이 아니다(ffmpeg 부재 시 정직 pending·가짜 mp4 0). 프로덕션은 §17.1
`conda -n art`(P7) 로 충족.

## 재사용 체크리스트 (다음 제품 사이클)

- [ ] spec-queue 는 순수 큐 연산만 — harvest 후 자동 액션 0(능동 표면 = §7.1 HARD 위반 유혹).
- [ ] 자동 sink 설계 전 graph state 에 트리거 신호가 *실재*하는지 grep(없으면 명시 호출·자동부착 deferral).
- [ ] C4 = 상태전이 단언 + 결합 불변식 N차원 + AC-구동 부정변이 FAIL(렌더-only·하드코딩·단일카운터 차단).
- [ ] Server B 의존은 ACTION 제3범주(≠pass)·로컬 가능분(ffmpeg 조립)은 실행.
- [ ] orch 회귀는 `-m "not slow"`(pyproject)·`pytest orch/tests/` 직접 금지(orch/pytest.ini 함정).
