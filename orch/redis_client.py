"""orch/redis_client.py — Redis Sentinel async 클라이언트 (명세 §5.1·§24.4).

HYBRID 코어가 상태계층(Blackboard·task 큐·status)으로 Redis를 쓰기 위한 **접속 프리미티브**.
현재는 *잠금해제(unlock)*만: 클라이언트 + 검증. 그래프 코어 배선은 후속(P3-8) 범위.

설계:
- **Sentinel 경유만**(직접 IP 금지·명세 §5.1·verify_phase1.sh 원칙). `redis.asyncio.sentinel.Sentinel`
  → `master_for(name, db=<선택>, password=…)`. failover/재연결은 Sentinel 풀이 처리.
- **enable 플래그** `REDIS_ENABLED`(기본 false): 미설정 시 ``make_sentinel_client`` 는 접속 0·
  ``(None, False, "disabled")`` 반환(라이브 미배선 안전). 검증/테스트는 ``os.environ`` 으로 강제 true.
- ``.env`` 접속정보는 ``_load_env_local`` 로 로드(``os.environ`` 가 파일을 덮어씀 — 테스트 주입 가능).
  **비밀값은 로그/예외 메시지에 평문 0**(``err`` 는 예외 *카테고리명*만).

공개 심볼(검증 테스트 계약): ``_load_env_local`` · ``_parse_sentinel_hosts`` ·
``make_sentinel_client`` (async ``(client, ok, err)``) · ``redis_healthcheck`` (async bool).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_TRUE = {"1", "true", "yes", "on"}


def _load_env_local(path: Path | None = None) -> dict[str, str]:
    """``.env`` 의 ``KEY=VALUE`` 파싱 후 ``os.environ`` 의 ``REDIS_*`` 로 오버레이(환경이 우선)."""
    env: dict[str, str] = {}
    p = path or _ENV_PATH
    if p.exists():
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key:
                env[key] = val.strip().strip('"').strip("'")
    for key, val in os.environ.items():  # 환경이 파일을 덮어씀(테스트 주입)
        if key.startswith("REDIS_"):
            env[key] = val
    return env


def _parse_sentinel_hosts(spec: str) -> list[tuple[str, int]]:
    """``host:port,host:port`` → ``[(host, port), …]``."""
    out: list[tuple[str, int]] = []
    for hp in spec.split(","):
        hp = hp.strip()
        if ":" in hp:
            host, _, port = hp.rpartition(":")
            try:
                out.append((host, int(port)))
            except ValueError:
                continue
    return out


def _enabled(env: dict[str, str]) -> bool:
    return str(env.get("REDIS_ENABLED", "false")).lower() in _TRUE


async def make_sentinel_client() -> tuple[Any, bool, str | None]:
    """Sentinel master 클라이언트 생성 + ping 확인 → ``(client, ok, err)``.

    ``REDIS_ENABLED`` 미설정 시 접속 없이 ``(None, False, "disabled")``. 접속/설정 실패 시
    ``(None, False, <예외 카테고리명>)`` — 비밀값은 절대 포함하지 않음. ``ok=True`` 면
    ``client`` 는 ping 확인된 라이브 master(호출측이 ``aclose()`` 로 정리).
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
        client = sentinel.master_for(
            name,
            password=pw,
            db=db,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
        )
        if not await client.ping():  # 실제 연결 강제(lazy master_for 확정)
            await _aclose(client)
            return (None, False, "ping_failed")
        return (client, True, None)
    except Exception as exc:  # 카테고리명만 — 비밀 0
        return (None, False, type(exc).__name__)


async def redis_healthcheck(client: Any) -> bool:
    """``client.ping()`` 헬스체크. ``None``/실패 시 False(비밀 0)."""
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def _aclose(client: Any) -> None:
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        await aclose()
    else:  # pragma: no cover - 구버전 redis-py
        await client.close()
