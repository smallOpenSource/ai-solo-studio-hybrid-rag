"""4가지 호출 방식 — payload 빌드 및 응답 추출."""

from __future__ import annotations

import json
from typing import Any

from core.scoring import lenient_extract_json


def _tools_to_prompt(tools: list[dict[str, Any]]) -> str:
    """도구 목록을 시스템 프롬프트용 텍스트로 직렬화한다."""
    lines = ["사용 가능한 도구 목록:"]
    for t in tools:
        fn = t.get("function") or t
        name = fn.get("name", "")
        desc = fn.get("description", "")
        params = fn.get("parameters") or {}
        lines.append(f"\n- {name}: {desc}")
        props = (params.get("properties") or {}) if isinstance(params, dict) else {}
        required = (params.get("required") or []) if isinstance(params, dict) else []
        for pname, pinfo in props.items():
            req_mark = " (필수)" if pname in required else ""
            ptype = pinfo.get("type", "") if isinstance(pinfo, dict) else ""
            pdesc = pinfo.get("description", "") if isinstance(pinfo, dict) else ""
            lines.append(f"  - {pname}{req_mark} ({ptype}): {pdesc}")
    return "\n".join(lines)


_PROMPT_JSON_SYSTEM_SUFFIX = (
    "\n\n반드시 다음 JSON 형식만 출력하십시오 (다른 텍스트 없이):\n"
    '{"name": "함수이름", "arguments": {"인자명": "값"}}'
)

_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "tool_call",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        },
    },
}


def build(
    case: dict[str, Any],
    mode: str,
    schema_supported: bool = True,
) -> dict[str, Any]:
    """케이스와 모드로부터 chat() 호출 kwargs를 만든다.

    Args:
        case: {"id", "lang", "prompt", "tools": [...], "expected": {...}}
        mode: "native_fc" | "prompt_json" | "schema_strict" | "prompt_json_retry"
        schema_supported: schema_strict 미지원 시 호출자가 False 전달

    Returns:
        core.llm_client.LLMClient.chat()에 **kwargs로 전달할 dict
    """
    user_msg: dict[str, Any] = {"role": "user", "content": case["prompt"]}
    tools: list[dict[str, Any]] = case.get("tools") or []

    if mode == "native_fc":
        return {
            "messages": [user_msg],
            "tools": tools,
        }

    if mode in ("prompt_json", "prompt_json_retry"):
        tool_desc = _tools_to_prompt(tools)
        system_content = tool_desc + _PROMPT_JSON_SYSTEM_SUFFIX
        return {
            "messages": [
                {"role": "system", "content": system_content},
                user_msg,
            ],
        }

    if mode == "schema_strict":
        if not schema_supported:
            raise ValueError(
                "schema_strict 모드: 서버가 json_schema response_format을 지원하지 않음"
            )
        tool_desc = _tools_to_prompt(tools)
        system_content = tool_desc + _PROMPT_JSON_SYSTEM_SUFFIX
        return {
            "messages": [
                {"role": "system", "content": system_content},
                user_msg,
            ],
            "response_format": _JSON_SCHEMA,
        }

    raise ValueError(f"알 수 없는 모드: {mode!r}")


def extract(
    resp: dict[str, Any],
    mode: str,
) -> dict[str, Any] | None:
    """모드별 응답에서 {"name": str, "args": dict}를 추출한다.

    반환값이 None이면 파싱 실패.
    """
    if mode == "native_fc":
        tool_calls = resp.get("tool_calls") or []
        if not tool_calls:
            return None
        tc = tool_calls[0]
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args
        return {"name": name, "args": args}

    if mode in ("prompt_json", "prompt_json_retry", "schema_strict"):
        content = resp.get("content") or ""
        parsed = lenient_extract_json(content)
        if parsed is None:
            return None
        name = parsed.get("name") or ""
        # "arguments" 또는 "args" 키 모두 허용
        args = parsed.get("arguments") or parsed.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        return {"name": name, "args": args}

    raise ValueError(f"알 수 없는 모드: {mode!r}")
