"""Redis Sentinel 연결성 검증 — 프로젝트의 실제 클라이언트(`orch.redis_client`) 사용.

``orch.redis_client.make_sentinel_client()`` 를 ``.env`` 의 Sentinel 접속정보로 실행한다.
연결성 테스트는 테스트 프로세스 한정으로 ``CHAT_AZ_REDIS_ENABLED`` 대신 ``REDIS_ENABLED=true``
를 강제(영속 플래그와 무관하게 도달성 검증). 비밀값은 절대 출력하지 않으며, 테스트 키는
네임스페이스(``orch:test:redis``) + 사용 후 삭제(선택된 db=``REDIS_DB``).

★이 모듈은 **라이브**(실 Redis 도달 필요)라 ``slow`` 마커로 결정론 suite(`-m 'not slow'`)서 제외.
Redis 미도달 시 각 테스트는 ``skip``(실패 아님). orch 테스트 관례대로 async는 ``asyncio.run``.

실행(라이브):
    PYTHONPATH=/opt/studio REDIS_ENABLED=true \\
      /opt/conda/envs/orch/bin/python -m pytest tests/test_redis.py -v -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytest.importorskip("redis")

from orch.redis_client import (  # noqa: E402 — importorskip 가드 이후
    _aclose,
    _load_env_local,
    _parse_sentinel_hosts,
    make_sentinel_client,
    redis_healthcheck,
)

pytestmark = pytest.mark.slow

_NS = "orch:test:redis"
# 소유자 확인 후 삭제(CAS): 우리가 여전히 소유할 때만 삭제.
_LUA_CAS_DEL = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _enable_redis():
    """테스트 프로세스 한정 ``REDIS_ENABLED=true``(영속 플래그 무관 도달성 검증)."""
    prev = os.environ.get("REDIS_ENABLED")
    os.environ["REDIS_ENABLED"] = "true"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("REDIS_ENABLED", None)
        else:
            os.environ["REDIS_ENABLED"] = prev


async def _connect():
    client, ok, err = await make_sentinel_client()
    if not ok:
        pytest.skip(f"Redis 미도달(카테고리={err}) — 라이브 검증서만 실행")
    return client


def test_env_local_declares_sentinel_targets():
    """``.env`` 가 클라이언트가 읽는 키를 선언(값 출력 0)."""
    env = _load_env_local()
    hosts = _parse_sentinel_hosts(env.get("REDIS_SENTINEL_HOSTS", ""))
    assert hosts, "REDIS_SENTINEL_HOSTS 미설정/무효"
    assert env.get("REDIS_MASTER_NAME"), "REDIS_MASTER_NAME 미설정"


def test_master_ping():
    async def _t():
        client = await _connect()
        try:
            assert await redis_healthcheck(client) is True
        finally:
            await _aclose(client)

    _run(_t())


def test_string_roundtrip():
    async def _t():
        client = await _connect()
        key = f"{_NS}:str:{uuid.uuid4().hex}"
        try:
            assert await client.set(key, "ok", ex=60) is True
            assert await client.get(key) == b"ok"
        finally:
            await client.delete(key)
            await _aclose(client)

    _run(_t())


def test_pubsub_loopback():
    async def _t():
        client = await _connect()
        channel = f"{_NS}:pubsub:{uuid.uuid4().hex}"
        message = f"msg-{uuid.uuid4().hex}"
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(channel)
            await asyncio.sleep(0.2)  # 구독 등록 대기
            assert await client.publish(channel, message) >= 0
            received = None
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                item = await pubsub.get_message(timeout=0.5)
                if item and item.get("type") == "message":
                    received = item.get("data")
                    break
            assert received == message.encode()
        finally:
            await pubsub.unsubscribe(channel)
            aclose = getattr(pubsub, "aclose", None)
            if aclose is not None:
                await aclose()
            else:  # pragma: no cover - 구버전
                await pubsub.close()
            await _aclose(client)

    _run(_t())


def test_setnx_lock_owner_release():
    """Redis SETNX 잠금 프리미티브 — 분산 잠금 백엔드 능력 증명(읽기 전용 코드와 별개)."""

    async def _t():
        client = await _connect()
        key = f"{_NS}:lock:{uuid.uuid4().hex}"
        try:
            assert await client.set(key, "owner-a", nx=True, ex=60) is True
            # 보유 중인 잠금은 2번째 소유자가 취득 불가.
            assert await client.set(key, "owner-b", nx=True, ex=60) is None
            assert await client.get(key) == b"owner-a"
            released = await client.eval(_LUA_CAS_DEL, 1, key, "owner-a")
            assert int(released) == 1
            assert await client.get(key) is None
        finally:
            await client.delete(key)
            await _aclose(client)

    _run(_t())
