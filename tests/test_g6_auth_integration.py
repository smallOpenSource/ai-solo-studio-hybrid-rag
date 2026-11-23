"""US-G6-0 — ★실 create_app(auth_required=True) 게이트 통합(엔진 env 전용).

discord env 통합 테스트(tests/test_discord_auth.py)는 fastapi 부재(one_man_corp-discord
=httpx·discord·dotenv·pyyaml 최소구성)로 게이트 계약을 순수 ASGI 로 재현한다. 이
파일은 그 재현이 실제 web_server 게이트와 일치함을 **실 create_app** 으로 확증한다
(contract-drift 0·AC6 문자 그대로 충족).

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
_PW = "testpw_real_789!"
_COOKIE = "omc_session"
_BOUND = 5.0

# 진짜 AsyncClient 를 모듈 로드 시점에 캡처(monkeypatch 이후에도 안전).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _route_to_app(monkeypatch: pytest.MonkeyPatch, module, app) -> None:
    """module.httpx.AsyncClient 를 in-process ASGITransport(app) 로 라우팅.

    auth_client/rest_client 는 내부에서 httpx.AsyncClient(timeout=...) 를 직접 생성하므로
    모듈의 httpx.AsyncClient 를 가짜로 바꿔 실 create_app 으로 보낸다(실 네트워크 0).
    """

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

    auth._expected_password 는 .env(LOGIN_PASSWORD)를 읽으므로, .env 의존을 끊고
    결정론 테스트를 위해 테스트 상수 _PW 를 반환하도록 패치한다. 게이트 미들웨어·
    create_session·verify_session 은 전부 실 코드(재현 아님)."""
    from office.event_store import EventStore
    from web_server import auth as auth_mod
    from web_server.app import create_app

    monkeypatch.setattr(auth_mod, "_expected_password", lambda: _PW)
    auth_mod._reset_for_tests()  # 세션 dict 격리(테스트 간 누수 0).
    store = EventStore(db_path=str(tmp_path / "g6auth.db"))
    return create_app(store=store, auth_required=True)


@pytest.mark.asyncio
async def test_real_gate_unauth_inbox_401(tmp_path, monkeypatch) -> None:
    """미인증 GET /api/inbox → 401(실 _auth_gate 미들웨어 동작)."""
    app = _make_real_app(tmp_path, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await asyncio.wait_for(client.get("/api/inbox"), timeout=_BOUND)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_real_gate_login_then_inbox_200(tmp_path, monkeypatch) -> None:
    """auth_client.login(실 /api/login)→실 Set-Cookie→쿠키 첨부 GET /api/inbox→200."""
    app = _make_real_app(tmp_path, monkeypatch)
    _route_to_app(monkeypatch, auth_client, app)

    token = await asyncio.wait_for(
        auth_client.login("http://testserver", _PW, cookie_name=_COOKIE),
        timeout=_BOUND,
    )
    assert token, "실 create_app 로그인 토큰 획득 실패"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await asyncio.wait_for(
            client.get("/api/inbox", headers={"Cookie": f"{_COOKIE}={token}"}),
            timeout=_BOUND,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_real_gate_wrong_password_returns_none(tmp_path, monkeypatch) -> None:
    """틀린 비번 → 실 /api/login 200 {ok:false} → auth_client None(401 아님)."""
    app = _make_real_app(tmp_path, monkeypatch)
    _route_to_app(monkeypatch, auth_client, app)
    token = await asyncio.wait_for(
        auth_client.login("http://testserver", "WRONG_PW", cookie_name=_COOKIE),
        timeout=_BOUND,
    )
    assert token is None


@pytest.mark.asyncio
async def test_real_gate_rest_approve_with_cookie_passes(tmp_path, monkeypatch) -> None:
    """rest_client.approve 가 실 게이트를 쿠키로 통과(401 아님)."""
    app = _make_real_app(tmp_path, monkeypatch)
    _route_to_app(monkeypatch, auth_client, app)
    token = await asyncio.wait_for(
        auth_client.login("http://testserver", _PW, cookie_name=_COOKIE),
        timeout=_BOUND,
    )
    assert token

    _route_to_app(monkeypatch, rest_client, app)
    result = await asyncio.wait_for(
        rest_client.approve(99999, "http://testserver", cookie=f"{_COOKIE}={token}"),
        timeout=_BOUND,
    )
    # 쿠키가 게이트를 통과했다는 증거 = 401 이 아님(존재X seq 라 404/handled 등은 무관).
    assert result.status != 401


@pytest.mark.asyncio
async def test_real_gate_rest_approve_without_cookie_401(tmp_path, monkeypatch) -> None:
    """쿠키 미첨부 rest_client.approve → 실 게이트 401(handled=False)."""
    app = _make_real_app(tmp_path, monkeypatch)
    _route_to_app(monkeypatch, rest_client, app)
    result = await asyncio.wait_for(
        rest_client.approve(1, "http://testserver"),  # cookie 기본 None.
        timeout=_BOUND,
    )
    assert result.status == 401
    assert result.handled is False
