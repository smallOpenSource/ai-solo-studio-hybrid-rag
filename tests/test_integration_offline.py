"""MockTransport 주입 통합 테스트 — toolcall 스위트 1케이스 end-to-end (서버 불필요)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx

from bench.client import LLMClient
from bench.modes import build, extract
from bench.scoring import score

_SAMPLE_CASE = {
    "id": "offline-001",
    "lang": "ko",
    "prompt": "서울 날씨 알려줘",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "도시의 날씨 조회",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "도시명"},
                    },
                    "required": ["city"],
                },
            },
        }
    ],
    "expected": {"name": "get_weather", "args": {"city": "서울"}},
}

# 고정 응답 — 캡처 파일과 무관하게 항상 tool_calls를 반환
_FIXED_NATIVE_FC_RESP = {
    "id": "fixed-001",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning_content": "도구를 호출합니다.",
                "tool_calls": [
                    {
                        "id": "call-fixed-001",
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

_FIXED_PROMPT_JSON_RESP = {
    "id": "fixed-002",
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
    "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
}


class _FixedTransport(httpx.BaseTransport):
    """캡처 파일을 무시하고 항상 고정 응답을 반환하는 목 전송계층."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body: dict = {}
        if request.content:
            body = json.loads(request.content)
        has_tools = bool(body.get("tools"))
        if has_tools:
            return httpx.Response(200, json=_FIXED_NATIVE_FC_RESP)
        return httpx.Response(200, json=_FIXED_PROMPT_JSON_RESP)


def _make_fixed_client() -> LLMClient:
    """고정 응답 transport를 주입한 LLMClient를 반환한다."""
    env = {
        "BENCH_API_URL": "http://mock-llm/v1",
        "BENCH_API_KEY": "mock-key",
    }
    client = LLMClient(base_url="http://mock-llm/v1", api_key="mock-key", env=env)
    client._http = httpx.Client(transport=_FixedTransport(), timeout=30)
    return client


def test_toolcall_native_fc_end_to_end() -> None:
    """native_fc 모드 end-to-end: build → chat → extract → score."""
    case = _SAMPLE_CASE
    client = _make_fixed_client()

    kwargs = build(case, "native_fc")
    resp = client.chat(
        model="qwen3.6-35b-a3b",
        messages=kwargs["messages"],
        tools=kwargs.get("tools"),
        max_tokens=256,
    )

    assert "finish_reason" in resp
    assert "tool_calls" in resp
    assert "latency_ms" in resp

    got = extract(resp, "native_fc")
    assert got is not None, "tool_calls에서 추출 실패"
    assert "name" in got
    assert "args" in got

    result = score(case["expected"], got)
    assert result["verdict"] == "correct", (
        f"verdict={result['verdict']}: {result['detail']}"
    )

    client.close()


def test_toolcall_prompt_json_end_to_end() -> None:
    """prompt_json 모드 end-to-end."""
    case = _SAMPLE_CASE
    client = _make_fixed_client()

    kwargs = build(case, "prompt_json")
    resp = client.chat(
        model="qwen3.6-35b-a3b",
        messages=kwargs["messages"],
        max_tokens=256,
    )

    got = extract(resp, "prompt_json")
    result = score(case["expected"], got)
    assert result["verdict"] == "correct", (
        f"verdict={result['verdict']}: {result['detail']}"
    )

    client.close()


def test_toolcall_suite_writes_jsonl() -> None:
    """toolcall 스위트가 raw.jsonl을 올바르게 기록하는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cases_file = Path(tmpdir) / "toolcall_ko.jsonl"
        with open(cases_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(_SAMPLE_CASE, ensure_ascii=False) + "\n")

        import bench.suites.toolcall as tc_module

        original_root = tc_module._RESULTS_ROOT
        tc_module._RESULTS_ROOT = Path(tmpdir) / "results"

        env = {
            "BENCH_API_URL": "http://mock-llm/v1",
            "BENCH_API_KEY": "mock-key",
        }

        original_init = LLMClient.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self._http = httpx.Client(transport=_FixedTransport(), timeout=30)

        LLMClient.__init__ = patched_init

        try:
            out_dir = tc_module.run(
                model="qwen3.6-35b-a3b",
                cases_paths=[cases_file],
                modes=["native_fc"],
                smoke=False,
                interval=0.0,
                env=env,
            )

            raw_path = out_dir / "raw.jsonl"
            assert raw_path.exists(), "raw.jsonl이 생성되지 않음"

            rows = []
            with open(raw_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))

            assert len(rows) >= 1, "결과 행이 없음"
            row = rows[0]

            assert row["suite"] == "toolcall"
            assert row["case_id"] == "offline-001"
            assert row["mode"] == "native_fc"
            assert "verdict" in row
            assert "latency_ms" in row
            assert "reasoning_chars" in row
            assert "content_chars" in row

        finally:
            tc_module._RESULTS_ROOT = original_root
            LLMClient.__init__ = original_init
