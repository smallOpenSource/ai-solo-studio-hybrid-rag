"""tools/a2_redis_probe.py — A2 라이브 Redis Sentinel async 소비자 실증.

``orch/state_store.make_state_store()``(async Sentinel master/replica·§5.1 첫 소비자)가
라이브 Owner Sentinel 에 실제 연결되는지 + ``aigs:*`` R/W 왕복을 실측한다.

★시크릿 0: 호스트·비밀번호는 출력하지 않는다(``err`` = 예외 *카테고리명*만). ★잔여 0:
probe 키(``aigs:task:_a2_probe``)를 즉시 삭제하고 삭제를 재확인한다. ``aigs:*`` 만 침해.

env 로딩은 orch 와 동형(``.env`` + ``os.environ`` overlay). ``REDIS_ENABLED`` 가 env 에
없으면 ``disabled`` 를 반환(=현 라이브 orch 와 동일) → ``REDIS_ENABLED=true`` 를 환경으로
주면 연결을 시도한다. 이로써 "flag 만이 누락분"임을 격리 실증한다.

실행(safe_run 래핑 권장·바운드):
    REDIS_ENABLED=true /opt/conda/envs/orch/bin/python tools/a2_redis_probe.py
"""

from __future__ import annotations

import asyncio
import json

_KEY = "_a2_probe"
_FULL = f"aigs:task:{_KEY}"
_MARKER = "a2-roundtrip-marker"


async def _main() -> int:
    from orch.state_store import make_state_store

    store, ok, err = await make_state_store()
    if not ok:
        # disabled/연결실패도 정직 보고 — 프로브 자체는 정상 종료(0). green-fake 0.
        print(f"A2_PROBE ok=False err={err}")
        return 0
    roundtrip = False
    residue = -1
    try:
        await store.set_task(_KEY, {"probe": "a2", "marker": _MARKER})  # master write
        raw = await store._m.get(_FULL)  # master read(결정론·replica lag 회피)
        roundtrip = bool(raw) and json.loads(raw).get("marker") == _MARKER
        await store._m.delete(_FULL)  # 정리
        residue = 0 if (await store._m.get(_FULL)) is None else 1
    finally:
        await store.aclose()
    print(f"A2_PROBE ok=True roundtrip={roundtrip} residue={residue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
