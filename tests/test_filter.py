"""--case / --chain 필터 단위 테스트 — 오프라인 전용."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from bench.client import LLMClient

# ── 공통 고정 응답 ────────────────────────────────────────────────────────────

_FIXED_TOOL_RESP = {
    "id": "fixed-filter-001",
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


class _FixedTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FIXED_TOOL_RESP)


def _make_case(case_id: str, lang: str = "ko") -> dict[str, Any]:
    return {
        "id": case_id,
        "lang": lang,
        "prompt": f"{case_id} 프롬프트",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "날씨",
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


def _patch_and_run_toolcall(
    cases: list[dict[str, Any]],
    case_ids: list[str] | None,
    tmpdir: str,
) -> list[dict[str, Any]]:
    """toolcall 스위트를 패치하여 실행하고 raw.jsonl 행을 반환한다."""
    import bench.suites.toolcall as tc_module

    original_root = tc_module._RESULTS_ROOT
    original_init = LLMClient.__init__

    transport = _FixedTransport()

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._http = httpx.Client(transport=transport, timeout=30)

    LLMClient.__init__ = patched_init  # type: ignore[method-assign]
    tc_module._RESULTS_ROOT = Path(tmpdir) / "results"

    cases_file = Path(tmpdir) / "toolcall_ko.jsonl"
    with open(cases_file, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    try:
        out_dir = tc_module.run(
            model="test-model",
            cases_paths=[cases_file],
            modes=["native_fc"],
            smoke=False,
            interval=0.0,
            env={"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"},
            case_ids=case_ids,
        )
        raw_path = out_dir / "raw.jsonl"
        if not raw_path.exists():
            return []
        return [
            json.loads(line)
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    finally:
        tc_module._RESULTS_ROOT = original_root
        LLMClient.__init__ = original_init  # type: ignore[method-assign]


# ── toolcall --case 필터 테스트 ───────────────────────────────────────────────


def test_case_filter_single_id() -> None:
    """--case ko-02 → ko-02만 실행되는지 확인."""
    cases = [_make_case("ko-01"), _make_case("ko-02"), _make_case("ko-03")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_toolcall(cases, ["ko-02"], tmpdir)

    assert len(rows) == 1, f"필터 후 행 수={len(rows)}, 예상=1"
    assert rows[0]["case_id"] == "ko-02"


def test_case_filter_multiple_ids() -> None:
    """--case ko-01,en-05 → 해당 두 케이스만 실행."""
    cases = [
        _make_case("ko-01", "ko"),
        _make_case("en-05", "en"),
        _make_case("ko-03", "ko"),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_toolcall(cases, ["ko-01", "en-05"], tmpdir)

    returned_ids = {r["case_id"] for r in rows}
    assert returned_ids == {"ko-01", "en-05"}, (
        f"예상 {{ko-01,en-05}}, 실제 {returned_ids}"
    )


def test_case_filter_none_runs_all() -> None:
    """--case 없으면 전체 케이스 실행."""
    cases = [_make_case("ko-01"), _make_case("ko-02"), _make_case("ko-03")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_toolcall(cases, None, tmpdir)

    assert len(rows) == 3, f"필터 없이 실행 시 행 수={len(rows)}, 예상=3"


def test_case_filter_nonexistent_id_yields_empty() -> None:
    """존재하지 않는 id 필터 시 결과가 없어야 한다."""
    cases = [_make_case("ko-01"), _make_case("ko-02")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_toolcall(cases, ["no-such-id"], tmpdir)

    assert rows == [], f"존재하지 않는 id 필터 시 빈 결과 예상, 실제={rows}"


# ── carryforward --chain 필터 테스트 ──────────────────────────────────────────

_FIXED_CHAIN_RESP = {
    "id": "fixed-chain-001",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning_content": "",
                "tool_calls": [
                    {
                        "id": "call-chain-001",
                        "type": "function",
                        "function": {
                            "name": "get_balance",
                            "arguments": '{"account": "A"}',
                        },
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55},
}


class _FixedChainTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FIXED_CHAIN_RESP)


def _make_chain(chain_id: str, lang: str = "ko") -> dict[str, Any]:
    return {
        "id": chain_id,
        "lang": lang,
        "goal": f"{chain_id} 목표",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_balance",
                    "description": "잔액 조회",
                    "parameters": {
                        "type": "object",
                        "properties": {"account": {"type": "string"}},
                        "required": ["account"],
                    },
                },
            }
        ],
        "steps": [
            {
                "expected": {"name": "get_balance", "args": {"account": "A"}},
                "tool_result": {"balance": 1000},
            }
        ],
    }


def _patch_and_run_carryforward(
    chains: list[dict[str, Any]],
    chain_ids: list[str] | None,
    tmpdir: str,
) -> list[dict[str, Any]]:
    """carryforward 스위트를 패치하여 실행하고 raw.jsonl 행을 반환한다."""
    import bench.suites.carryforward as cf_module

    original_root = cf_module._RESULTS_ROOT
    original_init = LLMClient.__init__

    transport = _FixedChainTransport()

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._http = httpx.Client(transport=transport, timeout=30)

    LLMClient.__init__ = patched_init  # type: ignore[method-assign]
    cf_module._RESULTS_ROOT = Path(tmpdir) / "results"

    chains_file = Path(tmpdir) / "multistep.jsonl"
    with open(chains_file, "w", encoding="utf-8") as f:
        for ch in chains:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")

    try:
        out_dir = cf_module.run(
            model="test-model",
            chains_path=chains_file,
            reps=1,
            interval=0.0,
            env={"BENCH_API_URL": "http://mock/v1", "BENCH_API_KEY": "mock-key"},
            chain_ids=chain_ids,
        )
        raw_path = out_dir / "raw.jsonl"
        if not raw_path.exists():
            return []
        return [
            json.loads(line)
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    finally:
        cf_module._RESULTS_ROOT = original_root
        LLMClient.__init__ = original_init  # type: ignore[method-assign]


def test_chain_filter_single_id() -> None:
    """--chain chain-02 → chain-02만 실행."""
    chains = [_make_chain("chain-01"), _make_chain("chain-02"), _make_chain("chain-03")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_carryforward(chains, ["chain-02"], tmpdir)

    chain_ids_seen = {r["chain_id"] for r in rows}
    assert chain_ids_seen == {"chain-02"}, f"실제={chain_ids_seen}"


def test_chain_filter_multiple_ids() -> None:
    """--chain chain-01,chain-03 → 두 체인만 실행."""
    chains = [_make_chain("chain-01"), _make_chain("chain-02"), _make_chain("chain-03")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_carryforward(chains, ["chain-01", "chain-03"], tmpdir)

    chain_ids_seen = {r["chain_id"] for r in rows}
    assert chain_ids_seen == {"chain-01", "chain-03"}, f"실제={chain_ids_seen}"


def test_chain_filter_none_runs_all() -> None:
    """--chain 없으면 전체 체인 실행 (reps=1이므로 체인당 2변형×1스텝=2행)."""
    chains = [_make_chain("chain-01"), _make_chain("chain-02")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_carryforward(chains, None, tmpdir)

    chain_ids_seen = {r["chain_id"] for r in rows}
    assert chain_ids_seen == {"chain-01", "chain-02"}


def test_chain_filter_nonexistent_yields_empty() -> None:
    """존재하지 않는 chain id 필터 시 결과 없음."""
    chains = [_make_chain("chain-01")]
    with tempfile.TemporaryDirectory() as tmpdir:
        rows = _patch_and_run_carryforward(chains, ["no-such-chain"], tmpdir)

    assert rows == []
