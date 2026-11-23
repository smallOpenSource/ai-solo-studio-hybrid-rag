"""office/events.py 단위 테스트 — §5 Unit 항목.

테스트 항목:
- 직렬화/역직렬화 라운드트립 (10이벤트 전체)
- TEXT_MESSAGE.image_url 기본값 None 단언
- NODE_FAILED.reason enum 강제 (잘못된 값 거부)
- 이벤트 10종 목록이 계획 §4.3과 일치 (TOOL_CALL_ARGS 없음·NODE_FAILED 있음)
- FailReason enum 값 확인 (gating·escalation)
"""

from __future__ import annotations

import json

import pytest

from office.events import (
    EVENT_REGISTRY,
    FailReason,
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunError,
    RunFinished,
    RunStarted,
    StateDelta,
    StateSnapshot,
    TextMessage,
    ToolCallResult,
    ToolCallStart,
    event_from_dict,
    event_from_json,
)


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _ts() -> str:
    return "2026-06-08T00:00:00"


def _run() -> str:
    return "run-test-001"


# ── 이벤트 10종 목록 단언 ────────────────────────────────────────────────────


def test_event_registry_count() -> None:
    """계획 §4.3 표의 10이벤트와 EVENT_REGISTRY가 정확히 일치한다."""
    expected_types = {
        # Lifecycle
        "RUN_STARTED",
        "RUN_FINISHED",
        "RUN_ERROR",
        # Text
        "TEXT_MESSAGE",
        # ToolCall (TOOL_CALL_ARGS는 Phase 4a 연기 → 없음)
        "TOOL_CALL_START",
        "TOOL_CALL_RESULT",
        # State
        "STATE_SNAPSHOT",
        "STATE_DELTA",
        # Interrupt
        "INTERRUPT_REQUESTED",
        "INTERRUPT_RESOLVED",
        "NODE_FAILED",
    }
    assert set(EVENT_REGISTRY.keys()) == expected_types
    assert (
        len(EVENT_REGISTRY) == 11
    )  # 11 type 문자열 → 10이벤트 (NODE_FAILED는 Interrupt 패밀리)


def test_tool_call_args_absent() -> None:
    """TOOL_CALL_ARGS는 EVENT_REGISTRY에 없어야 한다 (Phase 4a 연기)."""
    assert "TOOL_CALL_ARGS" not in EVENT_REGISTRY


def test_node_failed_present() -> None:
    """NODE_FAILED는 EVENT_REGISTRY에 있어야 한다."""
    assert "NODE_FAILED" in EVENT_REGISTRY


# ── TEXT_MESSAGE image_url 기본 None 단언 ────────────────────────────────────


def test_text_message_image_url_default_none() -> None:
    """TEXT_MESSAGE.image_url 기본값은 None (Phase 4a 예약 필드)."""
    event = TextMessage(run_id=_run(), ts=_ts(), content="안녕")
    assert event.image_url is None


def test_text_message_image_url_preserved_when_set() -> None:
    """image_url을 설정하면 직렬화/역직렬화 후에도 보존된다."""
    event = TextMessage(
        run_id=_run(), ts=_ts(), content="그림", image_url="https://example.com/img.png"
    )
    restored = event_from_dict(event.to_dict())
    assert isinstance(restored, TextMessage)
    assert restored.image_url == "https://example.com/img.png"


# ── NODE_FAILED.reason enum 강제 ─────────────────────────────────────────────


def test_node_failed_reason_valid_escalation() -> None:
    """reason=FailReason.escalation이면 정상 생성."""
    event = NodeFailed(
        run_id=_run(), ts=_ts(), node_id="n1", reason=FailReason.escalation
    )
    assert event.reason == FailReason.escalation


def test_node_failed_reason_valid_gating() -> None:
    """reason=FailReason.gating이면 정상 생성."""
    event = NodeFailed(run_id=_run(), ts=_ts(), node_id="n1", reason=FailReason.gating)
    assert event.reason == FailReason.gating


def test_node_failed_reason_invalid_raises() -> None:
    """잘못된 reason 문자열을 역직렬화하면 ValueError가 발생한다 (자유 문자열 금지)."""
    data = {
        "run_id": _run(),
        "ts": _ts(),
        "type": "NODE_FAILED",
        "family": "Interrupt",
        "schema_v": 1,
        "node_id": "n1",
        "reason": "invalid_reason_string",  # 잘못된 값
        "detail": "",
    }
    with pytest.raises(ValueError):
        event_from_dict(data)


def test_node_failed_reason_direct_invalid_raises() -> None:
    """NodeFailed.from_dict에 잘못된 reason을 넘기면 ValueError."""
    data = {
        "run_id": _run(),
        "ts": _ts(),
        "schema_v": 1,
        "node_id": "n1",
        "reason": "unknown",
        "detail": "",
    }
    with pytest.raises(ValueError):
        NodeFailed.from_dict(data)


def test_node_failed_reason_serialized_as_string() -> None:
    """NODE_FAILED.to_dict()에서 reason은 string으로 직렬화된다."""
    event = NodeFailed(run_id=_run(), ts=_ts(), reason=FailReason.gating)
    d = event.to_dict()
    assert d["reason"] == "gating"
    assert isinstance(d["reason"], str)


# ── FailReason enum 값 확인 ──────────────────────────────────────────────────


def test_fail_reason_enum_values() -> None:
    """FailReason enum은 gating·escalation·question 세 값을 가진다."""
    assert FailReason.gating.value == "gating"
    assert FailReason.escalation.value == "escalation"
    # Phase 4a에서 question 추가(rev3 R5 예약 실현 — Agent Inbox Question 칸).
    # review는 후속 예정.
    assert FailReason.question.value == "question"
    assert len(FailReason) == 3


# ── 직렬화/역직렬화 라운드트립 — 10이벤트 전체 ──────────────────────────────


def test_roundtrip_run_started() -> None:
    e = RunStarted(run_id=_run(), ts=_ts(), workflow="crm_notify")
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, RunStarted)
    assert restored.run_id == _run()
    assert restored.workflow == "crm_notify"
    assert restored.schema_v == 1


def test_roundtrip_run_finished() -> None:
    e = RunFinished(run_id=_run(), ts=_ts(), workflow="crm_notify", status="success")
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, RunFinished)
    assert restored.status == "success"


def test_roundtrip_run_finished_rejected() -> None:
    e = RunFinished(run_id=_run(), ts=_ts(), status="rejected")
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, RunFinished)
    assert restored.status == "rejected"


def test_roundtrip_run_error() -> None:
    e = RunError(run_id=_run(), ts=_ts(), error="5xx body: Internal Server Error")
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, RunError)
    assert "5xx" in restored.error


def test_roundtrip_text_message() -> None:
    e = TextMessage(
        run_id=_run(), ts=_ts(), role="assistant", content="요약: 배송 완료"
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, TextMessage)
    assert restored.content == "요약: 배송 완료"
    assert restored.image_url is None  # 기본값 None 유지


def test_roundtrip_tool_call_start() -> None:
    e = ToolCallStart(
        run_id=_run(),
        ts=_ts(),
        node_id="n1",
        name="crm_get_customer",
        args={"email": "a@b.com"},
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, ToolCallStart)
    assert restored.name == "crm_get_customer"
    assert restored.args == {"email": "a@b.com"}


def test_roundtrip_tool_call_result() -> None:
    e = ToolCallResult(
        run_id=_run(),
        ts=_ts(),
        node_id="n1",
        name="crm_get_customer",
        result={"customer_id": "C-3087"},
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, ToolCallResult)
    assert restored.result["customer_id"] == "C-3087"


def test_roundtrip_state_snapshot() -> None:
    e = StateSnapshot(
        run_id=_run(),
        ts=_ts(),
        accumulated={"customer_id": "C-3087", "order_id": "ORD-445"},
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, StateSnapshot)
    assert restored.accumulated["order_id"] == "ORD-445"


def test_roundtrip_state_delta() -> None:
    e = StateDelta(run_id=_run(), ts=_ts(), accumulated={"summary": "배송 완료"})
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, StateDelta)
    assert restored.accumulated["summary"] == "배송 완료"


def test_roundtrip_interrupt_requested() -> None:
    payload = {"node_id": "n3", "args": {"to": "a@b.com"}, "accumulated": {}}
    e = InterruptRequested(
        run_id=_run(), ts=_ts(), node_id="n3", tool="email_send", payload=payload
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, InterruptRequested)
    assert restored.tool == "email_send"
    assert restored.payload["args"] == {"to": "a@b.com"}


def test_roundtrip_interrupt_resolved() -> None:
    e = InterruptResolved(
        run_id=_run(), ts=_ts(), node_id="n3", tool="email_send", decision="approve"
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, InterruptResolved)
    assert restored.decision == "approve"


def test_roundtrip_node_failed_escalation() -> None:
    e = NodeFailed(
        run_id=_run(),
        ts=_ts(),
        node_id="n2",
        reason=FailReason.escalation,
        detail="검증 실패",
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, NodeFailed)
    assert restored.reason == FailReason.escalation
    assert restored.detail == "검증 실패"


def test_roundtrip_node_failed_gating() -> None:
    e = NodeFailed(
        run_id=_run(),
        ts=_ts(),
        node_id="n3",
        reason=FailReason.gating,
        detail="게이팅 차단",
    )
    restored = event_from_dict(e.to_dict())
    assert isinstance(restored, NodeFailed)
    assert restored.reason == FailReason.gating


# ── JSON 문자열 라운드트립 ────────────────────────────────────────────────────


def test_json_roundtrip_text_message() -> None:
    """to_json → event_from_json 라운드트립."""
    e = TextMessage(run_id=_run(), ts=_ts(), content="JSON 라운드트립 테스트")
    json_str = e.to_json()
    # 올바른 JSON인지 확인
    d = json.loads(json_str)
    assert d["type"] == "TEXT_MESSAGE"
    # 역직렬화
    restored = event_from_json(json_str)
    assert isinstance(restored, TextMessage)
    assert restored.content == "JSON 라운드트립 테스트"


def test_json_roundtrip_node_failed() -> None:
    """NODE_FAILED JSON 라운드트립 — reason이 string으로 직렬화·복원된다."""
    e = NodeFailed(
        run_id=_run(), ts=_ts(), reason=FailReason.escalation, detail="재시도 소진"
    )
    json_str = e.to_json()
    d = json.loads(json_str)
    assert d["reason"] == "escalation"  # string으로 직렬화
    restored = event_from_json(json_str)
    assert isinstance(restored, NodeFailed)
    assert restored.reason == FailReason.escalation  # enum으로 복원


# ── schema_v 기본값 확인 ─────────────────────────────────────────────────────


def test_schema_v_default_is_1() -> None:
    """모든 이벤트의 schema_v 기본값은 1."""
    events = [
        RunStarted(run_id=_run(), ts=_ts()),
        RunFinished(run_id=_run(), ts=_ts()),
        RunError(run_id=_run(), ts=_ts()),
        TextMessage(run_id=_run(), ts=_ts()),
        ToolCallStart(run_id=_run(), ts=_ts()),
        ToolCallResult(run_id=_run(), ts=_ts()),
        StateSnapshot(run_id=_run(), ts=_ts()),
        StateDelta(run_id=_run(), ts=_ts()),
        InterruptRequested(run_id=_run(), ts=_ts()),
        InterruptResolved(run_id=_run(), ts=_ts()),
        NodeFailed(run_id=_run(), ts=_ts()),
    ]
    for ev in events:
        assert ev.schema_v == 1, f"{type(ev).__name__}.schema_v != 1"


# ── family 필드 확인 ─────────────────────────────────────────────────────────


def test_family_fields_correct() -> None:
    """각 이벤트의 family 필드가 §4.3 표와 일치한다."""
    assert RunStarted(run_id=_run(), ts=_ts()).family == "Lifecycle"
    assert RunFinished(run_id=_run(), ts=_ts()).family == "Lifecycle"
    assert RunError(run_id=_run(), ts=_ts()).family == "Lifecycle"
    assert TextMessage(run_id=_run(), ts=_ts()).family == "Text"
    assert ToolCallStart(run_id=_run(), ts=_ts()).family == "ToolCall"
    assert ToolCallResult(run_id=_run(), ts=_ts()).family == "ToolCall"
    assert StateSnapshot(run_id=_run(), ts=_ts()).family == "State"
    assert StateDelta(run_id=_run(), ts=_ts()).family == "State"
    assert InterruptRequested(run_id=_run(), ts=_ts()).family == "Interrupt"
    assert InterruptResolved(run_id=_run(), ts=_ts()).family == "Interrupt"
    assert NodeFailed(run_id=_run(), ts=_ts()).family == "Interrupt"


# ── 알 수 없는 이벤트 타입 ──────────────────────────────────────────────────


def test_event_from_dict_unknown_type_raises() -> None:
    """알 수 없는 type 문자열이면 ValueError가 발생한다."""
    data = {"type": "TOTALLY_UNKNOWN", "run_id": _run(), "ts": _ts(), "schema_v": 1}
    with pytest.raises(ValueError):
        event_from_dict(data)
