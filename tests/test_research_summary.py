"""Phase 8 C — 리서치-요약 워크플로(research_summary) + 동적질문 messages seam.

계획 ralplan-phase8-research-summary.md §US-C·AC-C 검증(전부 mock·바운드):
  - 로더: _load_workflow("research_summary") = (2노드, "research_summary") (AC-C1)
  - mock 풀런: mock LLM + 주입 mock knowledge_search → RUN_FINISHED, 이벤트 11종만 (AC-C2/C3)
  - 빈-코퍼스 가드: count=0 → N2 고정 응답값 "근거 없음" (AC-C4)
  - thinking=on summarize → reasoning_chars>0 (AC-C5)
  - 동적질문 seam: initial_state["messages"]가 첫 노드 LLM에 도달 (AC-C0)
                  + worker payload["query"] → initial_state["messages"] 시딩(단위)
  - Inbox: 완료 run이 Notify 칸(RUN_FINISHED)에 표시 (AC-C6)

모두 FakeLLM + mock 도구로 결정론, 실서버/실 knowledge 접촉 0. 실행은
scripts/run_tests.sh engine으로 3중 가드(bare pytest 금지·hang).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from office import run as run_module
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import EVENT_REGISTRY, RunFinished, TextMessage, ToolCallResult
from office.run import _load_workflow
from office.tools import KNOWLEDGE_SEARCH_SCHEMA
from office.worker import Worker
from web_server.inbox import build_inbox
from workflows.research_summary import NODES, WORKFLOW_NAME


# ── Fake LLM (messages 기록 + 노드별 응답) ──────────────────────────────────


class RecordingLLM:
    """research_summary 2노드용 결정론 fake LLM. messages를 calls에 기록한다.

    N1 retrieve(tool)는 knowledge_search tool-call JSON을, N2 summarize(chat)는
    요약 텍스트를 발행한다. n2_content를 바꿔 빈-가드("근거 없음") 결정론을 만든다.
    """

    def __init__(self, n2_content: str, n2_reasoning: str = "요약 추론...") -> None:
        self.calls: list[dict[str, Any]] = []
        self._n2_content = n2_content
        self._n2_reasoning = n2_reasoning
        self._idx = 0

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append({"messages": list(messages), "extra_body": extra_body})
        if self._idx == 0:
            # N1 retrieve — knowledge_search tool-call.
            content = '{"name":"knowledge_search","arguments":{"query":"테스트 질의"}}'
            reasoning = ""
        else:
            # N2 summarize — 요약 텍스트(thinking=on이라 reasoning 동반).
            content = self._n2_content
            reasoning = self._n2_reasoning
        self._idx += 1
        return {
            "message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
                "tool_calls": [],
            },
            "content": content,
            "reasoning_content": reasoning,
            "finish_reason": "stop",
            "usage": {},
            "latency_ms": 7.0,
        }


def _mock_knowledge_search(results: list[dict[str, Any]]) -> Any:
    """주입용 mock knowledge_search callable — search.py 출력계약을 모사한다.

    계약: {"results":[{text,source,score}],"count":int} (search.py:16-18). 실 qdrant/
    실 bge 접촉 0(전부 인메모리 고정값). context= 인자(run_id/node_id)를 받는 2-인자
    시그니처(engine._run_tool가 context를 별도로 넘김)도 흡수한다.
    """

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"results": list(results), "count": len(results)}

    return _fn


def _make_engine(
    llm: RecordingLLM,
    db_path: str,
    knowledge_results: list[dict[str, Any]],
) -> tuple[WorkflowEngine, EventStore]:
    """주입 mock knowledge_search를 가진 엔진을 만든다(gating=기본 — knowledge_search는
    always-allow라 interrupt 없음). REAL_TOOLS/MOCK_TOOLS·실 서비스 무접촉."""
    store = EventStore(db_path)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"knowledge_search": _mock_knowledge_search(knowledge_results)},
    )
    return engine, store


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


_SAMPLE_RESULTS: list[dict[str, Any]] = [
    {
        "text": "리모트 근무 정책은 주 3일 출근이다.",
        "source": "wiki/hr.md",
        "score": 0.91,
    },
    {
        "text": "코드 리뷰는 2인 승인이 필요하다.",
        "source": "wiki/eng.md",
        "score": 0.88,
    },
]


# ── 1. 로더 (AC-C1) ──────────────────────────────────────────────────────────


def test_loader_returns_two_node_research_summary() -> None:
    """_load_workflow('research_summary') = (2노드, 'research_summary') + 노드 계약."""
    nodes, name = _load_workflow("research_summary")
    assert name == "research_summary"
    assert len(nodes) == 2

    n1, n2 = nodes
    # N1 retrieve(tool · knowledge_search · next=N2)
    assert n1.id == "n1_retrieve"
    assert n1.kind == "tool"
    assert n1.thinking == "off"
    assert n1.tools == [KNOWLEDGE_SEARCH_SCHEMA]
    assert n1.next == "n2_summarize"
    # N2 summarize(chat · thinking=on · 종료)
    assert n2.id == "n2_summarize"
    assert n2.kind == "chat"
    assert n2.thinking == "on"
    assert n2.next is None


def test_module_constants_match_loader() -> None:
    """workflows.research_summary.NODES/WORKFLOW_NAME가 로더 반환과 동일 식별자."""
    assert WORKFLOW_NAME == "research_summary"
    assert [n.id for n in NODES] == ["n1_retrieve", "n2_summarize"]


# ── 2. mock 풀런 (AC-C2 / AC-C3) ─────────────────────────────────────────────


def test_mock_full_run_reaches_run_finished() -> None:
    """mock LLM + 주입 mock knowledge_search 풀런 → RUN_FINISHED(success)."""
    db_path = _tmp_db()
    try:
        llm = RecordingLLM(n2_content="리모트 근무는 주 3일 출근이다 (wiki/hr.md).")
        engine, store = _make_engine(llm, db_path, _SAMPLE_RESULTS)
        state = engine.run(nodes=list(NODES), run_id="rs1", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "success"
        events = store.read("rs1")
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)

        # N1 knowledge_search 도구가 실제 실행됨(ToolCallResult).
        n1_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n1_retrieve"
        ]
        assert len(n1_results) == 1
        assert n1_results[0].name == "knowledge_search"
        assert n1_results[0].result["count"] == 2

        # N2 요약이 TEXT_MESSAGE로 발행됨.
        texts = [e for e in events if isinstance(e, TextMessage)]
        assert any("wiki/hr.md" in (t.content or "") for t in texts)
    finally:
        os.unlink(db_path)


def test_full_run_emits_only_registered_event_types() -> None:
    """워크플로가 발행하는 이벤트 type은 EVENT_REGISTRY 11종 내에서만 — 새 타입 0."""
    db_path = _tmp_db()
    try:
        llm = RecordingLLM(n2_content="요약 결과 (wiki/eng.md).")
        engine, store = _make_engine(llm, db_path, _SAMPLE_RESULTS)
        engine.run(nodes=list(NODES), run_id="rs2", workflow=WORKFLOW_NAME)

        # read_since(0) 원문 type 집합이 EVENT_REGISTRY 부분집합.
        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types  # 비어있지 않음
        assert types <= set(EVENT_REGISTRY), (
            f"미등록 이벤트 타입: {types - set(EVENT_REGISTRY)}"
        )
        assert len(EVENT_REGISTRY) == 11  # 계약 동결(새 타입 0)
    finally:
        os.unlink(db_path)


# ── 3. 빈-코퍼스 가드 (AC-C4 — 결정론 고정 응답값) ───────────────────────────

_EMPTY_GUARD_TEXT = "검색 결과에 근거가 없어 답변할 수 없습니다. 근거 없음."


def test_empty_corpus_guard_emits_no_basis_text() -> None:
    """빈 코퍼스(count=0) → N2 고정 응답값에 '근거 없음' 표현(환각 가드 결정론)."""
    db_path = _tmp_db()
    try:
        llm = RecordingLLM(n2_content=_EMPTY_GUARD_TEXT)
        engine, store = _make_engine(llm, db_path, knowledge_results=[])
        state = engine.run(nodes=list(NODES), run_id="rs3", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") == "success"
        events = store.read("rs3")
        # 검색이 빈 결과를 반환(count=0).
        n1_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n1_retrieve"
        ]
        assert n1_results[0].result["count"] == 0
        # 요약 TEXT_MESSAGE에 '근거 없음' 표현(결정론 고정값).
        texts = [e for e in events if isinstance(e, TextMessage)]
        assert any("근거 없음" in (t.content or "") for t in texts)
    finally:
        os.unlink(db_path)


# ── 4. thinking=on summarize (AC-C5) ─────────────────────────────────────────


def test_summarize_node_emits_reasoning() -> None:
    """summarize(thinking=on) 노드가 reasoning_chars>0를 기록한다."""
    db_path = _tmp_db()
    try:
        llm = RecordingLLM(
            n2_content="요약 (wiki/hr.md).", n2_reasoning="단계적 추론 내용..."
        )
        engine, _ = _make_engine(llm, db_path, _SAMPLE_RESULTS)
        state = engine.run(nodes=list(NODES), run_id="rs4", workflow=WORKFLOW_NAME)
        metrics = state["_node_metrics"]
        assert metrics["n1_retrieve"]["reasoning_chars"] == 0  # tool OFF
        assert metrics["n2_summarize"]["reasoning_chars"] > 0  # chat ON
    finally:
        os.unlink(db_path)


# ── 5. 동적질문 messages seam (AC-C0) ────────────────────────────────────────


def test_dynamic_question_reaches_first_node_llm() -> None:
    """initial_state['messages']의 user 질문이 engine.run 통과 후 첫 노드 LLM에 도달한다.

    engine.py:212가 state['messages']를 직접 LLM에 주입하므로(엔진 무수정), N1 호출의
    messages에 사용자 질문 문자열이 포함된다.
    """
    db_path = _tmp_db()
    try:
        llm = RecordingLLM(n2_content="요약 (wiki/hr.md).")
        engine, _ = _make_engine(llm, db_path, _SAMPLE_RESULTS)
        question = "리모트 근무 정책 질문X"
        engine.run(
            nodes=list(NODES),
            initial_state={"messages": [{"role": "user", "content": question}]},
            run_id="rs5",
            workflow=WORKFLOW_NAME,
        )
        # 첫 호출(N1)의 messages에 질문이 들어있다(messages seam).
        n1_call = llm.calls[0]
        all_content = " ".join(m.get("content", "") for m in n1_call["messages"])
        assert question in all_content, "동적질문이 N1 LLM messages에 도달하지 않음"
    finally:
        os.unlink(db_path)


def test_worker_payload_query_seeds_initial_messages() -> None:
    """worker._run_one 경로: payload['query']가 initial_state['messages']로 시딩됨(단위).

    Worker._payload_query가 payload에서 query를 읽어 user 메시지로 만들 수 있음을 단언한다
    (engine.py:212 seam의 워커측 진입 — 엔진 무수정·worker 핵심 루프 무변).
    """
    # 정상 query → 그대로.
    q = Worker._payload_query(
        {"payload_json": json.dumps({"query": "질문Y", "mock": True})}
    )
    assert q == "질문Y"
    # query 없음 → 빈 문자열(기존 워크플로 messages 미시딩).
    assert Worker._payload_query({"payload_json": json.dumps({"mock": True})}) == ""
    # payload 파손 → 빈 문자열(방어적).
    assert Worker._payload_query({"payload_json": "{not json"}) == ""
    assert Worker._payload_query({}) == ""


def test_worker_mock_sequence_for_research_summary() -> None:
    """mock 시퀀스에 research_summary 등록(knowledge_search→chat). run.py 단일 출처(run_full·worker 공유)."""
    assert run_module._MOCK_NODE_SEQUENCES["research_summary"] == [
        "knowledge_search",
        "chat",
    ]
    # 미등록 워크플로는 기본(crm) 시퀀스로 폴백(기존 동작 보존).
    assert run_module._MOCK_NODE_SEQUENCES.get("nonexistent") is None


# ── 6. Inbox (AC-C6 — 완료 run이 Notify 칸) ──────────────────────────────────


def test_completed_run_appears_in_inbox_notify() -> None:
    """mock 풀런 완료 후 build_inbox의 Notify 칸에 RUN_FINISHED가 표시된다."""
    db_path = _tmp_db()
    try:
        llm = RecordingLLM(n2_content="요약 (wiki/hr.md).")
        engine, _ = _make_engine(llm, db_path, _SAMPLE_RESULTS)
        engine.run(nodes=list(NODES), run_id="rs6", workflow=WORKFLOW_NAME)

        inbox = build_inbox(db_path)
        notify_types = {item.get("type") for item in inbox["notify"]}
        assert "RUN_FINISHED" in notify_types
        # 완료 신호 run_id 매칭.
        finished = [i for i in inbox["notify"] if i.get("type") == "RUN_FINISHED"]
        assert any(i.get("run_id") == "rs6" for i in finished)
    finally:
        os.unlink(db_path)


# ── 7. CLI run_full 동적질문 동형 시딩 (worker 경로와 동형·선택) ──────────────


def test_run_full_query_param_exists() -> None:
    """run_full에 query 파라미터가 있어 CLI 경로도 messages 시딩 가능(worker 동형)."""
    import inspect

    sig = inspect.signature(run_module.run_full)
    assert "query" in sig.parameters
