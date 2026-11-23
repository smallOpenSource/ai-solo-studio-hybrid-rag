"""정화 단일 관문(sanitize_for_history) 단위 테스트 — US-004 T4a.

테스트 범위 (계획 §5 Unit, 수용 기준 2·7, rev3 R2):
1. tool_calls 정화 — 기존 _sanitize_assistant 회귀 동치
2. content-JSON 정규화 (prompt_json_retry 경로)
3. 절단 content-JSON 복구/실패 마킹
4. reasoning_content 등 비표준 필드 제거 (공통)
5. ★ 멀티턴 content-JSON 되먹임 오염 0 (R2 핵심):
   finish_reason=stop + 부분 복구 가능한 절단 content-JSON을
   2번째 콜에 되먹였을 때, 깨진 JSON이 히스토리에 실리지 않음을
   length 가드 없이 단독 단언.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.history import _sanitize_assistant, sanitize_for_history
from office.nodes import Node
from office.engine import NodeExecutor


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _fake_resp(
    content: str = "",
    finish_reason: str = "stop",
    tool_calls: list | None = None,
    reasoning_content: str = "",
) -> dict[str, Any]:
    """fake LLM 응답 dict를 만든다."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "reasoning_content": reasoning_content,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "message": msg,
        "content": content,
        "reasoning_content": reasoning_content,
        "tool_calls": tool_calls or [],
        "finish_reason": finish_reason,
        "usage": {},
        "latency_ms": 1.0,
    }


# ── 1. tool_calls 정화 (기존 _sanitize_assistant 회귀 동치) ─────────────────


def test_tool_calls_valid_args_roundtrip() -> None:
    """tool_calls arguments가 파싱→재직렬화 후 동일하다."""
    msg = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "생각...",
        "tool_calls": [
            {
                "id": "x1",
                "type": "function",
                "function": {"name": "f", "arguments": '{"a": 1, "b": "값"}'},
            }
        ],
    }
    out = sanitize_for_history(msg, content_is_tool_json=False)
    assert "reasoning_content" not in out
    args = json.loads(out["tool_calls"][0]["function"]["arguments"])
    assert args == {"a": 1, "b": "값"}


def test_tool_calls_truncated_args_neutralized() -> None:
    """절단된 tool_calls.arguments가 valid JSON으로 복구된다 (ms-04 패턴)."""
    truncated = '{"filename": "r.md", "content": "# SaaS Pricing\\n\\n## Key Find'
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "x2",
                "type": "function",
                "function": {"name": "create_document", "arguments": truncated},
            },
        ],
    }
    out = sanitize_for_history(msg, content_is_tool_json=False)
    fixed = out["tool_calls"][0]["function"]["arguments"]
    json.loads(fixed)  # valid JSON이어야 함


def test_no_tool_calls_passthrough() -> None:
    """tool_calls 없는 메시지는 role/content만 통과한다."""
    out = sanitize_for_history({"role": "assistant", "content": "안녕하세요"})
    assert out == {"role": "assistant", "content": "안녕하세요"}


def test_sanitize_assistant_delegates_correctly() -> None:
    """_sanitize_assistant가 sanitize_for_history(content_is_tool_json=False)와 동일하다."""
    msg = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "think",
        "tool_calls": [
            {
                "id": "t1",
                "type": "function",
                "function": {"name": "fn", "arguments": '{"k": "v"}'},
            }
        ],
    }
    result_old = _sanitize_assistant(msg)
    result_new = sanitize_for_history(msg, content_is_tool_json=False)
    assert result_old == result_new


# ── 2. content-JSON 정규화 (prompt_json_retry 경로) ──────────────────────────


def test_content_json_normalizes_valid_json() -> None:
    """content가 valid JSON이면 파싱→재직렬화로 정규화된다."""
    raw = '  {"name": "crm_get_customer",   "arguments": {"email": "a@b.com"}}  '
    msg = {"role": "assistant", "content": raw, "reasoning_content": "think"}
    out = sanitize_for_history(msg, content_is_tool_json=True)
    # 정규화됨
    assert "reasoning_content" not in out
    parsed = json.loads(out["content"])
    assert parsed["name"] == "crm_get_customer"
    assert "_sanitize_failed" not in out


def test_content_json_normalizes_with_think_tag() -> None:
    """<think> 태그가 포함된 content-JSON도 파싱→재직렬화된다."""
    raw = '<think>생각</think>{"name":"fn","arguments":{"x":1}}'
    msg = {"role": "assistant", "content": raw}
    out = sanitize_for_history(msg, content_is_tool_json=True)
    parsed = json.loads(out["content"])
    assert parsed["name"] == "fn"


def test_content_json_normalizes_fence_wrapped() -> None:
    """코드 펜스(```json)로 감싸진 content-JSON도 정규화된다."""
    raw = '```json\n{"name":"fn","arguments":{"x":1}}\n```'
    msg = {"role": "assistant", "content": raw}
    out = sanitize_for_history(msg, content_is_tool_json=True)
    parsed = json.loads(out["content"])
    assert parsed["name"] == "fn"


# ── 3. 절단 content-JSON 복구/실패 마킹 ──────────────────────────────────────


def test_content_json_partial_recovery_succeeds() -> None:
    """lenient_extract_json이 복구할 수 있는 부분 절단 JSON은 정규화 성공."""
    # 균형 잡힌 JSON 앞부분이 있어 lenient_extract_json이 추출 가능한 경우
    raw = '{"name":"fn","arguments":{"a":"v"}} 뒤에 쓰레기'
    msg = {"role": "assistant", "content": raw}
    out = sanitize_for_history(msg, content_is_tool_json=True)
    # lenient_extract_json이 첫 번째 균형 잡힌 {} 추출 성공
    assert "_sanitize_failed" not in out
    parsed = json.loads(out["content"])
    assert parsed["name"] == "fn"


def test_content_json_unrecoverable_marks_failed() -> None:
    """복구 불가 절단 JSON은 _sanitize_failed=True + content="" 반환."""
    # 완전히 깨진 JSON (균형 없음)
    raw = '{"name": "fn", "arguments": {"body": "장문이 여기서 잘렸다...'
    msg = {"role": "assistant", "content": raw}
    out = sanitize_for_history(msg, content_is_tool_json=True)
    assert out["_sanitize_failed"] is True
    assert out["content"] == ""


def test_content_json_empty_content_marks_failed() -> None:
    """content가 비어있고 content_is_tool_json=True면 실패 마킹."""
    msg = {"role": "assistant", "content": ""}
    out = sanitize_for_history(msg, content_is_tool_json=True)
    assert out["_sanitize_failed"] is True


# ── 4. 비표준 필드 제거 (공통) ────────────────────────────────────────────────


def test_reasoning_content_removed_tool_calls_path() -> None:
    """reasoning_content가 tool_calls 경로에서 제거된다."""
    msg = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "긴 사고 과정...",
        "tool_calls": [
            {
                "id": "t",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }
        ],
        "extra_field": "무시됨",
    }
    out = sanitize_for_history(msg, content_is_tool_json=False)
    assert "reasoning_content" not in out
    assert "extra_field" not in out
    assert "tool_calls" in out


def test_reasoning_content_removed_content_json_path() -> None:
    """reasoning_content가 content-JSON 경로에서도 제거된다."""
    msg = {
        "role": "assistant",
        "content": '{"name":"fn","arguments":{}}',
        "reasoning_content": "사고",
    }
    out = sanitize_for_history(msg, content_is_tool_json=True)
    assert "reasoning_content" not in out


def test_only_standard_fields_in_output() -> None:
    """출력에 role/content/tool_calls/_sanitize_failed만 포함된다."""
    msg = {
        "role": "assistant",
        "content": "응답",
        "reasoning_content": "...",
        "hidden_field": "x",
        "another": 42,
    }
    out = sanitize_for_history(msg)
    allowed = {"role", "content", "tool_calls", "_sanitize_failed"}
    assert set(out.keys()) <= allowed


# ── 5. 멀티턴 content-JSON 되먹임 오염 0 (R2 핵심) ──────────────────────────


class FakeLLM:
    """멀티턴 테스트용 fake LLM.

    첫 번째 호출에서 부분 복구 가능한 절단 content-JSON(finish_reason=stop)을 반환.
    두 번째 호출에서는 받은 messages를 캡처해 검증에 사용.
    """

    def __init__(self, first_content: str) -> None:
        self.first_content = first_content
        self.call_count = 0
        self.captured_messages: list[dict] = []

    def chat(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.0,
        extra_body: dict | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.call_count += 1
        if self.call_count == 1:
            # 첫 번째 호출: finish_reason=stop + 부분 복구 가능 절단 JSON
            return {
                "message": {
                    "role": "assistant",
                    "content": self.first_content,
                    "reasoning_content": "",
                    "tool_calls": [],
                },
                "content": self.first_content,
                "reasoning_content": "",
                "tool_calls": [],
                "finish_reason": "stop",  # ★ length 아님 — 정화(b)분기가 잡는 케이스
                "usage": {},
                "latency_ms": 1.0,
            }
        else:
            # 두 번째 호출: messages를 캡처
            self.captured_messages = list(messages)
            return {
                "message": {
                    "role": "assistant",
                    "content": '{"name":"fn2","arguments":{}}',
                    "reasoning_content": "",
                    "tool_calls": [],
                },
                "content": '{"name":"fn2","arguments":{}}',
                "reasoning_content": "",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {},
                "latency_ms": 1.0,
            }


def test_multiturn_content_json_no_contamination() -> None:
    """R2: finish_reason=stop + 부분복구 가능 절단 content-JSON 되먹임 오염 0.

    length 가드 없이 단독 단언 — 정화(b)분기만의 효과를 검증한다.

    시나리오:
      1콜: finish_reason=stop, content = 균형잡힌 JSON 뒤에 쓰레기 붙은 형태
           (lenient_extract_json이 추출 가능 → 정규화 성공)
      2콜: messages 검사 → assistant content가 정규화된 JSON이어야 함
           (깨진 원본 문자열이 히스토리에 들어가 있으면 안 됨)
    """
    # 균형 잡힌 JSON 뒤에 쓰레기 텍스트가 붙은 "stop이지만 살짝 깨진" 케이스
    # lenient_extract_json은 첫 번째 균형 {} 추출 후 나머지 무시 → 복구 성공
    partially_broken = (
        '{"name":"crm_get_customer","arguments":{"email":"a@b.com"}} extra garbage text'
    )

    fake = FakeLLM(first_content=partially_broken)

    # 노드 1: tool 노드 (content_is_tool_json=True 경로)
    node1 = Node(id="n1", kind="tool", prompt="고객을 조회하라", thinking="off")
    # 노드 2: 다음 노드 (messages를 캡처하기 위해)
    node2 = Node(id="n2", kind="tool", prompt="주문을 조회하라", thinking="off")

    executor = NodeExecutor(llm_client=fake)

    # 상태 초기화
    state: dict[str, Any] = {
        "run_id": "test-r2",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }

    # 첫 번째 노드 실행
    state = executor.execute(node1, state)

    # 첫 번째 노드 성공이어야 함 (stop + 복구 가능)
    assert not state.get("_node_failed"), (
        "첫 번째 노드가 실패해서는 안 됨 — stop+복구가능 케이스"
    )

    # 두 번째 노드 실행 (messages가 히스토리에 되먹임됨)
    state = executor.execute(node2, state)

    # ★ 핵심 단언: 2콜째 messages에 깨진 JSON 원본이 없어야 한다
    assert fake.call_count == 2, "두 번째 LLM 호출이 이루어져야 함"

    # messages 중 assistant role 메시지를 찾는다
    assistant_msgs = [m for m in fake.captured_messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1, "assistant 메시지가 히스토리에 있어야 함"

    for amsg in assistant_msgs:
        content = amsg.get("content", "")
        # ★ 깨진 원본 문자열이 히스토리에 있으면 안 된다
        assert "extra garbage text" not in content, (
            f"깨진 JSON 원본이 히스토리에 실렸다: {content!r}"
        )
        # ★ content가 있으면 valid JSON이어야 한다 (빈 문자열은 허용)
        if content:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                pytest.fail(
                    f"assistant 히스토리 content가 valid JSON이 아님: {content!r}\n"
                    "정화 단일 관문(b)분기가 올바르게 동작하지 않았다."
                )


def test_multiturn_sanitize_failed_not_fed_back() -> None:
    """복구 불가 content-JSON은 히스토리에 되먹이지 않고 노드 실패로 처리된다.

    이 케이스는 _sanitize_failed=True → 노드 실패이므로
    두 번째 콜 자체가 일어나지 않는다.
    """
    # 완전 복구 불가 절단 (닫는 괄호 없음)
    unrecoverable = '{"name": "fn", "arguments": {"body": "잘린 내용...'

    call_log: list[int] = []

    class FakeLLMUnrecoverable:
        def chat(
            self,
            model,
            messages,
            max_tokens=512,
            temperature=0.0,
            extra_body=None,
            **kw,
        ):
            call_log.append(len(call_log) + 1)
            return {
                "message": {
                    "role": "assistant",
                    "content": unrecoverable,
                    "reasoning_content": "",
                    "tool_calls": [],
                },
                "content": unrecoverable,
                "reasoning_content": "",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {},
                "latency_ms": 1.0,
            }

    node = Node(id="n_fail", kind="tool", prompt="도구 호출", thinking="off")
    executor = NodeExecutor(llm_client=FakeLLMUnrecoverable())
    state: dict[str, Any] = {
        "run_id": "test-fail",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }

    result_state = executor.execute(node, state)

    # 노드가 실패로 처리되어야 한다
    assert result_state.get("_node_failed") is True, (
        "복구 불가 content-JSON → 노드 실패여야 한다"
    )
    # 히스토리 변화 없음 (messages가 추가되지 않음)
    assert result_state.get("messages", []) == [], (
        "노드 실패 시 히스토리에 오염된 메시지가 추가되면 안 된다"
    )
