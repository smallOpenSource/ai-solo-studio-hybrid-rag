"""orch/redis_client.py + tools/redis_db_probe.py 결정론 단위(네트워크 0·mock Sentinel).

orch 테스트 관례: async는 ``asyncio.run``(pytest-asyncio 미사용). 라이브 R/W 는 별도
``tests/test_redis.py``(slow). 여기선 순수 로직·계약·보안(비밀 미로그·읽기전용·atomic)만.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import orch.redis_client as rc
from tools.redis_db_probe import _atomic_set_redis_db, decide_db

_SECRET = "SECRET-PW-XYZ"  # mock 전용(실 비밀 아님)
_ENV_OK = {
    "REDIS_ENABLED": "true",
    "REDIS_SENTINEL_HOSTS": "h1:3410,h2:3410,h3:3410",
    "REDIS_MASTER_NAME": "redismaster",
    "REDIS_PASSWORD": _SECRET,
    "REDIS_DB": "7",
    "REDIS_SOCKET_CONNECT_TIMEOUT": "5",
}


def _run(coro):
    return asyncio.run(coro)


class _FakeMaster:
    def __init__(self, ping=True, raise_ping=False):
        self._ping, self._raise, self.closed = ping, raise_ping, False

    async def ping(self):
        if self._raise:
            raise ConnectionError("boom")
        return self._ping

    async def aclose(self):
        self.closed = True


def _patch(monkeypatch, env, master):
    monkeypatch.setattr(rc, "_load_env_local", lambda *a, **k: dict(env))

    class _FakeSentinel:
        def __init__(self, *a, **k):
            pass

        def master_for(self, name, **k):
            return master

    import redis.asyncio.sentinel as sm

    monkeypatch.setattr(sm, "Sentinel", _FakeSentinel)


# ── 파싱/로더 ──────────────────────────────────────────────────────────
def test_parse_sentinel_hosts():
    hosts = rc._parse_sentinel_hosts("a:1, b:2 ,c:3")
    assert hosts == [("a", 1), ("b", 2), ("c", 3)]
    assert rc._parse_sentinel_hosts("") == []
    assert rc._parse_sentinel_hosts("bad,x:9") == [("x", 9)]  # 잘못된 항목 스킵


def test_load_env_local_env_overlay(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('REDIS_DB=7\nREDIS_MASTER_NAME="redismaster"\nOTHER=keep\n')
    monkeypatch.setenv("REDIS_DB", "9")  # 환경이 파일을 덮어씀
    env = rc._load_env_local(f)
    assert env["REDIS_DB"] == "9"
    assert env["REDIS_MASTER_NAME"] == "redismaster"  # 따옴표 제거
    assert env["OTHER"] == "keep"


# ── make_sentinel_client ────────────────────────────────────────────────
def test_make_sentinel_client_disabled(monkeypatch):
    monkeypatch.setattr(
        rc, "_load_env_local", lambda *a, **k: {"REDIS_ENABLED": "false"}
    )
    client, ok, err = _run(rc.make_sentinel_client())
    assert (client, ok, err) == (None, False, "disabled")  # 네트워크 0


def test_make_sentinel_client_success(monkeypatch):
    master = _FakeMaster(ping=True)
    _patch(monkeypatch, _ENV_OK, master)
    client, ok, err = _run(rc.make_sentinel_client())
    assert ok is True and err is None and client is master


def test_make_sentinel_client_ping_false(monkeypatch):
    master = _FakeMaster(ping=False)
    _patch(monkeypatch, _ENV_OK, master)
    client, ok, err = _run(rc.make_sentinel_client())
    assert (client, ok, err) == (None, False, "ping_failed")
    assert master.closed is True  # 실패 시 정리


def test_make_sentinel_client_error_has_no_secret(monkeypatch):
    master = _FakeMaster(raise_ping=True)
    _patch(monkeypatch, _ENV_OK, master)
    client, ok, err = _run(rc.make_sentinel_client())
    assert client is None and ok is False
    assert err == "ConnectionError"  # 카테고리명만
    assert _SECRET not in (err or "")  # 비밀 미노출


def test_make_sentinel_client_config_missing(monkeypatch):
    env = dict(_ENV_OK, REDIS_SENTINEL_HOSTS="")
    _patch(monkeypatch, env, _FakeMaster())
    client, ok, err = _run(rc.make_sentinel_client())
    assert (client, ok, err) == (None, False, "config_missing")


def test_redis_healthcheck():
    assert _run(rc.redis_healthcheck(None)) is False
    assert _run(rc.redis_healthcheck(_FakeMaster(ping=True))) is True
    assert _run(rc.redis_healthcheck(_FakeMaster(raise_ping=True))) is False


# ── probe: decide_db (순수·db 클램프·전부 비-빈) ─────────────────────────
def test_decide_db_configured_empty():
    assert decide_db(7, 16, {i: 0 for i in range(16)}) == (7, False)


def test_decide_db_configured_nonempty_picks_first_empty():
    sizes = {i: 5 for i in range(16)}
    sizes[7] = 3
    sizes[2] = 0  # 첫 빈
    assert decide_db(7, 16, sizes) == (2, True)


def test_decide_db_out_of_range_clamped():
    sizes = {i: 1 for i in range(16)}
    sizes[1] = 0
    assert decide_db(20, 16, sizes) == (1, True)  # 설정 20(범위 밖) → 첫 빈


def test_decide_db_all_nonempty_returns_none():
    assert decide_db(7, 16, {i: 1 for i in range(16)}) is None  # clobber 금지


# ── probe: atomic .env 갱신(1줄·시크릿 무변) ────────────────────────────
def test_atomic_set_redis_db_only_one_line(tmp_path):
    f = tmp_path / ".env"
    original = (
        "# comment\n"
        'REDIS_PASSWORD="fake-test-pw"\n'
        "REDIS_DB=7\n"
        "REDIS_MASTER_NAME=redismaster\n"
    )
    f.write_text(original)
    _atomic_set_redis_db(f, 3)
    after = f.read_text()
    assert "REDIS_DB=3\n" in after
    assert "REDIS_DB=7" not in after
    # 다른 줄(시크릿 포함) byte 무변.
    assert "# comment\n" in after
    assert 'REDIS_PASSWORD="fake-test-pw"\n' in after
    assert "REDIS_MASTER_NAME=redismaster\n" in after
    # 정확히 한 줄만 변경(REDIS_DB 값).
    assert after == original.replace("REDIS_DB=7", "REDIS_DB=3")


# ── 읽기 전용·FLUSH 부재(파괴/쓰기 금지 소스 단언) ──────────────────────
def test_probe_is_read_only():
    src = (
        Path(rc.__file__).resolve().parent.parent / "tools" / "redis_db_probe.py"
    ).read_text()
    forbidden = re.compile(
        r"FLUSHDB|FLUSHALL|\.flushdb|\.flushall|\.set\(|\.delete\(|\.lpush|\.hset"
    )
    # 문서/주석 라인 제외(코드 라인만).
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    # docstring 블록(삼중따옴표) 제거.
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert not forbidden.search(code), "probe에 쓰기/파괴 Redis 명령 발견"


def test_redis_client_no_destructive_ops():
    src = Path(rc.__file__).read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"FLUSHDB|FLUSHALL|\.flushdb|\.flushall", code)
