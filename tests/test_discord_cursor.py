"""tests/test_discord_cursor.py — 멱등 커서 모듈 단위 테스트.

실제 state/ 디렉터리를 건드리지 않는다(tmp_path 픽스처 사용).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from adapters.discord.cursor import (
    advance_cursor,
    cursor_initialized,
    read_cursor,
    should_post,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def cursor_path(tmp_path: Path) -> Path:
    """각 테스트에서 사용할 격리된 cursor.json 경로."""
    return tmp_path / "state" / "discord" / "cursor.json"


# ---------------------------------------------------------------------------
# 기본 동작
# ---------------------------------------------------------------------------


def test_read_cursor_returns_0_when_file_absent(tmp_path: Path) -> None:
    """파일이 없으면 read_cursor()는 0을 반환해야 한다."""
    p = cursor_path(tmp_path)
    assert read_cursor(p) == 0


def test_advance_then_read(tmp_path: Path) -> None:
    """advance_cursor(5) 후 read_cursor()는 5를 반환해야 한다."""
    p = cursor_path(tmp_path)
    advance_cursor(5, p)
    assert read_cursor(p) == 5


def test_should_post_false_for_already_posted(tmp_path: Path) -> None:
    """이미 게시한 seq(5)는 should_post(5)가 False여야 한다."""
    p = cursor_path(tmp_path)
    advance_cursor(5, p)
    assert should_post(5, p) is False


def test_should_post_true_for_next_seq(tmp_path: Path) -> None:
    """커서(5) 이후 seq(6)는 should_post(6)가 True여야 한다."""
    p = cursor_path(tmp_path)
    advance_cursor(5, p)
    assert should_post(6, p) is True


# ---------------------------------------------------------------------------
# 원자성 — temp→rename(os.replace) 확인
# ---------------------------------------------------------------------------


def test_atomic_write_uses_os_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """advance_cursor가 os.replace를 사용하는지 확인한다(쓰다 만 파일 안 남음)."""

    replace_calls: list[tuple[str, str]] = []
    original_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:  # type: ignore[override]
        replace_calls.append((src, dst))
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    p = cursor_path(tmp_path)
    advance_cursor(7, p)

    # os.replace가 정확히 1회 호출되어야 한다
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    # 목적지가 cursor.json이어야 한다
    assert Path(dst) == p
    # temp 파일(src)은 rename 후 남아 있으면 안 된다
    assert not Path(src).exists(), "temp 파일이 rename 후에도 남아 있다"


def test_no_partial_file_on_write(tmp_path: Path) -> None:
    """advance_cursor 완료 후 cursor.json만 존재하고 .tmp 파일은 없어야 한다."""
    p = cursor_path(tmp_path)
    advance_cursor(3, p)

    leftovers = list(p.parent.glob("*.tmp"))
    assert leftovers == [], f"temp 파일이 남아 있다: {leftovers}"


# ---------------------------------------------------------------------------
# 재시작 시뮬레이션 — seq 중복 방지
# ---------------------------------------------------------------------------


def test_restart_simulation(tmp_path: Path) -> None:
    """advance(10) 후 새 read_cursor() 호출 시 10을 반환하고, seq 5~10 전부 should_post=False."""
    p = cursor_path(tmp_path)
    advance_cursor(10, p)

    # 프로세스 재시작 시뮬: 새로 read
    assert read_cursor(p) == 10

    for seq in range(5, 11):  # 5, 6, 7, 8, 9, 10
        assert should_post(seq, p) is False, f"seq={seq}가 중복 게시될 수 있다"


def test_restart_allows_new_seq(tmp_path: Path) -> None:
    """재시작 후 새 seq(11)은 should_post=True여야 한다."""
    p = cursor_path(tmp_path)
    advance_cursor(10, p)
    assert should_post(11, p) is True


# ---------------------------------------------------------------------------
# 게시-후-전진 순서 시나리오
# ---------------------------------------------------------------------------


def test_post_then_advance_order(tmp_path: Path) -> None:
    """should_post → (게시) → advance_cursor 순서 시나리오.

    at-least-once 정책:
    - advance_cursor()는 게시 성공 **후** 호출해야 한다.
    - 이 순서를 지키면 재시작 시 중복 최대 1건, 누락 0건이 보장된다.
    """
    p = cursor_path(tmp_path)

    # 초기 상태: seq=1 게시 가능
    seq = 1
    assert should_post(seq, p) is True

    # 게시 성공(시뮬) → 커서 전진
    advance_cursor(seq, p)

    # 전진 후: seq=1은 더 이상 게시 안 함
    assert should_post(seq, p) is False

    # 다음 seq=2는 게시 가능
    assert should_post(seq + 1, p) is True


def test_advance_creates_parent_dirs(tmp_path: Path) -> None:
    """부모 디렉터리가 없어도 advance_cursor가 자동 생성해야 한다."""
    p = tmp_path / "nested" / "deep" / "cursor.json"
    advance_cursor(1, p)
    assert p.exists()
    assert read_cursor(p) == 1


def test_cursor_json_content(tmp_path: Path) -> None:
    """cursor.json의 내용이 올바른 JSON 스키마여야 한다."""
    p = cursor_path(tmp_path)
    advance_cursor(42, p)

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"last_posted_seq": 42}


# ---------------------------------------------------------------------------
# cursor_initialized — '파일 부재' vs '정당히 0' 구분 (콜드스타트 재방송 방지)
# ---------------------------------------------------------------------------


def test_cursor_initialized_false_when_absent(tmp_path: Path) -> None:
    """파일이 없으면 cursor_initialized()는 False (콜드스타트 → head 시드 신호)."""
    p = cursor_path(tmp_path)
    assert cursor_initialized(p) is False


def test_cursor_initialized_true_when_zero(tmp_path: Path) -> None:
    """파일이 존재하고 0이면 True — '정당한 0'은 resume(부재와 구분).

    read_cursor()는 부재와 0을 모두 0으로 뭉개지만 cursor_initialized는 구분한다.
    """
    p = cursor_path(tmp_path)
    advance_cursor(0, p)
    assert read_cursor(p) == 0
    assert cursor_initialized(p) is True


def test_cursor_initialized_true_when_positive(tmp_path: Path) -> None:
    """파일이 존재하고 양수면 True."""
    p = cursor_path(tmp_path)
    advance_cursor(774, p)
    assert cursor_initialized(p) is True


def test_cursor_initialized_false_when_corrupt(tmp_path: Path) -> None:
    """깨진 JSON / 키 누락이면 False(안전하게 콜드스타트로 간주)."""
    p = cursor_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json", encoding="utf-8")
    assert cursor_initialized(p) is False
    p.write_text(json.dumps({"other_key": 1}), encoding="utf-8")
    assert cursor_initialized(p) is False
