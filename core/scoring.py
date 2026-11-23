"""채점기 — lenient JSON 추출 + 6-verdict AST식 채점."""

from __future__ import annotations

import json
import re
from typing import Any


def lenient_extract_json(text: str) -> dict[str, Any] | None:
    """텍스트에서 첫 번째 균형 잡힌 JSON 객체를 관대하게 추출한다.

    처리 순서:
    1. <think>...</think> 태그 제거
    2. ```json ... ``` 코드 펜스 추출 시도
    3. 첫 번째 '{' ~ 균형 닫힘 '}' 추출
    """
    if not text:
        return None

    # 1. <think> 태그 제거
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. ```json 펜스 시도
    fence_match = re.search(r"```(?:json)?\s*(\{.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 3. 첫 번째 균형 잡힌 {} 추출
    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(cleaned[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None

    return None


def _values_equal(expected: Any, got: Any) -> bool:
    """느슨한 값 비교.

    - expected가 None이면 무엇이든 OK
    - 문자열: trim + casefold
    - 숫자: 허용 오차 1e-6
    - 그 외: 동등
    """
    if expected is None:
        return True
    if got is None:
        return False
    if isinstance(expected, str) and isinstance(got, str):
        return expected.strip().casefold() == got.strip().casefold()
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        return abs(float(expected) - float(got)) <= 1e-6
    return expected == got


def score(
    expected: dict[str, Any],
    got: dict[str, Any] | None,
) -> dict[str, str]:
    """도구 호출 결과를 채점한다.

    Args:
        expected: {"name": str, "args": {k: v | None}}
        got: {"name": str, "args": dict} | None

    Returns:
        {"verdict": str, "detail": str}
        verdict 종류:
            correct          — 이름·필수인자 모두 일치
            wrong_name       — 함수 이름 불일치
            missing_required — 필수 인자 누락 (expected 값 non-None인데 got에 없음)
            undefined_arg    — got에 expected에 없는 인자 포함
            wrong_value      — 인자 값 불일치
            parse_fail       — got이 None (파싱 실패)
    """
    if got is None:
        return {"verdict": "parse_fail", "detail": "응답에서 도구 호출을 파싱하지 못함"}

    exp_name: str = expected.get("name", "")
    got_name: str = got.get("name", "")
    if exp_name.strip().casefold() != got_name.strip().casefold():
        return {
            "verdict": "wrong_name",
            "detail": f"함수명 불일치: expected={exp_name!r}, got={got_name!r}",
        }

    exp_args: dict[str, Any] = expected.get("args") or {}
    got_args: dict[str, Any] = got.get("args") or {}

    # 미정의 인자 검사 (got에 있는데 expected에 없는 것)
    for k in got_args:
        if k not in exp_args:
            return {
                "verdict": "undefined_arg",
                "detail": f"정의되지 않은 인자: {k!r}",
            }

    # 필수 인자 누락 및 값 검사
    for k, exp_v in exp_args.items():
        if exp_v is not None and k not in got_args:
            return {
                "verdict": "missing_required",
                "detail": f"필수 인자 누락: {k!r}",
            }
        if k in got_args and not _values_equal(exp_v, got_args[k]):
            return {
                "verdict": "wrong_value",
                "detail": f"인자 값 불일치: {k!r} expected={exp_v!r}, got={got_args[k]!r}",
            }

    return {"verdict": "correct", "detail": ""}
