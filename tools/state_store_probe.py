#!/usr/bin/env python3
"""S5 — orch.state_store 라이브 R/W 왕복 프로브 (aigs:* namespace · db 격리 · FLUSH 0).

REDIS_ENABLED 강제 true(프로브 한정). master 쓰기·replica 읽기 왕복(task/status/blackboard)
→ 임시키 즉시 삭제(EXISTS로 정리 확인·KEYS 스캔/FLUSH 없음). 비밀 출력 0.
종료코드 0=PASS·1=FAIL·2=BLOCKED."""

import asyncio
import os
import sys


async def main():
    os.environ["REDIS_ENABLED"] = "true"
    from orch.state_store import _NS, make_state_store

    store, ok, err = await make_state_store()
    if not ok:
        print(f"BLOCKED: make_state_store ok={ok} err={err}")
        sys.exit(2)
    print("sentinel master+replica ok (ping confirmed)")
    await store.push_task("_probe", {"id": 1, "kind": "test"})
    tasks = await store.peek_tasks("_probe", 5)
    await store.set_status("_probe_agent", {"state": "Idle"})
    st = await store.get_status("_probe_agent")
    await store.bb_set("_probe_proj", "fact", {"k": "v"})
    bb = await store.bb_getall("_probe_proj")
    print(f"task roundtrip:      {tasks}")
    print(f"status roundtrip:    {st}")
    print(f"blackboard roundtrip:{bb}")
    m = store._m
    keys = [
        f"{_NS}task:_probe",
        f"{_NS}status:_probe_agent",
        f"{_NS}blackboard:_probe_proj",
    ]
    for k in keys:
        await m.delete(k)
    leftover = sum([await m.exists(k) for k in keys])
    print(f"leftover probe keys after cleanup: {leftover} (FLUSH 미사용·aigs:* 한정)")
    await store.aclose()
    ok_all = (
        tasks == [{"id": 1, "kind": "test"}]
        and st == {"state": "Idle"}
        and bb == {"fact": {"k": "v"}}
        and leftover == 0
    )
    print(
        "RESULT: ✔ R/W roundtrip PASS (master write·replica read)"
        if ok_all
        else "RESULT: FAIL"
    )
    sys.exit(0 if ok_all else 1)


asyncio.run(main())
