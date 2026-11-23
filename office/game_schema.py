"""G005 게임 스키마 검증 — 단계간 JSON 계약의 SSOT (US-G5-1 AC6).

`.omc/prd.json` schema_contract를 정확히 구현한다. 모든 단계(설계→밸런스→콘텐츠→
조립→QA)·런타임·베이스라인이 공유하는 단일 검증 규칙. 순수 함수(디스크 미접촉)가
기본 — 각 validate_*(obj)는 {"ok": bool, "errors": list[str]}를 돌려준다. game/=
game/src/schema.ts(TS)에 동일 규칙이 2벌 포팅된다(US-G5-2).

참조 무결성(waves.spawn.enemyId ∈ enemies.id)은 단일 파일을 넘어서므로 validate_all
(파일 로드 후 cross-file 검증)에서만 검사한다 — 그 외 validate_*는 순수·단일파일.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# behavior enum (enemies.json) — schema_contract 고정값
_ENEMY_BEHAVIORS = {"chase", "swarm", "ranged", "charger"}


# ── 공통 검증 헬퍼 ────────────────────────────────────────────────────────────


def _is_number(v: Any) -> bool:
    """JSON number(int·float) 여부 — bool은 number로 보지 않는다(파이썬 bool⊂int 함정)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v: Any) -> bool:
    """JSON integer 여부 — bool 제외. float 1.0 등 정수값은 허용(TS isInt와 수렴)."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return v.is_integer()  # 1.0 OK, 1.5/nan/inf → False
    return False


def _check_str(obj: dict[str, Any], key: str, where: str, errors: list[str]) -> None:
    """필수 문자열 필드 — 존재·str·비어있지 않음."""
    if key not in obj:
        errors.append(f"{where}: 필수 필드 누락 '{key}'")
    elif not isinstance(obj[key], str) or not obj[key]:
        errors.append(f"{where}: '{key}'는 비어있지 않은 문자열이어야 함")


def _check_number(
    obj: dict[str, Any],
    key: str,
    where: str,
    errors: list[str],
    *,
    minimum: float | None = None,
    exclusive: bool = False,
    integer: bool = False,
) -> None:
    """필수 수치 필드 — 존재·타입·하한(minimum, exclusive=True면 >, False면 >=)."""
    if key not in obj:
        errors.append(f"{where}: 필수 필드 누락 '{key}'")
        return
    val = obj[key]
    if integer:
        if not _is_int(val):
            errors.append(f"{where}: '{key}'는 정수여야 함")
            return
    elif not _is_number(val):
        errors.append(f"{where}: '{key}'는 숫자여야 함")
        return
    if minimum is not None:
        if exclusive and not val > minimum:
            errors.append(f"{where}: '{key}'는 {minimum}보다 커야 함 (받음: {val})")
        elif not exclusive and not val >= minimum:
            errors.append(f"{where}: '{key}'는 {minimum} 이상이어야 함 (받음: {val})")


def _check_number_list(
    obj: dict[str, Any],
    key: str,
    where: str,
    errors: list[str],
    *,
    non_decreasing: bool = False,
) -> None:
    """수치 배열 필드 — 존재·list·각 원소 number·(옵션)비감소."""
    if key not in obj:
        errors.append(f"{where}: 필수 필드 누락 '{key}'")
        return
    arr = obj[key]
    if not isinstance(arr, list):
        errors.append(f"{where}: '{key}'는 배열이어야 함")
        return
    if not all(_is_number(x) for x in arr):
        errors.append(f"{where}: '{key}' 원소는 모두 숫자여야 함")
        return
    if non_decreasing:
        for i in range(1, len(arr)):
            if arr[i] < arr[i - 1]:
                errors.append(
                    f"{where}: '{key}'는 비감소여야 함 "
                    f"(index {i}: {arr[i]} < {arr[i - 1]})"
                )
                break


def _result(errors: list[str]) -> dict[str, Any]:
    return {"ok": not errors, "errors": errors}


def _items_list(
    obj: Any, key: str, min_count: int, label: str, errors: list[str]
) -> list[Any]:
    """obj[key]가 list이고 최소 개수 이상인지 검사 후 list를 반환(없으면 빈 list)."""
    if not isinstance(obj, dict):
        errors.append(f"{label}: 루트는 객체여야 함")
        return []
    items = obj.get(key)
    if not isinstance(items, list):
        errors.append(f"{label}: '{key}'는 배열이어야 함")
        return []
    if len(items) < min_count:
        errors.append(
            f"{label}: '{key}'는 최소 {min_count}개여야 함 (받음: {len(items)})"
        )
    return items


# ── 단계별 순수 검증 함수 ─────────────────────────────────────────────────────


def validate_meta(obj: Any) -> dict[str, Any]:
    """meta.json — theme/title/coreLoop/win/lose + continue{freeRetries,costCurve,sessionCap}."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return _result([f"meta: 루트는 객체여야 함 (받음: {type(obj).__name__})"])

    for key in ("theme", "title", "coreLoop", "win", "lose"):
        _check_str(obj, key, "meta", errors)

    cont = obj.get("continue")
    if not isinstance(cont, dict):
        errors.append("meta: 'continue'는 객체여야 함")
    else:
        _check_number(
            cont, "freeRetries", "meta.continue", errors, minimum=0, integer=True
        )
        _check_number_list(
            cont, "costCurve", "meta.continue", errors, non_decreasing=True
        )
        _check_number(
            cont, "sessionCap", "meta.continue", errors, minimum=1, integer=True
        )

    return _result(errors)


def validate_enemies(obj: Any) -> dict[str, Any]:
    """enemies.json — enemies[>=3]{id,name,hp>0,speed>0,damage>=0,spawnWeight>0,behavior∈enum}."""
    errors: list[str] = []
    items = _items_list(obj, "enemies", 3, "enemies", errors)

    seen_ids: set[str] = set()
    for i, e in enumerate(items):
        where = f"enemies[{i}]"
        if not isinstance(e, dict):
            errors.append(f"{where}: 객체여야 함")
            continue
        _check_str(e, "id", where, errors)
        _check_str(e, "name", where, errors)
        _check_number(e, "hp", where, errors, minimum=0, exclusive=True)
        _check_number(e, "speed", where, errors, minimum=0, exclusive=True)
        _check_number(e, "damage", where, errors, minimum=0)
        _check_number(e, "spawnWeight", where, errors, minimum=0, exclusive=True)
        behavior = e.get("behavior")
        if behavior not in _ENEMY_BEHAVIORS:
            errors.append(
                f"{where}: 'behavior'는 {sorted(_ENEMY_BEHAVIORS)} 중 하나여야 함 "
                f"(받음: {behavior!r})"
            )
        eid = e.get("id")
        if isinstance(eid, str) and eid:
            if eid in seen_ids:
                errors.append(f"{where}: 'id' 중복 ({eid!r})")
            seen_ids.add(eid)

    return _result(errors)


def validate_weapons(obj: Any) -> dict[str, Any]:
    """weapons.json — weapons[>=2]{id,name,fireRate>0,projectiles>=1,damage>0,range>0,pierce>=0}."""
    errors: list[str] = []
    items = _items_list(obj, "weapons", 2, "weapons", errors)

    for i, w in enumerate(items):
        where = f"weapons[{i}]"
        if not isinstance(w, dict):
            errors.append(f"{where}: 객체여야 함")
            continue
        _check_str(w, "id", where, errors)
        _check_str(w, "name", where, errors)
        _check_number(w, "fireRate", where, errors, minimum=0, exclusive=True)
        _check_number(w, "projectiles", where, errors, minimum=1, integer=True)
        _check_number(w, "damage", where, errors, minimum=0, exclusive=True)
        _check_number(w, "range", where, errors, minimum=0, exclusive=True)
        _check_number(w, "pierce", where, errors, minimum=0, integer=True)

    return _result(errors)


def validate_waves(obj: Any) -> dict[str, Any]:
    """waves.json — waves[>=2]{tStart>=0,tEnd>tStart,spawn[{enemyId,rate>0}],difficulty>0} + tStart 오름차순."""
    errors: list[str] = []
    items = _items_list(obj, "waves", 2, "waves", errors)

    prev_tstart: float | None = None
    for i, w in enumerate(items):
        where = f"waves[{i}]"
        if not isinstance(w, dict):
            errors.append(f"{where}: 객체여야 함")
            continue
        _check_number(w, "tStart", where, errors, minimum=0)
        _check_number(w, "difficulty", where, errors, minimum=0, exclusive=True)

        # tEnd > tStart (둘 다 number일 때만 비교)
        t_start = w.get("tStart")
        t_end = w.get("tEnd")
        if not _is_number(t_end):
            errors.append(f"{where}: 'tEnd'는 숫자여야 함")
        elif _is_number(t_start) and not t_end > t_start:
            errors.append(f"{where}: 'tEnd'({t_end})는 'tStart'({t_start})보다 커야 함")

        # tStart 오름차순(엄격 증가)
        if _is_number(t_start):
            if prev_tstart is not None and not t_start > prev_tstart:
                errors.append(
                    f"{where}: 'tStart'는 오름차순이어야 함 "
                    f"({t_start} <= 이전 {prev_tstart})"
                )
            prev_tstart = t_start

        # spawn[{enemyId, rate>0}]
        spawn = w.get("spawn")
        if not isinstance(spawn, list) or not spawn:
            errors.append(f"{where}: 'spawn'은 비어있지 않은 배열이어야 함")
        else:
            for j, s in enumerate(spawn):
                swhere = f"{where}.spawn[{j}]"
                if not isinstance(s, dict):
                    errors.append(f"{swhere}: 객체여야 함")
                    continue
                _check_str(s, "enemyId", swhere, errors)
                _check_number(s, "rate", swhere, errors, minimum=0, exclusive=True)

    return _result(errors)


def validate_upgrades(obj: Any) -> dict[str, Any]:
    """upgrades.json — upgrades[>=3]{id,name,stat,delta,cap}."""
    errors: list[str] = []
    items = _items_list(obj, "upgrades", 3, "upgrades", errors)

    for i, u in enumerate(items):
        where = f"upgrades[{i}]"
        if not isinstance(u, dict):
            errors.append(f"{where}: 객체여야 함")
            continue
        _check_str(u, "id", where, errors)
        _check_str(u, "name", where, errors)
        _check_str(u, "stat", where, errors)
        _check_number(u, "delta", where, errors)
        _check_number(u, "cap", where, errors)

    return _result(errors)


def validate_balance(obj: Any) -> dict[str, Any]:
    """balance.json — survivalTargetSec>0, dpsCurve[비감소], inRange:bool, report{check:bool}."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return _result([f"balance: 루트는 객체여야 함 (받음: {type(obj).__name__})"])

    _check_number(
        obj, "survivalTargetSec", "balance", errors, minimum=0, exclusive=True
    )
    _check_number_list(obj, "dpsCurve", "balance", errors, non_decreasing=True)

    if "inRange" not in obj:
        errors.append("balance: 필수 필드 누락 'inRange'")
    elif not isinstance(obj["inRange"], bool):
        errors.append("balance: 'inRange'는 불리언이어야 함")

    report = obj.get("report")
    if not isinstance(report, dict):
        errors.append("balance: 'report'는 객체여야 함")
    else:
        for k, v in report.items():
            if not isinstance(v, bool):
                errors.append(f"balance.report['{k}']: 불리언이어야 함")

    return _result(errors)


# ── 통합 검증 (파일 로드 + cross-file 참조 무결성) ────────────────────────────

# 파일명 → validator 매핑 (단일 출처)
_FILE_VALIDATORS: dict[str, Any] = {
    "meta.json": validate_meta,
    "enemies.json": validate_enemies,
    "weapons.json": validate_weapons,
    "waves.json": validate_waves,
    "upgrades.json": validate_upgrades,
    "balance.json": validate_balance,
}


def validate_all(data_dir: str | Path) -> dict[str, Any]:
    """game/data 디렉터리의 6파일을 로드해 각 validate_* + cross-file 참조 무결성 검증.

    순수 함수가 아님(디스크 로드). 반환: {"ok": bool, "errors": list[str]}.
    참조 무결성: waves.spawn.enemyId ∈ enemies.id (단계간 계약 핵심).
    """
    errors: list[str] = []
    base = Path(data_dir)
    loaded: dict[str, Any] = {}

    for fname, validator in _FILE_VALIDATORS.items():
        fpath = base / fname
        if not fpath.exists():
            errors.append(f"{fname}: 파일이 없음 ({fpath})")
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{fname}: JSON 로드 실패 ({exc})")
            continue
        loaded[fname] = data
        res = validator(data)
        if not res["ok"]:
            errors.extend(f"{fname} → {e}" for e in res["errors"])

    # cross-file 참조 무결성: waves.spawn.enemyId ∈ enemies.id
    enemies_data = loaded.get("enemies.json")
    waves_data = loaded.get("waves.json")
    if isinstance(enemies_data, dict) and isinstance(waves_data, dict):
        enemy_ids = {
            e["id"]
            for e in enemies_data.get("enemies", [])
            if isinstance(e, dict) and isinstance(e.get("id"), str)
        }
        for i, w in enumerate(waves_data.get("waves", [])):
            if not isinstance(w, dict):
                continue
            for j, s in enumerate(w.get("spawn", []) or []):
                if not isinstance(s, dict):
                    continue
                ref = s.get("enemyId")
                if isinstance(ref, str) and ref not in enemy_ids:
                    errors.append(
                        f"참조 무결성: waves[{i}].spawn[{j}].enemyId={ref!r}가 "
                        f"enemies.id에 없음"
                    )

    return _result(errors)
