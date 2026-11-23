# 워크플로 엔진 (Phase 3)

## 개요

Phase 3에서 구현된 결정론 워크플로 엔진. 노드 기반 실행, AG-UI 정렬 이벤트버스, 위험기반 게이팅을 제공한다.

> **후속(2026-06 renewal)**: 이 커스텀 엔진은 renewal에서 **LangGraph 코어(`orch/`)로 대체**되어 프로덕션 권위가 이관됐다(AG-UI 11이벤트·SSE·위험기반 게이팅 개념은 계승). 구 엔진(`office/`)은 0수정 standby·즉시 역전 가역. → [09 Renewal LangGraph 코어](09_renewal_langgraph_core.md).

## 핵심 구조

- **`office/`**: 워크플로 엔진 패키지
  - `engine.py`: `WorkflowEngine` — `__init__(tools: dict[str, Callable])` 로 도구 레지스트리 주입
  - `nodes.py`: `Node` 정의 — `kind`, `tools: list[dict]`, `expected`
  - `gating.py`: `GatingPolicy` — 위험기반 게이팅 (`always-allow` / `needs-approval` / `block`)
  - `tools.py`: 도구 스키마 + callable 레지스트리 (`MOCK_TOOLS`)
  - `event_store.py`: SQLite 기반 이벤트 영속. `read_since(after_seq)` 메서드

## 도구 레지스트리

`WorkflowEngine.__init__(tools: dict[str, Callable])`이 name→callable 레지스트리를 받는다. tool 노드가 `_run_tool`에서 `self._tools.get(tool_name)`으로 디스패치한다.

도구 등록 방식: `office/tools.py`에 OpenAI function 스키마 + callable 추가 → `WorkflowEngine(tools=...)` 주입.

## 위험기반 게이팅

`config/tools_policy.yaml`로 도구별 위험 등급 설정:

```yaml
crm_get_customer:
  risk: always-allow
email_send:
  risk: needs-approval
default_risk: needs-approval
```

**미등록 도구의 기본값은 `needs-approval`** — 사람 승인 대기로 멈춘다. 읽기전용 조회 도구(예: `knowledge_search`)는 반드시 `always-allow`로 등록해야 한다.

## 엔진 비침투 원칙

Phase 3 엔진 **파이썬 코드**(engine.py/nodes.py/gating.py)는 변경 0. **config 데이터만 추가**:
- `config/endpoint.yaml` — `knowledge` 블록 추가
- `config/tools_policy.yaml` — `knowledge_search: {risk: always-allow}` 1줄 추가

## 동시성

사용자 지시로 **동시성 1 고정** (순차 실행). LLM 호출은 한 번에 하나만.

## tool 노드 실행 순서

tool 노드는 도구 디스패치 전에 **LLM 추출 1콜**을 선행한다 (engine.py:573). 따라서:
- "LLM 0"은 인덱싱 파이프라인에만 해당
- 엔진 실증은 LLM 추출 1콜(gemma×OFF, 256~512토큰) 후 도구 디스패치

## AG-UI 정렬 이벤트

- **이벤트 타입**: RunStarted·RunFinished·RunError / TextMessage / ToolCallStart·ToolCallResult / StateSnapshot·StateDelta / InterruptRequested·InterruptResolved·NodeFailed
- **transport**: SSE (Server-Sent Events)
- **이벤트 영속**: SQLite `events` 테이블 (`seq`, `payload_json`)
- ★SSE 소비자(디스코드 어댑터)의 커서 유실 → 전체재생 재방송 함정·콜드스타트 head 시드·fanout
  staleness는 [13 디스코드 재방송 차단 + 데모 게이트](13_discord_replay_and_demo_gates.md) 참조.

## 완료 게이트 (Phase 3)

- engine·bench 양 env 285 passed (Phase 3 기준)
- Phase 4a 기준: engine 341 passed, bench 304 passed
