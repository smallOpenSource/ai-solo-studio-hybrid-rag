"""thinking off 시 extra_body 병합 검증 — MockTransport 오프라인 전용."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from bench.client import LLMClient

# ── 공통 픽스처 ────────────────────────────────────────────────────────────────

_FIXED_RESP = {
    "id": "fixed-thinking-001",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning_content": "",
                "tool_calls": [
                    {
                        "id": "call-001",
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
    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
}

_SAMPLE_CASE = {
    "id": "thinking-test-001",
    "lang": "ko",
    "prompt": "서울 날씨 알려줘",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "날씨 조회",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ],
    "expected": {"name": "get_weather", "args": {"city": "서울"}},
}


class _CapturingTransport(httpx.BaseTransport):
    """요청 body를 캡처하고 고정 응답을 반환하는 MockTransport."""

    def __init__(self) -> None:
        self.captured_bodies: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.content:
            self.captured_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_FIXED_RESP)


def _make_client_with_transport(
    transport: httpx.BaseTransport,
) -> LLMClient:
    env = {"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"}
    client = LLMClient(base_url="http://mock/v1", api_key="mock-key", env=env)
    client._http = httpx.Client(transport=transport, timeout=30)
    return client


# ── 테스트 ──────────────────────────────────────────────────────────────────────


def test_thinking_off_extra_body_injected_in_direct_chat() -> None:
    """thinking=off 시 client.chat에 extra_body가 병합되는지 확인."""
    transport = _CapturingTransport()
    client = _make_client_with_transport(transport)

    client.chat(
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    client.close()

    assert len(transport.captured_bodies) == 1
    body = transport.captured_bodies[0]
    assert "chat_template_kwargs" in body, "extra_body가 요청 body에 병합되지 않음"
    assert body["chat_template_kwargs"]["enable_thinking"] is False


def test_thinking_on_no_extra_body() -> None:
    """thinking=on(기본) 시 extra_body 없이 호출되는지 확인."""
    transport = _CapturingTransport()
    client = _make_client_with_transport(transport)

    client.chat(
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
    )
    client.close()

    assert len(transport.captured_bodies) == 1
    body = transport.captured_bodies[0]
    assert "chat_template_kwargs" not in body


def test_thinking_off_injected_via_toolcall_suite() -> None:
    """toolcall 스위트에서 thinking=off 시 모든 chat 호출에 extra_body 병합 확인."""
    transport = _CapturingTransport()

    import bench.suites.toolcall as tc_module
    from bench.client import LLMClient as _LLMClient

    original_root = tc_module._RESULTS_ROOT
    original_init = _LLMClient.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._http = httpx.Client(transport=transport, timeout=30)

    _LLMClient.__init__ = patched_init  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory() as tmpdir:
        tc_module._RESULTS_ROOT = Path(tmpdir) / "results"
        cases_file = Path(tmpdir) / "toolcall_ko.jsonl"
        cases_file.write_text(
            json.dumps(_SAMPLE_CASE, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        try:
            tc_module.run(
                model="test-model",
                cases_paths=[cases_file],
                modes=["native_fc"],
                smoke=False,
                interval=0.0,
                env={"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"},
                thinking="off",
            )
        finally:
            tc_module._RESULTS_ROOT = original_root
            _LLMClient.__init__ = original_init  # type: ignore[method-assign]

    assert len(transport.captured_bodies) >= 1, "요청이 하나도 발생하지 않음"
    for body in transport.captured_bodies:
        assert "chat_template_kwargs" in body, (
            f"thinking=off 이지만 chat_template_kwargs 누락: {body}"
        )
        assert body["chat_template_kwargs"]["enable_thinking"] is False


def test_thinking_off_injected_in_prompt_json_retry() -> None:
    """prompt_json_retry 모드에서도 thinking=off extra_body가 병합되는지 확인."""
    transport = _CapturingTransport()

    import bench.suites.toolcall as tc_module
    from bench.client import LLMClient as _LLMClient

    original_root = tc_module._RESULTS_ROOT
    original_init = _LLMClient.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._http = httpx.Client(transport=transport, timeout=30)

    _LLMClient.__init__ = patched_init  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory() as tmpdir:
        tc_module._RESULTS_ROOT = Path(tmpdir) / "results"
        cases_file = Path(tmpdir) / "toolcall_ko.jsonl"
        cases_file.write_text(
            json.dumps(_SAMPLE_CASE, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        try:
            tc_module.run(
                model="test-model",
                cases_paths=[cases_file],
                modes=["prompt_json_retry"],
                smoke=False,
                interval=0.0,
                env={"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"},
                thinking="off",
            )
        finally:
            tc_module._RESULTS_ROOT = original_root
            _LLMClient.__init__ = original_init  # type: ignore[method-assign]

    assert len(transport.captured_bodies) >= 1
    for body in transport.captured_bodies:
        assert "chat_template_kwargs" in body, (
            f"prompt_json_retry에서 thinking=off extra_body 누락: {body}"
        )
        assert body["chat_template_kwargs"]["enable_thinking"] is False


def test_raw_jsonl_thinking_field_recorded() -> None:
    """raw.jsonl 각 행에 thinking 필드가 기록되는지 확인."""
    transport = _CapturingTransport()

    import bench.suites.toolcall as tc_module
    from bench.client import LLMClient as _LLMClient

    original_root = tc_module._RESULTS_ROOT
    original_init = _LLMClient.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._http = httpx.Client(transport=transport, timeout=30)

    _LLMClient.__init__ = patched_init  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory() as tmpdir:
        tc_module._RESULTS_ROOT = Path(tmpdir) / "results"
        cases_file = Path(tmpdir) / "toolcall_ko.jsonl"
        cases_file.write_text(
            json.dumps(_SAMPLE_CASE, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        try:
            out_dir = tc_module.run(
                model="test-model",
                cases_paths=[cases_file],
                modes=["native_fc"],
                smoke=False,
                interval=0.0,
                env={"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"},
                thinking="off",
            )
            raw_path = out_dir / "raw.jsonl"
            assert raw_path.exists()
            rows = [
                json.loads(line)
                for line in raw_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert rows, "raw.jsonl이 비어 있음"
            for row in rows:
                assert "thinking" in row, f"thinking 필드 누락: {row}"
                assert row["thinking"] == "off"
        finally:
            tc_module._RESULTS_ROOT = original_root
            _LLMClient.__init__ = original_init  # type: ignore[method-assign]


def test_meta_json_written_by_toolcall_suite() -> None:
    """toolcall 스위트 실행 시 meta.json이 올바르게 기록되는지 확인."""
    transport = _CapturingTransport()

    import bench.suites.toolcall as tc_module
    from bench.client import LLMClient as _LLMClient

    original_root = tc_module._RESULTS_ROOT
    original_init = _LLMClient.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._http = httpx.Client(transport=transport, timeout=30)

    _LLMClient.__init__ = patched_init  # type: ignore[method-assign]

    with tempfile.TemporaryDirectory() as tmpdir:
        tc_module._RESULTS_ROOT = Path(tmpdir) / "results"
        cases_file = Path(tmpdir) / "toolcall_ko.jsonl"
        cases_file.write_text(
            json.dumps(_SAMPLE_CASE, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        try:
            out_dir = tc_module.run(
                model="my-model",
                cases_paths=[cases_file],
                modes=["native_fc"],
                smoke=False,
                interval=0.0,
                env={"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"},
                thinking="on",
            )
            meta_path = out_dir / "meta.json"
            assert meta_path.exists(), "meta.json이 생성되지 않음"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert meta["suite"] == "toolcall"
            assert meta["model"] == "my-model"
            assert meta["thinking"] == "on"
            assert "ts" in meta
            assert "cases_paths" in meta
        finally:
            tc_module._RESULTS_ROOT = original_root
            _LLMClient.__init__ = original_init  # type: ignore[method-assign]
