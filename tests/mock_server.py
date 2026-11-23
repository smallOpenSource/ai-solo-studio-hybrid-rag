"""httpx.BaseTransport 기반 목 서버 — endpoint_captures 기반 오프라인 테스트용."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

_CAPTURES_DIR = Path(__file__).parent / "fixtures" / "endpoint_captures"

# endpoint_captures 없을 때 쓰는 기본 응답
_DEFAULT_NATIVE_FC = {
    "id": "mock-001",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning_content": "도구를 호출합니다.",
                "tool_calls": [
                    {
                        "id": "call-mock-001",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "서울"}',
                        },
                    }
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 50,
        "completion_tokens": 20,
        "total_tokens": 70,
    },
}

_DEFAULT_PROMPT_JSON = {
    "id": "mock-002",
    "choices": [
        {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": '{"name": "get_weather", "arguments": {"city": "서울"}}',
                "reasoning_content": "JSON으로 응답합니다.",
            },
        }
    ],
    "usage": {
        "prompt_tokens": 80,
        "completion_tokens": 30,
        "total_tokens": 110,
    },
}

_DEFAULT_SCHEMA_STRICT = {
    "id": "mock-003",
    "choices": [
        {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": '{"name": "get_weather", "arguments": {"city": "서울"}}',
                "reasoning_content": "",
            },
        }
    ],
    "usage": {
        "prompt_tokens": 80,
        "completion_tokens": 25,
        "total_tokens": 105,
    },
}


def _load_capture(name: str) -> dict[str, Any] | None:
    path = _CAPTURES_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _choose(name: str, default: Any) -> Any:
    captured = _load_capture(name)
    return captured if captured is not None else default


class MockLLMTransport(httpx.BaseTransport):
    """LLM chat/completions 엔드포인트 목 전송계층.

    요청 body를 검사해 tools 유무 / response_format 여부로 응답을 선택한다.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {}
        if request.content:
            body = json.loads(request.content)

        path = request.url.path

        if path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "qwen3.6-35b-a3b", "object": "model"}]},
            )

        # chat/completions 분기
        has_tools = bool(body.get("tools"))
        has_rf = bool(body.get("response_format"))

        if has_tools:
            resp_body = _choose("probe_native_fc", _DEFAULT_NATIVE_FC)
        elif has_rf:
            resp_body = _choose("probe_schema_strict", _DEFAULT_SCHEMA_STRICT)
        else:
            resp_body = _choose("probe_native_fc", _DEFAULT_PROMPT_JSON)

        return httpx.Response(200, json=resp_body)


class MockBGETransport(httpx.BaseTransport):
    """BGE embed/rerank 엔드포인트 목 전송계층."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body: dict[str, Any] = {}
        if request.content:
            body = json.loads(request.content)

        if "embed" in path:
            captured = _load_capture("probe_bge_embed")
            if captured:
                count = len(body.get("texts", [""]))
                vec = captured.get("dense_embeddings", [[0.1] * 1024])[0]
                if len(vec) < 1024:
                    vec = vec + [0.0] * (1024 - len(vec))
                return httpx.Response(
                    200,
                    json={"dense_embeddings": [vec] * count},
                )
            count = len(body.get("texts", [""]))
            return httpx.Response(
                200,
                json={"dense_embeddings": [[0.1] * 1024] * count},
            )

        if "rerank" in path:
            captured = _load_capture("probe_bge_rerank")
            if captured:
                return httpx.Response(200, json=captured)
            docs = body.get("documents", [])
            results = [
                {"index": i, "relevance_score": 0.9 - i * 0.1} for i in range(len(docs))
            ]
            return httpx.Response(200, json=results)

        return httpx.Response(404, json={"error": "not found"})


def make_llm_transport() -> MockLLMTransport:
    """LLM 목 전송계층 인스턴스를 반환한다."""
    return MockLLMTransport()


def make_bge_transport() -> MockBGETransport:
    """BGE 목 전송계층 인스턴스를 반환한다."""
    return MockBGETransport()
