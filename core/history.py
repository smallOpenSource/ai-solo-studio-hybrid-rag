"""히스토리 되먹임 정화 — 단일 진실(bench·office 공용).

assistant 메시지를 대화 히스토리에 다시 넣기 전, 절단된 JSON이
다음 콜의 컨텍스트를 오염시키지 않도록 정화한다.

US-004(T4a) 확장:
  sanitize_for_history(message, content_is_tool_json) — 범용 단일 관문.
    (a) tool_calls 있으면 기존 정화 (b) content-JSON 정화 (c) 비표준 필드 제거.
  _sanitize_assistant — 기존 시그니처 그대로 유지 (bench 재export 호환).
    내부적으로 sanitize_for_history를 위임 호출한다.
"""

from __future__ import annotations

import json
from typing import Any

from core.scoring import lenient_extract_json


def sanitize_for_history(
    message: dict[str, Any],
    content_is_tool_json: bool = False,
) -> dict[str, Any]:
    """assistant 메시지를 히스토리에 되먹이기 전 정화하는 범용 단일 관문.

    계획 §4.4.5 + 수용 기준 2·16 (US-004 T4a).

    처리 규칙 (직렬 순서):
    (a) tool_calls 있으면 arguments를 파싱→재직렬화.
        절단/invalid면 lenient_extract_json 복구, 실패 시 빈 객체("{}").
    (b) content_is_tool_json=True면 content를 lenient_extract_json으로
        파싱→재직렬화(정규화). 복구 불가면 _sanitize_failed=True 마킹 +
        content를 ""로 설정해 깨진 JSON이 다음 콜 컨텍스트에 안 실리도록.
        (호출측이 _sanitize_failed를 보고 노드 실패 처리한다.)
    (c) 공통: reasoning_content 등 비표준 필드 제거.
        role/content/tool_calls/_sanitize_failed만 출력에 포함한다.

    Args:
        message: LLM이 반환한 assistant 메시지 dict.
        content_is_tool_json: True면 content를 도구호출 JSON으로 간주해
                              (b) 분기를 적용한다 (prompt_json_retry 기본 경로).

    Returns:
        정화된 메시지 dict. 복구 불가면 _sanitize_failed=True 포함.
    """
    out: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }

    tool_calls = message.get("tool_calls") or []

    # (a) native_fc / 미래 혼합 경로 — tool_calls 정화
    if tool_calls:
        clean: list[dict[str, Any]] = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                parsed = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed = lenient_extract_json(raw_args) or {}
            clean.append(
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": json.dumps(parsed, ensure_ascii=False),
                    },
                }
            )
        out["tool_calls"] = clean

    # (b) prompt_json_retry 기본 경로 — content-JSON 정규화
    # finish_reason=stop인데 JSON이 살짝 깨진 케이스를 잡는다.
    # (length로 절단된 응답은 엔진이 (1)단계에서 먼저 막으므로 여기까지 안 옴 — 계획 §4.4.5)
    elif content_is_tool_json:
        raw_content = message.get("content") or ""
        parsed_content = lenient_extract_json(raw_content)
        if parsed_content is not None:
            # 복구 성공: 재직렬화(정규화)해서 다음 콜에 깔끔하게 실음
            out["content"] = json.dumps(parsed_content, ensure_ascii=False)
        else:
            # 복구 불가: 빈 content + 실패 마킹. 깨진 JSON을 히스토리에 싣지 않는다.
            out["content"] = ""
            out["_sanitize_failed"] = True

    # (c) 공통: 비표준 필드는 모두 제거됨 (out에 role/content/tool_calls만 포함)
    return out


def _sanitize_assistant(message: dict[str, Any]) -> dict[str, Any]:
    """히스토리 되먹임용 assistant 메시지 정화 (기존 시그니처 유지).

    bench.suites.carryforward 및 기존 테스트(test_sanitize.py)와의
    호환성을 위해 그대로 유지한다. 내부적으로 sanitize_for_history를 사용.

    - reasoning_content 등 비표준 필드 제거
    - tool_calls.arguments를 파싱→재직렬화. 절단 시 lenient 복구/빈 객체.
    """
    return sanitize_for_history(message, content_is_tool_json=False)
