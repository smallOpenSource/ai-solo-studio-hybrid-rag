# Renewal 직원 레이어: 9 페르소나 · 조직그래프 · 다부서 협업

## 개요

HYBRID 코어([09](09_renewal_langgraph_core.md)) 위에 **직원 레이어를 비파괴로** 쌓았다(2026-06, Path A=제자리 적응). 세 단계: ① 9 페르소나 정의 + 기존 16봇 닉네임, ② orch 조직그래프(9노드), ③ 부서 다중협업(map-reduce) + CEO 전략모델(qwen). **라이브 프로덕션 코어(`orch/graph.py` 7노드 사이클·`app.py`)는 0수정**이고, 조직그래프는 (당시) **미승격**(검증만) — 이후 [11 cutover](11_renewal_cutover_redis.md)에서 **coexist flag로 프로덕션 승격**(app.py만 수정·1줄 가역) + Redis 잠금해제. 커밋: 페르소나 `93758c6` · 조직그래프 `2f9888e` · 다부서+qwen `1fffda8` · cutover+Redis `b891a23`.

## 1. 9 페르소나 + 기존 봇 닉네임 (데이터 주도)

- **`config/personas.yaml`**: 9 페르소나(CEO·COO·DevLead·RESEARCH·ART·LIBRARIAN·INTAKE·DEV·QA) + secretary(`skip_nick`) = 10 엔트리. `bot_role` 로 `config/endpoint.yaml` `discord.bots` 에 매핑(토큰은 `token_env` 참조·평문 0).
- **`prompts/personas/<id>.md` ×9**: §26 공통 프리앰블(우선순위·RPI·지식적재·가역성+conf·BLUF·`<untrusted_data>` 지시거부) + 역할블록. RESEARCH·INTAKE 는 §11.1 권한분리(실행/게시/삭제 도구 0) 명시.
- **`office/personas.py`**: 로더(`load_personas`/`mappable_personas`/`nickname_for`). 닉네임 = `"{display_name} · {label}"` (≤32자).
- **`tools/set_bot_nicknames.py`**: 닉네임 일괄 설정(`tools/set_bot_avatars.py` 패턴). `--dry-run`(기본·네트워크 0)·`--apply`·`--restore`(빈 nick=username 복원). 토큰 `Authorization` 헤더만·출력/로그 평문 0.

### ★★ Discord 봇 자기-닉 read-back 함정 (재사용·비자명)

봇이 **자기 길드 닉네임을 설정**하는 것과 **읽는** 것은 엔드포인트가 다르다:

| 동작 | 엔드포인트 | 결과 |
|---|---|---|
| 닉 **설정** | `PATCH /guilds/{id}/members/@me` `{nick}` | OK(modify-current-member). 응답이 갱신된 `nick` 을 echo |
| 닉 read(틀림) | `GET /guilds/{id}/members/@me` | **400** (`@me` 는 PATCH 전용·GET 불가) |
| 닉 read(틀림) | `GET /users/@me/guilds/{id}/member` | 봇 토큰에 **403** (OAuth2 `guilds.members.read` 스코프 필요) |
| 닉 read(**정답**) | `GET /guilds/{id}/members/{bot_user_id}` | OK (`bot_user_id` = `GET /users/@me`.id) |

→ 봇 자기 닉 검증 = `members/{bot_user_id}` GET **또는 PATCH 응답의 `nick`**. ★이 함정은 adversarial review(architect+critic)가 **둘 다 `members/@me` GET을 승인**했으나 **Stage-3 라이브 검증이 교정**했다(verification-method defect).

- ★`DISCORD_GUILD_ID` 가 `.env` 에 **없으면** `channel_id` 로 유도: `GET /channels/{DISCORD_CHANNEL_ID}`.`guild_id`. REST 직접 호출엔 proper `User-Agent` 필수(Cloudflare 1010, [09](09_renewal_langgraph_core.md) 참조).

## 2. orch 조직그래프 (비파괴·미승격)

- **`orch/org_graph.py`** (신규 모듈): `orch/graph.py`·`app.py`·`outbound.py`·`adapters/` **0수정**. `orch.graph` 의 결정성 seam(`use_caller`/`_active_caller`)·`Command`/`interrupt`/`AsyncSqliteSaver` 를 **import 재사용**(graph.py 수정 0·동일 ContextVar seam).
- 위계(§6.2): `START → ceo_route → {coo | dev_lead | escalate | END}` · `coo → 부서` · `dev_lead → {dev,qa}` · `부서 → finalize` · `escalate → finalize`. 각 노드는 자기 페르소나 §26 프롬프트를 caller 컨텍스트로 사용.
- 검증: 직접 드라이브 `orch/drive_org.py`(★전용 `orch_org_ckpt.db` — 라이브 `orch_ckpt.db` WAL contention 회피). **미승격(당시)**: `app.py` 는 여전히 `build_cycle_graph` — 이후 coexist 승격은 [11 cutover](11_renewal_cutover_redis.md).

### ★★ ESCALATE goto 함정 (재사용·langgraph)

스펙 §6.6/§24.2 `escalate` 스켈레톤은 `Command(update=...)` 로 **`goto` 가 없다**. 그대로 베끼면 `interrupt()` resume 후 그래프가 `finalize` 에 도달하지 못하고 **BLUF 무음 종료**(에러 없이 사라짐). `graph.py:hitl_gate` 패턴대로 **`Command(goto="finalize", update={...})` 필수**. 회귀가드: `orch/tests/test_org_graph.py::test_escalate_interrupt_and_resume`(resume 후 `bluf` 단언 — goto 없으면 실패).

## 3. 부서 다중협업 (map-reduce Send) + CEO qwen

### ★ 병렬 부서 = reducer 필수 (langgraph map-reduce)

COO/DevLead 가 `Command(goto=[Send(dept, {"directive": d}) for dept in depts])` 로 1+ 부서를 **병렬** 디스패치. 병렬 노드가 같은 state 키에 동시 기록하므로:

- `artifacts: Annotated[dict[str, str], operator.or_]` (dict 머지)
- `departments: Annotated[list[str], operator.add]` (list 누적)
- 부서 노드는 **자기 항목만 partial 반환**(`{"artifacts": {id: content}}`). ★read-modify-write(전체 dict 반환) 금지 — fan-out 시 동시쓰기 손실/`InvalidUpdateError`. reducer + partial 반환은 **쌍으로만** 안전.
- `finalize` 는 단일 노드(다부서 `add_edge → finalize`)로 **1회 조인**·다부서 artifacts 집계.

### ★ CEO 전략모델(qwen) seam

CEO 만 `BENCH_PRIMARY_MODEL`(qwen)을 별도 `use_strategy_caller` ContextVar seam 으로 사용·부서는 gemma(`_active_caller`). gemma-affinity: CEO **1콜/사이클**만 qwen. 결정성: 테스트가 `use_caller`(부서)/`use_strategy_caller`(CEO) 양쪽 stub 주입(seam 격리 테스트로 CEO 가 gemma 아닌 strategy seam 사용 증명).

- ★**실측**: CEO=qwen → 라이브 라우팅 **23–27s**(qwen↔gemma 스왑 포함). gemma-affinity 비용 실증([09](09_renewal_langgraph_core.md) 모델 스왑 6–21s 와 정합).
- **라이브 실증**: "시장조사+에셋 동시" → `ceo_route → coo → research+art(병렬) → finalize` · `departments=['research','art']` · finalize 가 양 부서 BLUF 집계.

## 방법론 교훈 (재사용)

- ★ **비파괴 확장 패턴**: 라이브 프로덕션 코어를 확장할 때 = **신규 모듈 + 기존 seam을 import 재사용 + 미승격**(검증만). 승격은 별도 가역 결정(`app.py` 1줄). 비파괴 증명 게이트 = **기존 테스트 N개 그린**(여기선 orch 56→66, 회귀 0).
- ★ **Stage-3 라이브 검증이 적대검증을 보완**: architect/critic 둘 다 승인한 verification 엔드포인트(`members/@me` GET)가 라이브서 400 → **실동작이 방법론 결함을 교정**. headless/리뷰 PASS ≠ 라이브 진실.

## 관련 문서

- [11 renewal cutover + Redis](11_renewal_cutover_redis.md) — 본 직원 레이어를 **프로덕션 승격**(coexist·durable thread-id 마커) + Redis 잠금해제.
- [09 renewal LangGraph 코어](09_renewal_langgraph_core.md) — 본 직원 레이어의 토대(HYBRID 코어 스왑).
- [04 워크플로 엔진](04_workflow_engine.md) · [05 HITL·게이팅](05_hitl_and_gating.md).
- 상세 계획(`.omc/`): `ralplan-personas-bot-nicknames.md` · `ralplan-org-graph-wiring.md` · `ralplan-org-multidept-qwen.md`.
