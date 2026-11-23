"""game_schema 순수 검증 함수 테스트 — G005 US-G5-1 (AC6·AC7).

각 validate_*에 valid 1 + invalid 다수(필수 누락·타입 오류·수치범위 위반·enum 위반·
개수 미달·참조 깨짐)를 단언한다. ok False면 errors가 비어있지 않음도 확인.
validate_all(파일 로드 + cross-file 참조 무결성)은 tmp_path로 격리해 검증한다.

실행: scripts/run_tests.sh engine tests/test_game_schema.py -m "not slow"
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from office.game_schema import (
    validate_all,
    validate_balance,
    validate_enemies,
    validate_meta,
    validate_upgrades,
    validate_waves,
    validate_weapons,
)


# ── valid 베이스라인 픽스처 (schema_contract 준수·서바이버라이크 별세계) ──────


def _meta() -> dict[str, Any]:
    return {
        "theme": "뱀파이어 서바이벌 별세계",
        "title": "Eternal Swarm",
        "coreLoop": "이동·자동공격·생존·레벨업",
        "win": "10분 생존",
        "lose": "HP 0",
        "continue": {"freeRetries": 1, "costCurve": [0, 100, 250], "sessionCap": 3},
    }


def _enemies() -> dict[str, Any]:
    return {
        "enemies": [
            {
                "id": "bat",
                "name": "박쥐",
                "hp": 10,
                "speed": 2.0,
                "damage": 1,
                "spawnWeight": 5,
                "behavior": "swarm",
            },
            {
                "id": "zombie",
                "name": "좀비",
                "hp": 30,
                "speed": 1.0,
                "damage": 3,
                "spawnWeight": 3,
                "behavior": "chase",
            },
            {
                "id": "archer",
                "name": "궁수",
                "hp": 15,
                "speed": 1.5,
                "damage": 2,
                "spawnWeight": 2,
                "behavior": "ranged",
            },
        ]
    }


def _weapons() -> dict[str, Any]:
    return {
        "weapons": [
            {
                "id": "knife",
                "name": "단검",
                "fireRate": 2.0,
                "projectiles": 1,
                "damage": 5,
                "range": 100,
                "pierce": 0,
            },
            {
                "id": "axe",
                "name": "도끼",
                "fireRate": 0.5,
                "projectiles": 2,
                "damage": 12,
                "range": 200,
                "pierce": 3,
            },
        ]
    }


def _waves() -> dict[str, Any]:
    return {
        "waves": [
            {
                "tStart": 0,
                "tEnd": 30,
                "difficulty": 1.0,
                "spawn": [{"enemyId": "bat", "rate": 1.0}],
            },
            {
                "tStart": 30,
                "tEnd": 60,
                "difficulty": 2.0,
                "spawn": [{"enemyId": "zombie", "rate": 0.5}],
            },
        ]
    }


def _upgrades() -> dict[str, Any]:
    return {
        "upgrades": [
            {"id": "spd", "name": "속도", "stat": "speed", "delta": 0.1, "cap": 2.0},
            {"id": "dmg", "name": "공격력", "stat": "damage", "delta": 1, "cap": 10},
            {"id": "hp", "name": "체력", "stat": "maxHp", "delta": 5, "cap": 200},
        ]
    }


def _balance() -> dict[str, Any]:
    return {
        "survivalTargetSec": 600,
        "dpsCurve": [5, 10, 18, 30],
        "inRange": True,
        "report": {"dpsNonDecreasing": True, "survivalReachable": True},
    }


def _assert_invalid(result: dict[str, Any]) -> None:
    """ok False이고 errors가 비어있지 않음을 단언(공통)."""
    assert result["ok"] is False
    assert isinstance(result["errors"], list) and len(result["errors"]) > 0


# ── validate_meta ─────────────────────────────────────────────────────────────


def test_meta_valid() -> None:
    assert validate_meta(_meta()) == {"ok": True, "errors": []}


def test_meta_missing_required_field() -> None:
    m = _meta()
    del m["title"]
    _assert_invalid(validate_meta(m))


def test_meta_wrong_type() -> None:
    m = _meta()
    m["theme"] = 123  # str이어야 함
    _assert_invalid(validate_meta(m))


def test_meta_continue_freeretries_negative() -> None:
    m = _meta()
    m["continue"]["freeRetries"] = -1  # >=0 위반
    _assert_invalid(validate_meta(m))


def test_meta_continue_costcurve_decreasing() -> None:
    m = _meta()
    m["continue"]["costCurve"] = [0, 100, 50]  # 비감소 위반
    _assert_invalid(validate_meta(m))


def test_meta_continue_sessioncap_too_low() -> None:
    m = _meta()
    m["continue"]["sessionCap"] = 0  # >=1 위반
    _assert_invalid(validate_meta(m))


def test_meta_continue_not_object() -> None:
    m = _meta()
    m["continue"] = "nope"
    _assert_invalid(validate_meta(m))


def test_meta_root_not_object() -> None:
    _assert_invalid(validate_meta([1, 2, 3]))


# ── validate_enemies ──────────────────────────────────────────────────────────


def test_enemies_valid() -> None:
    assert validate_enemies(_enemies())["ok"] is True


def test_enemies_too_few() -> None:
    e = _enemies()
    e["enemies"] = e["enemies"][:2]  # <3
    _assert_invalid(validate_enemies(e))


def test_enemies_hp_not_positive() -> None:
    e = _enemies()
    e["enemies"][0]["hp"] = 0  # >0 위반
    _assert_invalid(validate_enemies(e))


def test_enemies_bad_behavior_enum() -> None:
    e = _enemies()
    e["enemies"][0]["behavior"] = "flying"  # enum 밖
    _assert_invalid(validate_enemies(e))


def test_enemies_missing_field() -> None:
    e = _enemies()
    del e["enemies"][1]["name"]
    _assert_invalid(validate_enemies(e))


def test_enemies_damage_negative() -> None:
    e = _enemies()
    e["enemies"][0]["damage"] = -1  # >=0 위반
    _assert_invalid(validate_enemies(e))


def test_enemies_bool_hp_rejected() -> None:
    e = _enemies()
    e["enemies"][0]["hp"] = True  # bool은 number 아님
    _assert_invalid(validate_enemies(e))


def test_enemies_not_list() -> None:
    _assert_invalid(validate_enemies({"enemies": "x"}))


# ── validate_weapons ──────────────────────────────────────────────────────────


def test_weapons_valid() -> None:
    assert validate_weapons(_weapons())["ok"] is True


def test_weapons_too_few() -> None:
    w = _weapons()
    w["weapons"] = w["weapons"][:1]  # <2
    _assert_invalid(validate_weapons(w))


def test_weapons_firerate_not_positive() -> None:
    w = _weapons()
    w["weapons"][0]["fireRate"] = 0  # >0 위반
    _assert_invalid(validate_weapons(w))


def test_weapons_projectiles_too_low() -> None:
    w = _weapons()
    w["weapons"][0]["projectiles"] = 0  # >=1 위반
    _assert_invalid(validate_weapons(w))


def test_weapons_pierce_negative() -> None:
    w = _weapons()
    w["weapons"][0]["pierce"] = -1  # >=0 위반
    _assert_invalid(validate_weapons(w))


def test_weapons_projectiles_non_integer() -> None:
    w = _weapons()
    w["weapons"][0]["projectiles"] = 1.5  # 정수여야 함
    _assert_invalid(validate_weapons(w))


# ── validate_waves ────────────────────────────────────────────────────────────


def test_waves_valid() -> None:
    assert validate_waves(_waves())["ok"] is True


def test_waves_too_few() -> None:
    w = _waves()
    w["waves"] = w["waves"][:1]  # <2
    _assert_invalid(validate_waves(w))


def test_waves_tend_not_greater_than_tstart() -> None:
    w = _waves()
    w["waves"][0]["tEnd"] = 0  # tEnd > tStart 위반(tStart=0)
    _assert_invalid(validate_waves(w))


def test_waves_tstart_not_ascending() -> None:
    w = _waves()
    w["waves"][1]["tStart"] = 0  # 오름차순 위반(이전과 동일)
    _assert_invalid(validate_waves(w))


def test_waves_spawn_rate_not_positive() -> None:
    w = _waves()
    w["waves"][0]["spawn"][0]["rate"] = 0  # >0 위반
    _assert_invalid(validate_waves(w))


def test_waves_spawn_empty() -> None:
    w = _waves()
    w["waves"][0]["spawn"] = []  # 비어있으면 안 됨
    _assert_invalid(validate_waves(w))


def test_waves_difficulty_not_positive() -> None:
    w = _waves()
    w["waves"][0]["difficulty"] = 0  # >0 위반
    _assert_invalid(validate_waves(w))


def test_waves_spawn_missing_enemyid() -> None:
    w = _waves()
    del w["waves"][0]["spawn"][0]["enemyId"]
    _assert_invalid(validate_waves(w))


# ── validate_upgrades ─────────────────────────────────────────────────────────


def test_upgrades_valid() -> None:
    assert validate_upgrades(_upgrades())["ok"] is True


def test_upgrades_too_few() -> None:
    u = _upgrades()
    u["upgrades"] = u["upgrades"][:2]  # <3
    _assert_invalid(validate_upgrades(u))


def test_upgrades_missing_stat() -> None:
    u = _upgrades()
    del u["upgrades"][0]["stat"]
    _assert_invalid(validate_upgrades(u))


def test_upgrades_delta_wrong_type() -> None:
    u = _upgrades()
    u["upgrades"][0]["delta"] = "x"  # number여야 함
    _assert_invalid(validate_upgrades(u))


# ── validate_balance ──────────────────────────────────────────────────────────


def test_balance_valid() -> None:
    assert validate_balance(_balance())["ok"] is True


def test_balance_survival_not_positive() -> None:
    b = _balance()
    b["survivalTargetSec"] = 0  # >0 위반
    _assert_invalid(validate_balance(b))


def test_balance_dpscurve_decreasing() -> None:
    b = _balance()
    b["dpsCurve"] = [10, 5, 8]  # 비감소 위반
    _assert_invalid(validate_balance(b))


def test_balance_inrange_not_bool() -> None:
    b = _balance()
    b["inRange"] = "yes"  # bool이어야 함
    _assert_invalid(validate_balance(b))


def test_balance_report_value_not_bool() -> None:
    b = _balance()
    b["report"]["check1"] = "true"  # 값은 bool이어야 함
    _assert_invalid(validate_balance(b))


def test_balance_report_not_object() -> None:
    b = _balance()
    b["report"] = []
    _assert_invalid(validate_balance(b))


# ── validate_all (파일 로드 + cross-file 참조 무결성) ─────────────────────────


def _write_baseline(data_dir: Path, **overrides: Any) -> None:
    """6개 베이스라인 파일을 data_dir에 쓴다(overrides로 특정 파일 교체)."""
    files = {
        "meta.json": _meta(),
        "enemies.json": _enemies(),
        "weapons.json": _weapons(),
        "waves.json": _waves(),
        "upgrades.json": _upgrades(),
        "balance.json": _balance(),
    }
    files.update(overrides)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        (data_dir / name).write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8"
        )


def test_validate_all_baseline_ok(tmp_path: Path) -> None:
    """베이스라인 6파일 전부 유효 + 참조 무결성 충족 → ok True."""
    _write_baseline(tmp_path)
    assert validate_all(tmp_path) == {"ok": True, "errors": []}


def test_validate_all_reference_integrity_broken(tmp_path: Path) -> None:
    """waves.spawn.enemyId가 enemies.id에 없으면 ok False(참조 깨짐)."""
    bad_waves = copy.deepcopy(_waves())
    bad_waves["waves"][0]["spawn"][0]["enemyId"] = "ghost_없음"
    _write_baseline(tmp_path, **{"waves.json": bad_waves})
    res = validate_all(tmp_path)
    _assert_invalid(res)
    assert any("참조 무결성" in e for e in res["errors"])


def test_validate_all_missing_file(tmp_path: Path) -> None:
    """파일 하나가 없으면 ok False."""
    _write_baseline(tmp_path)
    (tmp_path / "balance.json").unlink()
    res = validate_all(tmp_path)
    _assert_invalid(res)
    assert any("balance.json" in e for e in res["errors"])


def test_validate_all_propagates_single_file_error(tmp_path: Path) -> None:
    """단일 파일 검증 실패가 validate_all 에러로 전파된다."""
    bad_enemies = copy.deepcopy(_enemies())
    bad_enemies["enemies"][0]["hp"] = -5  # >0 위반
    _write_baseline(tmp_path, **{"enemies.json": bad_enemies})
    res = validate_all(tmp_path)
    _assert_invalid(res)
    assert any("enemies.json" in e for e in res["errors"])


def test_validate_all_bad_json(tmp_path: Path) -> None:
    """JSON 파싱 실패도 ok False로 처리(크래시 0)."""
    _write_baseline(tmp_path)
    (tmp_path / "meta.json").write_text("{not valid json", encoding="utf-8")
    res = validate_all(tmp_path)
    _assert_invalid(res)


# ── _is_int / TS isInt 언어간 수렴 (US-G5-2 M-1) ─────────────────────────────


def test_meta_freeretries_float_integer_accepted() -> None:
    """freeRetries=1.0(float 정수값) → ok True — TS isInt 동치(LLM 산출 수렴)."""
    m = _meta()
    m["continue"]["freeRetries"] = 1.0  # float 정수값 허용
    assert validate_meta(m) == {"ok": True, "errors": []}


def test_meta_freeretries_float_noninteger_rejected() -> None:
    """freeRetries=1.5(비정수 float) → ok False."""
    m = _meta()
    m["continue"]["freeRetries"] = 1.5
    _assert_invalid(validate_meta(m))


def test_meta_freeretries_bool_rejected() -> None:
    """freeRetries=True(bool) → ok False — bool은 int 서브타입이지만 거부."""
    m = _meta()
    m["continue"]["freeRetries"] = True
    _assert_invalid(validate_meta(m))


def test_weapons_projectiles_float_integer_accepted() -> None:
    """projectiles=2.0(float 정수값) → ok True — 정수필드 float 허용."""
    w = _weapons()
    w["weapons"][0]["projectiles"] = 2.0
    assert validate_weapons(w) == {"ok": True, "errors": []}


def test_weapons_projectiles_float_noninteger_rejected() -> None:
    """projectiles=1.5(비정수) → ok False."""
    w = _weapons()
    w["weapons"][0]["projectiles"] = 1.5
    _assert_invalid(validate_weapons(w))
