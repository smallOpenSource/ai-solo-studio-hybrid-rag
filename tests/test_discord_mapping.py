"""US-001 — event_to_message 순수 매핑 함수 전수 단언.

10이벤트 type 전수 커버:
  INTERRUPT_REQUESTED, RUN_STARTED, RUN_FINISHED, RUN_ERROR,
  NODE_FAILED(escalation/question/gating), STATE_SNAPSHOT, STATE_DELTA,
  INTERRUPT_RESOLVED, TEXT_MESSAGE, TOOL_CALL_START, TOOL_CALL_RESULT

검증 항목:
  - 버튼 custom_id 정확히 f"approve:{seq}" / f"reject:{seq}"
  - is_persistent=True (INTERRUPT_REQUESTED)
  - 토글 OFF 시 TEXT/TOOL/gating → None
  - 토글 ON 시 TEXT/TOOL/gating → spec
  - STATE_SNAPSHOT/DELTA, INTERRUPT_RESOLVED 항상 None
  - 미지 type → None
  - 결정론: 같은 입력 2회 → 동일 spec
  - mapping.py 소스에 "import discord" 문자열 없음 (정적 단언)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.discord.commands import STAGE_LABELS as _GAME_LABELS
from adapters.discord.mapping import (
    ButtonSpec,
    DiscordMessageSpec,
    event_to_message,
)

# ── 정적 단언: mapping.py에 "import discord" 문자열 없음 ─────────────────────


def test_no_discord_import_in_mapping_source():
    """mapping.py 소스에 'import discord' 문자열이 없어야 한다."""
    mapping_path = Path(__file__).parent.parent / "adapters" / "discord" / "mapping.py"
    source = mapping_path.read_text(encoding="utf-8")
    assert "import discord" not in source, (
        "mapping.py에 'import discord'가 발견됨 — discord 모듈 import 금지 위반"
    )


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _base(type_: str, **kwargs) -> dict:
    """최소 공통 payload (run_id·ts·schema_v·family·type)."""
    return {
        "run_id": "run-test-001",
        "ts": "2026-06-08T00:00:00Z",
        "schema_v": 1,
        "type": type_,
        **kwargs,
    }


# ── INTERRUPT_REQUESTED ──────────────────────────────────────────────────────


def test_interrupt_requested_returns_spec():
    payload = _base(
        "INTERRUPT_REQUESTED",
        family="Interrupt",
        node_id="node_42",
        tool="send_email",
        risk="needs-approval",
        payload={},
    )
    spec = event_to_message(seq=7, payload=payload)
    assert spec is not None
    assert isinstance(spec, DiscordMessageSpec)


def test_interrupt_requested_is_persistent():
    payload = _base(
        "INTERRUPT_REQUESTED",
        family="Interrupt",
        node_id="node_42",
        tool="send_email",
        risk="needs-approval",
        payload={},
    )
    spec = event_to_message(seq=7, payload=payload)
    assert spec is not None
    assert spec.is_persistent is True


def test_interrupt_requested_button_custom_ids():
    seq = 99
    payload = _base(
        "INTERRUPT_REQUESTED",
        family="Interrupt",
        node_id="node_1",
        tool="delete_file",
        risk="needs-approval",
        payload={},
    )
    spec = event_to_message(seq=seq, payload=payload)
    assert spec is not None
    assert len(spec.buttons) == 2
    custom_ids = {b.custom_id for b in spec.buttons}
    assert f"approve:{seq}" in custom_ids
    assert f"reject:{seq}" in custom_ids


def test_interrupt_requested_button_styles():
    payload = _base(
        "INTERRUPT_REQUESTED",
        family="Interrupt",
        node_id="node_1",
        tool="delete_file",
        risk="needs-approval",
        payload={},
    )
    spec = event_to_message(seq=5, payload=payload)
    assert spec is not None
    approve_btn = next(b for b in spec.buttons if "approve" in b.custom_id)
    reject_btn = next(b for b in spec.buttons if "reject" in b.custom_id)
    assert approve_btn.style == "success"
    assert reject_btn.style == "danger"


def test_interrupt_requested_seq_reflected_in_custom_id():
    """서로 다른 seq 값이 custom_id에 정확히 반영된다."""
    payload = _base(
        "INTERRUPT_REQUESTED",
        family="Interrupt",
        node_id="n",
        tool="t",
        risk="needs-approval",
        payload={},
    )
    for seq in (1, 42, 9999):
        spec = event_to_message(seq=seq, payload=payload)
        assert spec is not None
        ids = {b.custom_id for b in spec.buttons}
        assert f"approve:{seq}" in ids
        assert f"reject:{seq}" in ids


# ── RUN_STARTED ──────────────────────────────────────────────────────────────


def test_run_started_nongame_suppressed():
    """일반(비game) 워크플로 RUN_STARTED는 노이즈라 미게시(None·자연어 정책)."""
    payload = _base("RUN_STARTED", family="Lifecycle", workflow="crm_notify")
    assert event_to_message(seq=1, payload=payload) is None


# ── RUN_FINISHED ─────────────────────────────────────────────────────────────


def test_run_finished_nongame_suppressed():
    """일반(비game) 워크플로 RUN_FINISHED는 노이즈라 미게시(None·자연어 정책)."""
    payload = _base(
        "RUN_FINISHED", family="Lifecycle", workflow="crm_notify", status="success"
    )
    assert event_to_message(seq=2, payload=payload) is None


# ── RUN_ERROR ────────────────────────────────────────────────────────────────


def test_run_error_returns_spec():
    payload = _base("RUN_ERROR", family="Lifecycle", error="timeout after 30s")
    spec = event_to_message(seq=3, payload=payload)
    assert spec is not None
    assert "timeout" in spec.content
    assert spec.buttons == []


# ── NODE_FAILED ──────────────────────────────────────────────────────────────


def test_node_failed_escalation_returns_spec():
    payload = _base(
        "NODE_FAILED",
        family="Interrupt",
        node_id="node_5",
        reason="escalation",
        detail="재시도 소진",
    )
    spec = event_to_message(seq=10, payload=payload)
    assert spec is not None
    assert "재시도 소진" in spec.content  # detail이 자연 발화로 실림(라벨 0).
    assert "확인이 필요한" in spec.content
    assert spec.buttons == []


def test_node_failed_question_returns_spec():
    payload = _base(
        "NODE_FAILED",
        family="Interrupt",
        node_id="node_6",
        reason="question",
        detail="추가 정보 필요",
    )
    spec = event_to_message(seq=11, payload=payload)
    assert spec is not None
    assert "추가 정보 필요" in spec.content  # question 본문(detail)이 곧 자연 발화.


def test_node_failed_gating_default_none():
    """토글 기본값(None)에서 NODE_FAILED gating은 None."""
    payload = _base(
        "NODE_FAILED",
        family="Interrupt",
        node_id="node_7",
        reason="gating",
        detail="차단됨",
    )
    spec = event_to_message(seq=12, payload=payload)
    assert spec is None


def test_node_failed_gating_toggle_on_returns_spec():
    """post_gating=True 토글 시 gating도 spec 반환."""
    payload = _base(
        "NODE_FAILED",
        family="Interrupt",
        node_id="node_7",
        reason="gating",
        detail="차단됨",
    )
    spec = event_to_message(seq=12, payload=payload, toggles={"post_gating": True})
    assert spec is not None
    assert isinstance(spec, DiscordMessageSpec)


# ── STATE_SNAPSHOT / STATE_DELTA → 항상 None ─────────────────────────────────


def test_state_snapshot_always_none():
    payload = _base("STATE_SNAPSHOT", family="State", accumulated={"k": "v"})
    assert event_to_message(seq=20, payload=payload) is None


def test_state_delta_always_none():
    payload = _base("STATE_DELTA", family="State", accumulated={"k": "v"})
    assert event_to_message(seq=21, payload=payload) is None


def test_state_snapshot_always_none_even_with_toggles():
    payload = _base("STATE_SNAPSHOT", family="State", accumulated={})
    result = event_to_message(
        seq=20,
        payload=payload,
        toggles={"post_text": True, "post_tool": True, "post_gating": True},
    )
    assert result is None


def test_state_delta_always_none_even_with_toggles():
    payload = _base("STATE_DELTA", family="State", accumulated={})
    result = event_to_message(
        seq=21,
        payload=payload,
        toggles={"post_text": True, "post_tool": True, "post_gating": True},
    )
    assert result is None


# ── INTERRUPT_RESOLVED → 항상 None ───────────────────────────────────────────


def test_interrupt_resolved_always_none():
    payload = _base(
        "INTERRUPT_RESOLVED",
        family="Interrupt",
        node_id="node_42",
        tool="send_email",
        decision="approve",
    )
    assert event_to_message(seq=30, payload=payload) is None


def test_interrupt_resolved_always_none_even_with_toggles():
    payload = _base(
        "INTERRUPT_RESOLVED",
        family="Interrupt",
        node_id="n",
        tool="t",
        decision="reject",
    )
    result = event_to_message(
        seq=30,
        payload=payload,
        toggles={"post_text": True, "post_tool": True, "post_gating": True},
    )
    assert result is None


# ── TEXT_MESSAGE ──────────────────────────────────────────────────────────────


def test_text_message_default_none():
    """기본(toggles=None)에서 TEXT_MESSAGE는 None."""
    payload = _base(
        "TEXT_MESSAGE", family="Text", role="assistant", content="안녕하세요"
    )
    assert event_to_message(seq=40, payload=payload) is None


def test_text_message_toggle_off_none():
    payload = _base("TEXT_MESSAGE", family="Text", role="assistant", content="안녕")
    assert (
        event_to_message(seq=40, payload=payload, toggles={"post_text": False}) is None
    )


def test_text_message_toggle_on_returns_spec():
    payload = _base(
        "TEXT_MESSAGE", family="Text", role="assistant", content="안녕하세요"
    )
    spec = event_to_message(seq=40, payload=payload, toggles={"post_text": True})
    assert spec is not None
    assert isinstance(spec, DiscordMessageSpec)
    assert spec.buttons == []


# ── TOOL_CALL_START ───────────────────────────────────────────────────────────


def test_tool_call_start_default_none():
    payload = _base(
        "TOOL_CALL_START", family="ToolCall", node_id="n1", name="search", args={}
    )
    assert event_to_message(seq=50, payload=payload) is None


def test_tool_call_start_toggle_on_returns_spec():
    payload = _base(
        "TOOL_CALL_START", family="ToolCall", node_id="n1", name="search", args={}
    )
    spec = event_to_message(seq=50, payload=payload, toggles={"post_tool": True})
    assert spec is not None
    assert spec.buttons == []


# ── TOOL_CALL_RESULT ──────────────────────────────────────────────────────────


def test_tool_call_result_default_none():
    payload = _base(
        "TOOL_CALL_RESULT", family="ToolCall", node_id="n1", name="search", result={}
    )
    assert event_to_message(seq=51, payload=payload) is None


def test_tool_call_result_toggle_on_returns_spec():
    payload = _base(
        "TOOL_CALL_RESULT", family="ToolCall", node_id="n1", name="search", result={}
    )
    spec = event_to_message(seq=51, payload=payload, toggles={"post_tool": True})
    assert spec is not None


# ── 미지 type → None ──────────────────────────────────────────────────────────


def test_unknown_type_returns_none():
    payload = _base("UNKNOWN_FUTURE_EVENT", family="Unknown")
    assert event_to_message(seq=99, payload=payload) is None


def test_empty_type_returns_none():
    payload = _base("")
    assert event_to_message(seq=0, payload=payload) is None


def test_missing_type_key_returns_none():
    payload = {"run_id": "x", "ts": "2026-01-01T00:00:00Z"}
    assert event_to_message(seq=0, payload=payload) is None


# ── 결정론: 같은 입력 2회 → 동일 spec ───────────────────────────────────────


def test_determinism_interrupt_requested():
    payload = _base(
        "INTERRUPT_REQUESTED",
        family="Interrupt",
        node_id="node_1",
        tool="send_email",
        risk="needs-approval",
        payload={},
    )
    seq = 77
    spec1 = event_to_message(seq=seq, payload=payload)
    spec2 = event_to_message(seq=seq, payload=payload)
    assert spec1 is not None and spec2 is not None
    assert spec1.content == spec2.content
    assert spec1.is_persistent == spec2.is_persistent
    assert [(b.label, b.custom_id, b.style) for b in spec1.buttons] == [
        (b.label, b.custom_id, b.style) for b in spec2.buttons
    ]


def test_determinism_run_started():
    workflow = next(iter(_GAME_LABELS))  # game_* (자연어 상태 게시 경로 — spec 존재).
    payload = _base("RUN_STARTED", family="Lifecycle", workflow=workflow)
    spec1 = event_to_message(seq=1, payload=payload)
    spec2 = event_to_message(seq=1, payload=payload)
    assert spec1 is not None and spec2 is not None
    assert spec1.content == spec2.content


def test_determinism_text_message_toggle_on():
    payload = _base("TEXT_MESSAGE", family="Text", role="assistant", content="hello")
    toggles = {"post_text": True}
    spec1 = event_to_message(seq=5, payload=payload, toggles=toggles)
    spec2 = event_to_message(seq=5, payload=payload, toggles=toggles)
    assert spec1 is not None and spec2 is not None
    assert spec1.content == spec2.content


# ── DiscordMessageSpec / ButtonSpec 직렬화 가능 (dataclass) ──────────────────


def test_discord_message_spec_is_dataclass():
    spec = DiscordMessageSpec(content="test")
    assert hasattr(spec, "content")
    assert hasattr(spec, "buttons")
    assert hasattr(spec, "is_persistent")
    # dict 변환 가능한지 확인 (dataclass 보장)
    import dataclasses

    d = dataclasses.asdict(spec)
    assert d["content"] == "test"
    assert d["buttons"] == []
    assert d["is_persistent"] is False


def test_button_spec_is_dataclass():
    btn = ButtonSpec(label="OK", custom_id="approve:1", style="success")
    import dataclasses

    d = dataclasses.asdict(btn)
    assert d["label"] == "OK"
    assert d["custom_id"] == "approve:1"
    assert d["style"] == "success"


# ── US-G6-2: game_* 워크플로 RUN_STARTED/FINISHED 가독 단계 라벨 ──────────────


@pytest.mark.parametrize("workflow,label", list(_GAME_LABELS.items()))
def test_run_started_game_workflow_uses_stage_label(workflow: str, label: str) -> None:
    """game_* RUN_STARTED → 자연어 단계 시작 발화(브래킷 0)."""
    payload = _base("RUN_STARTED", family="Lifecycle", workflow=workflow)
    spec = event_to_message(seq=1, payload=payload)
    assert spec is not None
    assert spec.content == f"{label} 작업 시작할게요."
    assert "[" not in spec.content  # 로봇 라벨 폐기.
    assert spec.buttons == []


@pytest.mark.parametrize("workflow,label", list(_GAME_LABELS.items()))
def test_run_finished_game_workflow_uses_stage_label(workflow: str, label: str) -> None:
    """game_* RUN_FINISHED(success) → 자연어 완료 발화(브래킷 0)."""
    payload = _base(
        "RUN_FINISHED", family="Lifecycle", workflow=workflow, status="success"
    )
    spec = event_to_message(seq=2, payload=payload)
    assert spec is not None
    assert spec.content == f"{label} 작업 마쳤어요. ✅"
    assert "[" not in spec.content


@pytest.mark.parametrize(
    "workflow", ["crm_notify", "chat", "research_summary", "agent_smoke"]
)
def test_run_nongame_workflow_suppressed(workflow: str) -> None:
    """★비게임 워크플로 RUN_STARTED/FINISHED는 노이즈라 미게시(None·자연어 정책)."""
    started = event_to_message(
        seq=1, payload=_base("RUN_STARTED", family="Lifecycle", workflow=workflow)
    )
    finished = event_to_message(
        seq=2,
        payload=_base(
            "RUN_FINISHED", family="Lifecycle", workflow=workflow, status="success"
        ),
    )
    assert started is None
    assert finished is None


# ── 자연어 발화(2026-06-16): assistant verbatim · user 되울림 0 · game 실패 ─────


def test_text_message_user_role_not_echoed():
    """★사용자 발화(role=user)는 post_text여도 되울리지 않음(None)."""
    payload = _base("TEXT_MESSAGE", family="Text", role="user", content="내 말")
    assert (
        event_to_message(seq=41, payload=payload, toggles={"post_text": True}) is None
    )


def test_text_message_assistant_posted_verbatim():
    """★assistant 발화는 라벨/브래킷 0·내용 그대로(트렁케이션 0)."""
    payload = _base(
        "TEXT_MESSAGE",
        family="Text",
        role="assistant",
        content="네, 빌드는 잘 돌아가요!",
    )
    spec = event_to_message(seq=42, payload=payload, toggles={"post_text": True})
    assert spec is not None
    assert spec.content == "네, 빌드는 잘 돌아가요!"
    assert "[" not in spec.content and "메시지:" not in spec.content


def test_run_finished_game_failure_natural():
    """game_* RUN_FINISHED(비success) → 자연어 실패 발화(브래킷 0)."""
    workflow = next(iter(_GAME_LABELS))
    label = _GAME_LABELS[workflow]
    payload = _base(
        "RUN_FINISHED", family="Lifecycle", workflow=workflow, status="failed"
    )
    spec = event_to_message(seq=3, payload=payload)
    assert spec is not None
    assert label in spec.content and "끝내지 못했어요" in spec.content
    assert "[" not in spec.content
