"""G005 US-G5-4 — game_balance 워크플로(data·선형) → enemies/weapons/waves/upgrades
+ balance.json (AC1·AC2·AC3·AC4).

검증(전부 mock·바운드·실 game/data 오염 0):
  ① meta **존재** 시 mock 풀런: RecordingLLM + mock game_data_read(유효 meta 반환) +
     mock game_data_write(tmp 기록) + _make_engine → status success, enemies/weapons/
     waves/upgrades.json 각 validate_* ok:true, balance.json validate_balance ok:true,
     참조 무결성(waves.enemyId∈enemies.id) 단언. tmp 격리(실 game/data 무접촉). (AC1·AC3·AC4)
  ② ★meta **부재** 시: game_data_read required=True가 없는 meta를 만나 워크플로가
     **성공하지 않음**(NODE_FAILED/RUN_ERROR/도구에러)을 단언 — 단계 계약 검증(AC2).
     엔진 tool 예외 전파(engine.py:1279 예외→error dict·1294 error_fails_node→NODE_FAILED)에
     맞춰 error_fails_node=True 엔진으로 단언. 실 game_data_read의 required-raise도 직접 단언.
  ③ 11종 이벤트만 + NODES 구조(kind∈chat/longform/tool·agent 0·next 체인) +
     _load_workflow('game_balance') 로더 단언. (AC1·AC4)

설계 모델: tests/test_game_design.py(엔진 배선·tmp 격리) + tests/test_research_summary.py.
RecordingLLM은 노드순으로 game_data_read tool-call → 설계 평문 → 5× game_data_write
tool-call(각 유효 데이터)을 발행한다. mock 도구는 실 game/ 대신 tmp_path에 읽고/쓴다.

실행: scripts/run_tests.sh engine tests/test_game_balance.py -m "not slow"
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
from office.game_schema import (
    validate_balance,
    validate_enemies,
    validate_upgrades,
    validate_waves,
    validate_weapons,
)
from office.run import _is_game_workflow, _load_workflow
from office.tools import (
    GAME_DATA_READ_SCHEMA,
    GAME_DATA_WRITE_SCHEMA,
    game_data_read,
)
from workflows.game_balance import NODES, WORKFLOW_NAME

# 입력 계약용 유효 meta(validate_meta ok:true). game_data_read mock이 돌려준다.
_VALID_META: dict[str, Any] = {
    "theme": "폐허 도시 생존",
    "title": "네온 서바이버",
    "coreLoop": "이동·자동공격·레벨업·업그레이드 반복",
    "win": "10분 생존",
    "lose": "플레이어 HP 0",
    "continue": {"freeRetries": 1, "costCurve": [100, 200, 400], "sessionCap": 3},
}

# 각 write 노드가 발행할 유효 데이터(validate_* ok:true·참조 무결성 충족). enemy id는
# walker/bat/brute로 고정하고 waves가 그 id만 참조한다(waves.enemyId∈enemies.id).
_ENEMIES: dict[str, Any] = {
    "enemies": [
        {
            "id": "walker",
            "name": "워커",
            "hp": 30,
            "speed": 40,
            "damage": 8,
            "spawnWeight": 10,
            "behavior": "chase",
        },
        {
            "id": "bat",
            "name": "박쥐 떼",
            "hp": 10,
            "speed": 90,
            "damage": 4,
            "spawnWeight": 8,
            "behavior": "swarm",
        },
        {
            "id": "brute",
            "name": "브루트",
            "hp": 80,
            "speed": 50,
            "damage": 20,
            "spawnWeight": 3,
            "behavior": "charger",
        },
    ]
}
_WEAPONS: dict[str, Any] = {
    "weapons": [
        {
            "id": "auto_pistol",
            "name": "자동권총",
            "fireRate": 2.0,
            "projectiles": 1,
            "damage": 12,
            "range": 180,
            "pierce": 0,
        },
        {
            "id": "garlic_aura",
            "name": "마늘 오라",
            "fireRate": 0.5,
            "projectiles": 8,
            "damage": 5,
            "range": 80,
            "pierce": 3,
        },
    ]
}
_WAVES: dict[str, Any] = {
    "waves": [
        {
            "tStart": 0,
            "tEnd": 60,
            "spawn": [
                {"enemyId": "walker", "rate": 0.5},
                {"enemyId": "bat", "rate": 0.3},
            ],
            "difficulty": 1.0,
        },
        {
            "tStart": 60,
            "tEnd": 120,
            "spawn": [
                {"enemyId": "walker", "rate": 1.0},
                {"enemyId": "brute", "rate": 0.1},
            ],
            "difficulty": 2.0,
        },
    ]
}
_UPGRADES: dict[str, Any] = {
    "upgrades": [
        {
            "id": "dmg_boost",
            "name": "공격력 증가",
            "stat": "damage",
            "delta": 5,
            "cap": 50,
        },
        {
            "id": "move_speed",
            "name": "이동 속도 증가",
            "stat": "speed",
            "delta": 10,
            "cap": 100,
        },
        {
            "id": "fire_rate",
            "name": "공격 속도 증가",
            "stat": "fireRate",
            "delta": 0.2,
            "cap": 5.0,
        },
    ]
}
_BALANCE: dict[str, Any] = {
    "survivalTargetSec": 600,
    "dpsCurve": [12, 18, 30, 48],
    "inRange": True,
    "report": {
        "enemyHpScaling": True,
        "waveProgression": True,
        "weaponBalance": True,
        "upgradeEffectiveness": True,
    },
}

# 노드 id → (path, content) 매핑 — write 노드가 순서대로 발행한다.
_WRITE_PLAN: list[tuple[str, dict[str, Any]]] = [
    ("data/enemies.json", _ENEMIES),
    ("data/weapons.json", _WEAPONS),
    ("data/waves.json", _WAVES),
    ("data/upgrades.json", _UPGRADES),
    ("data/balance.json", _BALANCE),
]


# ── Fake LLM (노드순: read tool-call → 설계 평문 → 5× write tool-call) ─────────


class RecordingLLM:
    """game_balance 7노드용 결정론 fake LLM. messages를 calls에 기록한다.

    노드순 발행:
      idx 0  n1_read_meta      — game_data_read tool-call(required=true·입력 계약)
      idx 1  n2_design_numbers — 설계 평문(longform·thinking=on)
      idx 2~6 n3~n7            — 각 game_data_write tool-call(유효 데이터)
    """

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
        if idx == 0:
            content = json.dumps(
                {
                    "name": "game_data_read",
                    "arguments": {"path": "data/meta.json", "required": True},
                },
                ensure_ascii=False,
            )
            reasoning = ""
        elif idx == 1:
            content = "적/무기/웨이브/업그레이드 수치를 설계했다."
            reasoning = "밸런스 단계적 추론..."
        else:
            path, payload = _WRITE_PLAN[idx - 2]
            content = json.dumps(
                {
                    "name": "game_data_write",
                    "arguments": {"path": path, "content": payload},
                },
                ensure_ascii=False,
            )
            reasoning = ""
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


def _mock_game_data_read(meta: dict[str, Any] | None, out_dir: Path) -> Any:
    """주입용 mock game_data_read — 실 game/ 대신 tmp(out_dir)를 본다.

    meta가 주어지면 data/meta.json을 out_dir에 미리 깔아 둔 셈치고 그 내용을 돌려준다.
    meta=None이면 실 game_data_read와 동일하게 required=True에서 FileNotFoundError를
    raise한다(AC2 입력 계약 — 이전 단계 미완 시 명시 실패).
    """

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        rel = args.get("path", "")
        required = bool(args.get("required", False))
        if meta is None:
            if required:
                raise FileNotFoundError(
                    f"required game data file not found: {rel} (이전 단계 미완)"
                )
            return {"exists": False, "content": None, "path": rel}
        return {
            "exists": True,
            "content": json.dumps(meta, ensure_ascii=False),
            "path": rel,
        }

    return _fn


def _mock_game_data_write(captured: dict[str, dict[str, Any]], out_dir: Path) -> Any:
    """주입용 mock game_data_write — ★실 game/ 대신 tmp(out_dir)에 쓴다(오염 0).

    받은 args를 captured[path]에 기록(content를 validate_*로 단언)하고, tmp 하위에
    파일을 써서 '기록됨'을 디스크로도 확인 가능하게 한다. 실 game_data_write의 반환
    dict 계약({written,path,bytes,deduplicated})을 모사한다.
    """

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


def _make_engine(
    llm: RecordingLLM,
    db_path: str,
    *,
    meta: dict[str, Any] | None,
    captured: dict[str, dict[str, Any]],
    out_dir: Path,
    error_fails_node: bool = False,
) -> tuple[WorkflowEngine, EventStore]:
    """주입 mock game_data_read/write를 가진 엔진을 만든다(gating=기본 — 두 도구 모두
    always-allow라 interrupt 없음). REAL_TOOLS/MOCK_TOOLS·실 game/ 무접촉.

    error_fails_node=True면 도구 결과 dict에 error가 있을 때 NODE_FAILED로 노드를
    실패시킨다(AC2 입력 계약 단언용 — game_data_read 예외→error dict→NODE_FAILED).
    """
    store = EventStore(db_path)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={
            "game_data_read": _mock_game_data_read(meta, out_dir),
            "game_data_write": _mock_game_data_write(captured, out_dir),
        },
        error_fails_node=error_fails_node,
    )
    return engine, store


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


# ── 1. meta 존재 → mock 풀런 성공·전 파일 valid·참조 무결성 (AC1·AC3·AC4) ──────


def test_mock_full_run_writes_valid_data(tmp_path: Path) -> None:
    """meta 존재 시 mock 풀런 → status success·5파일 valid·참조 무결성·tmp 격리."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm, db_path, meta=_VALID_META, captured=captured, out_dir=tmp_path
        )
        state = engine.run(nodes=list(NODES), run_id="gb1", workflow=WORKFLOW_NAME)

        # ① status success(7노드 전부 통과 — read+설계+5 write).
        assert state.get("_workflow_status") == "success", state.get("_error")

        # ② 5파일 전부 기록됨 + 각 validate_* ok:true.
        enemies = captured["data/enemies.json"]["content"]
        weapons = captured["data/weapons.json"]["content"]
        waves = captured["data/waves.json"]["content"]
        upgrades = captured["data/upgrades.json"]["content"]
        balance = captured["data/balance.json"]["content"]

        assert validate_enemies(enemies)["ok"], validate_enemies(enemies)["errors"]
        assert validate_weapons(weapons)["ok"], validate_weapons(weapons)["errors"]
        assert validate_waves(waves)["ok"], validate_waves(waves)["errors"]
        assert validate_upgrades(upgrades)["ok"], validate_upgrades(upgrades)["errors"]
        assert validate_balance(balance)["ok"], validate_balance(balance)["errors"]

        # ③ 참조 무결성: waves.spawn.enemyId ∈ enemies.id (단계간 계약 핵심).
        enemy_ids = {e["id"] for e in enemies["enemies"]}
        for w in waves["waves"]:
            for s in w["spawn"]:
                assert s["enemyId"] in enemy_ids, (
                    f"참조 무결성 위반: {s['enemyId']} ∉ {enemy_ids}"
                )

        # ④ balance.json 수치 리포트 계약(inRange·dpsCurve 비감소).
        assert balance["inRange"] is True
        dps = balance["dpsCurve"]
        assert all(dps[i] >= dps[i - 1] for i in range(1, len(dps)))

        # ⑤ tmp 격리 — 5파일이 tmp_path 하위에 실제로 기록됨(실 game/ 무접촉).
        for rel in (
            "data/enemies.json",
            "data/weapons.json",
            "data/waves.json",
            "data/upgrades.json",
            "data/balance.json",
        ):
            assert (tmp_path / rel).exists()

        # ⑥ 도구 실행 흔적: game_data_read 1회 + game_data_write 5회(ToolCallResult).
        events = store.read("gb1")
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
        assert len(reads) == 1
        assert len(writes) == 5
        assert reads[0].result["exists"] is True

        # RUN_FINISHED(success).
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)
    finally:
        os.unlink(db_path)


# ── 2. ★meta 부재 → 입력 계약 명시 실패(AC2) ─────────────────────────────────


def test_missing_meta_fails_workflow(tmp_path: Path) -> None:
    """meta 부재 시 game_data_read required=True가 예외 → 워크플로 비성공(단계 계약).

    엔진 tool 예외 전파(engine.py:1279 예외→error dict·1294 error_fails_node→NODE_FAILED)에
    맞춰 error_fails_node=True 엔진으로 NODE_FAILED→status='failed'를 단언한다. 첫 노드
    (n1_read_meta)에서 멈추므로 write 노드는 한 번도 실행되지 않는다(진행 거부).
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            meta=None,  # ★meta 부재 — required read가 raise
            captured=captured,
            out_dir=tmp_path,
            error_fails_node=True,
        )
        state = engine.run(nodes=list(NODES), run_id="gb2", workflow=WORKFLOW_NAME)

        # ① 워크플로가 성공하지 않음 — 입력 계약 위반(이전 단계 미완)의 명시 실패.
        assert state.get("_workflow_status") != "success"
        # error_fails_node=True 경로에선 NODE_FAILED → 'failed'.
        assert state.get("_workflow_status") == "failed"

        # ② 진행 거부: 어떤 write 파일도 기록되지 않음(첫 read 노드에서 멈춤).
        assert captured == {}
        for rel in (
            "data/enemies.json",
            "data/weapons.json",
            "data/waves.json",
            "data/upgrades.json",
            "data/balance.json",
        ):
            assert not (tmp_path / rel).exists()

        # ③ read 도구는 호출됐고 결과는 error(예외→error dict 전파).
        events = store.read("gb2")
        reads = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_data_read"
        ]
        assert len(reads) == 1
        assert "error" in reads[0].result
    finally:
        os.unlink(db_path)


def test_missing_meta_fails_in_production_engine_config(tmp_path: Path) -> None:
    """★프로덕션경로(Major-1): run_full/worker가 쓰는 것과 동일한 error_fails_node
    구성으로 meta 부재 풀런 → _workflow_status != 'success'(명시 실패) 단언.

    테스트 전용 플래그가 아니라 _is_game_workflow('game_balance')로 프로덕션 구성을
    복제한다 — game_balance ∈ GAME_WORKFLOWS이므로 run.py/worker.py는 error_fails_node=
    True로 엔진을 띄운다. 누군가 game_balance를 GAME_WORKFLOWS에서 빼면(또는 게임
    워크플로에 error_fails_node 주입을 제거하면) 이 단언이 깨져 AC2 프로덕션 갭이
    회귀로 잡힌다.
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        # ★프로덕션 구성 복제 — 테스트 전용 True가 아니라 워크플로명으로 도출.
        production_efn = _is_game_workflow(WORKFLOW_NAME)
        assert production_efn is True, "game_balance는 GAME_WORKFLOWS에 있어야 한다"

        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            meta=None,  # ★meta 부재 — required read가 raise(이전 단계 미완)
            captured=captured,
            out_dir=tmp_path,
            error_fails_node=production_efn,
        )
        state = engine.run(nodes=list(NODES), run_id="gb_prod", workflow=WORKFLOW_NAME)

        # 프로덕션 구성에서도 워크플로가 성공하지 않음(가짜 데이터로 완주 차단).
        assert state.get("_workflow_status") != "success", (
            "프로덕션 error_fails_node 구성에서 meta 부재인데 success로 끝남 — AC2 갭"
        )
        # 진행 거부: write 파일 0(첫 read 노드에서 멈춤).
        assert captured == {}
    finally:
        os.unlink(db_path)


def test_real_game_data_read_required_raises_when_missing(tmp_path: Path) -> None:
    """실 game_data_read: required=True인데 없는 파일이면 FileNotFoundError raise.

    AC2 입력 계약의 도구 단위 단언 — 실 도구가 game/ 하위 존재하지 않는 파일을
    required=True로 읽으면 예외를 던진다(엔진이 NODE_FAILED/RUN_ERROR로 드러내게).
    실 game/ 디렉터리를 오염시키지 않도록 결코 존재하지 않을 임시 파일명을 쓴다.
    """
    import pytest

    missing = "data/__nonexistent_for_test__.json"
    with pytest.raises(FileNotFoundError):
        game_data_read({"path": missing, "required": True})

    # required=False/생략이면 예외 없이 exists=False.
    res = game_data_read({"path": missing})
    assert res["exists"] is False
    assert res["content"] is None


def test_real_game_data_read_rejects_outside_path() -> None:
    """실 game_data_read: game/ 밖 경로(`..`·절대경로)는 ValueError로 거부(디스크 미접촉)."""
    import pytest

    with pytest.raises(ValueError):
        game_data_read({"path": "../office/tools.py"})
    with pytest.raises(ValueError):
        game_data_read({"path": "/etc/passwd"})


# ── 3. 이벤트 11종만 + NODES 구조 + 로더 (AC1·AC4) ───────────────────────────


def test_full_run_emits_only_registered_event_types(tmp_path: Path) -> None:
    """워크플로가 발행하는 이벤트 type은 EVENT_REGISTRY 11종 내에서만 — 새 타입 0."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm, db_path, meta=_VALID_META, captured=captured, out_dir=tmp_path
        )
        engine.run(nodes=list(NODES), run_id="gb3", workflow=WORKFLOW_NAME)

        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types  # 비어있지 않음
        assert types <= set(EVENT_REGISTRY), (
            f"미등록 이벤트 타입: {types - set(EVENT_REGISTRY)}"
        )
        assert len(EVENT_REGISTRY) == 11  # 계약 동결(새 타입 0)
    finally:
        os.unlink(db_path)


def test_nodes_structure_no_agent_and_chain() -> None:
    """NODES 구조: kind∈{chat,longform,tool}·★agent 0·next 체인·마지막 None."""
    assert WORKFLOW_NAME == "game_balance"
    assert len(NODES) == 7
    kinds = [n.kind for n in NODES]
    assert all(k in {"chat", "longform", "tool"} for k in kinds)
    assert "agent" not in kinds  # ★선형 — 자율 agent 노드 0
    # next 체인: 마지막을 제외하고 모두 다음 노드 id를 가리키고, 마지막은 None.
    ids = [n.id for n in NODES]
    for i, n in enumerate(NODES[:-1]):
        assert n.next == ids[i + 1]
    assert NODES[-1].next is None


def test_loader_returns_seven_node_game_balance() -> None:
    """_load_workflow('game_balance') = (7노드, 'game_balance') + 노드 계약(★agent 0).

    ★node id 목록(US-G5-7 역할 매핑용·안정적): n1_read_meta·n2_design_numbers·
    n3_write_enemies·n4_write_weapons·n5_write_waves·n6_write_upgrades·n7_write_balance.
    """
    nodes, name = _load_workflow("game_balance")
    assert name == "game_balance"
    assert len(nodes) == 7

    expected_ids = [
        "n1_read_meta",
        "n2_design_numbers",
        "n3_write_enemies",
        "n4_write_weapons",
        "n5_write_waves",
        "n6_write_upgrades",
        "n7_write_balance",
    ]
    assert [n.id for n in nodes] == expected_ids

    n1 = nodes[0]
    # n1 읽기(tool · game_data_read · required 입력 계약 · next=n2)
    assert n1.kind == "tool"
    assert n1.thinking == "off"
    assert n1.tools == [GAME_DATA_READ_SCHEMA]
    assert n1.next == "n2_design_numbers"

    # n2 설계(longform · thinking=on)
    n2 = nodes[1]
    assert n2.kind == "longform"
    assert n2.thinking == "on"

    # n3~n7 기록(tool · game_data_write)
    for n in nodes[2:]:
        assert n.kind == "tool"
        assert n.tools == [GAME_DATA_WRITE_SCHEMA]
    assert nodes[-1].next is None
