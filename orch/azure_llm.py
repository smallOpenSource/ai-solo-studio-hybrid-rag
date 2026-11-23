"""orch.azure_llm — Azure OpenAI Responses API primary caller + primary/fallback helper.

Owner 지시: LLM primary = Azure Responses(gpt-5.4-mini); 현 gemma/qwen = fallback. 본 모듈은
**graph-agnostic**(R1): ``make_primary_fallback_caller(fallback)`` 는 fallback ModelCaller 를
인자로 받는다 — ``orch.graph`` 를 import 하지 않는다(graph/org_graph/deliberation 이 이 helper 를
import·단방향·순환 0).

ModelCaller 계약(orch): ``(prompt, temperature, max_tokens) -> str``.
- temperature 는 Azure Responses 호출에 **미전달**(추론모델·spike 검증). max_tokens → max_output_tokens.
- ``ORCH_LLM_PRIMARY`` 는 **call-time** 에 읽는다(import-time 캡처 금지) — eval 드라이버/테스트가 강제 off 가능.

시크릿 규율: ``AZURE_OPENAI_API_KEY`` 는 env/config 에서만. 에러는 예외 **타입명만**(응답 본문/url/헤더
절대 포함 금지 — ``core/llm_client.py`` 의 body-append 패턴 복제 금지).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_APP_ENV = _ROOT / "config" / "app.env"

ModelCaller = Callable[[str, float, int], str]


def _parse_env_file(path: Path) -> dict[str, str]:
    """stdlib KEY=VALUE 파싱(python-dotenv 의존 회피·comfy.py 선례). 따옴표/주석 처리."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _load_env() -> dict[str, str]:
    """config/app.env 를 os.environ 우선으로 병합(환경이 파일을 덮어씀)."""
    merged = _parse_env_file(_APP_ENV)
    merged.update(os.environ)
    return merged


def azure_configured(env: Optional[dict[str, str]] = None) -> bool:
    e = env if env is not None else _load_env()
    return bool(e.get("AZURE_OPENAI_ENDPOINT") and e.get("AZURE_OPENAI_API_KEY"))


def azure_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """Azure Responses 1콜 → ``output_text``(str). 실패 시 raise(호출측 fallback). 비밀 0노출.

    temperature 미전달(추론모델). max_tokens → max_output_tokens(하한 16).
    """
    env = _load_env()
    endpoint = env.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    api_key = env.get("AZURE_OPENAI_API_KEY", "")
    model = env.get("AZURE_OPENAI_MODEL_NAME", "")
    api_version = env.get("AZURE_OPENAI_RESPONSES_API_VERSION", "2025-04-01-preview")
    if not (endpoint and api_key and model):
        raise RuntimeError("azure not configured")
    try:
        from openai import OpenAI  # noqa: PLC0415 — 지연 import(모듈 로드 비용 0)

        client = OpenAI(
            api_key=api_key,
            base_url=f"{endpoint}/openai/v1",
            default_headers={"api-version": api_version},
            timeout=float(env.get("AZURE_OPENAI_TIMEOUT", "90") or 90),
        )
        resp = client.responses.create(
            model=model, input=prompt, max_output_tokens=max(16, int(max_tokens))
        )
        text = getattr(resp, "output_text", None)
        if not text:
            raise RuntimeError("azure empty output_text")
        return str(text)
    except Exception as exc:  # 카테고리명만 — 비밀/본문/url 0노출(F6)
        raise RuntimeError(f"azure_caller failed: {type(exc).__name__}") from None


def _primary_enabled() -> bool:
    """``ORCH_LLM_PRIMARY`` 를 CALL-TIME 에 읽는다. 미설정→azure(Owner default ON)·``off``→비활성."""
    return os.environ.get("ORCH_LLM_PRIMARY", "azure").strip().lower() != "off"


def make_primary_fallback_caller(
    fallback: ModelCaller, *, label: str = "llm"
) -> ModelCaller:
    """primary=Azure → fallback=현 caller(gemma/qwen) 래퍼(graph-agnostic).

    call-time 에 ``ORCH_LLM_PRIMARY`` 를 읽어, azure 가능 시 시도하고 실패하면 ``fallback`` 을
    **inline** 호출한다(세마포어 중첩 acquire 0 — fallback 이 같은 콜스택에서 실행). served_by
    로그는 greppable·redact(`served_by=primary|fallback`). 미설정/off/미구성 시 fallback 직행.
    """

    def _caller(prompt: str, temperature: float, max_tokens: int) -> str:
        if _primary_enabled() and azure_configured():
            try:
                out = azure_caller(prompt, temperature, max_tokens)
                logger.info("served_by=primary provider=azure label=%s", label)
                return out
            except Exception as exc:  # noqa: BLE001 — primary 실패는 fallback 으로
                logger.warning(
                    "served_by=fallback provider=local label=%s reason=%s",
                    label,
                    type(exc).__name__,
                )
                return fallback(prompt, temperature, max_tokens)
        return fallback(prompt, temperature, max_tokens)

    return _caller
