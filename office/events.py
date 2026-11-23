"""AG-UI 정렬 이벤트 모델 — 5개 패밀리 10이벤트.

계획 §4.3 표가 유일한 진실:
- Lifecycle : RUN_STARTED, RUN_FINISHED, RUN_ERROR
- Text       : TEXT_MESSAGE
- ToolCall   : TOOL_CALL_START, TOOL_CALL_RESULT
- State      : STATE_SNAPSHOT, STATE_DELTA
- Interrupt  : INTERRUPT_REQUESTED, INTERRUPT_RESOLVED, NODE_FAILED
  (TOOL_CALL_ARGS는 스트리밍 미구현·Phase 4a 연기 → 이번 구현 없음)

NODE_FAILED.reason은 확장 가능 enum (rev3 R5):
  현재 값 = gating | escalation | question
  (question은 Phase 4a에서 추가 — Agent Inbox Question 칸. review는 후속 예정.)
  자유 문자열 금지.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ── FailReason enum ──────────────────────────────────────────────────────────


class FailReason(str, Enum):
    """NODE_FAILED 이벤트의 실패 이유 (확장 가능 enum).

    gating    — 게이팅 정책(needs-approval/block)에 의한 중단
    escalation — length 가드 소진·사후검증 실패로 사람 호출 필요
    question  — 에이전트가 사람에게 되묻기 (Agent Inbox Question 칸).
                Phase 4a에서 추가(rev3 R5 예약 실현). 자유 문자열은 여전히 금지.
    """

    gating = "gating"
    escalation = "escalation"
    question = "question"


# ── 기반 클래스 ──────────────────────────────────────────────────────────────


@dataclass
class BaseEvent:
    """모든 이벤트의 공통 필드."""

    # 실행 식별자 (워크플로 한 번 실행의 UUID)
    run_id: str
    # 이벤트 발생 시각 (ISO-8601 문자열)
    ts: str
    # 스키마 버전 — 이번은 1 고정. Phase 4a부터 마이그레이션 지원.
    schema_v: int = 1

    def to_dict(self) -> dict[str, Any]:
        """dataclass → dict 직렬화."""
        return asdict(self)

    def to_json(self) -> str:
        """dataclass → JSON 문자열 직렬화."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseEvent":
        """dict → dataclass 역직렬화."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})  # type: ignore[attr-defined]


# ── Lifecycle 패밀리 ─────────────────────────────────────────────────────────


@dataclass
class RunStarted(BaseEvent):
    """워크플로 실행 시작."""

    family: str = field(default="Lifecycle", init=False)
    type: str = field(default="RUN_STARTED", init=False)

    # 워크플로 이름 (예: crm_notify)
    workflow: str = ""


@dataclass
class RunFinished(BaseEvent):
    """워크플로 실행 종료.

    status: success | rejected | failed
    """

    family: str = field(default="Lifecycle", init=False)
    type: str = field(default="RUN_FINISHED", init=False)

    workflow: str = ""
    # 종료 상태 — success / rejected / failed
    status: str = "success"


@dataclass
class RunError(BaseEvent):
    """워크플로 실행 중 복구 불가 오류 (5xx body 보존 포함).

    error: 오류 메시지 (5xx면 body[:300] 포함)
    """

    family: str = field(default="Lifecycle", init=False)
    type: str = field(default="RUN_ERROR", init=False)

    error: str = ""


# ── Text 패밀리 ──────────────────────────────────────────────────────────────


@dataclass
class TextMessage(BaseEvent):
    """직원 텍스트 출력 또는 대화 메시지.

    image_url: Phase 4a 이미지 기능을 위한 예약 필드. 현재는 None 고정.
    """

    family: str = field(default="Text", init=False)
    type: str = field(default="TEXT_MESSAGE", init=False)

    # 발화 주체 (assistant | user | system)
    role: str = "assistant"
    content: str = ""
    # Phase 4a 이미지 토글 예약 필드 — 현재 미구현, 기본 None
    image_url: str | None = None


# ── ToolCall 패밀리 ──────────────────────────────────────────────────────────


@dataclass
class ToolCallStart(BaseEvent):
    """도구 호출 시작 기록 (정화 완료된 인자)."""

    family: str = field(default="ToolCall", init=False)
    type: str = field(default="TOOL_CALL_START", init=False)

    # 이 이벤트를 발생시킨 노드 id
    node_id: str = ""
    # 호출할 도구 이름
    name: str = ""
    # 정화 완료된 인자 (dict 또는 JSON 문자열)
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallResult(BaseEvent):
    """도구 호출 결과 기록."""

    family: str = field(default="ToolCall", init=False)
    type: str = field(default="TOOL_CALL_RESULT", init=False)

    node_id: str = ""
    name: str = ""
    # 도구가 돌려준 결과 (성공이면 값, 실패면 error 필드 포함 가능)
    result: dict[str, Any] = field(default_factory=dict)


# ── State 패밀리 ─────────────────────────────────────────────────────────────


@dataclass
class StateSnapshot(BaseEvent):
    """상태 전체 스냅샷 — 캐리포워드 누적값 전부."""

    family: str = field(default="State", init=False)
    type: str = field(default="STATE_SNAPSHOT", init=False)

    # 현재 누적된 캐리포워드 전체 (키→값)
    accumulated: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateDelta(BaseEvent):
    """상태 변경분만 기록 — 새로 추가/변경된 키만 담는다."""

    family: str = field(default="State", init=False)
    type: str = field(default="STATE_DELTA", init=False)

    # 변경된 키→값 쌍만
    accumulated: dict[str, Any] = field(default_factory=dict)


# ── Interrupt 패밀리 ─────────────────────────────────────────────────────────


@dataclass
class InterruptRequested(BaseEvent):
    """게이팅이 needs-approval 도구를 만나 사람 승인을 요청함.

    payload에는 재개에 필요한 모든 정보를 담는다 (M10b):
      - node_id: 승인 대기 중인 노드
      - tool: 호출하려 했던 도구 이름
      - args: 정화된 도구 인자 (승인 후 LLM 재호출 없이 이것으로 도구만 실행)
      - accumulated: 현재까지의 캐리포워드 누적
    """

    family: str = field(default="Interrupt", init=False)
    type: str = field(default="INTERRUPT_REQUESTED", init=False)

    node_id: str = ""
    # 승인 대기 중인 도구 이름
    tool: str = ""
    # 위험 등급 (needs-approval)
    risk: str = "needs-approval"
    # 재개에 필요한 데이터 (정화된 args + 캐리포워드 누적)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class InterruptResolved(BaseEvent):
    """사람이 승인/거부 결정을 내림.

    decision: approve | reject
    """

    family: str = field(default="Interrupt", init=False)
    type: str = field(default="INTERRUPT_RESOLVED", init=False)

    node_id: str = ""
    tool: str = ""
    # 사람의 결정: approve 또는 reject
    decision: str = "approve"


@dataclass
class NodeFailed(BaseEvent):
    """노드 실패 — 사후검증 실패·length 가드 소진·에스컬레이션 등.

    reason은 확장 가능 enum (FailReason). 자유 문자열 금지.
    gating    : 게이팅에 의한 차단
    escalation: 검증 실패·재시도 소진으로 사람 호출 필요
    """

    family: str = field(default="Interrupt", init=False)
    type: str = field(default="NODE_FAILED", init=False)

    node_id: str = ""
    # FailReason enum 값만 허용 (자유 문자열 금지 — rev3 R5)
    reason: FailReason = FailReason.escalation
    # 실패 상세 메시지
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """reason을 str로 직렬화 (enum 값 그대로)."""
        d = asdict(self)
        # asdict은 enum을 값으로 직렬화하지 않으므로 명시 변환
        d["reason"] = self.reason.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeFailed":
        """역직렬화 시 reason 문자열을 FailReason enum으로 변환.

        잘못된 reason 값이면 ValueError를 발생시킨다 (자유 문자열 금지).
        """
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        reason_raw = kwargs.pop("reason", FailReason.escalation)
        kwargs["reason"] = FailReason(reason_raw)  # 잘못된 값이면 ValueError
        # family/type은 init=False이므로 제거
        kwargs.pop("family", None)
        kwargs.pop("type", None)
        return cls(**kwargs)


# ── 이벤트 레지스트리 (type 문자열 → 클래스) ────────────────────────────────

# 역직렬화 시 type 문자열로 올바른 클래스를 찾기 위한 매핑
EVENT_REGISTRY: dict[str, type[BaseEvent]] = {
    "RUN_STARTED": RunStarted,
    "RUN_FINISHED": RunFinished,
    "RUN_ERROR": RunError,
    "TEXT_MESSAGE": TextMessage,
    "TOOL_CALL_START": ToolCallStart,
    "TOOL_CALL_RESULT": ToolCallResult,
    "STATE_SNAPSHOT": StateSnapshot,
    "STATE_DELTA": StateDelta,
    "INTERRUPT_REQUESTED": InterruptRequested,
    "INTERRUPT_RESOLVED": InterruptResolved,
    "NODE_FAILED": NodeFailed,
}


def event_from_dict(data: dict[str, Any]) -> BaseEvent:
    """type 필드를 보고 올바른 이벤트 클래스로 역직렬화한다."""
    event_type = data.get("type", "")
    cls = EVENT_REGISTRY.get(event_type)
    if cls is None:
        raise ValueError(f"알 수 없는 이벤트 타입: {event_type!r}")
    if cls is NodeFailed:
        return NodeFailed.from_dict(data)
    # family/type은 init=False 필드라 제거 후 생성
    kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    kwargs.pop("family", None)
    kwargs.pop("type", None)
    return cls(**kwargs)


def event_from_json(json_str: str) -> BaseEvent:
    """JSON 문자열 → 이벤트 dataclass 역직렬화."""
    return event_from_dict(json.loads(json_str))
