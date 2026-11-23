"""orch.state_store ``set_task``/``get_task`` add-only 단위 (P5a A0·mock Redis·라이브 0).

orch env 은 pytest-asyncio 미설치 → async 는 ``asyncio.run`` 으로 구동
([[discord-adapter-test-hang-guard]] 패턴·test_state_store.py 동형).

검증: set→get 라운드트립·status queued→done RMW(handoff_ref/assignee/trigger 보존·
full 덮어쓰기 시 FAIL)·``aigs:`` 접두 유지·기존 메서드 byte 보존(import 회귀 0).
"""

import asyncio
from pathlib import Path

import orch.state_store as ss
from orch.tests.test_state_store import _FakeRedis


def test_set_get_task_roundtrip():
    """set_task→get_task 라운드트립(개별 키·JSON 직렬화)."""
    m = _FakeRedis()
    store = ss.StateStore(m, m)

    async def run():
        payload = {
            "status": "queued",
            "handoff_ref": "vault/specs/t1.md",
            "assignee": "Claude Code",
            "trigger": "impl",
        }
        await store.set_task("t1", payload)
        assert await store.get_task("t1") == payload
        # 미존재 task → None(graceful).
        assert await store.get_task("missing") is None

    asyncio.run(run())


def test_task_key_uses_aigs_namespace():
    """``aigs:task:<id>`` 키 — 신규 *최상위* 네임스페이스 0(aigs: 접두 유지)."""
    m = _FakeRedis()
    store = ss.StateStore(m, m)

    async def run():
        await store.set_task("t2", {"status": "queued"})
        keys = list(m.kv)
        assert keys == ["aigs:task:t2"]
        assert all(k.startswith("aigs:") for k in keys)

    asyncio.run(run())


def test_status_update_rmw_preserves_fields():
    """queued→done 갱신이 RMW 로 기존 필드(handoff_ref/assignee/trigger) 보존(LOW-1).

    get_task→status 만 변경→set_task. full 덮어쓰기(``{"status":"done"}`` 만 set)는
    handoff_ref 등을 잃으므로 *금지* — 이 테스트가 보존을 양성 입증한다.
    """
    m = _FakeRedis()
    store = ss.StateStore(m, m)

    async def run():
        original = {
            "status": "queued",
            "handoff_ref": "vault/specs/t3.md",
            "assignee": "Claude Code",
            "trigger": "impl",
        }
        await store.set_task("t3", original)
        # RMW: 복원→status 만 변경→set.
        cur = await store.get_task("t3")
        cur["status"] = "done"
        await store.set_task("t3", cur)
        got = await store.get_task("t3")
        assert got["status"] == "done"
        # ★보존 단언: full 덮어쓰기였다면 아래가 깨진다.
        assert got["handoff_ref"] == "vault/specs/t3.md"
        assert got["assignee"] == "Claude Code"
        assert got["trigger"] == "impl"

    asyncio.run(run())


def test_full_overwrite_loses_fields_negative_control():
    """음성 대조: full 덮어쓰기는 기존 필드를 실제로 잃는다(보존 단언이 공허하지 않음).

    RMW 가 *아닌* 경로(status 만 담은 dict 를 set)는 handoff_ref 를 소실 — 이를
    명시 입증해 위 보존 테스트가 green-theater 가 아님을 보장(부정변이 FAIL 경로).
    """
    m = _FakeRedis()
    store = ss.StateStore(m, m)

    async def run():
        await store.set_task(
            "t4", {"status": "queued", "handoff_ref": "vault/specs/t4.md"}
        )
        # full 덮어쓰기(잘못된 패턴) — 보존 안 됨.
        await store.set_task("t4", {"status": "done"})
        got = await store.get_task("t4")
        assert got == {"status": "done"}
        assert "handoff_ref" not in got

    asyncio.run(run())


def test_existing_methods_byte_preserved():
    """기존 메서드(push/peek/pop/status/bb/incr) 식별자가 소스에 그대로 — 회귀 0 가드."""
    src = Path(ss.__file__).read_text()
    for name in (
        "push_task",
        "peek_tasks",
        "pop_task",
        "set_status",
        "get_status",
        "bb_set",
        "bb_getall",
        "incr",
    ):
        assert f"def {name}" in src, f"기존 메서드 소실: {name}"
    # add-only 신규 2종 존재.
    assert "def set_task" in src and "def get_task" in src
