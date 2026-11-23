"""G005 US-G5-6 — game_assemble(런타임 조립) + game_qa(결정론 QA 게이트) 워크플로
백엔드 테스트 (AC4·AC5·AC6).

검증(전부 mock·바운드·실 game/data 오염 0):
  game_assemble:
    ① 입력 데이터 존재 시 mock 풀런: RecordingLLM + mock game_data_read(유효 데이터 반환) +
       mock game_data_write(tmp 기록) → status success, assembly.json{ready:true} 기록,
       read 5회·write 1회. tmp 격리(실 game/data 무접촉). (AC4)
    ② ★입력 부재 시: game_data_read required=True가 없는 파일을 만나 워크플로가
       **성공하지 않음**(NODE_FAILED) 단언 — 단계 계약 검증(입력 부재→명시 실패).
  game_qa:
    ③ 베이스라인(유효) 데이터 → game_validate ok:true → qa_report.json pass:true (AC5).
    ④ ★고의 깨진 데이터 → game_validate ok:false → qa_report.json pass:false 결정론 (AC5).
  공통:
    ⑤ 11종 이벤트만(EVENT_REGISTRY) + NODES 구조(kind∈chat/longform/coding/tool·agent 0·
       next 체인) + _load_workflow 로더 단언(★안정적 node id — US-G5-7용). (AC4)

설계 모델: tests/test_game_balance.py(엔진 배선·tmp 격리·RecordingLLM).

실행: scripts/run_tests.sh engine tests/test_game_assemble_qa.py -m "not slow"
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
from office.run import _is_game_workflow, _load_workflow
from office.tools import (
    GAME_DATA_READ_SCHEMA,
    GAME_DATA_WRITE_SCHEMA,
    GAME_VALIDATE_SCHEMA,
)
from workflows.game_assemble import NODES as ASSEMBLE_NODES
from workflows.game_assemble import WORKFLOW_NAME as ASSEMBLE_NAME
from workflows.game_qa import NODES as QA_NODES
from workflows.game_qa import WORKFLOW_NAME as QA_NAME

# game_assemble n1~n5가 읽을 유효 데이터(validate_* ok:true). game_data_read mock이 돌려준다.
_VALID_META: dict[str, Any] = {
    "theme": "심야 묘지 생존",
    "title": "Midnight Graveyard Survivor",
    "coreLoop": "자동공격으로 적을 처치하며 생존",
    "win": "지정 생존 시간 달성",
    "lose": "HP 0",
    "continue": {"freeRetries": 2, "costCurve": [0, 100, 200], "sessionCap": 5},
}
_VALID_ENEMIES: dict[str, Any] = {
    "enemies": [
        {
            "id": "zombie",
            "name": "좀비",
            "hp": 30,
            "speed": 40,
            "damage": 8,
            "spawnWeight": 10,
            "behavior": "chase",
        },
        {
            "id": "bat",
            "name": "박쥐",
            "hp": 10,
            "speed": 90,
            "damage": 4,
            "spawnWeight": 8,
            "behavior": "swarm",
        },
        {
            "id": "boar",
            "name": "멧돼지",
            "hp": 60,
            "speed": 120,
            "damage": 20,
            "spawnWeight": 3,
            "behavior": "charger",
        },
    ]
}
_VALID_WEAPONS: dict[str, Any] = {
    "weapons": [
        {
            "id": "cross",
            "name": "십자가",
            "fireRate": 1.5,
            "projectiles": 1,
            "damage": 15,
            "range": 200,
            "pierce": 0,
        },
        {
            "id": "aura",
            "name": "오라",
            "fireRate": 0.5,
            "projectiles": 8,
            "damage": 5,
            "range": 80,
            "pierce": 3,
        },
    ]
}
_VALID_WAVES: dict[str, Any] = {
    "waves": [
        {
            "tStart": 0,
            "tEnd": 60,
            "spawn": [{"enemyId": "zombie", "rate": 0.5}],
            "difficulty": 1.0,
        },
        {
            "tStart": 60,
            "tEnd": 120,
            "spawn": [{"enemyId": "boar", "rate": 0.1}],
            "difficulty": 2.0,
        },
    ]
}
_VALID_UPGRADES: dict[str, Any] = {
    "upgrades": [
        {"id": "dmg", "name": "공격력", "stat": "damage", "delta": 5, "cap": 50},
        {"id": "spd", "name": "이속", "stat": "speed", "delta": 10, "cap": 100},
        {"id": "rate", "name": "공속", "stat": "fireRate", "delta": 0.2, "cap": 5.0},
    ]
}

# game_data_read가 path별로 돌려줄 유효 데이터 맵(존재 케이스).
_READ_DATA: dict[str, dict[str, Any]] = {
    "data/meta.json": _VALID_META,
    "data/enemies.json": _VALID_ENEMIES,
    "data/weapons.json": _VALID_WEAPONS,
    "data/waves.json": _VALID_WAVES,
    "data/upgrades.json": _VALID_UPGRADES,
}


# ── Fake LLM ──────────────────────────────────────────────────────────────────


class AssembleLLM:
    """game_assemble 7노드용 결정론 fake LLM.

    노드순 발행:
      idx 0~4  n1~n5  — game_data_read tool-call(각 required=true·입력 계약)
      idx 5    n6     — 조립 노트 평문(coding·thinking=on)
      idx 6    n7     — assembly.json game_data_write tool-call(ready:true)
    """

    _READ_PATHS = [
        "data/meta.json",
        "data/enemies.json",
        "data/weapons.json",
        "data/waves.json",
        "data/upgrades.json",
    ]

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
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
        idx = self._idx
        self._idx += 1
        if idx < 5:
            content = json.dumps(
                {
                    "name": "game_data_read",
                    "arguments": {"path": self._READ_PATHS[idx], "required": True},
                },
                ensure_ascii=False,
            )
            reasoning = ""
        elif idx == 5:
            content = "런타임 바인딩 검토 완료 — 조립 준비됨."
            reasoning = "조립 추론..."
        else:
            content = json.dumps(
                {
                    "name": "game_data_write",
                    "arguments": {
                        "path": "data/assembly.json",
                        "content": {
                            "ready": True,
                            "runtime": "survivor-sim",
                            "entry": "src/main.ts",
                            "entityCap": 300,
                            "notes": "바인딩·참조 무결성·풀링 검토 완료",
                        },
                    },
                },
                ensure_ascii=False,
            )
            reasoning = ""
        return _llm_reply(content, reasoning)


class QaLLM:
    """game_qa 2노드용 결정론 fake LLM.

    노드순 발행:
      idx 0  n1  — game_validate tool-call(data 디렉터리 검증)
      idx 1  n2  — qa_report.json game_data_write tool-call(pass = 직전 검증 ok)

    n2의 pass 값은 직전 game_validate 결과에 따라 달라진다 — 생성자에 validate_ok를
    받아 결정론으로 pass/errors를 구성한다(베이스라인=true, 깨진 데이터=false).
    """

    def __init__(self, validate_ok: bool, validate_errors: list[str]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._idx = 0
        self._ok = validate_ok
        self._errors = validate_errors

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
        idx = self._idx
        self._idx += 1
        if idx == 0:
            content = json.dumps(
                {"name": "game_validate", "arguments": {"data_dir": "data"}},
                ensure_ascii=False,
            )
        else:
            content = json.dumps(
                {
                    "name": "game_data_write",
                    "arguments": {
                        "path": "data/qa_report.json",
                        "content": {"pass": self._ok, "errors": self._errors},
                    },
                },
                ensure_ascii=False,
            )
        return _llm_reply(content, "")


def _llm_reply(content: str, reasoning: str) -> dict[str, Any]:
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


# ── mock 도구(실 game/ 대신 tmp·주입 데이터) ─────────────────────────────────


def _mock_game_data_read(data_map: dict[str, dict[str, Any]] | None) -> Any:
    """주입용 mock game_data_read — data_map이 있으면 path별 내용을 돌려준다.

    data_map=None이면 required=True에서 FileNotFoundError(입력 부재 계약 — 명시 실패).
    """

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        rel = args.get("path", "")
        required = bool(args.get("required", False))
        if data_map is None or rel not in data_map:
            if required:
                raise FileNotFoundError(f"required game data file not found: {rel}")
            return {"exists": False, "content": None, "path": rel}
        return {
            "exists": True,
            "content": json.dumps(data_map[rel], ensure_ascii=False),
            "path": rel,
        }

    return _fn


def _mock_game_data_write(captured: dict[str, dict[str, Any]], out_dir: Path) -> Any:
    """주입용 mock game_data_write — 실 game/ 대신 tmp(out_dir)에 쓴다(오염 0)."""

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        rel = args.get("path", "")
        content = args.get("content", "")
        text = (
            json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
            if isinstance(content, (dict, list))
            else str(content)
        )
        captured[rel] = {"content": content}
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        captured[rel]["target"] = target
        return {
            "written": True,
            "path": rel,
            "bytes": len(text.encode("utf-8")),
            "deduplicated": False,
        }

    return _fn


def _mock_game_validate(ok: bool, errors: list[str]) -> Any:
    """주입용 mock game_validate — 결정론 결과(ok/errors)를 돌려준다(실 디스크 무접촉)."""

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "errors": list(errors),
            "report": {"valid": ok, "errorCount": len(errors)},
        }

    return _fn


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


# ── 1. game_assemble — 입력 존재 시 mock 풀런 성공 (AC4) ──────────────────────


def test_assemble_mock_full_run_success(tmp_path: Path) -> None:
    """입력 5파일 존재 시 game_assemble mock 풀런 → status success·assembly.json{ready:true}·tmp 격리."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = AssembleLLM()
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_data_read": _mock_game_data_read(_READ_DATA),
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
        )
        state = engine.run(
            nodes=list(ASSEMBLE_NODES), run_id="ga1", workflow=ASSEMBLE_NAME
        )

        # ① 7노드 전부 통과(5 read + 조립노트 + write).
        assert state.get("_workflow_status") == "success", state.get("_error")

        # ② assembly.json 기록 + ready:true 계약.
        assembly = captured["data/assembly.json"]["content"]
        assert assembly["ready"] is True
        assert assembly["entityCap"] == 300

        # ③ tmp 격리 — assembly.json이 tmp_path 하위에 실제 기록(실 game/ 무접촉).
        assert (tmp_path / "data/assembly.json").exists()

        # ④ 도구 흔적: game_data_read 5회 + game_data_write 1회.
        events = store.read("ga1")
        reads = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_data_read"
        ]
        writes = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_data_write"
        ]
        assert len(reads) == 5
        assert len(writes) == 1
        assert all(r.result["exists"] is True for r in reads)

        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)
    finally:
        os.unlink(db_path)


# ── 2. ★game_assemble — 입력 부재 시 명시 실패(단계 계약) ──────────────────────


def test_assemble_missing_input_fails(tmp_path: Path) -> None:
    """입력 부재 시 game_data_read required=True가 예외 → 워크플로 비성공(단계 계약).

    첫 노드(asm_read_meta)에서 멈추므로 assembly.json은 기록되지 않는다(진행 거부).
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = AssembleLLM()
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_data_read": _mock_game_data_read(
                    None
                ),  # ★입력 부재 — required raise
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
            error_fails_node=True,
        )
        state = engine.run(
            nodes=list(ASSEMBLE_NODES), run_id="ga2", workflow=ASSEMBLE_NAME
        )

        # ① 워크플로가 성공하지 않음 — 입력 계약 위반(이전 단계 미완)의 명시 실패.
        assert state.get("_workflow_status") != "success"
        assert state.get("_workflow_status") == "failed"

        # ② 진행 거부: assembly.json 미기록(첫 read 노드에서 멈춤).
        assert captured == {}
        assert not (tmp_path / "data/assembly.json").exists()

        # ③ read 도구는 호출됐고 결과는 error(예외→error dict 전파).
        events = store.read("ga2")
        reads = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_data_read"
        ]
        assert len(reads) == 1
        assert "error" in reads[0].result
    finally:
        os.unlink(db_path)


def test_assemble_missing_input_fails_in_production_engine_config(
    tmp_path: Path,
) -> None:
    """★프로덕션경로(Major-1): run_full/worker가 쓰는 것과 동일한 error_fails_node
    구성으로 입력 부재 풀런 → _workflow_status != 'success'(명시 실패) 단언.

    테스트 전용 플래그가 아니라 _is_game_workflow('game_assemble')로 프로덕션 구성을
    복제한다 — game_assemble ∈ GAME_WORKFLOWS이므로 run.py/worker.py는 error_fails_node=
    True로 엔진을 띄운다. 입력(이전 단계 산출 5파일) 미완이면 조립 단계가 프로덕션에서도
    가짜 데이터로 완주하지 않고 명시 실패로 진행 거부됨을 단언.
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        production_efn = _is_game_workflow(ASSEMBLE_NAME)
        assert production_efn is True, "game_assemble은 GAME_WORKFLOWS에 있어야 한다"

        llm = AssembleLLM()
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_data_read": _mock_game_data_read(None),  # ★입력 부재
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
            error_fails_node=production_efn,
        )
        state = engine.run(
            nodes=list(ASSEMBLE_NODES), run_id="ga_prod", workflow=ASSEMBLE_NAME
        )

        assert state.get("_workflow_status") != "success", (
            "프로덕션 error_fails_node 구성에서 입력 부재인데 success로 끝남 — 갭"
        )
        assert captured == {}
    finally:
        os.unlink(db_path)


# ── 3. game_qa — 베이스라인(유효) → pass:true (AC5) ───────────────────────────


def test_qa_baseline_passes(tmp_path: Path) -> None:
    """유효 데이터 → game_validate ok:true → qa_report.json pass:true 결정론."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = QaLLM(validate_ok=True, validate_errors=[])
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_validate": _mock_game_validate(True, []),
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
        )
        state = engine.run(nodes=list(QA_NODES), run_id="qa1", workflow=QA_NAME)

        assert state.get("_workflow_status") == "success", state.get("_error")

        # qa_report.json pass:true·errors:[].
        report = captured["data/qa_report.json"]["content"]
        assert report["pass"] is True
        assert report["errors"] == []

        # game_validate 결과가 ok:true(게이트 통과).
        events = store.read("qa1")
        validates = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_validate"
        ]
        assert len(validates) == 1
        assert validates[0].result["ok"] is True
    finally:
        os.unlink(db_path)


# ── 4. ★game_qa — 고의 깨진 데이터 → pass:false (AC5) ─────────────────────────


def test_qa_broken_data_fails(tmp_path: Path) -> None:
    """깨진 데이터 → game_validate ok:false → qa_report.json pass:false 결정론."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    broken_errors = ["enemies.json → enemies[0]: 'hp'는 0보다 커야 함 (받음: 0)"]
    try:
        llm = QaLLM(validate_ok=False, validate_errors=broken_errors)
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_validate": _mock_game_validate(False, broken_errors),
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
        )
        state = engine.run(nodes=list(QA_NODES), run_id="qa2", workflow=QA_NAME)

        assert state.get("_workflow_status") == "success", state.get("_error")

        # ★qa_report.json pass:false + errors 보존(결정론 — 베이스라인과 정반대).
        report = captured["data/qa_report.json"]["content"]
        assert report["pass"] is False
        assert report["errors"] == broken_errors

        # game_validate 결과가 ok:false(게이트 차단).
        events = store.read("qa2")
        validates = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_validate"
        ]
        assert len(validates) == 1
        assert validates[0].result["ok"] is False
    finally:
        os.unlink(db_path)


def test_qa_broken_data_records_report_under_production_engine_config(
    tmp_path: Path,
) -> None:
    """★Major-1 확인: game_qa는 error_fails_node=True(프로덕션 game 구성)여도 깨진
    데이터에서 노드 실패가 아니라 pass:false 리포트를 정상 기록한다.

    이유: game_validate는 {"ok":false,"errors":[...]}를 돌려주며 "error" 키가 없다 —
    error_fails_node는 결과 dict에 "error" 키가 있을 때만 노드를 실패시키므로 game_qa는
    영향받지 않는다(의도된 동작 — 깨진 데이터를 success 워크플로의 pass:false 리포트로
    기록해 게이트가 작동). game_qa ∈ GAME_WORKFLOWS라 프로덕션은 error_fails_node=True.
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    broken_errors = ["enemies.json → enemies[0]: 'hp'는 0보다 커야 함 (받음: 0)"]
    try:
        production_efn = _is_game_workflow(QA_NAME)
        assert production_efn is True, "game_qa는 GAME_WORKFLOWS에 있어야 한다"

        llm = QaLLM(validate_ok=False, validate_errors=broken_errors)
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_validate": _mock_game_validate(False, broken_errors),
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
            error_fails_node=production_efn,  # ★프로덕션 game 구성(True)
        )
        state = engine.run(nodes=list(QA_NODES), run_id="qa_prod", workflow=QA_NAME)

        # 워크플로는 success(노드 실패 아님) — game_validate ok:false는 "error" 키가
        # 없어 error_fails_node에 안 걸린다. 깨진 데이터는 pass:false 리포트로 기록.
        assert state.get("_workflow_status") == "success", state.get("_error")
        report = captured["data/qa_report.json"]["content"]
        assert report["pass"] is False
        assert report["errors"] == broken_errors
    finally:
        os.unlink(db_path)


# ── 5. 실 game_validate — 베이스라인=pass·깨진 데이터=fail (AC5·AC6) ───────────


def test_real_game_validate_baseline_and_broken(tmp_path: Path) -> None:
    """실 game_validate: 실 베이스라인 game/data=ok:true, tmp 깨진 데이터=ok:false.

    AC5 결정론 게이트의 도구 단위 단언. 베이스라인은 실 game/data(읽기전용·무오염),
    깨진 데이터는 tmp에 hp=0 적을 깔아 검증한다(실 game/ 무접촉).
    """
    from office.tools import game_validate

    # 실 베이스라인 game/data — 읽기전용 검증(오염 0).
    base = game_validate({})
    assert base["ok"] is True, base["errors"]
    assert base["report"]["valid"] is True

    # game/ 밖 경로 거부(읽기전용·디스크 미접촉).
    outside = game_validate({"data_dir": "../office"})
    assert outside["ok"] is False
    assert any("outside game/" in e for e in outside["errors"])


# ── 6. 이벤트 11종만 + NODES 구조 + 로더 (AC4) ───────────────────────────────


def test_assemble_emits_only_registered_events(tmp_path: Path) -> None:
    """game_assemble 발행 이벤트 type은 EVENT_REGISTRY 11종 내에서만 — 새 타입 0."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = AssembleLLM()
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_data_read": _mock_game_data_read(_READ_DATA),
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
        )
        engine.run(nodes=list(ASSEMBLE_NODES), run_id="ga3", workflow=ASSEMBLE_NAME)

        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types
        assert types <= set(EVENT_REGISTRY), (
            f"미등록 이벤트 타입: {types - set(EVENT_REGISTRY)}"
        )
        assert len(EVENT_REGISTRY) == 11
    finally:
        os.unlink(db_path)


def test_qa_emits_only_registered_events(tmp_path: Path) -> None:
    """game_qa 발행 이벤트 type은 EVENT_REGISTRY 11종 내에서만 — 새 타입 0."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = QaLLM(validate_ok=True, validate_errors=[])
        store = EventStore(db_path)
        executor = NodeExecutor(llm_client=llm, event_store=store)
        engine = WorkflowEngine(
            executor=executor,
            event_store=store,
            tools={
                "game_validate": _mock_game_validate(True, []),
                "game_data_write": _mock_game_data_write(captured, tmp_path),
            },
        )
        engine.run(nodes=list(QA_NODES), run_id="qa3", workflow=QA_NAME)

        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types
        assert types <= set(EVENT_REGISTRY)
        assert len(EVENT_REGISTRY) == 11
    finally:
        os.unlink(db_path)


def test_assemble_nodes_structure_no_agent() -> None:
    """game_assemble NODES: kind∈{coding,tool}·★agent 0·next 체인·마지막 None."""
    assert ASSEMBLE_NAME == "game_assemble"
    assert len(ASSEMBLE_NODES) == 7
    kinds = [n.kind for n in ASSEMBLE_NODES]
    assert all(k in {"chat", "longform", "coding", "tool"} for k in kinds)
    assert "agent" not in kinds  # ★선형 — 자율 agent 노드 0
    ids = [n.id for n in ASSEMBLE_NODES]
    for i, n in enumerate(ASSEMBLE_NODES[:-1]):
        assert n.next == ids[i + 1]
    assert ASSEMBLE_NODES[-1].next is None


def test_qa_nodes_structure_no_agent() -> None:
    """game_qa NODES: kind∈{tool}·★agent 0·next 체인·마지막 None."""
    assert QA_NAME == "game_qa"
    assert len(QA_NODES) == 2
    kinds = [n.kind for n in QA_NODES]
    assert all(k == "tool" for k in kinds)
    assert "agent" not in kinds
    assert QA_NODES[0].next == "n2_write_report"
    assert QA_NODES[-1].next is None


def test_loader_returns_game_assemble() -> None:
    """_load_workflow('game_assemble') = (7노드, 'game_assemble') + ★안정적 node id(US-G5-7)."""
    nodes, name = _load_workflow("game_assemble")
    assert name == "game_assemble"
    assert len(nodes) == 7
    expected_ids = [
        "asm_read_meta",
        "asm_read_enemies",
        "asm_read_weapons",
        "asm_read_waves",
        "asm_read_upgrades",
        "asm_assemble",
        "asm_write_assembly",
    ]
    assert [n.id for n in nodes] == expected_ids
    # n1~n5 읽기(tool · game_data_read · required 입력 계약).
    for n in nodes[:5]:
        assert n.kind == "tool"
        assert n.tools == [GAME_DATA_READ_SCHEMA]
    # n6 조립(coding).
    assert nodes[5].kind == "coding"
    assert nodes[5].thinking == "on"
    # n7 기록(tool · game_data_write).
    assert nodes[6].kind == "tool"
    assert nodes[6].tools == [GAME_DATA_WRITE_SCHEMA]
    assert nodes[6].next is None


def test_loader_returns_game_qa() -> None:
    """_load_workflow('game_qa') = (2노드, 'game_qa') + ★안정적 node id(US-G5-7)."""
    nodes, name = _load_workflow("game_qa")
    assert name == "game_qa"
    assert len(nodes) == 2
    assert [n.id for n in nodes] == ["n1_validate", "n2_write_report"]
    # n1 검증(tool · game_validate).
    assert nodes[0].kind == "tool"
    assert nodes[0].tools == [GAME_VALIDATE_SCHEMA]
    # n2 기록(tool · game_data_write).
    assert nodes[1].kind == "tool"
    assert nodes[1].tools == [GAME_DATA_WRITE_SCHEMA]
    assert nodes[1].next is None
