"""US-005 length 가드 — max_tokens ×2 재시도 단위 테스트.

수용 기준:
  A. 순서 단언: 첫 번째 호출이 원본 max_tokens, 두 번째가 ×2 캡 max_tokens
  B. 재시도 max_tokens 검증: retry_max_tokens = min(max_tokens * 2, 16384)
  C. 재시도도 length → NODE_FAILED(reason=escalation)
  D. 오염 없음: 절단 응답은 히스토리(messages)에 들어가지 않는다
  E. 재시도 성공(stop) → 노드 성공, sanitize 경로 정상 진행
"""

from __future__ import annotations

from typing import Any

from office.engine import NodeExecutor
from office.nodes import MAX_TOKENS_BY_KIND, Node


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class CaptureLLM:
    """호출 인자를 캡처하고 미리 정해진 응답을 순서대로 반환한다."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses
        self._idx = 0

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model": model,
                "messages": list(messages),  # 스냅샷 복사
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        resp = self._responses[self._idx]
        if self._idx < len(self._responses) - 1:
            self._idx += 1
        return resp


def _length_resp(
    content: str = '{"name":"fn","arguments":{"x":"절단...',
) -> dict[str, Any]:
    """finish_reason=length 절단 응답."""
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "reasoning_content": "",
            "tool_calls": [],
        },
        "content": content,
        "reasoning_content": "",
        "tool_calls": [],
        "finish_reason": "length",
        "usage": {},
        "latency_ms": 1.0,
    }


def _stop_resp(content: str = '{"name":"fn","arguments":{}}') -> dict[str, Any]:
    """finish_reason=stop 정상 응답."""
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "reasoning_content": "",
            "tool_calls": [],
        },
        "content": content,
        "reasoning_content": "",
        "tool_calls": [],
        "finish_reason": "stop",
        "usage": {},
        "latency_ms": 1.0,
    }


def _base_state(model: str = "test-model") -> dict[str, Any]:
    return {
        "run_id": "r-us005",
        "messages": [],
        "accumulated": {},
        "model": model,
    }


# ── A. 순서 단언: 첫 호출 원본 max_tokens, 두 번째 ×2 캡 ────────────────────


def test_retry_call_order_and_max_tokens() -> None:
    """length → 재시도 순서 및 각 호출의 max_tokens 값을 검증한다."""
    kind = "tool"
    original_max = MAX_TOKENS_BY_KIND[kind]  # 512
    expected_retry = min(original_max * 2, 16384)  # 1024

    fake = CaptureLLM([_length_resp(), _stop_resp()])
    node = Node(id="n1", kind=kind, prompt="도구 호출")
    executor = NodeExecutor(llm_client=fake)

    executor.execute(node, _base_state())

    assert len(fake.calls) == 2, f"호출 횟수가 2여야 한다, 실제: {len(fake.calls)}"
    assert fake.calls[0]["max_tokens"] == original_max, (
        f"첫 번째 호출 max_tokens={fake.calls[0]['max_tokens']}, 기대={original_max}"
    )
    assert fake.calls[1]["max_tokens"] == expected_retry, (
        f"재시도 max_tokens={fake.calls[1]['max_tokens']}, 기대={expected_retry}"
    )


def test_retry_max_tokens_cap_at_16384() -> None:
    """coding 노드(16384)의 ×2는 32768이지만 캡 16384로 제한된다."""
    kind = "coding"
    original_max = MAX_TOKENS_BY_KIND[kind]  # 16384
    expected_retry = min(original_max * 2, 16384)  # 16384 (캡 적용)

    fake = CaptureLLM([_length_resp(), _stop_resp("print('hello')")])
    node = Node(id="n_code", kind=kind, prompt="코드 작성")
    executor = NodeExecutor(llm_client=fake)

    executor.execute(node, _base_state())

    assert len(fake.calls) == 2
    assert fake.calls[1]["max_tokens"] == expected_retry, (
        f"캡 적용 실패: retry max_tokens={fake.calls[1]['max_tokens']}, 기대=16384"
    )
    assert fake.calls[1]["max_tokens"] <= 16384, "32k 절대 금지 위반"


def test_retry_max_tokens_never_exceeds_16384() -> None:
    """노드 오버라이드 max_tokens가 8192인 경우 ×2=16384, 16384 이하 보장."""
    fake = CaptureLLM([_length_resp(), _stop_resp("결과")])
    node = Node(id="n_lf", kind="longform", prompt="장문", max_tokens=8192)
    executor = NodeExecutor(llm_client=fake)

    executor.execute(node, _base_state())

    assert len(fake.calls) == 2
    assert fake.calls[1]["max_tokens"] == 16384
    assert fake.calls[1]["max_tokens"] <= 16384


# ── B. 재시도도 length → NODE_FAILED(escalation) ────────────────────────────


def test_retry_length_again_node_failed() -> None:
    """재시도도 finish_reason=length면 NODE_FAILED다."""
    fake = CaptureLLM([_length_resp(), _length_resp()])
    node = Node(id="n1", kind="tool", prompt="도구 호출")
    executor = NodeExecutor(llm_client=fake)

    result = executor.execute(node, _base_state())

    assert result.get("_node_failed") is True
    assert result.get("_fail_detail") == "finish_reason=length"
    assert len(fake.calls) == 2, "원본 + 재시도 정확히 2회 호출"


def test_single_length_also_node_failed() -> None:
    """응답이 1개(length)만 있어도 재시도 후 노드 실패다 (마지막 응답 반복)."""
    # CaptureLLM은 마지막 응답을 반복한다
    fake = CaptureLLM([_length_resp()])
    node = Node(id="n1", kind="tool", prompt="도구 호출")
    executor = NodeExecutor(llm_client=fake)

    result = executor.execute(node, _base_state())

    assert result.get("_node_failed") is True
    assert len(fake.calls) == 2


def test_retry_fail_emits_node_failed_event() -> None:
    """재시도 실패 시 NodeFailed 이벤트가 발행된다."""
    import tempfile
    import os
    from office.event_store import EventStore
    from office.events import NodeFailed, FailReason

    fake = CaptureLLM([_length_resp(), _length_resp()])
    node = Node(id="n-evt", kind="tool", prompt="이벤트 테스트")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=fake, event_store=store)
        executor.execute(node, _base_state())

        events = store.read("r-us005")
        failed_events = [e for e in events if isinstance(e, NodeFailed)]
        assert len(failed_events) == 1
        assert failed_events[0].reason == FailReason.escalation
        assert failed_events[0].node_id == "n-evt"
    finally:
        os.unlink(db_path)


# ── C. 오염 없음: 절단 응답이 히스토리에 들어가지 않는다 ────────────────────


def test_length_response_not_in_history_after_retry_fail() -> None:
    """재시도도 실패하면 절단 content가 두 번째 노드 messages에 없어야 한다."""
    truncated_content = '{"name":"fn","arguments":{"secret":"절단된 민감 데이터...'
    fake = CaptureLLM(
        [_length_resp(truncated_content), _length_resp(truncated_content)]
    )
    node = Node(id="n1", kind="tool", prompt="도구 호출")
    executor = NodeExecutor(llm_client=fake)

    result = executor.execute(node, _base_state())
    assert result.get("_node_failed") is True

    # messages에 절단 content가 없어야 한다
    for call in fake.calls:
        for msg in call["messages"]:
            assert truncated_content not in msg.get("content", ""), (
                "절단 응답이 히스토리에 오염되었다"
            )


def test_length_then_stop_no_contamination() -> None:
    """재시도 성공 시, 원본 절단 content가 히스토리에 들어가지 않아야 한다."""
    truncated = '{"name":"fn","arguments":{"x":"절단중단...'
    clean_json = '{"name":"crm_get_customer","arguments":{"email":"a@b.com"}}'

    # 2노드 시나리오: n1 length→stop, n2는 clean stop
    fake = CaptureLLM(
        [
            _length_resp(truncated),  # n1 원본 호출 → length
            _stop_resp(clean_json),  # n1 재시도 → stop
            _stop_resp(clean_json),  # n2 호출 → stop
        ]
    )
    node1 = Node(id="n1", kind="tool", prompt="1단계")
    node2 = Node(id="n2", kind="tool", prompt="2단계")
    executor = NodeExecutor(llm_client=fake)

    state = _base_state()
    state1 = executor.execute(node1, state)
    assert not state1.get("_node_failed"), "n1이 실패해서는 안 된다 (재시도 성공)"

    # n2 실행 시 messages에 truncated content 없어야 한다
    executor.execute(node2, state1)
    n2_messages = fake.calls[2]["messages"]  # 세 번째 호출 = n2
    for msg in n2_messages:
        assert truncated not in msg.get("content", ""), (
            f"절단 content가 n2 히스토리에 오염됨: {msg}"
        )


# ── D. 재시도 성공(stop) → 노드 성공 ────────────────────────────────────────


def test_retry_success_node_not_failed() -> None:
    """length → 재시도 stop이면 노드 성공이다."""
    clean = '{"name":"crm_get_customer","arguments":{"email":"x@y.com"}}'
    fake = CaptureLLM([_length_resp(), _stop_resp(clean)])
    node = Node(id="n1", kind="tool", prompt="도구 호출")
    executor = NodeExecutor(llm_client=fake)

    result = executor.execute(node, _base_state())

    assert not result.get("_node_failed"), "재시도 성공 후 노드 실패해서는 안 된다"
    assert len(fake.calls) == 2


def test_retry_success_accumulated_correct() -> None:
    """재시도 성공 시 추출 결과가 accumulated에 정상 누적된다."""
    clean = '{"name":"crm_get_customer","arguments":{"email":"a@b.com"}}'
    fake = CaptureLLM([_length_resp(), _stop_resp(clean)])
    node = Node(id="n_acc", kind="tool", prompt="도구 호출")
    executor = NodeExecutor(llm_client=fake)

    result = executor.execute(node, _base_state())

    assert not result.get("_node_failed")
    call_data = result.get("accumulated", {}).get("n_acc_call")
    assert call_data is not None, "accumulated에 n_acc_call이 없다"
    assert call_data.get("name") == "crm_get_customer"
