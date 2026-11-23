"""G005 US-G5-7 (AC3) — 게임 제작 풀 파이프라인 슬라이스 완주 통합 테스트.

★슬라이스 완주: 5개 워크플로를 순차로 mock 풀런해 게임 1개가 설계→밸런스→콘텐츠→
조립→QA 5단계를 1회 통과함을 단언한다(.omc/prd.json model_invariant — AI 직원들이
분업 제작하는 산출물).

검증(전부 mock·바운드·★실 game/data 오염 0 — tmp 격리):
  1) game_design   → meta.json 기록
  2) game_balance  → asm_read_meta… 아님: n1_read_meta가 직전 meta.json을 읽어(단계간
                     파일계약) enemies/weapons/waves/upgrades + balance.json 기록
  3) game_content  → enemies/weapons.json을 읽어 flavor 병합(스키마 비파괴)
  4) game_assemble → 6단계 산출 파일(meta/enemies/weapons/waves/upgrades)을 읽어
                     assembly.json{ready:true} 기록
  5) game_qa       → game_validate로 6파일 스키마+참조 무결성 검증 → qa_report.json

  최종(AC3):
    - tmp game/data에 6파일(meta/enemies/weapons/waves/upgrades/balance) 존재 + flavor.
    - office.game_schema.validate_all(tmp data) ok:true(6파일 스키마-유효·참조 무결성).
    - assembly.json{ready:true} + qa_report.json{pass:true} 산출.
    - ★단계간 파일계약: 각 단계가 이전 단계가 디스크에 남긴 파일을 실제로 읽어 진행
      (game_data_read가 tmp 파일을 읽고, 부재 시 명시 실패). 중간 단계 산출을 삭제하면
      다음 단계가 NODE_FAILED로 진행 거부됨도 단언(계약의 음성 케이스).

설계: 실 game/data 대신 tmp game 루트를 가리키는 game_data_write/read/validate 래퍼를
주입한다(실 도구의 경로 화이트리스트·멱등·스키마 검증 로직을 tmp 루트로 미러). 단계간
계약이 진짜로 디스크 파일을 통해 흐르도록 mock이 아닌 실제 tmp 파일 I/O를 쓴다(실
office.game_schema.validate_all로 최종 단언 — 산출물이 진짜 스키마-유효).

실행: scripts/run_tests.sh engine tests/test_game_pipeline_e2e.py -m "not slow"
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import EVENT_REGISTRY
from office.game_schema import (
    validate_all,
    validate_balance,
    validate_enemies,
    validate_meta,
    validate_upgrades,
    validate_waves,
    validate_weapons,
)
from office.run import _is_game_workflow, _load_workflow

# ★실 game/data 기존 6파일 베이스라인(파이프라인 실행 전후 불변 단언용 — 수정 3).
_REAL_GAME_DATA_BASELINE: frozenset[str] = frozenset(
    {
        "meta.json",
        "enemies.json",
        "weapons.json",
        "waves.json",
        "upgrades.json",
        "balance.json",
    }
)

# ── 단계별 유효 산출 데이터(각 validate_* ok:true·참조 무결성 충족) ────────────────
#
# 실 game/data 베이스라인과 동형. 각 워크플로의 fake LLM이 game_data_write content로
# 이 값을 발행 → tmp game/data에 실제 기록 → 다음 단계가 game_data_read로 읽음.

_META: dict[str, Any] = {
    "theme": "심야 묘지 생존 — 뱀파이어 서바이벌류 별세계",
    "title": "Midnight Graveyard Survivor",
    "coreLoop": "자동공격으로 밀려오는 언데드 무리를 생존하며 업그레이드 선택",
    "win": "지정 생존 시간 달성 시 탈출 성공",
    "lose": "플레이어 HP가 0이 되면 게임 오버",
    "continue": {
        "freeRetries": 2,
        "costCurve": [0, 100, 200, 400, 800],
        "sessionCap": 5,
    },
}
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
_WAVES: dict[str, Any] = {
    "waves": [
        {
            "tStart": 0,
            "tEnd": 60,
            "spawn": [
                {"enemyId": "zombie", "rate": 0.5},
                {"enemyId": "bat_swarm", "rate": 0.3},
            ],
            "difficulty": 1.0,
        },
        {
            "tStart": 60,
            "tEnd": 120,
            "spawn": [
                {"enemyId": "zombie", "rate": 1.0},
                {"enemyId": "skeleton_archer", "rate": 0.2},
            ],
            "difficulty": 1.5,
        },
        {
            "tStart": 120,
            "tEnd": 300,
            "spawn": [{"enemyId": "grave_boar", "rate": 0.1}],
            "difficulty": 2.5,
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
    "survivalTargetSec": 300,
    "dpsCurve": [15, 20, 30, 45, 65],
    "inRange": True,
    "report": {"enemyHpScaling": True, "waveProgression": True, "weaponBalance": True},
}


# ── tmp 루트를 가리키는 실 도구 래퍼(실 game/data 무접촉) ────────────────────────
#
# office.tools.game_data_write/read/validate는 repo_root/"game"을 하드코딩한다. 단계간
# 계약이 진짜 디스크 파일로 흐르되 실 game/를 오염시키지 않도록, 동일 경로 화이트리스트·
# 멱등·스키마 검증 로직을 tmp game 루트(game_root)로 미러한 래퍼를 주입한다.


def _make_tmp_tools(game_root: Path) -> dict[str, Any]:
    """tmp game_root를 가리키는 game_data_write/read/validate 도구 3종을 만든다."""

    def _resolve(raw_path: str) -> Path | None:
        """game_root 하위로 정규화. game_root 밖/자기참조면 None(거부)."""
        target = (game_root / raw_path).resolve()
        try:
            rel = target.relative_to(game_root.resolve())
        except ValueError:
            return None
        if str(rel) == ".":
            return None
        return target

    def _write(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw_path = args.get("path", "")
        content = args.get("content", "")
        target = _resolve(raw_path)
        if target is None or target.is_dir():
            return {
                "written": False,
                "error": "path outside game/ or dir",
                "path": raw_path,
            }
        text = (
            json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
            if isinstance(content, (dict, list))
            else str(content)
        )
        new_bytes = text.encode("utf-8")
        rel = str(target.relative_to(game_root.resolve()))
        # 멱등(내용해시 no-op) — 실 도구와 동일.
        if target.exists() and target.read_bytes() == new_bytes:
            return {
                "written": False,
                "path": rel,
                "bytes": len(new_bytes),
                "deduplicated": True,
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(new_bytes)
        return {
            "written": True,
            "path": rel,
            "bytes": len(new_bytes),
            "deduplicated": False,
        }

    def _read(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw_path = args.get("path", "")
        required = bool(args.get("required", False))
        target = _resolve(raw_path)
        if target is None:
            raise ValueError(f"path outside game/: {raw_path!r}")
        rel = str(target.relative_to(game_root.resolve()))
        if not target.exists():
            if required:
                # ★단계 계약 위반: 이전 단계 산출 파일이 없음 → 명시 실패 유도.
                raise FileNotFoundError(f"required game data file not found: {rel}")
            return {"exists": False, "content": None, "path": rel}
        return {
            "exists": True,
            "content": target.read_text(encoding="utf-8"),
            "path": rel,
        }

    def _validate(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw_dir = args.get("data_dir") or "data"
        target = (game_root / raw_dir).resolve()
        try:
            target.relative_to(game_root.resolve())
        except ValueError:
            return {
                "ok": False,
                "errors": [f"data_dir outside game/: {raw_dir!r}"],
                "report": {"valid": False, "errorCount": 1},
            }
        res = validate_all(target)
        errors = res.get("errors", [])
        return {
            "ok": res["ok"],
            "errors": errors,
            "report": {"valid": res["ok"], "errorCount": len(errors)},
        }

    return {
        "game_data_write": _write,
        "game_data_read": _read,
        "game_validate": _validate,
    }


# ── 단계별 결정론 fake LLM(노드 순서대로 tool-call/평문 발행) ──────────────────────


def _reply(content: str, reasoning: str = "") -> dict[str, Any]:
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
        "latency_ms": 5.0,
    }


def _tool(name: str, arguments: dict[str, Any]) -> str:
    return json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False)


class _SeqLLM:
    """노드 순서대로 미리 정한 응답 시퀀스를 발행하는 결정론 fake LLM.

    each item: ("tool", name, args) | ("text", content). 노드 수와 길이 일치.
    """

    def __init__(
        self, seq: list[tuple[str, ...] | tuple[str, str, dict[str, Any]]]
    ) -> None:
        self._seq = seq
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    def chat(
        self, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append({"messages": list(messages)})
        item = self._seq[min(self._idx, len(self._seq) - 1)]
        self._idx += 1
        if item[0] == "tool":
            return _reply(_tool(item[1], item[2]))  # type: ignore[index]
        return _reply(item[1], reasoning="추론 중...")  # type: ignore[index]


def _design_seq() -> list[Any]:
    # n1_design_concept(longform·평문) → n2_write_meta(tool·meta.json).
    return [
        ("text", "테마/코어루프/승패/retry 과금 설계 완료."),
        ("tool", "game_data_write", {"path": "data/meta.json", "content": _META}),
    ]


def _balance_seq() -> list[Any]:
    # n1_read_meta(tool·required) → n2_design(평문) → n3~n7 write(tool).
    return [
        ("tool", "game_data_read", {"path": "data/meta.json", "required": True}),
        ("text", "meta 기반 적/무기/웨이브/업그레이드 수치 설계 완료."),
        ("tool", "game_data_write", {"path": "data/enemies.json", "content": _ENEMIES}),
        ("tool", "game_data_write", {"path": "data/weapons.json", "content": _WEAPONS}),
        ("tool", "game_data_write", {"path": "data/waves.json", "content": _WAVES}),
        (
            "tool",
            "game_data_write",
            {"path": "data/upgrades.json", "content": _UPGRADES},
        ),
        ("tool", "game_data_write", {"path": "data/balance.json", "content": _BALANCE}),
    ]


def _enemies_with_flavor() -> dict[str, Any]:
    out = json.loads(json.dumps(_ENEMIES))  # deep copy
    flavors = {
        "zombie": "묘지에서 기어나온 느린 시체.",
        "bat_swarm": "달빛 아래 몰려드는 흡혈 박쥐 떼.",
        "skeleton_archer": "녹슨 활을 든 해골 사수.",
        "grave_boar": "성난 돌진형 묘지 멧돼지.",
    }
    for e in out["enemies"]:
        e["flavor"] = flavors[e["id"]]
    return out


def _weapons_with_flavor() -> dict[str, Any]:
    out = json.loads(json.dumps(_WEAPONS))
    flavors = {
        "holy_cross": "전방으로 날아가는 신성한 십자가.",
        "garlic_aura": "주변을 감싸는 마늘 보호막.",
        "silver_knife": "관통하는 은제 투척 단검.",
    }
    for w in out["weapons"]:
        w["flavor"] = flavors[w["id"]]
    return out


def _content_seq() -> list[Any]:
    # n1_read_enemies(tool·req) → n2_read_weapons(tool·req) → n3_flavor(평문)
    # → n4_write_enemies(tool·flavor 병합) → n5_write_weapons(tool·flavor 병합).
    return [
        ("tool", "game_data_read", {"path": "data/enemies.json", "required": True}),
        ("tool", "game_data_read", {"path": "data/weapons.json", "required": True}),
        ("text", "적/무기 플레이버 텍스트 양산 완료."),
        (
            "tool",
            "game_data_write",
            {"path": "data/enemies.json", "content": _enemies_with_flavor()},
        ),
        (
            "tool",
            "game_data_write",
            {"path": "data/weapons.json", "content": _weapons_with_flavor()},
        ),
    ]


def _assemble_seq() -> list[Any]:
    # asm_read_meta/enemies/weapons/waves/upgrades(tool·req) → asm_assemble(평문)
    # → asm_write_assembly(tool·assembly.json{ready:true}).
    return [
        ("tool", "game_data_read", {"path": "data/meta.json", "required": True}),
        ("tool", "game_data_read", {"path": "data/enemies.json", "required": True}),
        ("tool", "game_data_read", {"path": "data/weapons.json", "required": True}),
        ("tool", "game_data_read", {"path": "data/waves.json", "required": True}),
        ("tool", "game_data_read", {"path": "data/upgrades.json", "required": True}),
        ("text", "런타임 바인딩·참조 무결성·풀링 검토 완료 — 조립 준비됨."),
        (
            "tool",
            "game_data_write",
            {
                "path": "data/assembly.json",
                "content": {
                    "ready": True,
                    "runtime": "survivor-sim",
                    "entry": "src/main.ts",
                    "entityCap": 300,
                    "notes": "바인딩 검토 완료",
                },
            },
        ),
    ]


def _qa_seq(report_pass: bool, errors: list[str]) -> list[Any]:
    # n1_validate(tool·game_validate) → n2_write_report(tool·qa_report.json).
    return [
        ("tool", "game_validate", {"data_dir": "data"}),
        (
            "tool",
            "game_data_write",
            {
                "path": "data/qa_report.json",
                "content": {"pass": report_pass, "errors": errors},
            },
        ),
    ]


# ── 엔진 배선 헬퍼 ─────────────────────────────────────────────────────────────


def _tmp_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return f.name


def _run_stage(
    workflow: str, seq: list[Any], tools: dict[str, Any], store: EventStore, run_id: str
) -> dict[str, Any]:
    """한 워크플로를 _load_workflow로 로드해 프로덕션 game 구성(error_fails_node)으로 풀런."""
    nodes, name = _load_workflow(workflow)
    assert name == workflow
    llm = _SeqLLM(seq)
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=tools,
        # ★game 워크플로 프로덕션 구성: 입력 부재(required read 예외)면 NODE_FAILED.
        error_fails_node=_is_game_workflow(workflow),
    )
    return engine.run(nodes=list(nodes), run_id=run_id, workflow=workflow)


# ── 1. ★풀 파이프라인 슬라이스 완주(AC3) ──────────────────────────────────────


def test_full_pipeline_slice_completes(tmp_path: Path) -> None:
    """game_design→balance→content→assemble→qa 순차 풀런 → 6파일 스키마-유효·qa pass."""
    game_root = tmp_path / "game"
    (game_root / "data").mkdir(parents=True)
    tools = _make_tmp_tools(game_root)
    data_dir = game_root / "data"
    db_path = _tmp_db()
    try:
        store = EventStore(db_path)

        # ── 단계 1: game_design → meta.json ──
        s1 = _run_stage("game_design", _design_seq(), tools, store, "e2e_design")
        assert s1.get("_workflow_status") == "success", s1.get("_error")
        assert (data_dir / "meta.json").exists()  # 단계간 파일계약: 디스크에 남음.

        # ── 단계 2: game_balance(meta.json 읽음) → enemies/weapons/waves/upgrades/balance ──
        s2 = _run_stage("game_balance", _balance_seq(), tools, store, "e2e_balance")
        assert s2.get("_workflow_status") == "success", s2.get("_error")
        for fname in (
            "enemies.json",
            "weapons.json",
            "waves.json",
            "upgrades.json",
            "balance.json",
        ):
            assert (data_dir / fname).exists(), f"{fname} 미기록"

        # ── 단계 3: game_content(enemies/weapons 읽음) → flavor 병합 ──
        s3 = _run_stage("game_content", _content_seq(), tools, store, "e2e_content")
        assert s3.get("_workflow_status") == "success", s3.get("_error")
        enemies_disk = json.loads(
            (data_dir / "enemies.json").read_text(encoding="utf-8")
        )
        weapons_disk = json.loads(
            (data_dir / "weapons.json").read_text(encoding="utf-8")
        )
        # flavor 병합됨 + 기존 필수필드 보존(스키마 비파괴).
        assert all("flavor" in e for e in enemies_disk["enemies"])
        assert all("flavor" in w for w in weapons_disk["weapons"])
        assert validate_enemies(enemies_disk)["ok"], validate_enemies(enemies_disk)[
            "errors"
        ]
        assert validate_weapons(weapons_disk)["ok"], validate_weapons(weapons_disk)[
            "errors"
        ]

        # ── 단계 4: game_assemble(5파일 읽음) → assembly.json{ready:true} ──
        s4 = _run_stage("game_assemble", _assemble_seq(), tools, store, "e2e_assemble")
        assert s4.get("_workflow_status") == "success", s4.get("_error")
        assembly = json.loads((data_dir / "assembly.json").read_text(encoding="utf-8"))
        assert assembly["ready"] is True

        # ── 단계 5: game_qa(6파일 검증) → qa_report.json{pass:true} ──
        s5 = _run_stage("game_qa", _qa_seq(True, []), tools, store, "e2e_qa")
        assert s5.get("_workflow_status") == "success", s5.get("_error")
        qa_report = json.loads(
            (data_dir / "qa_report.json").read_text(encoding="utf-8")
        )
        assert qa_report["pass"] is True, qa_report.get("errors")

        # ── 최종(AC3): 6파일 전부 존재 + validate_all ok:true(스키마+참조 무결성) ──
        for fname in (
            "meta.json",
            "enemies.json",
            "weapons.json",
            "waves.json",
            "upgrades.json",
            "balance.json",
        ):
            assert (data_dir / fname).exists(), f"최종 6파일 누락: {fname}"
        final = validate_all(data_dir)
        assert final["ok"] is True, final["errors"]

        # 개별 validator도 전부 ok(이중 확인).
        assert validate_meta(json.loads((data_dir / "meta.json").read_text("utf-8")))[
            "ok"
        ]
        assert validate_waves(json.loads((data_dir / "waves.json").read_text("utf-8")))[
            "ok"
        ]
        assert validate_upgrades(
            json.loads((data_dir / "upgrades.json").read_text("utf-8"))
        )["ok"]
        assert validate_balance(
            json.loads((data_dir / "balance.json").read_text("utf-8"))
        )["ok"]

        # ★실 game/data 무오염: 풀 파이프라인 실행 후 실 repo game/data에
        # assembly.json·qa_report.json이 생성되지 않았음을 실검증.
        real_data = Path(__file__).resolve().parent.parent / "game" / "data"
        assert not (real_data / "assembly.json").exists(), (
            "실 game/data가 오염됨: assembly.json이 실 repo에 생성됨"
        )
        assert not (real_data / "qa_report.json").exists(), (
            "실 game/data가 오염됨: qa_report.json이 실 repo에 생성됨"
        )
        # 실 game/data의 기존 6파일 목록이 파이프라인 실행 전후로 동일(추가·삭제 없음).
        real_files_after = {p.name for p in real_data.iterdir() if p.is_file()}
        assert real_files_after == _REAL_GAME_DATA_BASELINE, (
            f"실 game/data 파일목록 변경됨: {real_files_after} != {_REAL_GAME_DATA_BASELINE}"
        )
    finally:
        os.unlink(db_path)


# ── 2. ★단계간 파일계약(음성): 중간 산출 부재 시 다음 단계 진행 거부 ─────────────


def test_pipeline_stage_contract_blocks_when_input_missing(tmp_path: Path) -> None:
    """meta.json 없이 game_balance 실행 → required read 예외 → NODE_FAILED(진행 거부).

    단계간 파일계약의 음성 케이스: 이전 단계(game_design) 산출이 디스크에 없으면 다음
    단계가 가짜 데이터로 완주하지 않고 명시 실패한다(수동 체이닝 계약).
    """
    game_root = tmp_path / "game"
    (game_root / "data").mkdir(parents=True)
    tools = _make_tmp_tools(game_root)
    data_dir = game_root / "data"
    db_path = _tmp_db()
    try:
        store = EventStore(db_path)
        # game_design을 건너뛰고 바로 balance — meta.json 부재.
        s2 = _run_stage("game_balance", _balance_seq(), tools, store, "e2e_nofile")
        assert s2.get("_workflow_status") != "success"
        assert s2.get("_workflow_status") == "failed"
        # 첫 노드(n1_read_meta)에서 멈춤 → write 산출 0.
        for fname in (
            "enemies.json",
            "weapons.json",
            "waves.json",
            "upgrades.json",
            "balance.json",
        ):
            assert not (data_dir / fname).exists(), (
                f"{fname}이 부당하게 기록됨(진행 거부 실패)"
            )
    finally:
        os.unlink(db_path)


# ── 3. 파이프라인 발행 이벤트는 11종(EVENT_REGISTRY)만 — 새 타입 0 ───────────────


def test_pipeline_emits_only_registered_events(tmp_path: Path) -> None:
    """5단계 풀런 누적 이벤트 type이 EVENT_REGISTRY 11종 내에서만(새 이벤트 0)."""
    game_root = tmp_path / "game"
    (game_root / "data").mkdir(parents=True)
    tools = _make_tmp_tools(game_root)
    db_path = _tmp_db()
    try:
        store = EventStore(db_path)
        _run_stage("game_design", _design_seq(), tools, store, "ev_design")
        _run_stage("game_balance", _balance_seq(), tools, store, "ev_balance")
        _run_stage("game_content", _content_seq(), tools, store, "ev_content")
        _run_stage("game_assemble", _assemble_seq(), tools, store, "ev_assemble")
        _run_stage("game_qa", _qa_seq(True, []), tools, store, "ev_qa")

        types = {json.loads(pj).get("type") for _, pj in store.read_since(0)}
        assert types
        assert types <= set(EVENT_REGISTRY), (
            f"미등록 이벤트: {types - set(EVENT_REGISTRY)}"
        )
        assert len(EVENT_REGISTRY) == 11
    finally:
        os.unlink(db_path)
