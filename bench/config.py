"""설정 로더 — core.config로 승격됨(재export, 무중단).

실제 구현은 core/config.py에 있다. 기존 공개 이름은 전부 여기서 그대로
재노출하여 bench.config를 import 하던 코드·테스트가 무변경으로 동작한다.
"""

from __future__ import annotations

from core.config import (
    _PROJECT_ROOT,
    get_llm_api_key,
    get_llm_base_url,
    get_primary_model,
    load_endpoint_config,
    load_env,
    update_models_thinking_off,
)

__all__ = [
    "_PROJECT_ROOT",
    "get_llm_api_key",
    "get_llm_base_url",
    "get_primary_model",
    "load_endpoint_config",
    "load_env",
    "update_models_thinking_off",
]
