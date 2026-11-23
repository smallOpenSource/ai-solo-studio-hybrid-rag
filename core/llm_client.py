"""LLM HTTP 클라이언트 — httpx 동기, 동시성 1, 순차 호출만 제공."""

from __future__ import annotations

import time
from typing import Any

import httpx

from core.config import get_llm_api_key, get_llm_base_url, load_env


_DEFAULT_TIMEOUT = 120.0
_MAX_RETRIES = 2


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_timings_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """llama.cpp timings 필드에서 핵심 수치만 추출한다."""
    t = raw.get("timings") or raw.get("usage", {})
    if not isinstance(t, dict):
        return {}
    keys = [
        "prompt_n",
        "prompt_ms",
        "prompt_per_token_ms",
        "predicted_n",
        "predicted_ms",
        "predicted_per_token_ms",
        "predicted_per_second",
    ]
    return {k: t[k] for k in keys if k in t}


def _parse_response(raw: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    """API 응답 dict를 정규화된 형태로 변환한다."""
    choices = raw.get("choices") or []
    choice = choices[0] if choices else {}
    message: dict[str, Any] = choice.get("message") or {}

    content: str = message.get("content") or ""
    reasoning_content: str = message.get("reasoning_content") or ""
    tool_calls: list[dict[str, Any]] = message.get("tool_calls") or []
    finish_reason: str = choice.get("finish_reason") or ""
    usage: dict[str, Any] = raw.get("usage") or {}

    return {
        "message": message,
        "content": content,
        "reasoning_content": reasoning_content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "usage": usage,
        "timings": _extract_timings_summary(raw),
        "latency_ms": latency_ms,
        "raw": raw,
    }


class LLMClient:
    """순차 LLM 호출 클라이언트. 동시 요청 경로 없음."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        env: dict[str, str] | None = None,
    ) -> None:
        e = env or load_env()
        self._base_url = (base_url or get_llm_base_url(e)).rstrip("/")
        self._api_key = api_key or get_llm_api_key(e)
        self._timeout = timeout
        # connect/read 분리: connect=10s로 서버 다운을 빠르게 감지(H-02), read는
        # self._timeout(기본 120s) 유지 — LLM 생성은 길 수 있어 단축 금지.
        self._http = httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0, read=self._timeout, write=10.0, pool=10.0
            )
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def models(self) -> list[dict[str, Any]]:
        """GET /models — 사용 가능한 모델 목록 반환."""
        url = f"{self._base_url}/models"
        headers = _build_headers(self._api_key)
        resp = self._http.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or []

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        interval: float = 0.0,
    ) -> dict[str, Any]:
        """chat/completions 호출 — 순차, 재시도 최대 2회(네트워크 오류만).

        Args:
            model: 모델 alias
            messages: 대화 메시지 목록
            tools: OpenAI 형식 도구 목록 (선택)
            response_format: JSON 스키마 강제 등 (선택)
            max_tokens: 최대 생성 토큰
            temperature: 샘플링 온도
            extra_body: 추가 요청 필드 (thinking 제어 등)
            interval: 호출 전 대기 시간(초) — 서버 부하 조절용
        """
        if interval > 0:
            time.sleep(interval)

        url = f"{self._base_url}/chat/completions"
        headers = _build_headers(self._api_key)

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        if extra_body:
            body.update(extra_body)

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                t0 = time.perf_counter()
                resp = self._http.post(url, headers=headers, json=body)
                latency_ms = (time.perf_counter() - t0) * 1000
                resp.raise_for_status()
                return _parse_response(resp.json(), latency_ms)
            except httpx.NetworkError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    backoff = 2.0**attempt
                    time.sleep(backoff)
            except httpx.HTTPStatusError as exc:
                # 응답 body를 에러 메시지에 보존 (서버 에러 사유 사후 분석용)
                snippet = exc.response.text[:300]
                exc.args = (f"{exc} | body[:300]: {snippet}",)
                # 5xx는 일시적 서버 오류일 수 있어 재시도, 4xx는 즉시 전파
                if exc.response.status_code < 500 or attempt >= _MAX_RETRIES:
                    raise
                last_exc = exc
                time.sleep(2.0**attempt)

        raise last_exc  # type: ignore[misc]
