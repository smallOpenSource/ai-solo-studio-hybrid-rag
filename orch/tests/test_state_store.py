"""orch.state_store 결정론 단위 (mock Redis·라이브 0).

orch env은 pytest-asyncio 미설치 → async는 ``asyncio.run`` 으로 구동([[discord-adapter-test-hang-guard]]
패턴). 라이브 R/W는 tools/state_store_probe.py(별도·실 Sentinel)가 담당."""

import asyncio
import re
from pathlib import Path

import orch.state_store as ss


class _FakeRedis:
    """in-memory Redis mock (master==replica·단일노드 폴백 의미)."""

    def __init__(self) -> None:
        self.kv: dict = {}
        self.lists: dict = {}
        self.hashes: dict = {}

    async def set(self, k, v):
        self.kv[k] = v

    async def get(self, k):
        return self.kv.get(k)

    async def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    async def lrange(self, k, a, b):
        lst = self.lists.get(k, [])
        return lst[a : (b + 1 if b >= 0 else None)]

    async def rpop(self, k):
        lst = self.lists.get(k)
        return lst.pop() if lst else None

    async def hset(self, k, f, v):
        self.hashes.setdefault(k, {})[f] = v

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def delete(self, k):
        self.kv.pop(k, None)
        self.lists.pop(k, None)
        self.hashes.pop(k, None)

    async def exists(self, k):
        return int(k in self.kv or k in self.lists or k in self.hashes)

    async def ping(self):
        return True

    async def aclose(self):
        pass


def test_disabled_when_flag_off(monkeypatch):
    """REDIS_ENABLED 미설정 → 접속 0·(None, False, 'disabled')."""
    monkeypatch.setattr(ss, "_load_env_local", lambda: {"REDIS_ENABLED": "false"})
    assert asyncio.run(ss.make_state_store()) == (None, False, "disabled")


def test_namespace_and_roundtrip():
    """task/status/blackboard 왕복 + 모든 키가 aigs:* namespace."""
    m = _FakeRedis()
    store = ss.StateStore(m, m)  # 단일노드: replica=master 폴백

    async def run():
        await store.push_task("q", {"a": 1})
        assert await store.peek_tasks("q") == [{"a": 1}]
        await store.set_status("agent1", {"s": "Idle"})
        assert await store.get_status("agent1") == {"s": "Idle"}
        await store.bb_set("proj", "f", {"v": 2})
        assert await store.bb_getall("proj") == {"f": {"v": 2}}
        keys = list(m.kv) + list(m.lists) + list(m.hashes)
        assert keys and all(k.startswith("aigs:") for k in keys)

    asyncio.run(run())


def test_no_flush_in_source():
    """소스에 FLUSHDB/FLUSHALL 0 (타 데이터 안전)."""
    src = Path(ss.__file__).read_text().lower()
    # 메서드 호출 형태(.flushdb/.flushall)만 검사 — docstring의 "FLUSHDB 없음" 멘션 오탐 방지
    assert ".flushdb" not in src and ".flushall" not in src


def test_no_hardcoded_secret_in_source():
    """비밀은 .env 런타임 로드만 — 하드코딩 비번 리터럴 0(키 이름 참조는 허용)."""
    src = Path(ss.__file__).read_text()
    assert "REDIS_PASSWORD" in src
    assert not re.search(r'password\s*=\s*["\'][^"\']{6,}["\']', src)
