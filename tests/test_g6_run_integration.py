"""US-G6-1 — ★실 enqueue 통합(엔진 env 전용·POST /api/run → TaskQueue 행 확증).

rest_client.post_run 이 실 web_server.create_app(auth_required=True) 의 /api/run 게이트를
세션 쿠키로 통과해 워크플로를 **실제로 enqueue** 함을 확증한다(contract-drift 0). enqueue
증거 = 같은 office.db 의 TaskQueue.list_by_status("queued") 에 workflow 행이 존재.

수집 범위: 엔진 env(fastapi 有)에서만 — fastapi 없으면 importorskip 으로 skip.
파일명이 test_discord_* 글롭에 비매칭이라 discord 러너는 수집하지 않는다(engine 러너만).
실서버 미접촉: in-process httpx.ASGITransport 로 create_app 을 직접 호출(네트워크 0).
모든 async 테스트는 asyncio.wait_for 로 바운드(hang 0).
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")  # 엔진 env 전용(discord env 는 skip — fastapi 부재).

import httpx  # noqa: E402

from adapters.discord import auth_client, rest_client  # noqa: E402

# 임의 테스트 암호(monkeypatch 주입 — 실 .env LOGIN_PASSWORD 미사용·코드 하드코딩 아님).
_PW = "testpw_g6run_321!"
_COOKIE = "omc_session"
_BOUND = 5.0

# 진짜 AsyncClient 를 모듈 로드 시점에 캡처(monkeypatch 이후에도 안전).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _route_to_app(monkeypatch: pytest.MonkeyPatch, module, app) -> None:
    """module.httpx.AsyncClient 를 in-process ASGITransport(app) 로 라우팅(실 네트워크 0)."""

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            kwargs.pop("transport", None)
            kwargs.setdefault("base_url", "http://testserver")
            self._real = _REAL_ASYNC_CLIENT(
                transport=httpx.ASGITransport(app=app), **kwargs
            )

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._real.__aenter__()

        async def __aexit__(self, *exc: object) -> None:
            await self._real.__aexit__(*exc)

    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)


def _make_real_app(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """실 web_server.create_app(auth_required=True) — 비번은 _expected_password 패치.

    store 를 반환해 테스트가 동일 db_path(app.state.store._db_path)로 TaskQueue 를 열어
    실 enqueue 행을 직접 조회할 수 있게 한다(런너와 같은 SQLite 공유)."""
    from office.event_store import EventStore
    from web_server import auth as auth_mod
    from web_server.app import create_app

    monkeypatch.setattr(auth_mod, "_expected_password", lambda: _PW)
    auth_mod._reset_for_tests()  # 세션 dict 격리(테스트 간 누수 0).
    store = EventStore(db_path=str(tmp_path / "g6run.db"))
    app = create_app(store=store, auth_required=True)
    return app, store


@pytest.mark.asyncio
async def test_post_run_with_cookie_enqueues_real_task(tmp_path, monkeypatch) -> None:
    """rest_client.post_run(cookie 첨부) → 실 /api/run 200 handled + ★실 enqueue 확증.

    enqueue 증거 = 같은 db 의 TaskQueue.list_by_status("queued") 에 workflow=="game_qa"
    행이 존재(웹이 워크플로 실행 0·enqueue만 — 워커가 별도로 claim/run)."""
    from office.task_queue import TaskQueue

    app, store = _make_real_app(tmp_path, monkeypatch)
    _route_to_app(monkeypatch, auth_client, app)

    token = await asyncio.wait_for(
        auth_client.login("http://testserver", _PW, cookie_name=_COOKIE),
        timeout=_BOUND,
    )
    assert token, "실 create_app 로그인 토큰 획득 실패"

    _route_to_app(monkeypatch, rest_client, app)
    result = await asyncio.wait_for(
        rest_client.post_run(
            "game_qa", "http://testserver", cookie=f"{_COOKIE}={token}"
        ),
        timeout=_BOUND,
    )
    # 쿠키가 게이트를 통과(401 아님) + 200 enqueue + task_id 부여.
    assert result.status == 200
    assert result.handled is True
    assert result.task_id is not None

    # ★실 enqueue 확증: 같은 db 의 queued 목록에 workflow=="game_qa" 행 존재.
    queue = TaskQueue(db_path=store._db_path)
    queued = queue.list_by_status("queued")
    workflows = [row["workflow"] for row in queued]
    assert "game_qa" in workflows, f"enqueue 미확증: queued={workflows}"
    # 반환 task_id 가 실제 행과 일치.
    ids = [int(row["id"]) for row in queued]
    assert result.task_id in ids


@pytest.mark.asyncio
async def test_post_run_without_cookie_401_no_enqueue(tmp_path, monkeypatch) -> None:
    """쿠키 미첨부 post_run → 실 게이트 401(handled=False) + enqueue 0(게이트 차단 증거)."""
    from office.task_queue import TaskQueue

    app, store = _make_real_app(tmp_path, monkeypatch)
    _route_to_app(monkeypatch, rest_client, app)

    result = await asyncio.wait_for(
        rest_client.post_run("game_qa", "http://testserver"),  # cookie 기본 None.
        timeout=_BOUND,
    )
    assert result.status == 401
    assert result.handled is False
    assert result.task_id is None

    # 게이트가 막았으므로 enqueue 0(queued 비어 있음).
    queue = TaskQueue(db_path=store._db_path)
    assert queue.list_by_status("queued") == []
