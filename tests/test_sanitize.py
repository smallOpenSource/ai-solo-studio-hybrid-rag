"""_sanitize_assistant — 절단된 tool_call arguments의 히스토리 오염 방지 검증."""

from __future__ import annotations

import json

from bench.suites.carryforward import _sanitize_assistant


def test_valid_args_roundtrip() -> None:
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
    out = _sanitize_assistant(msg)
    assert "reasoning_content" not in out  # 비표준 필드 제거
    args = json.loads(out["tool_calls"][0]["function"]["arguments"])
    assert args == {"a": 1, "b": "값"}


def test_truncated_args_neutralized() -> None:
    # ms-04 실사례 패턴: 장문 content 인자가 max_tokens에 잘려 미종결 문자열
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
    out = _sanitize_assistant(msg)
    fixed = out["tool_calls"][0]["function"]["arguments"]
    json.loads(fixed)  # 무조건 valid JSON이어야 함 (다음 콜 500 방지)


def test_no_tool_calls_passthrough() -> None:
    out = _sanitize_assistant({"role": "assistant", "content": "안녕하세요"})
    assert out == {"role": "assistant", "content": "안녕하세요"}
