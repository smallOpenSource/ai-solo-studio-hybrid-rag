"""game_data_write 도구 단위 + 게이팅 회귀 테스트 — G005 US-G5-1 (AC5·AC7).

덮는 범위:
  1) 경로 화이트리스트(AC2): `..`·절대경로·game/ 밖 탈출 = written False·디스크 미접촉
  2) 멱등(AC3): 동일 content 2회 → 2번째 deduplicated True·파일 1개
  3) 정상 쓰기(AC1): game/ 하위 기록·반환 dict 계약
  4) 게이팅 회귀(AC5): tools_policy.yaml 로드 시 game_data_write→allow(suspend 0),
     정책에서 제거(미등록)하면 → interrupt(suspend)

★실제 game/data 오염 금지: 모든 쓰기는 game/ 하위 테스트 전용 서브디렉터리에서만 하고
finalizer로 정리한다. gating.py는 sha 보호라 읽기만(생성자·evaluate API만 사용).

실행: scripts/run_tests.sh engine tests/test_game_data_write.py -m "not slow"
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from office.gating import GatingPolicy, ToolCallRequest, Verdict
from office.tools import game_data_write

# game/ 하위 테스트 전용 서브디렉터리(실 data 오염 금지). game_data_write가
# repo_root/game을 __file__로 계산하므로, 동일 game/ 아래 격리 경로를 쓴다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_GAME_DIR = _REPO_ROOT / "game"
_TEST_SUBDIR = "__pytest_tmp_g5__"


@pytest.fixture()
def write_prefix() -> Any:
    """game/ 하위 테스트 전용 prefix를 주고, 끝나면 통째로 정리한다.

    game/ 자체가 테스트 중 새로 생겼고(US-G5-2 전) 비었으면 함께 제거 — 실 산출물
    디렉터리를 테스트가 남기지 않게 한다(베이스라인 데이터는 절대 건드리지 않음).
    """
    game_preexisted = _GAME_DIR.exists()
    yield _TEST_SUBDIR
    tmp_root = _GAME_DIR / _TEST_SUBDIR
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    # game/이 이 테스트로 새로 생겼고 비었으면 제거(실 data 미존재 상태 보존).
    if not game_preexisted and _GAME_DIR.exists():
        try:
            _GAME_DIR.rmdir()  # 비어있을 때만 성공
        except OSError:
            pass  # 다른 내용(베이스라인 등) 있으면 보존


# ── 1. 경로 화이트리스트 (AC2) ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_path",
    [
        "../etc/x",  # 상위 탈출
        "/etc/x",  # 절대경로
        "data/../../x",  # 정규화 후 game/ 밖
        "../../tmp/escape",  # 다중 상위 탈출
    ],
)
def test_whitelist_rejects_escape_paths(bad_path: str) -> None:
    """game/ 밖을 가리키는 경로는 written False·디스크 미접촉."""
    # 탈출 대상이 우연히 존재해 덮어쓰기 사고가 나지 않도록 사전 스냅샷.
    escape_target = (_GAME_DIR / bad_path).resolve()
    existed_before = escape_target.exists()
    bytes_before = escape_target.read_bytes() if existed_before else None

    result = game_data_write({"path": bad_path, "content": {"x": 1}})

    assert result["written"] is False
    assert result.get("error") == "path outside game/"
    # 디스크 미접촉: 새 파일 생성 0 / 기존 파일 내용 불변.
    if existed_before:
        assert escape_target.read_bytes() == bytes_before
    else:
        assert not escape_target.exists()


@pytest.mark.parametrize("self_ref", ["", ".", "data/.."])
def test_rejects_self_referential_paths(self_ref: str) -> None:
    """빈/자기참조 경로(`""`·`.`·`data/..`)는 거부 — game/ 자체를 파일로 덮어쓰지 않음(Major-1).

    이 입력들은 rel='.'로 귀결된다. 가드가 없으면 repo_root/game 이 regular file로
    생성돼 이후 game/ 디렉터리 생성이 영구 차단된다(US-G5-2 파괴). written False +
    game 이 파일이 아님(부재 또는 디렉터리)을 단언한다.
    """
    game_preexisted_as_dir = _GAME_DIR.is_dir()

    result = game_data_write({"path": self_ref, "content": {"x": 1}})

    assert result["written"] is False
    assert result.get("error") == "path must name a file under game/"
    # ★핵심: repo_root/game 이 regular file로 클로버되지 않았는지.
    assert not (_GAME_DIR.exists() and _GAME_DIR.is_file())
    # 원래 디렉터리였으면 디렉터리로 보존.
    if game_preexisted_as_dir:
        assert _GAME_DIR.is_dir()


# ── 2. 멱등 (AC3) ────────────────────────────────────────────────────────────


def test_idempotent_same_content_dedupes(write_prefix: str) -> None:
    """동일 content 2회 호출 → 1번째 written True, 2번째 deduplicated True·파일 1개."""
    rel = f"{write_prefix}/enemies.json"
    content = {"enemies": [{"id": "a", "hp": 10}], "z": "마지막"}

    first = game_data_write({"path": rel, "content": content})
    assert first["written"] is True
    assert first["deduplicated"] is False
    assert first["path"] == rel

    target = _GAME_DIR / rel
    assert target.exists()
    bytes_after_first = target.read_bytes()

    second = game_data_write({"path": rel, "content": content})
    assert second["written"] is False
    assert second["deduplicated"] is True
    assert second["bytes"] == first["bytes"]
    # 디스크 내용 불변(중복쓰기 0).
    assert target.read_bytes() == bytes_after_first


def test_changed_content_rewrites(write_prefix: str) -> None:
    """내용이 바뀌면 deduplicated False로 다시 쓴다."""
    rel = f"{write_prefix}/meta.json"
    game_data_write({"path": rel, "content": {"v": 1}})
    res2 = game_data_write({"path": rel, "content": {"v": 2}})
    assert res2["written"] is True
    assert res2["deduplicated"] is False
    assert (_GAME_DIR / rel).read_text(encoding="utf-8").find('"v": 2') >= 0


# ── 3. 정상 쓰기 (AC1) ───────────────────────────────────────────────────────


def test_write_dict_serializes_deterministically(write_prefix: str) -> None:
    """dict content는 결정론 JSON(sort_keys·indent=2·ensure_ascii=False)으로 직렬화된다."""
    rel = f"{write_prefix}/data/sub/balance.json"
    res = game_data_write({"path": rel, "content": {"b": 2, "a": 1, "한": "글"}})
    assert res["written"] is True
    text = (_GAME_DIR / rel).read_text(encoding="utf-8")
    # sort_keys → 'a'가 'b'보다 먼저, 한글 비-escape(ensure_ascii=False).
    assert text.index('"a"') < text.index('"b"')
    assert "글" in text
    # 부모 디렉터리 자동 생성(parents=True).
    assert (_GAME_DIR / rel).parent.is_dir()


def test_write_str_content_verbatim(write_prefix: str) -> None:
    """str content는 그대로 기록된다(직렬화 안 함)."""
    rel = f"{write_prefix}/note.txt"
    res = game_data_write({"path": rel, "content": "안녕 plain text"})
    assert res["written"] is True
    assert (_GAME_DIR / rel).read_text(encoding="utf-8") == "안녕 plain text"


def test_returned_path_is_relative_to_game(write_prefix: str) -> None:
    """반환 path는 game/ 기준 상대경로(이벤트 누출 안전 — 절대경로 아님)."""
    rel = f"{write_prefix}/upgrades.json"
    res = game_data_write({"path": rel, "content": {"upgrades": []}})
    assert res["path"] == rel
    assert not Path(res["path"]).is_absolute()


# ── 4. 게이팅 회귀 가드 (AC5) ────────────────────────────────────────────────


def test_gating_allows_game_data_write_from_policy() -> None:
    """실 tools_policy.yaml 로드 시 game_data_write evaluate → allow(suspend 0)."""
    policy = GatingPolicy()  # config/tools_policy.yaml 기본 로드
    assert policy.risk_of("game_data_write") == "always-allow"
    verdict = policy.evaluate(
        ToolCallRequest(
            name="game_data_write",
            args={"path": "data/enemies.json", "content": {}},
            node_id="n_write",
            run_id="r1",
        )
    )
    assert verdict == Verdict.allow


def test_gating_interrupts_when_unregistered(tmp_path: Path) -> None:
    """game_data_write를 뺀 정책(미등록)이면 evaluate → interrupt(suspend)."""
    # 실 정책을 읽어 game_data_write만 제거한 뒤 tmp yaml로 다시 로드.
    real = yaml.safe_load(
        (_REPO_ROOT / "config" / "tools_policy.yaml").read_text(encoding="utf-8")
    )
    real.get("tools", {}).pop("game_data_write", None)
    assert real.get("default_risk") == "needs-approval"  # 미등록→needs-approval 전제

    pol_path = tmp_path / "policy_no_gdw.yaml"
    pol_path.write_text(yaml.safe_dump(real), encoding="utf-8")

    policy = GatingPolicy(policy_path=pol_path)
    verdict = policy.evaluate(
        ToolCallRequest(
            name="game_data_write",
            args={"path": "data/enemies.json", "content": {}},
            node_id="n_write",
            run_id="r1",
        )
    )
    assert verdict == Verdict.interrupt
