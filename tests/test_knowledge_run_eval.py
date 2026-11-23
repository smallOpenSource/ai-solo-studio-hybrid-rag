"""US-007 K8 — 실서버 평가 오케스트레이션(run_eval) 오프라인 배선 검증.

오프라인 전용(slow 마커 없음) — 실서버를 치지 않는다. 실 LLM·실 bge·실
Qdrant는 fake/mock으로 우회하고, **run_eval의 배선**(엔진실증 경로가 게이팅을
켠 채로 knowledge_search를 suspend 없이 디스패치하는지, 이벤트 순서·산출물
보존)만 결정론으로 검증한다. 실서버 단계(--smoke/--full/--engine-demo 실제
호출)는 slow E2E라 별도(사용자 게이트)다.

검증:
  (1) 엔진실증 데모 노드 형식(tool·thinking=off·KNOWLEDGE_SEARCH_SCHEMA).
  (2) run_engine_demo가 게이팅 켠 경로로 success + suspend 없음 + 이벤트 순서
      (RUN_STARTED→TOOL_CALL_START→TOOL_CALL_RESULT→RUN_FINISHED) + 산출물 보존.
  (3) 실 tools_policy.yaml이 knowledge_search를 always-allow로 분류(suspend 차단).
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import knowledge.run_eval as run_eval
from office.gating import GatingPolicy, ToolCallRequest, Verdict
from office.tools import KNOWLEDGE_SEARCH_SCHEMA


# ── (1) 데모 노드 형식 ────────────────────────────────────────────────────────


def test_demo_node_shape() -> None:
    """엔진실증 데모 노드 = tool·thinking=off·knowledge_search 스키마 1개."""
    node = run_eval._make_knowledge_demo_node()
    assert node.kind == "tool"
    assert node.thinking == "off"
    assert node.next is None
    assert node.tools == [KNOWLEDGE_SEARCH_SCHEMA]


# ── (3) 실 정책이 knowledge_search를 always-allow로 분류 ──────────────────────


def test_real_policy_knowledge_search_always_allow() -> None:
    """config/tools_policy.yaml에서 knowledge_search가 always-allow(suspend 차단)."""
    real_policy_path = (
        Path(run_eval.__file__).parent.parent / "config" / "tools_policy.yaml"
    )
    policy = GatingPolicy(real_policy_path)
    req = ToolCallRequest(name="knowledge_search", args={"query": "q"})
    assert policy.evaluate(req) == Verdict.allow


# ── (2) run_engine_demo 배선 — fake LLM + mock search, 게이팅 ON ──────────────


class _FakeLLM:
    """knowledge_search 도구 호출을 추출하는 LLM stub(실서버 우회).

    NodeExecutor가 기대하는 최상위 응답 형식을 반환한다(tool 노드 prompt_json
    분기 — content가 도구 이름+인자 JSON).
    """

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls += 1
        content = (
            '{"name":"knowledge_search","arguments":{"query":"워크플로 엔진","k":3}}'
        )
        return {
            "message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": "",
                "tool_calls": [],
            },
            "content": content,
            "reasoning_content": "",
            "finish_reason": "stop",
            "usage": {},
            "latency_ms": 1.0,
        }


def _mock_search_fn(query: str, k: int = 5) -> dict[str, Any]:
    """결정론 mock — knowledge.search.search 대체(실 bge·Qdrant 우회)."""
    return {
        "results": [
            {
                "text": f"결과:{query}",
                "source": "wiki/04_workflow_engine.md",
                "score": 0.93,
            }
        ],
        "count": 1,
    }


def test_run_engine_demo_gating_on_no_suspend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_engine_demo가 게이팅 켠 경로로 success·suspend 없이 도구를 디스패치한다.

    실 LLM은 _FakeLLM으로, 실 search는 mock으로 우회한다. GatingPolicy()는
    실 정책 파일을 읽되 knowledge_search가 always-allow라 멈추지 않아야 한다.
    이벤트 순서와 산출물 보존(JSONL·JSON)까지 단언한다.
    """
    # 실 LLMClient를 _FakeLLM으로 치환(run_engine_demo 내부 import 경로).
    import core.llm_client as llm_mod

    monkeypatch.setattr(llm_mod, "LLMClient", _FakeLLM)

    # 실 knowledge.search를 mock으로 치환(office.tools.knowledge_search 지연 import 경로).
    fake_knowledge = types.ModuleType("knowledge")
    fake_search_mod = types.ModuleType("knowledge.search")
    fake_search_mod.search = _mock_search_fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "knowledge", fake_knowledge)
    monkeypatch.setitem(sys.modules, "knowledge.search", fake_search_mod)

    summary = run_eval.run_engine_demo(out_dir=tmp_path / "demo")

    # 게이팅 켠 경로인데도 always-allow라 suspend 없이 success.
    assert summary["ok"] is True
    assert summary["status"] == "success"
    assert summary["suspended"] is False
    assert summary["tool_called"] is True
    assert summary["result_count"] == 1

    # 이벤트 순서: RUN_STARTED → TOOL_CALL_START → TOOL_CALL_RESULT → RUN_FINISHED.
    types_seq = summary["event_types"]
    assert types_seq[0] == "RUN_STARTED"
    assert "TOOL_CALL_START" in types_seq
    assert "TOOL_CALL_RESULT" in types_seq
    assert types_seq[-1] == "RUN_FINISHED"
    assert "INTERRUPT_REQUESTED" not in types_seq
    assert types_seq.index("TOOL_CALL_START") < types_seq.index("TOOL_CALL_RESULT")

    # 산출물 보존 — events JSONL + result JSON.
    events_path = Path(summary["events_path"])
    result_path = Path(summary["result_path"])
    assert events_path.exists()
    assert result_path.exists()
    # JSONL이 실제 이벤트 줄을 담는다.
    lines = [
        ln for ln in events_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) == summary["n_events"]
    first = json.loads(lines[0])
    assert first["type"] == "RUN_STARTED"
    # 결과 JSON에 검색 결과 미리보기(source·score) 보존.
    saved = json.loads(result_path.read_text(encoding="utf-8"))
    assert saved["tool_result_preview"]["count"] == 1
