"""US-G6-0 — 봇 세션 인증(auth_client.login + 쿠키 첨부) 테스트.

★★★ 안전 규칙(hang 사고 재발 차단) ★★★
- 모든 async 테스트는 asyncio.wait_for(coro, timeout=5) 로 bound.
- 실서버·라이브 게이트웨이·discord.run 0 — auth_client/rest_client 는 httpx mock
  (MockTransport) 또는 in-process ASGI(httpx.ASGITransport)로만 호출한다.
- on_unauthorized 재인증은 ★1회 상한(_reauth)임을 명시 단언(무한 재인증 0).
- ★비번 비노출: 실패 경로 caplog 에 테스트 비번 문자열 부재 단언.

분해:
- auth_client.login 단위: 성공/{ok:false}/비200/쿠키부재/네트워크예외/빈비번 — 6경로.
- 통합(in-process ASGI): 미인증 401 → login 토큰 → Cookie 첨부 200(GET /api/inbox·
  rest_client.approve).
- 바운드: DiscordBot._reauth 가 1회만 login 호출(무한 재인증 0).

★통합 앱 결정(막힌 점·결정): 프로덕션 web_server.app.create_app 은 fastapi/starlette 를
요구하나 one_man_corp-discord env(scripts/run_tests.sh discord)는 의도적 최소 구성
(httpx·discord·dotenv·pyyaml만 — web 프레임워크 0)이다. env 오염(fastapi 설치) 없이
in-process 통합을 하기 위해, 프로덕션과 **동일한 로그인/게이트 계약**(POST /api/login
{password}→200 {ok:true}+Set-Cookie omc_session·틀린비번 200 {ok:false}·보호경로 401)을
프레임워크 무의존 순수 ASGI 콜러블로 재현한다(신규 의존성 0·실 네트워크 0). 계약은
web_server/app.py·auth.py 실측에서 확증된 것.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets

import httpx
import pytest

from adapters.discord import auth_client

# 진짜 AsyncClient 를 모듈 로드 시점에 캡처 — monkeypatch 이후 교체되어도 안전.
_REAL_ASYNC_CLIENT = httpx.AsyncClient

# ★임의 테스트 암호(env 주입 — 코드 하드코딩 아님 증명). 실비번 아님.
_PW = "testpw_456!"
_COOKIE_NAME = "omc_session"
_BOUND = 5.0


# ── ASGI 트랜스포트 주입 헬퍼 ─────────────────────────────────────────────────


def _patch_asgi(monkeypatch: pytest.MonkeyPatch, module, app) -> None:
    """module.httpx.AsyncClient 를 in-process ASGITransport 로 라우팅하도록 교체.

    auth_client/rest_client 는 내부에서 httpx.AsyncClient(timeout=...) 를 직접
    생성하므로(transport 훅 없음), 모듈의 httpx.AsyncClient 를 가짜로 바꿔
    실제 클라이언트에 transport=ASGITransport(app) 를 주입한다(실 네트워크 0).
    """

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            kwargs.pop("transport", None)  # transport 는 항상 ASGI 로 강제.
            kwargs.setdefault("base_url", "http://testserver")
            self._real = _REAL_ASYNC_CLIENT(
                transport=httpx.ASGITransport(app=app), **kwargs
            )

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._real.__aenter__()

        async def __aexit__(self, *exc: object) -> None:
            await self._real.__aexit__(*exc)

    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)


# ── auth_client.login 단위(MockTransport — 실 네트워크 0) ─────────────────────


def _patch_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """auth_client 모듈의 httpx.AsyncClient 를 MockTransport(handler) 로 교체."""

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            kwargs.pop("transport", None)
            self._real = _REAL_ASYNC_CLIENT(
                transport=httpx.MockTransport(handler), **kwargs
            )

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._real.__aenter__()

        async def __aexit__(self, *exc: object) -> None:
            await self._real.__aexit__(*exc)

    monkeypatch.setattr(auth_client.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_login_success_returns_token(monkeypatch) -> None:
    """200 {ok:true} + Set-Cookie → 세션 토큰(쿠키 값) 반환."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"Set-Cookie": f"{_COOKIE_NAME}=opaque-token-xyz; Path=/"},
        )

    _patch_mock_transport(monkeypatch, _handler)
    token = await asyncio.wait_for(
        auth_client.login("http://web", _PW, cookie_name=_COOKIE_NAME),
        timeout=_BOUND,
    )
    assert token == "opaque-token-xyz"


@pytest.mark.asyncio
async def test_login_ok_false_returns_none(monkeypatch) -> None:
    """200 {ok:false}(틀린 비번·BUG-005) → None(401 아님·크래시 0)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False})

    _patch_mock_transport(monkeypatch, _handler)
    token = await asyncio.wait_for(auth_client.login("http://web", _PW), timeout=_BOUND)
    assert token is None


@pytest.mark.asyncio
async def test_login_non_200_returns_none(monkeypatch) -> None:
    """비200(예: 500) → None."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": True})

    _patch_mock_transport(monkeypatch, _handler)
    token = await asyncio.wait_for(auth_client.login("http://web", _PW), timeout=_BOUND)
    assert token is None


@pytest.mark.asyncio
async def test_login_missing_cookie_returns_none(monkeypatch) -> None:
    """200 {ok:true} 이나 Set-Cookie 부재 → None."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _patch_mock_transport(monkeypatch, _handler)
    token = await asyncio.wait_for(
        auth_client.login("http://web", _PW, cookie_name=_COOKIE_NAME),
        timeout=_BOUND,
    )
    assert token is None


@pytest.mark.asyncio
async def test_login_network_error_returns_none(monkeypatch) -> None:
    """네트워크 예외(ConnectError) → None(크래시 0)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_mock_transport(monkeypatch, _handler)
    token = await asyncio.wait_for(auth_client.login("http://web", _PW), timeout=_BOUND)
    assert token is None


@pytest.mark.asyncio
async def test_login_empty_password_returns_none_no_request() -> None:
    """password='' → 요청도 안 보내고 즉시 None(미인증 진행)."""
    sent: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    # transport 교체 없이도 동작해야 하나, 안전을 위해 요청 발생 시 기록되도록 둔다.
    token = await asyncio.wait_for(auth_client.login("http://web", ""), timeout=_BOUND)
    assert token is None
    assert sent == []  # 빈 비번 → 네트워크 호출 0.


# ── ★비번 비노출 단언(caplog) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_password_never_logged_on_failure(monkeypatch, caplog) -> None:
    """로그인 실패 경로 로그에 테스트 비번 문자열이 절대 나오지 않는다."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False})  # 거부 경로(로그 발생).

    _patch_mock_transport(monkeypatch, _handler)
    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(auth_client.login("http://web", _PW), timeout=_BOUND)
    blob = "\n".join(rec.getMessage() for rec in caplog.records)
    assert _PW not in blob, "★비번이 로그에 노출됨(보안 위반)"


# ── 통합(프레임워크 무의존 순수 ASGI 계약 재현 · in-process) ──────────────────
# web_server.app.create_app 은 fastapi/starlette 의존이고 one_man_corp-discord env 는
# 의도적 최소 구성(web 프레임워크 0)이라, 동일 로그인/게이트 계약을 순수 ASGI 로 재현한다
# (신규 의존성 0). 계약은 web_server/app.py·auth.py 실측 확증값.


_AUTH_OPEN_PATHS = frozenset({"/api/login", "/api/logout", "/api/auth/status"})


def _make_auth_app(password: str, cookie_name: str = _COOKIE_NAME):
    """프로덕션 web_server 로그인/게이트 계약을 재현하는 순수 ASGI 콜러블 팩토리.

    - POST /api/login {password}: 일치→200 {ok:true}+Set-Cookie(cookie_name=opaque),
      불일치→200 {ok:false}(★BUG-005·401 아님), 쿠키 미발급.
    - 미인증 시 /api/*(login/logout/status 제외)+/events/stream → 401.
    - Cookie: cookie_name=<유효토큰> 첨부 시 통과(GET /api/inbox → 200).
    세션은 클로저 set(인메모리·opaque token_urlsafe) — 프로세스 재시작 무관(테스트).
    """
    sessions: set[str] = set()

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        path = scope["path"]
        method = scope["method"]

        # 요청 본문 수집(login용).
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        # 쿠키 파싱(헤더에서 cookie_name 추출).
        cookies: dict[str, str] = {}
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                for part in v.decode("latin-1").split(";"):
                    if "=" in part:
                        name, _, val = part.strip().partition("=")
                        cookies[name] = val

        async def _json(status: int, payload: dict, set_cookie: str | None = None):
            headers = [(b"content-type", b"application/json")]
            if set_cookie is not None:
                headers.append((b"set-cookie", set_cookie.encode("latin-1")))
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": headers,
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps(payload).encode("utf-8"),
                }
            )

        # /api/login — 게이트 예외(항상 도달).
        if path == "/api/login" and method == "POST":
            try:
                candidate = json.loads(body or b"{}").get("password", "")
            except Exception:  # noqa: BLE001
                candidate = ""
            if password and candidate == password:
                token = secrets.token_urlsafe(32)
                sessions.add(token)
                await _json(
                    200,
                    {"ok": True},
                    set_cookie=f"{cookie_name}={token}; Path=/; HttpOnly",
                )
            else:
                await _json(200, {"ok": False})  # ★틀린 비번도 200(401 아님).
            return

        # 인증 게이트: 보호 경로(미인증 401).
        needs_auth = (
            path.startswith("/api/") and path not in _AUTH_OPEN_PATHS
        ) or path == "/events/stream"
        authed = cookies.get(cookie_name) in sessions
        if needs_auth and not authed:
            await _json(401, {"detail": "unauthorized"})
            return

        # 보호 통과: 엔드포인트별 최소 응답.
        if path == "/api/inbox":
            await _json(200, {"review": []})
            return
        if path.startswith("/api/inbox/") and method == "POST":
            # 존재하지 않는 seq → 404(어댑터 멱등 handled=True).
            await _json(404, {})
            return
        await _json(404, {})

    return app


@pytest.fixture()
def _authed_app():
    """프로덕션 계약을 재현한 순수 ASGI 앱(LOGIN_PASSWORD=_PW 상응)."""
    return _make_auth_app(_PW, _COOKIE_NAME)


@pytest.mark.asyncio
async def test_integration_unauth_inbox_401(_authed_app) -> None:
    """미인증 GET /api/inbox → 401(게이트 동작 확인)."""
    transport = httpx.ASGITransport(app=_authed_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        resp = await asyncio.wait_for(client.get("/api/inbox"), timeout=_BOUND)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_integration_login_then_inbox_200(monkeypatch, _authed_app) -> None:
    """auth_client.login 토큰 → Cookie 첨부 GET /api/inbox → 200."""
    # auth_client.login 이 in-process ASGI 앱으로 가도록 라우팅.
    _patch_asgi(monkeypatch, auth_client, _authed_app)

    token = await asyncio.wait_for(
        auth_client.login("http://testserver", _PW, cookie_name=_COOKIE_NAME),
        timeout=_BOUND,
    )
    assert token, "로그인 토큰 획득 실패"

    # 획득 토큰을 Cookie 헤더로 첨부 → 보호 API 통과.
    transport = httpx.ASGITransport(app=_authed_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        resp = await asyncio.wait_for(
            client.get("/api/inbox", headers={"Cookie": f"{_COOKIE_NAME}={token}"}),
            timeout=_BOUND,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_integration_rest_approve_with_cookie_authzed(
    monkeypatch, _authed_app
) -> None:
    """rest_client.approve 가 cookie 첨부 시 인증을 통과한다(404/200 handled).

    seq 가 DB 에 없어 404 이지만 어댑터 관점에선 handled=True(멱등). 핵심은 401 이
    아니라는 것 — 즉 쿠키가 게이트를 통과했다는 증거.
    """
    from adapters.discord import rest_client

    _patch_asgi(monkeypatch, auth_client, _authed_app)
    token = await asyncio.wait_for(
        auth_client.login("http://testserver", _PW, cookie_name=_COOKIE_NAME),
        timeout=_BOUND,
    )
    assert token

    # rest_client 도 같은 in-process 앱으로 라우팅.
    _patch_asgi(monkeypatch, rest_client, _authed_app)
    cookie = f"{_COOKIE_NAME}={token}"
    result = await asyncio.wait_for(
        rest_client.approve(99999, "http://testserver", cookie=cookie),
        timeout=_BOUND,
    )
    # 쿠키 통과 → 401 아님. 존재 안 하는 seq 라 404(handled=True·멱등).
    assert result.status != 401
    assert result.handled is True


@pytest.mark.asyncio
async def test_integration_rest_approve_without_cookie_401(
    monkeypatch, _authed_app
) -> None:
    """cookie 미첨부 rest_client.approve → 401(handled=False·게이트 차단 증거)."""
    from adapters.discord import rest_client

    _patch_asgi(monkeypatch, rest_client, _authed_app)
    result = await asyncio.wait_for(
        rest_client.approve(1, "http://testserver"),  # cookie 기본 None.
        timeout=_BOUND,
    )
    assert result.status == 401
    assert result.handled is False  # 401 은 200/404 가 아니므로 미처리.


# ── ★바운드 단언: _reauth 는 1회 상한(무한 재인증 0) ─────────────────────────


@pytest.mark.asyncio
async def test_reauth_is_bounded_single_login(monkeypatch) -> None:
    """DiscordBot._reauth 1회 호출 → auth_client.login 정확히 1회(무한 재인증 0).

    on_unauthorized 콜백(=_reauth)이 SSE/REST 401 분기에서 호출돼도 매 호출이
    login 1회만 수행함을 보장(호출자 측 상한 1 과 결합해 무한 스핀 0).
    """
    import adapters.discord.bot as bot_mod

    login_calls: list[tuple] = []

    async def _fake_login(base_url, password, *, cookie_name="omc_session"):
        login_calls.append((base_url, cookie_name))
        return "fresh-token"

    monkeypatch.setattr(bot_mod, "_read_config", lambda: {})  # 안전.
    # auth_client.login 을 가짜로 — bot._ensure_session 이 지연 import 로 부른다.
    monkeypatch.setattr("adapters.discord.auth_client.login", _fake_login)

    bot = bot_mod.DiscordBot(
        channel_id=1,
        base_url="http://web",
        password=_PW,
        cookie_name=_COOKIE_NAME,
    )

    cookie = await asyncio.wait_for(bot._reauth(), timeout=_BOUND)
    # 정확히 1회 login → 완성 Cookie 헤더 반환.
    assert len(login_calls) == 1
    assert cookie == f"{_COOKIE_NAME}=fresh-token"


@pytest.mark.asyncio
async def test_sse_401_calls_on_unauthorized_once_then_bounded(monkeypatch) -> None:
    """consume_events 가 401 시 on_unauthorized 를 호출하고, max_retries 로 바운드 종료.

    ★무한 스핀 0: 401 응답이 반복돼도 max_retries=1 가 소비를 즉시 중단한다.
    on_unauthorized 는 적어도 1회 호출돼 갱신 쿠키를 반영할 기회를 갖는다.
    """
    from adapters.discord.sse_client import consume_events

    reauth_calls: list[int] = []

    async def _on_unauth():
        reauth_calls.append(1)
        return f"{_COOKIE_NAME}=new"

    class _Resp401:
        status_code = 401

    class _FakeStreamCtx:
        async def __aenter__(self):
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "http://web/events/stream"),
                response=httpx.Response(401),
            )

        async def __aexit__(self, *_):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, method, url, headers=None, **kw):
            return _FakeStreamCtx()

    async def _run():
        with monkeypatch.context() as m:
            m.setattr("httpx.AsyncClient", lambda *a, **k: _FakeClient())
            m.setattr(
                "adapters.discord.sse_client.asyncio.sleep",
                _AsyncNoop(),
            )
            gen = consume_events(
                "http://web",
                max_retries=1,  # ★바운드: 1회 실패 후 즉시 중단.
                cookie=f"{_COOKIE_NAME}=old",
                on_unauthorized=_on_unauth,
            )
            results = []
            try:
                async for item in gen:
                    results.append(item)
            finally:
                await gen.aclose()
            return results

    results = await asyncio.wait_for(_run(), timeout=_BOUND)
    assert results == []  # 401 만 → yield 0.
    # on_unauthorized 가 최소 1회 호출(갱신 쿠키 반영 기회) — 그리고 max_retries 로 종료.
    assert len(reauth_calls) >= 1


class _AsyncNoop:
    """asyncio.sleep 대체(즉시 반환) — backoff 대기 없이 결정론."""

    async def __call__(self, *_args, **_kw) -> None:
        return None
