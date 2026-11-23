"""4가지 호출 방식 payload/extract 스냅샷 테스트."""

from __future__ import annotations


import pytest

from bench.modes import build, extract

_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "도시 날씨 조회",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "도시명"},
            },
            "required": ["city"],
        },
    },
}

_CASE = {
    "id": "test-001",
    "lang": "ko",
    "prompt": "서울 날씨 알려줘",
    "tools": [_TOOL],
    "expected": {"name": "get_weather", "args": {"city": "서울"}},
}


# ── build() 테스트 ──────────────────────────────────────────────


def test_build_native_fc_has_tools() -> None:
    kwargs = build(_CASE, "native_fc")
    assert "tools" in kwargs
    assert kwargs["tools"] == [_TOOL]
    assert kwargs["messages"][-1]["role"] == "user"
    assert kwargs["messages"][-1]["content"] == "서울 날씨 알려줘"


def test_build_native_fc_no_system() -> None:
    kwargs = build(_CASE, "native_fc")
    roles = [m["role"] for m in kwargs["messages"]]
    assert "system" not in roles


def test_build_prompt_json_has_system() -> None:
    kwargs = build(_CASE, "prompt_json")
    roles = [m["role"] for m in kwargs["messages"]]
    assert "system" in roles
    assert "tools" not in kwargs


def test_build_prompt_json_system_contains_tool_name() -> None:
    kwargs = build(_CASE, "prompt_json")
    system_content = next(
        m["content"] for m in kwargs["messages"] if m["role"] == "system"
    )
    assert "get_weather" in system_content


def test_build_prompt_json_system_contains_json_instruction() -> None:
    kwargs = build(_CASE, "prompt_json")
    system_content = next(
        m["content"] for m in kwargs["messages"] if m["role"] == "system"
    )
    assert "JSON" in system_content


def test_build_schema_strict_has_response_format() -> None:
    kwargs = build(_CASE, "schema_strict", schema_supported=True)
    assert "response_format" in kwargs
    rf = kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert "json_schema" in rf


def test_build_schema_strict_unsupported_raises() -> None:
    with pytest.raises(ValueError, match="지원하지 않음"):
        build(_CASE, "schema_strict", schema_supported=False)


def test_build_prompt_json_retry_same_as_prompt_json() -> None:
    kwargs_pj = build(_CASE, "prompt_json")
    kwargs_pjr = build(_CASE, "prompt_json_retry")
    assert kwargs_pj == kwargs_pjr


def test_build_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 모드"):
        build(_CASE, "nonexistent_mode")


# ── extract() 테스트 ────────────────────────────────────────────


def test_extract_native_fc_from_tool_calls() -> None:
    resp = {
        "tool_calls": [
            {
                "id": "call-001",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "서울"}',
                },
            }
        ],
        "content": "",
        "finish_reason": "tool_calls",
    }
    result = extract(resp, "native_fc")
    assert result is not None
    assert result["name"] == "get_weather"
    assert result["args"]["city"] == "서울"


def test_extract_native_fc_no_tool_calls_returns_none() -> None:
    resp = {"tool_calls": [], "content": "응답없음"}
    result = extract(resp, "native_fc")
    assert result is None


def test_extract_prompt_json_from_content() -> None:
    resp = {
        "tool_calls": [],
        "content": '{"name": "get_weather", "arguments": {"city": "부산"}}',
    }
    result = extract(resp, "prompt_json")
    assert result is not None
    assert result["name"] == "get_weather"
    assert result["args"]["city"] == "부산"


def test_extract_prompt_json_with_fence() -> None:
    resp = {
        "content": '```json\n{"name": "search", "arguments": {"q": "test"}}\n```',
    }
    result = extract(resp, "prompt_json")
    assert result is not None
    assert result["name"] == "search"


def test_extract_prompt_json_no_json_returns_none() -> None:
    resp = {"content": "저는 날씨를 모릅니다."}
    result = extract(resp, "prompt_json")
    assert result is None


def test_extract_schema_strict_from_content() -> None:
    resp = {
        "content": '{"name": "get_weather", "arguments": {"city": "인천"}}',
    }
    result = extract(resp, "schema_strict")
    assert result is not None
    assert result["name"] == "get_weather"
    assert result["args"]["city"] == "인천"


def test_extract_prompt_json_retry_same_as_prompt_json() -> None:
    resp = {
        "content": '{"name": "fn", "arguments": {"x": 1}}',
    }
    r1 = extract(resp, "prompt_json")
    r2 = extract(resp, "prompt_json_retry")
    assert r1 == r2


def test_extract_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 모드"):
        extract({}, "bad_mode")


def test_extract_native_fc_args_dict_passthrough() -> None:
    """arguments가 이미 dict인 경우도 처리."""
    resp = {
        "tool_calls": [
            {
                "function": {
                    "name": "calc",
                    "arguments": {"x": 10, "y": 20},
                }
            }
        ],
    }
    result = extract(resp, "native_fc")
    assert result is not None
    assert result["args"] == {"x": 10, "y": 20}
