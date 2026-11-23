"""G005 US-G5-3 — game_design 워크플로(planner·선형) → meta.json (AC3·AC4·AC5).

검증(전부 mock·바운드·실 game/data 오염 0):
  1) 로더 단위: _load_workflow('game_design') = (2노드, 'game_design') + 노드 계약
     (노드 수·kind∈{chat,longform,tool}·★agent 0·next 체인·마지막 None) (AC4)
  2) mock 풀런: RecordingLLM + mock game_data_write(tmp 격리) + _make_engine(tools=)
     → engine.run(workflow='game_design') → status success·meta.json 기록 (AC3)
  3) 기록된 meta가 office.game_schema.validate_meta → ok:true (retry 과금 continue 포함) (AC4)
  4) 발행 이벤트가 EVENT_REGISTRY 11종 내에서만 — 새 타입 0 (AC5)

설계 모델: tests/test_research_summary.py(엔진 배선) + tests/test_game_data_write.py
(tmp 격리). RecordingLLM은 n1_design_concept(longform)에 평문, n2_write_meta(tool)에
game_data_write tool-call JSON을 발행한다. mock game_data_write는 실 game/ 대신
tmp_path에 쓰고 받은 content를 기록해 validate_meta로 단언한다(실 산출물 무접촉).

실행: scripts/run_tests.sh engine tests/test_game_design.py -m "not slow"
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import EVENT_REGISTRY, RunFinished, ToolCallResult
from office.game_schema import validate_meta
from office.run import _load_workflow
from office.tools import GAME_DATA_WRITE_SCHEMA
from workflows.game_design import NODES, WORKFLOW_NAME

# n2가 발행할 game_data_write tool-call의 meta — validate_meta ok:true(retry 과금 포함).
_VALID_META: dict[str, Any] = {
    "theme": "폐허 도시 생존",
    "title": "네온 서바이버",
    "coreLoop": "이동·자동공격·레벨업·업그레이드 반복",
    "win": "10분 생존",
    "lose": "플레이어 HP 0",
    "continue": {"freeRetries": 1, "costCurve": [100, 200, 400], "sessionCap": 3},
}


# ── Fake LLM (노드순 응답: 설계=평문, 기록=tool-call JSON) ────────────────────


class RecordingLLM:
    """game_design 2노드용 결정론 fake LLM. messages를 calls에 기록한다.

    n1_design_concept(longform)는 설계 평문을, n2_write_meta(tool)는 game_data_write
    tool-call JSON(arguments.content=유효 meta)을 발행한다. meta를 바꿔 검증 결정론을
    만들 수 있다.
    """

    def __init__(self, meta: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._meta = meta
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
            # n1_design_concept — 설계 평문(thinking=on이라 reasoning 동반).
            content = "테마·코어루프·승패·컨티뉴 과금을 설계했다."
            reasoning = "설계 단계적 추론..."
        else:
            # n2_write_meta — game_data_write tool-call(meta를 content로).
            content = json.dumps(
                {
                    "name": "game_data_write",
                    "arguments": {"path": "data/meta.json", "content": self._meta},
                },
                ensure_ascii=False,
            )
            reasoning = ""
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


def _mock_game_data_write(captured: dict[str, Any], out_dir: Path) -> Any:
    """주입용 mock game_data_write — ★실 game/ 대신 tmp(out_dir)에 쓴다(오염 0).

    받은 args를 captured에 기록(content를 validate_meta로 단언)하고, tmp_path 하위에
    파일을 써서 '기록됨'을 디스크로도 확인 가능하게 한다. context= 2-인자 시그니처
    (engine._run_tool가 context를 별도로 넘김)를 흡수한다. 실 game_data_write의 반환
    dict 계약({written,path,bytes,deduplicated})을 모사한다.
    """

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        captured["args"] = args
        rel = args.get("path", "")
        content = args.get("content", "")
        text = (
            json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
            if isinstance(content, (dict, list))
            else str(content)
        )
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        captured["target"] = target
        return {
            "written": True,
            "path": rel,
            "bytes": len(text.encode("utf-8")),
            "deduplicated": False,
        }

    return _fn


def _make_engine(
    llm: RecordingLLM, db_path: str, captured: dict[str, Any], out_dir: Path
) -> tuple[WorkflowEngine, EventStore]:
    """주입 mock game_data_write를 가진 엔진을 만든다(gating=기본 — game_data_write는
    always-allow라 interrupt 없음). REAL_TOOLS/MOCK_TOOLS·실 game/ 무접촉."""
    store = EventStore(db_path)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"game_data_write": _mock_game_data_write(captured, out_dir)},
    )
    return engine, store


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


# ── 1. 로더 + NODES 구조 (AC4) ───────────────────────────────────────────────


def test_loader_returns_two_node_game_design() -> None:
    """_load_workflow('game_design') = (2노드, 'game_design') + 노드 계약(★agent 0)."""
    nodes, name = _load_workflow("game_design")
    assert name == "game_design"
    assert len(nodes) == 2

    n1, n2 = nodes
    # n1 설계(longform · thinking=on · next=n2)
    assert n1.id == "n1_design_concept"
    assert n1.kind == "longform"
    assert n1.thinking == "on"
    assert n1.next == "n2_write_meta"
    # n2 기록(tool · game_data_write · 종료)
    assert n2.id == "n2_write_meta"
    assert n2.kind == "tool"
    assert n2.thinking == "off"
    assert n2.tools == [GAME_DATA_WRITE_SCHEMA]
    assert n2.next is None


def test_nodes_structure_no_agent_and_chain() -> None:
    """NODES 구조: kind∈{chat,longform,tool}·★agent 0·next 체인·마지막 None."""
    assert WORKFLOW_NAME == "game_design"
    kinds = [n.kind for n in NODES]
    assert all(k in {"chat", "longform", "tool"} for k in kinds)
    assert "agent" not in kinds  # ★선형 — 자율 agent 노드 0
    # next 체인: 마지막을 제외하고 모두 다음 노드 id를 가리키고, 마지막은 None.
    ids = [n.id for n in NODES]
    for i, n in enumerate(NODES[:-1]):
        assert n.next == ids[i + 1]
    assert NODES[-1].next is None


# ── 2. mock 풀런 + meta.json 기록·검증 (AC3 / AC4) ───────────────────────────


def test_mock_full_run_writes_valid_meta(tmp_path: Path) -> None:
    """mock 풀런 → status success·meta.json 기록·받은 content가 validate_meta ok:true."""
    db_path = _tmp_db()
    captured: dict[str, Any] = {}
    try:
        llm = RecordingLLM(meta=_VALID_META)
        engine, store = _make_engine(llm, db_path, captured, tmp_path)
        state = engine.run(nodes=list(NODES), run_id="gd1", workflow=WORKFLOW_NAME)

        # ① status success
        assert state.get("_workflow_status") == "success"

        # ② meta.json 기록됨 — mock이 받은 content가 validate_meta ok:true
        assert captured["args"]["path"] == "data/meta.json"
        written_meta = captured["args"]["content"]
        res = validate_meta(written_meta)
        assert res["ok"], res["errors"]
        # retry 과금 정책(continue) 포함 확인.
        assert written_meta["continue"]["freeRetries"] == 1
        assert written_meta["continue"]["costCurve"] == [100, 200, 400]
        assert written_meta["continue"]["sessionCap"] == 3
        # 디스크에도 기록됨(tmp 격리).
        target: Path = captured["target"]
        assert target.exists()
        assert validate_meta(json.loads(target.read_text(encoding="utf-8")))["ok"]

        # game_data_write 도구가 실제 실행됨(ToolCallResult).
        events = store.read("gd1")
        tool_results = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.node_id == "n2_write_meta"
        ]
        assert len(tool_results) == 1
        assert tool_results[0].name == "game_data_write"
        assert tool_results[0].result["written"] is True

        # RUN_FINISHED(success).
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)
    finally:
        os.unlink(db_path)


# ── 3. 이벤트 11종만 (AC5) ───────────────────────────────────────────────────


def test_full_run_emits_only_registered_event_types(tmp_path: Path) -> None:
    """워크플로가 발행하는 이벤트 type은 EVENT_REGISTRY 11종 내에서만 — 새 타입 0."""
    db_path = _tmp_db()
    captured: dict[str, Any] = {}
    try:
        llm = RecordingLLM(meta=_VALID_META)
        engine, store = _make_engine(llm, db_path, captured, tmp_path)
        engine.run(nodes=list(NODES), run_id="gd2", workflow=WORKFLOW_NAME)

        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types  # 비어있지 않음
        assert types <= set(EVENT_REGISTRY), (
            f"미등록 이벤트 타입: {types - set(EVENT_REGISTRY)}"
        )
        assert len(EVENT_REGISTRY) == 11  # 계약 동결(새 타입 0)
    finally:
        os.unlink(db_path)
