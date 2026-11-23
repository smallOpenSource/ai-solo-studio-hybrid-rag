"""orch/state_store.py — AIGS 상태계층 (Redis Sentinel · §5.1 · §27.3-5).

`redis_client` 의 env/sentinel 프리미티브를 재사용해 **상태계층의 첫 실 소비자**가 된다
(§5.1 dormant 해제). 전용 namespace ``aigs:*`` 로 task 큐(§27.3)·agent status(§27.4)·
blackboard(§27.5)를 저장한다. **master 쓰기·replica 읽기**(Sentinel `slave_for` 는 replica
부재 시 master 폴백). `REDIS_ENABLED`(기본 false) 게이트 — 미설정 시 비활성.

★데이터 안전: ``aigs:*`` 키만 다룬다(타 키스페이스 무침해). FLUSHDB/FLUSHALL 없음·직접 IP 없음
(Sentinel 발견). db = ``.env REDIS_DB``(빈 logical db·공유 인프라 안전).
비밀값은 로그/예외에 평문 0(``err`` = 예외 카테고리명).
"""

from __future__ import annotations

import json
from typing import Any

from orch.redis_client import (
    _enabled,
    _load_env_local,
    _parse_sentinel_hosts,
)

_NS = "aigs:"  # 전용 namespace 접두 (타 키스페이스 불침해)


class StateStore:
    """``aigs:*`` 상태계층 — master 쓰기·replica 읽기. 값은 JSON 직렬화."""

    def __init__(self, master: Any, replica: Any) -> None:
        self._m = master
        self._r = replica

    # ── task 큐 (§27.3) ───────────────────────────────────────────────
    async def push_task(self, queue: str, item: dict[str, Any]) -> None:
        await self._m.lpush(f"{_NS}task:{queue}", json.dumps(item))

    async def peek_tasks(self, queue: str, n: int = 10) -> list[dict[str, Any]]:
        raw = await self._r.lrange(f"{_NS}task:{queue}", 0, n - 1)
        return [json.loads(x) for x in raw]

    async def pop_task(self, queue: str) -> dict[str, Any] | None:
        raw = await self._m.rpop(f"{_NS}task:{queue}")
        return json.loads(raw) if raw else None

    # ── task 개별 키 (§27.3 ``aigs:task:<task_id>`` 상태레코드) ─────────
    # push_task/peek_tasks/pop_task = List 큐(``aigs:task:<queue>``)이고, 아래 두
    # 메서드는 *개별 task 의 상태 레코드*(``aigs:task:<task_id>``)다 — §27.3 의
    # "status: queued|in_progress|...|done · handoff_ref" 갱신은 List 연산만으론
    # 불가하므로 add-only 로 추가한다(기존 메서드 0수정·set_status/get_status 동형).
    async def set_task(self, task_id: str, payload: dict[str, Any]) -> None:
        await self._m.set(f"{_NS}task:{task_id}", json.dumps(payload))

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        raw = await self._r.get(f"{_NS}task:{task_id}")
        return json.loads(raw) if raw else None

    # ── agent status (§27.4) ──────────────────────────────────────────
    async def set_status(self, agent_id: str, status: dict[str, Any]) -> None:
        await self._m.set(f"{_NS}status:{agent_id}", json.dumps(status))

    async def get_status(self, agent_id: str) -> dict[str, Any] | None:
        raw = await self._r.get(f"{_NS}status:{agent_id}")
        return json.loads(raw) if raw else None

    # ── blackboard (§27.5) ────────────────────────────────────────────
    async def bb_set(self, proj: str, field: str, value: Any) -> None:
        await self._m.hset(f"{_NS}blackboard:{proj}", field, json.dumps(value))

    async def bb_getall(self, proj: str) -> dict[str, Any]:
        raw = await self._r.hgetall(f"{_NS}blackboard:{proj}")
        return {k: json.loads(v) for k, v in raw.items()}

    # ── 단조 카운터 (§27.2 ADR 번호) ─────────────────────────────────
    async def incr(self, key: str) -> int:
        """단조 카운터(ADR 번호 등). ``aigs:*`` 네임스페이스·**master 쓰기**.

        호출측은 논리키(예 ``"adr:seq"``)만 넘긴다 → 실제 키 ``aigs:adr:seq``.
        Redis ``INCR`` 은 원자적 → N=2 동시 완료(cycle+org)에서도 같은 번호 충돌 0
        (§27.2 ADR-NNNN 직렬화·LOW-6). 기타 메서드(push_task/bb_set 등)는 무수정.
        """
        return int(await self._m.incr(f"{_NS}{key}"))

    # ── 정리 ──────────────────────────────────────────────────────────
    async def aclose(self) -> None:
        for c in (self._m, self._r):
            ac = getattr(c, "aclose", None)
            if ac is not None:
                await ac()


async def make_state_store() -> tuple[StateStore | None, bool, str | None]:
    """Sentinel master+replica로 ``StateStore`` 생성 → ``(store, ok, err)``.

    ``REDIS_ENABLED`` 미설정 시 ``(None, False, "disabled")`` (접속 0). 실패 시
    ``(None, False, <예외 카테고리명>)`` — 비밀 0. ``ok=True`` 면 master ping 확인됨.
    """
    env = _load_env_local()
    if not _enabled(env):
        return (None, False, "disabled")
    try:
        from redis.asyncio.sentinel import Sentinel

        hosts = _parse_sentinel_hosts(env.get("REDIS_SENTINEL_HOSTS", ""))
        name = env.get("REDIS_MASTER_NAME", "")
        if not hosts or not name:
            return (None, False, "config_missing")
        pw = env.get("REDIS_PASSWORD") or None
        db = int(env.get("REDIS_DB", "0") or 0)
        timeout = float(env.get("REDIS_SOCKET_CONNECT_TIMEOUT", "30") or 30)
        skw = {"password": pw} if pw else {}
        sentinel = Sentinel(hosts, sentinel_kwargs=skw, socket_timeout=timeout)
        common = {
            "password": pw,
            "db": db,
            "socket_timeout": timeout,
            "socket_connect_timeout": timeout,
            "decode_responses": True,
        }
        master = sentinel.master_for(name, **common)
        replica = sentinel.slave_for(name, **common)
        if not await master.ping():
            return (None, False, "ping_failed")
        return (StateStore(master, replica), True, None)
    except Exception as exc:  # 카테고리명만 — 비밀 0
        return (None, False, type(exc).__name__)
