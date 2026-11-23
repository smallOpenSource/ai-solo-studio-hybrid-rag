# 디스코드 커서유실 재방송 차단 + 라이브 데모/더미 게이트

## 개요

2026-06-21 인시던트: 디스코드 채널에 며칠 전 **데모/스모크 산출물**(mock 김민준 CRM·"Aether
Scourge" 게임빌드·F2P 리서치)이 스팸처럼 대량 도착했다. 진단 결과 **새 작업은 0건**이고, 디스코드
어댑터 재시작 시 **커서가 유실되어 SSE 전체 히스토리(774 이벤트)가 라이브 채널로 재방송**된 것.
근원 더미는 **실 CRM 백엔드 부재로 프로덕션이 mock 시드 `business.db`로 동작**한 데서 나왔다.

수정 = WS1 커서 하드닝(재방송 구조 차단) + WS4 라이브 게이트(더미 워크플로/시드 차단) + 데모 데이터
purge + 채널 스팸 메시지 정리. 커밋 `74b85aa`(코드·테스트·동결비준) + `620b001`(.omc 계획).

## 근본원인: 커서 유실 → SSE 전체재생 (확신도 0.95·증거사슬)

1. **내용물 = 옛 데모**: `state/office.db` task가 전부 06-07~06-20 데모/스모크/코업(crm_notify
   더미·game_*·research_summary). 인시던트 당일 생성 task **0건**. 당일 이벤트는 `INTERRUPT_RESOLVED`
   (승인 reject)뿐.
2. **커서 유실**: discord 어댑터 재시작 시 `state/discord/cursor.json` 부재(디렉터리가 재시작 시각에
   신규 생성됨 = 직전 부재).
3. **재시작-스팸 방어의 구멍**: `adapters/discord/bot.py`의 SSE 시작 커서가 `read_cursor() or None`
   였다 — `cursor.py read_cursor()`는 **파일 부재 시 0** 반환 → `0 or None == None` → `consume_events
   (last_event_id=None)` → web SSE를 **seq 0부터 전체재생** → `should_post(seq)=seq>0`이 전 이벤트
   통과 → 774건 라이브 재게시. **방어 주석의 의도와 정반대로 커서 유실 시 작동**.

★시간대 함정: 이벤트 `ts`는 UTC, journal/디스코드는 KST. `09:24 UTC = 18:24 KST`. 인시던트 상관 시
항상 +9h 변환.

## WS1 커서 하드닝 — 콜드스타트는 head부터 (재방송 구조 차단)

핵심 = **커서 부재(콜드스타트·상태유실)면 0(전체재생)이 아니라 현재 이벤트 head로 시드**한다. 디스코드는
영구 부작용 싱크라, 기록이 없을 때 과거 전체를 재방송하느니 "지금부터"가 압도적으로 안전.

- `office/event_store.py` `head_seq()` — `SELECT COALESCE(MAX(seq),0) FROM events`(읽기전용·추가).
- `adapters/discord/cursor.py` `cursor_initialized()` — read_cursor가 뭉개는 **'파일 부재' vs '정당한 0'을
  구분**(콜드스타트 판정의 핵심).
- `adapters/discord/bot.py` `_resolve_start_cursor()`/`_fetch_head_seq()` — 커서 있으면 resume,
  없으면 head 시드 + cursor.json 전진. head 조회 실패는 try 안에서 흡수(0 폴백 재방송보다 소비중단이 안전).
- `web_server/app.py` `GET /api/events/head`(auth-gated·읽기전용).

★**비침투 어댑터 경계 (테스트가 잡은 설계 제약)**: 처음엔 어댑터에서 `from office.event_store import
EventStore`로 head를 직독했더니 **`test_no_office_or_web_server_import_in_adapters` 불변식이 차단**.
어댑터는 순수 HTTP 소비자여야 한다(office/web_server import 금지) → head도 **HTTP `/api/events/head`로**
받게 피벗. 어댑터가 자기 환경(one_man_corp-discord)에서 office를 import하지 않는 게 계약.

★정상운영에선 WS1로 충분: web 시작 시 fanout이 DB에서 로드하므로 **fanout 큐 ⊆ DB**, 즉 `head_seq()`(DB)
≥ fanout이 재생 가능한 어떤 seq → head 시드가 그것들을 전부 스킵. (단 아래 함정 참조.)

## ★★ 핵심 운영 함정: purge-before-fanout-clear (실측 재스팸)

**라이브 `office.db`를 purge해도 web의 인메모리 fanout 큐는 purge 전 이벤트를 그대로 보유한다.**

- `web_server/sse.py` `EventFanout`/`replay_from()` — web은 **시작 시 DB→메모리로 이벤트를 로드**하고,
  SSE 구독자에게 인메모리 큐에서 재생한다(`_event_stream`이 `replay_from(cursor)`로 흘림).
- 따라서 web을 19:14에 재시작(774 로드) → 19:16에 DB purge(events→0) 하면, **web fanout은 여전히 774를
  메모리에 들고 있다**. 이 상태로 discord 어댑터를 콜드스타트하면 WS1이 DB head(=0)로 시드하지만 fanout이
  **stale 인메모리 774를 SSE로 재방송** → 어댑터가 옛 이벤트를 디스코드에 또 게시(이번에 cursor 0→774
  상승·실측 재스팸 유발).
- ★**해법**: 라이브 DB purge 시 **fanout 보유 서비스(web)를 purge *후* 재시작**해 인메모리를 비운 *다음*
  어댑터 처리. 검증 = 인증 SSE `GET /events/stream?idle_close_ms=2000` 스냅샷에서 `data:` 라인 0 확인.
- ★WS1 자체는 결함 아님 — 실패는 *가동 fanout 밑에서 수동 DB purge*를 한 **운영 순서** 탓. 정상운영(자동
  enqueue/소비)에선 fanout⊆DB라 재현 안 됨.

## WS4 라이브 데모/더미 게이트 (env·테스트 안전)

근원 더미는 `office/tools.py` `mock_crm_get_customer`(C-2041 김민준/노트북 Pro 15)로 시드된
`state/business.db`를 라이브 워커의 `REAL_TOOLS.crm_get_customer`가 읽어 생성. 실 CRM 부재로 프로덕션이
데모로 돈 것. 두 env 게이트로 차단(기본값 무변 = 테스트/개발 무영향):

- **enqueue 게이트**: `office/task_queue.py` `WorkflowDisabledError` + `_live_disabled_workflows()`
  (`OFFICE_DISABLED_WORKFLOWS` 쉼표구분). enqueue가 단일 관문이라 전 경로(API·coordinator·coop) 커버.
  `web_server/runner.py`가 이를 잡아 **409**로 변환. 라이브=`crm_notify,smoke_email`.
- **시드 게이트**: `office/business.py` `_should_seed_default()`(`OFFICE_BUSINESS_SEED`, 기본 1).
  라이브=`0` → BusinessStore가 더미 미시드(프로덕션=실데이터만). game_*는 **제품 기능**이라 비활성 제외.
- 게이트값은 `config/app.env`(gitignored·SoT) + `tools/render_app_env.py`(재렌더 영속). 라이브 서비스는
  EnvironmentFile=config/app.env로 읽음.
- ★테스트 안전 패턴: 생성자 인자(`disabled_workflows`/`seed`)는 `None`이면 env로 결정. **기본=비활성 없음/
  시드 on** → 기존 테스트 0수정. 라이브만 env로 게이팅.

데모 데이터 정리(WS3)는 백업(`.omc/backups/discord-replay-cleanup-*`) 후 `office.db` events/tasks +
`business.db` 더미행 삭제. 자동 enqueue 경로가 없는 데모 스크립트(smoke_seed·run_coop_build_live)는 수동
dev 도구라 별도 차단 불요.

## 디스코드 채널 스팸 정리 방법론 (재사용)

재방송으로 채널에 쌓인 봇 메시지 124건을 `tools/discord_purge_replay.py`로 정리(ok=124/fail=0). 재사용
교훈:

- **REST만**(httpx·게이트웨이 IDENTIFY 0) → 가동 중 라이브 어댑터와 토큰 충돌 없음.
- ★**primary 봇은 채널 history GET이 401**(채널 미소속) → **역할봇 토큰을 reader로 자동 선택**(첫 200
  응답 봇). 스팸을 게시한 건 역할봇(SECRETARY 이수빈 등)이라 그들로 읽고, 삭제는 **각 메시지를 author 봇
  토큰으로 자기삭제**(MANAGE_MESSAGES 불요).
- **드라이런 기본**(--apply 없으면 삭제 0)·**시간창 필수**(창 밖 불침해)·**우리 봇 author만**(사람/타 봇 보호).
- ★**재방송 확증법**: 같은 내용이 두 플러드 시각(17:xx·19:xx)에 **중복 출현** = 옛 이벤트 재방송(정상
  메시지였다면 1회만). 키워드 패턴 비매칭 18건도 이 중복으로 스팸 확정 → 정상 메시지 0 확신 후 삭제.
- 검증 = 삭제 후 동일·확대 창 재스캔 0건.
- 토큰은 `config/app.env`에서 읽기만·값 출력/로그 0. 도구는 untracked(기존 discord 도구 관례).

## 동결 해동 적용 사례 (FREEZE.md 절차)

`office/event_store.py`(#3)·`web_server/runner.py`(#7)는 엔진계약 동결 파일이라 pre-commit이 차단. 변경이
**순수 additive**(head_seq 읽기전용·409 에러경로·계약 무변경)임을 [동결 절차](../.omc/state/FREEZE.md)대로
처리: **code-reviewer 별도 승인**(self-approve 금지·계약위반0) → `sha256sum`으로 `protected_pre.sha`
재생성(미변경 5파일 일치·`verify_freeze.sh` 드리프트0) → FREEZE.md 해동이력 기록 → `git commit
--no-verify`(절차 완료 후 정식 경로). 단일 진실원=sha 매니페스트라 파일 목록·계약 무변 시 표는 그대로.

## 관련 문서

- [04 워크플로 엔진](04_workflow_engine.md) — 이벤트 11종·SSE·EventStore(이 인시던트가 건드린 계약 레이어).
- [05 HITL·게이팅](05_hitl_and_gating.md) — 승인 게이팅(재방송된 email_send 승인의 출처).
- [11 renewal cutover + Redis](11_renewal_cutover_redis.md) — 디스코드 권위/커서 함정의 인접 계열(orch 직접
  outbound vs 어댑터 web-SSE 소비).
- [12 renewal P1 토대](12_renewal_p1_foundation.md) — app.env SoT(WS4 게이트값이 얹히는 SoT).
- 상세: `.omc/plans/plan-discord-replay-and-demo-cleanup.md` · 커밋 `74b85aa`.
