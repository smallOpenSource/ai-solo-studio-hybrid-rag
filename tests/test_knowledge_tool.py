"""US-006 K7 — 엔진 도구 연결(knowledge_search) 단위 테스트.

오프라인 전용 — engine env(qdrant-client 미설치)에서 실행된다.
knowledge.search를 직접 import하지 않고 mock callable로 우회한다.

검증:
  (1) KNOWLEDGE_SEARCH_SCHEMA 형식(OpenAI function 규약).
  (2) knowledge_search callable이 mock search로 올바르게 디스패치.
  (3) GatingPolicy가 knowledge_search를 always-allow로 분류.
  (4) WorkflowEngine(tools={knowledge_search:...}) 주입 시 호출 가능.
  (5) 지연 import 격리 — import office.tools는 qdrant 없이도 성공.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.gating import GatingPolicy, ToolCallRequest, Verdict
from office.nodes import Node
from office.tools import KNOWLEDGE_SEARCH_SCHEMA, MOCK_TOOLS, knowledge_search

# ── (5) 지연 import 격리 ──────────────────────────────────────────────────────


def test_import_tools_does_not_require_qdrant() -> None:
    """office.tools import 자체는 knowledge/qdrant 의존성을 건드리지 않는다."""
    import office.tools  # noqa: F401 — 에러 없으면 격리 성공


# ── (1) KNOWLEDGE_SEARCH_SCHEMA 형식 검증 ────────────────────────────────────


def test_schema_top_level_type() -> None:
    """KNOWLEDGE_SEARCH_SCHEMA 최상위 type='function' (OpenAI function 규약)."""
    assert KNOWLEDGE_SEARCH_SCHEMA["type"] == "function"


def test_schema_name() -> None:
    """스키마 function.name == 'knowledge_search'."""
    assert KNOWLEDGE_SEARCH_SCHEMA["function"]["name"] == "knowledge_search"


def test_schema_query_param_required() -> None:
    """query 파라미터가 required에 포함된다."""
    params = KNOWLEDGE_SEARCH_SCHEMA["function"]["parameters"]
    assert "query" in params["required"]
    assert params["properties"]["query"]["type"] == "string"


def test_schema_k_param_optional_integer() -> None:
    """k 파라미터는 optional integer(required에 없음)."""
    params = KNOWLEDGE_SEARCH_SCHEMA["function"]["parameters"]
    assert "k" not in params.get("required", [])
    assert params["properties"]["k"]["type"] == "integer"


def test_schema_k_default_5() -> None:
    """k 파라미터 default=5."""
    k_prop = KNOWLEDGE_SEARCH_SCHEMA["function"]["parameters"]["properties"]["k"]
    assert k_prop.get("default") == 5


# ── (2) callable 디스패치 — mock search 주입 ─────────────────────────────────


def _mock_search_fn(query: str, k: int = 5) -> dict[str, Any]:
    """결정론 mock — knowledge.search.search 대체."""
    return {
        "results": [{"text": f"결과:{query}", "source": "wiki/doc.md", "score": 0.9}],
        "count": 1,
    }


def test_callable_dispatches_to_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """knowledge_search callable이 knowledge.search.search에 위임한다(mock 주입)."""
    import types

    fake_knowledge = types.ModuleType("knowledge")
    fake_search_mod = types.ModuleType("knowledge.search")
    fake_search_mod.search = _mock_search_fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "knowledge", fake_knowledge)
    monkeypatch.setitem(sys.modules, "knowledge.search", fake_search_mod)

    result = knowledge_search({"query": "워크플로 엔진", "k": 3})
    assert result["count"] == 1
    assert result["results"][0]["text"] == "결과:워크플로 엔진"


def test_callable_default_k(monkeypatch: pytest.MonkeyPatch) -> None:
    """k 인자 없으면 기본값 5로 호출된다."""
    import types

    called_with_k: list[int] = []

    def _capture_k(query: str, k: int = 5) -> dict[str, Any]:
        called_with_k.append(k)
        return {"results": [], "count": 0}

    fake_knowledge = types.ModuleType("knowledge")
    fake_search_mod = types.ModuleType("knowledge.search")
    fake_search_mod.search = _capture_k  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "knowledge", fake_knowledge)
    monkeypatch.setitem(sys.modules, "knowledge.search", fake_search_mod)

    knowledge_search({"query": "테스트"})
    assert called_with_k == [5]


def test_mock_tools_contains_knowledge_search() -> None:
    """MOCK_TOOLS 레지스트리에 'knowledge_search' 키가 등록돼 있다."""
    assert "knowledge_search" in MOCK_TOOLS
    assert callable(MOCK_TOOLS["knowledge_search"])


# ── (3) GatingPolicy — always-allow 분류 ─────────────────────────────────────


def _write_tmp_policy(policy: dict[str, Any]) -> Path:
    """임시 파일에 YAML 정책을 쓰고 경로를 반환한다."""
    import yaml

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(policy, f, allow_unicode=True)
        return Path(f.name)


_POLICY_WITH_KNOWLEDGE: dict[str, Any] = {
    "tools": {
        "crm_get_customer": {"risk": "always-allow"},
        "knowledge_search": {"risk": "always-allow"},
        "email_send": {"risk": "needs-approval", "when": "always_interrupt"},
    },
    "default_risk": "needs-approval",
}


def test_gating_knowledge_search_always_allow() -> None:
    """GatingPolicy가 knowledge_search를 always-allow → Verdict.allow로 분류한다."""
    policy = GatingPolicy(_write_tmp_policy(_POLICY_WITH_KNOWLEDGE))
    req = ToolCallRequest(name="knowledge_search", args={"query": "테스트"})
    assert policy.evaluate(req) == Verdict.allow


def test_gating_knowledge_search_allow_in_plan_mode() -> None:
    """Plan Mode에서도 knowledge_search는 allow(이미 always-allow이므로 동일)."""
    policy = GatingPolicy(_write_tmp_policy(_POLICY_WITH_KNOWLEDGE))
    req = ToolCallRequest(name="knowledge_search", args={"query": "테스트"})
    assert policy.evaluate(req, plan_mode=True) == Verdict.allow


def test_gating_knowledge_search_from_real_policy() -> None:
    """실제 config/tools_policy.yaml에 knowledge_search: always-allow가 있다."""
    real_policy_path = Path(__file__).parent.parent / "config" / "tools_policy.yaml"
    policy = GatingPolicy(real_policy_path)
    req = ToolCallRequest(name="knowledge_search", args={"query": "실제 정책 테스트"})
    assert policy.evaluate(req) == Verdict.allow


# ── (4) WorkflowEngine 주입 시 knowledge_search 호출 가능 ─────────────────────


class _FakeLLM:
    """knowledge_search 도구 호출 인자를 추출하는 LLM stub.

    NodeExecutor가 기대하는 최상위 응답 형식(message·finish_reason·usage·latency_ms)을
    반환한다 — SequenceLLM/_stop() 패턴과 동일.
    """

    def __init__(self, tool_call_json: str) -> None:
        self._json = tool_call_json
        self.calls = 0

    def chat(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls += 1
        # tool 노드: content는 JSON 문자열로 도구 이름+인자를 담는다(prompt_json 분기).
        content = f'{{"name":"knowledge_search","arguments":{self._json}}}'
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


def _make_knowledge_node() -> Node:
    """knowledge_search 도구를 쓰는 단일 tool 노드."""
    return Node(
        id="n_ksearch",
        kind="tool",
        prompt="지식 베이스에서 워크플로 엔진을 검색하라",
        tools=[KNOWLEDGE_SEARCH_SCHEMA],
        next=None,
    )


def test_engine_dispatches_knowledge_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """WorkflowEngine에 mock knowledge_search를 주입하면 도구가 호출된다."""
    import types

    # knowledge.search mock 주입 (qdrant 불요)
    fake_knowledge = types.ModuleType("knowledge")
    fake_search_mod = types.ModuleType("knowledge.search")
    fake_search_mod.search = _mock_search_fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "knowledge", fake_knowledge)
    monkeypatch.setitem(sys.modules, "knowledge.search", fake_search_mod)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = EventStore(db_path)
    llm = _FakeLLM('{"query": "워크플로 엔진", "k": 2}')
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"knowledge_search": knowledge_search},
        gating_policy=None,
    )

    state = engine.run(
        nodes=[_make_knowledge_node()],
        run_id="ksearch-test",
        workflow="test_knowledge",
    )

    # 노드가 성공하고 결과가 accumulated에 쌓여야 한다
    assert state.get("_workflow_status") == "success"
    accumulated = state.get("accumulated", {})
    # accumulated는 {node_id_result: tool_result, ...} dict
    result_val = accumulated.get("n_ksearch_result", {})
    assert result_val.get("count") == 1
    assert result_val["results"][0]["text"] == "결과:워크플로 엔진"


def test_engine_knowledge_search_always_allow_no_suspend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """always-allow 정책이면 knowledge_search 실행 시 suspend 없이 통과한다."""
    import types

    fake_knowledge = types.ModuleType("knowledge")
    fake_search_mod = types.ModuleType("knowledge.search")
    fake_search_mod.search = _mock_search_fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "knowledge", fake_knowledge)
    monkeypatch.setitem(sys.modules, "knowledge.search", fake_search_mod)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    real_policy_path = Path(__file__).parent.parent / "config" / "tools_policy.yaml"
    gating = GatingPolicy(real_policy_path)
    store = EventStore(db_path)
    llm = _FakeLLM('{"query": "테스트 쿼리"}')
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"knowledge_search": knowledge_search},
        gating_policy=gating,
    )

    state = engine.run(
        nodes=[_make_knowledge_node()],
        run_id="ksearch-gating-test",
        workflow="test_knowledge_gating",
    )

    # always-allow이므로 suspend 없이 success
    assert state.get("_workflow_status") == "success"
    assert state.get("_workflow_status") != "suspended"
