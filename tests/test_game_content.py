"""G005 US-G5-5 — game_content 워크플로(콘텐츠·선형) → 플레이버 텍스트 병합
(AC1·AC2·AC3·AC4).

검증(전부 mock·바운드·실 game/data 오염 0):
  ① enemies/weapons **존재** 시 mock 풀런: RecordingLLM + mock game_data_read(유효
     enemies/weapons 반환) + mock game_data_write(tmp 기록) → status success,
     병합된 enemies/weapons가 validate_enemies/validate_weapons ok:true(flavor 추가·
     기존 필수필드 보존) 단언. tmp 격리(실 game/data 무접촉). (AC1·AC2·AC3)
  ② ★입력 부재 시: enemies(또는 weapons) 없으면 game_data_read required=True가
     워크플로 **비성공**(명시 실패) 단언. (AC2)
  ③ 멱등: 동일 입력 2회 병합 → 중복 텍스트 0(flavor 1개)·결정론 단언. (AC3)
  ④ 11종 이벤트만·NODES 구조(kind∈longform/tool·agent 0)·_load_workflow("game_content")
     로더 단언. (AC1·AC4)

설계 모델: tests/test_game_balance.py(엔진 배선·tmp 격리).
RecordingLLM은 노드순으로
  n1: game_data_read tool-call(enemies, required=True)
  n2: game_data_read tool-call(weapons, required=True)
  n3: 플레이버 평문(longform·thinking=on)
  n4: game_data_write tool-call(enemies + flavor 병합)
  n5: game_data_write tool-call(weapons + flavor 병합)
을 발행한다.

실행: scripts/run_tests.sh engine tests/test_game_content.py -m "not slow"
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
from office.game_schema import validate_enemies, validate_weapons
from office.run import _is_game_workflow, _load_workflow
from office.tools import (
    GAME_DATA_READ_SCHEMA,
    GAME_DATA_WRITE_SCHEMA,
    game_data_write,
)
from workflows.game_content import NODES, WORKFLOW_NAME

# ── 테스트용 고정 입력 데이터 ──────────────────────────────────────────────────

# 유효 enemies(validate_enemies ok:true) — 실 game/data/enemies.json 내용과 동일 구조.
_ENEMIES: dict[str, Any] = {
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
            "id": "bat_swarm",
            "name": "박쥐 떼",
            "hp": 10,
            "speed": 90,
            "damage": 4,
            "spawnWeight": 8,
            "behavior": "swarm",
        },
        {
            "id": "skeleton_archer",
            "name": "해골 궁수",
            "hp": 20,
            "speed": 30,
            "damage": 12,
            "spawnWeight": 5,
            "behavior": "ranged",
        },
        {
            "id": "grave_boar",
            "name": "묘지 멧돼지",
            "hp": 60,
            "speed": 120,
            "damage": 20,
            "spawnWeight": 3,
            "behavior": "charger",
        },
    ]
}

# 유효 weapons(validate_weapons ok:true).
_WEAPONS: dict[str, Any] = {
    "weapons": [
        {
            "id": "holy_cross",
            "name": "성십자가",
            "fireRate": 1.5,
            "projectiles": 1,
            "damage": 15,
            "range": 200,
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
        {
            "id": "silver_knife",
            "name": "은제 단검",
            "fireRate": 3.0,
            "projectiles": 2,
            "damage": 8,
            "range": 150,
            "pierce": 1,
        },
    ]
}

# flavor가 병합된 enemies(n4 write 노드가 기록할 내용).
_ENEMIES_WITH_FLAVOR: dict[str, Any] = {
    "enemies": [
        {
            "id": "zombie",
            "name": "좀비",
            "hp": 30,
            "speed": 40,
            "damage": 8,
            "spawnWeight": 10,
            "behavior": "chase",
            "flavor": "도시의 어둠 속에서 무리지어 다가오는 언데드",
        },
        {
            "id": "bat_swarm",
            "name": "박쥐 떼",
            "hp": 10,
            "speed": 90,
            "damage": 4,
            "spawnWeight": 8,
            "behavior": "swarm",
            "flavor": "날개짓으로 공기를 가르며 덮쳐드는 박쥐 떼",
        },
        {
            "id": "skeleton_archer",
            "name": "해골 궁수",
            "hp": 20,
            "speed": 30,
            "damage": 12,
            "spawnWeight": 5,
            "behavior": "ranged",
            "flavor": "뼈만 남은 손가락으로 화살을 쏘는 해골 궁수",
        },
        {
            "id": "grave_boar",
            "name": "묘지 멧돼지",
            "hp": 60,
            "speed": 120,
            "damage": 20,
            "spawnWeight": 3,
            "behavior": "charger",
            "flavor": "묘지에서 깨어난 분노한 멧돼지",
        },
    ]
}

# flavor가 병합된 weapons(n5 write 노드가 기록할 내용).
_WEAPONS_WITH_FLAVOR: dict[str, Any] = {
    "weapons": [
        {
            "id": "holy_cross",
            "name": "성십자가",
            "fireRate": 1.5,
            "projectiles": 1,
            "damage": 15,
            "range": 200,
            "pierce": 0,
            "flavor": "빛의 십자가로 언데드를 정화한다",
        },
        {
            "id": "garlic_aura",
            "name": "마늘 오라",
            "fireRate": 0.5,
            "projectiles": 8,
            "damage": 5,
            "range": 80,
            "pierce": 3,
            "flavor": "마늘 향기로 주변 적을 밀쳐낸다",
        },
        {
            "id": "silver_knife",
            "name": "은제 단검",
            "fireRate": 3.0,
            "projectiles": 2,
            "damage": 8,
            "range": 150,
            "pierce": 1,
            "flavor": "은빛 날이 몬스터의 심장을 꿰뚫는다",
        },
    ]
}

# 노드 id → (path, content) 매핑 — write 노드(n4·n5)가 순서대로 발행한다.
_WRITE_PLAN: list[tuple[str, dict[str, Any]]] = [
    ("data/enemies.json", _ENEMIES_WITH_FLAVOR),
    ("data/weapons.json", _WEAPONS_WITH_FLAVOR),
]


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class RecordingLLM:
    """game_content 5노드용 결정론 fake LLM. messages를 calls에 기록한다.

    노드순 발행:
      idx 0  n1_read_enemies   — game_data_read tool-call(enemies, required=True)
      idx 1  n2_read_weapons   — game_data_read tool-call(weapons, required=True)
      idx 2  n3_write_flavor   — 플레이버 평문(longform·thinking=on)
      idx 3  n4_write_enemies  — game_data_write tool-call(enemies + flavor)
      idx 4  n5_write_weapons  — game_data_write tool-call(weapons + flavor)
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
            # n1: enemies 읽기
            content = json.dumps(
                {
                    "name": "game_data_read",
                    "arguments": {"path": "data/enemies.json", "required": True},
                },
                ensure_ascii=False,
            )
            reasoning = ""
        elif idx == 1:
            # n2: weapons 읽기
            content = json.dumps(
                {
                    "name": "game_data_read",
                    "arguments": {"path": "data/weapons.json", "required": True},
                },
                ensure_ascii=False,
            )
            reasoning = ""
        elif idx == 2:
            # n3: 플레이버 설계 평문(longform)
            content = (
                "적 플레이버: zombie=도시의 어둠 속에서 무리지어 다가오는 언데드, "
                "bat_swarm=날개짓으로 공기를 가르며 덮쳐드는 박쥐 떼, "
                "skeleton_archer=뼈만 남은 손가락으로 화살을 쏘는 해골 궁수, "
                "grave_boar=묘지에서 깨어난 분노한 멧돼지. "
                "무기 플레이버: holy_cross=빛의 십자가로 언데드를 정화한다, "
                "garlic_aura=마늘 향기로 주변 적을 밀쳐낸다, "
                "silver_knife=은빛 날이 몬스터의 심장을 꿰뚫는다."
            )
            reasoning = "플레이버 텍스트 설계 추론..."
        else:
            # n4·n5: write tool-call(flavor 병합 데이터)
            path, payload = _WRITE_PLAN[idx - 3]
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


# ── mock 도구 헬퍼 ─────────────────────────────────────────────────────────────


def _mock_game_data_read(
    enemies: dict[str, Any] | None,
    weapons: dict[str, Any] | None,
    out_dir: Path,
) -> Any:
    """주입용 mock game_data_read — 실 game/ 대신 in-memory 데이터를 돌려준다.

    enemies/weapons가 주어지면 해당 파일 경로 요청 시 그 내용을 반환한다.
    None이면 required=True에서 FileNotFoundError를 raise한다(AC2 입력 계약).
    """

    def _fn(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        rel = args.get("path", "")
        required = bool(args.get("required", False))

        if rel == "data/enemies.json":
            data = enemies
        elif rel == "data/weapons.json":
            data = weapons
        else:
            data = None

        if data is None:
            if required:
                raise FileNotFoundError(
                    f"required game data file not found: {rel} (이전 단계 미완)"
                )
            return {"exists": False, "content": None, "path": rel}

        return {
            "exists": True,
            "content": json.dumps(data, ensure_ascii=False),
            "path": rel,
        }

    return _fn


def _mock_game_data_write(captured: dict[str, dict[str, Any]], out_dir: Path) -> Any:
    """주입용 mock game_data_write — 실 game/ 대신 tmp(out_dir)에 쓴다(오염 0).

    game_balance 패턴과 동일: captured[path]에 content를 기록하고 tmp에도 파일 생성.
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
    enemies: dict[str, Any] | None,
    weapons: dict[str, Any] | None,
    captured: dict[str, dict[str, Any]],
    out_dir: Path,
    error_fails_node: bool = False,
) -> tuple[WorkflowEngine, EventStore]:
    """mock 도구 주입 엔진(gating 기본 — game_data_* always-allow, interrupt 없음)."""
    store = EventStore(db_path)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={
            "game_data_read": _mock_game_data_read(enemies, weapons, out_dir),
            "game_data_write": _mock_game_data_write(captured, out_dir),
        },
        error_fails_node=error_fails_node,
    )
    return engine, store


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


# ── 1. enemies/weapons 존재 → mock 풀런 성공·병합 valid·tmp 격리 (AC1·AC2·AC3) ─


def test_mock_full_run_writes_valid_data(tmp_path: Path) -> None:
    """enemies/weapons 존재 시 mock 풀런 → status success·병합 valid·tmp 격리.

    AC2: 기존 필수필드(id/name/hp/...) 보존 확인.
    AC3: flavor 필드가 각 항목에 추가됨 확인.
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            enemies=_ENEMIES,
            weapons=_WEAPONS,
            captured=captured,
            out_dir=tmp_path,
        )
        state = engine.run(nodes=list(NODES), run_id="gc1", workflow=WORKFLOW_NAME)

        # ① status success(5노드 전부 통과).
        assert state.get("_workflow_status") == "success", state.get("_error")

        # ② 2파일 기록됨 + validate_* ok:true(스키마 비파괴).
        enemies_out = captured["data/enemies.json"]["content"]
        weapons_out = captured["data/weapons.json"]["content"]

        assert validate_enemies(enemies_out)["ok"], validate_enemies(enemies_out)[
            "errors"
        ]
        assert validate_weapons(weapons_out)["ok"], validate_weapons(weapons_out)[
            "errors"
        ]

        # ③ 기존 필수필드 보존(AC2: 병합 후 필수 필드 삭제·변형 금지).
        for e_in, e_out in zip(_ENEMIES["enemies"], enemies_out["enemies"]):
            for key in (
                "id",
                "name",
                "hp",
                "speed",
                "damage",
                "spawnWeight",
                "behavior",
            ):
                assert e_out.get(key) == e_in[key], (
                    f"enemies[{e_in['id']}].{key} 변형: {e_in[key]} → {e_out.get(key)}"
                )

        for w_in, w_out in zip(_WEAPONS["weapons"], weapons_out["weapons"]):
            for key in (
                "id",
                "name",
                "fireRate",
                "projectiles",
                "damage",
                "range",
                "pierce",
            ):
                assert w_out.get(key) == w_in[key], (
                    f"weapons[{w_in['id']}].{key} 변형: {w_in[key]} → {w_out.get(key)}"
                )

        # ④ flavor 필드가 각 항목에 추가됨(AC3).
        for e in enemies_out["enemies"]:
            assert "flavor" in e, f"enemies[{e['id']}]: flavor 필드 없음"
            assert isinstance(e["flavor"], str) and e["flavor"], (
                f"enemies[{e['id']}]: flavor가 비어있음"
            )

        for w in weapons_out["weapons"]:
            assert "flavor" in w, f"weapons[{w['id']}]: flavor 필드 없음"
            assert isinstance(w["flavor"], str) and w["flavor"], (
                f"weapons[{w['id']}]: flavor가 비어있음"
            )

        # ④-b ★Minor-1: 읽은 id == 쓴 id(병합이 carryforward 항목 집합을 보존 — 새
        # id 추가/누락 0). read 입력과 write 출력의 id 집합이 정확히 일치해야 한다.
        assert {e["id"] for e in _ENEMIES["enemies"]} == {
            e["id"] for e in enemies_out["enemies"]
        }, "enemies: 읽은 id ≠ 쓴 id(병합이 항목 집합을 변형)"
        assert {w["id"] for w in _WEAPONS["weapons"]} == {
            w["id"] for w in weapons_out["weapons"]
        }, "weapons: 읽은 id ≠ 쓴 id(병합이 항목 집합을 변형)"

        # ⑤ tmp 격리 — 2파일이 tmp_path 하위에 기록됨(실 game/ 무접촉).
        assert (tmp_path / "data/enemies.json").exists()
        assert (tmp_path / "data/weapons.json").exists()

        # ⑥ 도구 실행 흔적: game_data_read 2회 + game_data_write 2회.
        events = store.read("gc1")
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
        assert len(reads) == 2
        assert len(writes) == 2
        assert all(e.result["exists"] is True for e in reads)

        # RUN_FINISHED(success).
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any(e.status == "success" for e in finished)
    finally:
        os.unlink(db_path)


# ── 2. ★입력 부재 시 → 워크플로 비성공(AC2) ──────────────────────────────────


def test_missing_enemies_fails_workflow(tmp_path: Path) -> None:
    """enemies 부재 시 game_data_read required=True가 예외 → 워크플로 비성공.

    n1_read_enemies에서 멈추므로 write 노드는 실행되지 않는다(진행 거부).
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            enemies=None,  # ★enemies 부재
            weapons=_WEAPONS,
            captured=captured,
            out_dir=tmp_path,
            error_fails_node=True,
        )
        state = engine.run(nodes=list(NODES), run_id="gc2", workflow=WORKFLOW_NAME)

        # 워크플로가 성공하지 않음(AC2).
        assert state.get("_workflow_status") != "success"
        assert state.get("_workflow_status") == "failed"

        # 진행 거부: write 파일 없음.
        assert captured == {}
        assert not (tmp_path / "data/enemies.json").exists()
        assert not (tmp_path / "data/weapons.json").exists()

        # read 도구는 호출됐고 결과는 error(예외→error dict 전파).
        events = store.read("gc2")
        reads = [
            e
            for e in events
            if isinstance(e, ToolCallResult) and e.name == "game_data_read"
        ]
        assert len(reads) == 1
        assert "error" in reads[0].result
    finally:
        os.unlink(db_path)


def test_missing_weapons_fails_workflow(tmp_path: Path) -> None:
    """weapons 부재 시 game_data_read required=True가 예외 → 워크플로 비성공.

    n2_read_weapons에서 멈추므로 write 노드는 실행되지 않는다(진행 거부).
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            enemies=_ENEMIES,
            weapons=None,  # ★weapons 부재
            captured=captured,
            out_dir=tmp_path,
            error_fails_node=True,
        )
        state = engine.run(nodes=list(NODES), run_id="gc3", workflow=WORKFLOW_NAME)

        # 워크플로가 성공하지 않음(AC2).
        assert state.get("_workflow_status") != "success"
        assert state.get("_workflow_status") == "failed"

        # write 파일 없음.
        assert not (tmp_path / "data/enemies.json").exists()
        assert not (tmp_path / "data/weapons.json").exists()
    finally:
        os.unlink(db_path)


def test_missing_input_fails_in_production_engine_config(tmp_path: Path) -> None:
    """★프로덕션경로(Major-1): run_full/worker가 쓰는 것과 동일한 error_fails_node
    구성으로 enemies 부재 풀런 → _workflow_status != 'success'(명시 실패) 단언.

    테스트 전용 플래그가 아니라 _is_game_workflow('game_content')로 프로덕션 구성을
    복제한다 — game_content ∈ GAME_WORKFLOWS이므로 run.py/worker.py는 error_fails_node=
    True로 엔진을 띄운다. 입력(이전 밸런스 단계 산출 enemies.json) 미완이면 콘텐츠 단계가
    프로덕션에서도 가짜 데이터로 완주하지 않고 명시 실패로 진행 거부됨을 단언(AC2).
    """
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        production_efn = _is_game_workflow(WORKFLOW_NAME)
        assert production_efn is True, "game_content는 GAME_WORKFLOWS에 있어야 한다"

        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            enemies=None,  # ★enemies 부재 — required read가 raise(이전 단계 미완)
            weapons=_WEAPONS,
            captured=captured,
            out_dir=tmp_path,
            error_fails_node=production_efn,
        )
        state = engine.run(nodes=list(NODES), run_id="gc_prod", workflow=WORKFLOW_NAME)

        assert state.get("_workflow_status") != "success", (
            "프로덕션 error_fails_node 구성에서 enemies 부재인데 success로 끝남 — AC2 갭"
        )
        # 진행 거부: 병합 write 파일 0(첫 read 노드에서 멈춤·필드 보존 위반 다운스트림 차단).
        assert captured == {}
    finally:
        os.unlink(db_path)


# ── 3. 멱등: 동일 입력 2회 병합 → 중복 텍스트 0(flavor 1개)·결정론 (AC3) ────────


def test_real_game_data_write_dedup_on_second_run(tmp_path: Path) -> None:
    """★Major-2: 실 game_data_write로 병합 멱등을 실경로로 검증한다.

    test_idempotent_double_run은 비-dedup mock write로 "결과 동일"만 봤다. 여기서는
    실 game_data_write(tools.py 내용해시 dedup)를 game/ 밖 tmp 디렉터리(monkeypatch로
    game_dir을 옮길 수 없으므로 path 화이트리스트를 만족하는 game/ 하위 격리 경로 사용)에
    1차 기록 → 2차 동일 content 재기록 → deduplicated=True(쓰기 skip)를 단언한다.

    ★실 game/data 오염 0: game/ 하위에 평소 없는 임시 파일명(__g5_dedup_probe__/)을 쓰고
    finally에서 지운다(화이트리스트 통과 + 베이스라인 무접촉).
    """
    import shutil
    from pathlib import Path as _P

    repo_root = _P(__file__).resolve().parent.parent
    probe_dir = repo_root / "game" / "data" / "__g5_dedup_probe__"
    rel = "data/__g5_dedup_probe__/enemies_merged.json"
    try:
        # 1차 기록 — 병합된(flavor 포함) enemies. 실 디스크 쓰기.
        res1 = game_data_write({"path": rel, "content": _ENEMIES_WITH_FLAVOR})
        assert res1["written"] is True, res1
        assert res1["deduplicated"] is False, res1
        # 디스크 내용이 validate_enemies ok:true(필드 보존 + flavor 병합).
        on_disk = json.loads((repo_root / "game" / rel).read_text(encoding="utf-8"))
        assert validate_enemies(on_disk)["ok"], validate_enemies(on_disk)["errors"]
        for e in on_disk["enemies"]:
            assert "flavor" in e and isinstance(e["flavor"], str) and e["flavor"]

        # 2차 동일 content 재실행 → 내용해시 dedup(쓰기 skip).
        res2 = game_data_write({"path": rel, "content": _ENEMIES_WITH_FLAVOR})
        assert res2["written"] is False, res2
        assert res2["deduplicated"] is True, (
            "동일 content 재기록인데 dedup 안 됨 — 멱등 실경로 위반"
        )
        assert res2["bytes"] == res1["bytes"]
    finally:
        # tmp probe 디렉터리 정리(베이스라인 game/data 무접촉).
        if probe_dir.exists():
            shutil.rmtree(probe_dir, ignore_errors=True)


def test_idempotent_double_run(tmp_path: Path) -> None:
    """동일 입력으로 2회 실행해도 flavor 필드가 1개만 있고 내용이 동일함(멱등·결정론).

    첫 번째 실행으로 enemies/weapons+flavor가 기록된다. 두 번째 실행에서는
    이미 flavor 키가 있어도 덮어쓰기만 하고 중복 추가 없음을 단언한다.
    """
    db_path1 = _tmp_db()
    db_path2 = _tmp_db()
    tmp1 = tmp_path / "run1"
    tmp2 = tmp_path / "run2"
    tmp1.mkdir()
    tmp2.mkdir()
    captured1: dict[str, dict[str, Any]] = {}
    captured2: dict[str, dict[str, Any]] = {}
    try:
        # 1회차
        llm1 = RecordingLLM()
        engine1, _ = _make_engine(
            llm1,
            db_path1,
            enemies=_ENEMIES,
            weapons=_WEAPONS,
            captured=captured1,
            out_dir=tmp1,
        )
        state1 = engine1.run(
            nodes=list(NODES), run_id="gc_idem1", workflow=WORKFLOW_NAME
        )
        assert state1.get("_workflow_status") == "success"

        # 2회차 — 동일 입력 재실행
        llm2 = RecordingLLM()
        engine2, _ = _make_engine(
            llm2,
            db_path2,
            enemies=_ENEMIES,
            weapons=_WEAPONS,
            captured=captured2,
            out_dir=tmp2,
        )
        state2 = engine2.run(
            nodes=list(NODES), run_id="gc_idem2", workflow=WORKFLOW_NAME
        )
        assert state2.get("_workflow_status") == "success"

        # 두 결과가 동일(결정론).
        enemies1 = captured1["data/enemies.json"]["content"]
        enemies2 = captured2["data/enemies.json"]["content"]
        weapons1 = captured1["data/weapons.json"]["content"]
        weapons2 = captured2["data/weapons.json"]["content"]

        assert enemies1 == enemies2, "enemies 결정론 위반: 2회 실행 결과 다름"
        assert weapons1 == weapons2, "weapons 결정론 위반: 2회 실행 결과 다름"

        # 각 항목에 flavor가 정확히 1개(중복 추가 없음·AC3).
        for e in enemies1["enemies"]:
            flavor_val = e.get("flavor")
            # flavor는 str 1개 — list/중복 구조 아님.
            assert isinstance(flavor_val, str), (
                f"enemies[{e['id']}]: flavor가 str이 아님 ({type(flavor_val).__name__})"
            )

        for w in weapons1["weapons"]:
            flavor_val = w.get("flavor")
            assert isinstance(flavor_val, str), (
                f"weapons[{w['id']}]: flavor가 str이 아님 ({type(flavor_val).__name__})"
            )

        # 스키마 비파괴(2회차도 ok:true).
        assert validate_enemies(enemies2)["ok"]
        assert validate_weapons(weapons2)["ok"]
    finally:
        os.unlink(db_path1)
        os.unlink(db_path2)


# ── 4. 11종 이벤트만·NODES 구조·로더 단언 (AC1·AC4) ─────────────────────────


def test_full_run_emits_only_registered_event_types(tmp_path: Path) -> None:
    """워크플로가 발행하는 이벤트 type은 EVENT_REGISTRY 11종 내에서만 — 새 타입 0."""
    db_path = _tmp_db()
    captured: dict[str, dict[str, Any]] = {}
    try:
        llm = RecordingLLM()
        engine, store = _make_engine(
            llm,
            db_path,
            enemies=_ENEMIES,
            weapons=_WEAPONS,
            captured=captured,
            out_dir=tmp_path,
        )
        engine.run(nodes=list(NODES), run_id="gc4", workflow=WORKFLOW_NAME)

        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types  # 비어있지 않음
        assert types <= set(EVENT_REGISTRY), (
            f"미등록 이벤트 타입: {types - set(EVENT_REGISTRY)}"
        )
        assert len(EVENT_REGISTRY) == 11  # 계약 동결(새 타입 0)
    finally:
        os.unlink(db_path)


def test_nodes_structure_no_agent_and_chain() -> None:
    """NODES 구조: kind∈{longform,tool}·★agent 0·next 체인·마지막 None."""
    assert WORKFLOW_NAME == "game_content"
    assert len(NODES) == 5
    kinds = [n.kind for n in NODES]
    assert all(k in {"chat", "longform", "tool"} for k in kinds)
    assert "agent" not in kinds  # ★선형 — 자율 agent 노드 0

    # next 체인: 마지막을 제외하고 모두 다음 노드 id를 가리키고, 마지막은 None.
    ids = [n.id for n in NODES]
    for i, n in enumerate(NODES[:-1]):
        assert n.next == ids[i + 1]
    assert NODES[-1].next is None


def test_loader_returns_five_node_game_content() -> None:
    """_load_workflow('game_content') = (5노드, 'game_content') + 노드 계약(★agent 0).

    ★node id 목록(US-G5-7 역할 매핑용·안정적):
    n1_read_enemies·n2_read_weapons·n3_write_flavor·n4_write_enemies·n5_write_weapons.
    """
    nodes, name = _load_workflow("game_content")
    assert name == "game_content"
    assert len(nodes) == 5

    expected_ids = [
        "n1_read_enemies",
        "n2_read_weapons",
        "n3_write_flavor",
        "n4_write_enemies",
        "n5_write_weapons",
    ]
    assert [n.id for n in nodes] == expected_ids

    # n1·n2: 읽기(tool · game_data_read · required 입력 계약)
    for n in nodes[:2]:
        assert n.kind == "tool"
        assert n.thinking == "off"
        assert n.tools == [GAME_DATA_READ_SCHEMA]

    # n1 → n2 체인
    assert nodes[0].next == "n2_read_weapons"
    assert nodes[1].next == "n3_write_flavor"

    # n3: 플레이버 설계(longform · thinking=on)
    n3 = nodes[2]
    assert n3.kind == "longform"
    assert n3.thinking == "on"

    # n4·n5: 기록(tool · game_data_write)
    for n in nodes[3:]:
        assert n.kind == "tool"
        assert n.tools == [GAME_DATA_WRITE_SCHEMA]

    assert nodes[-1].next is None
