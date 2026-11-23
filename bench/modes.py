"""호출 방식 — core.call_modes로 승격됨(재export, 무중단).

실제 구현은 core/call_modes.py에 있다. 기존 공개 이름은 전부 여기서 그대로
재노출하여 bench.modes를 import 하던 코드·테스트가 무변경으로 동작한다.
"""

from __future__ import annotations

from core.call_modes import (
    _JSON_SCHEMA,
    _PROMPT_JSON_SYSTEM_SUFFIX,
    _tools_to_prompt,
    build,
    extract,
)

__all__ = [
    "_JSON_SCHEMA",
    "_PROMPT_JSON_SYSTEM_SUFFIX",
    "_tools_to_prompt",
    "build",
    "extract",
]
