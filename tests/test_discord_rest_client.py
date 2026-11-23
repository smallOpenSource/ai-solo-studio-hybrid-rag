"""US-004 — 승인 REST 클라이언트 단위 테스트.

mock httpx(MockTransport)로 네트워크 없이 동작 검증.
★ `import discord` 정적 단언 포함 — discord 패키지 무의존 확인.

Mock 전략
---------
monkeypatch 로 adapters.discord.rest_client 모듈의 httpx.AsyncClient 를
대체한다. _FakeAsyncClient 는 진짜 httpx.AsyncClient 를 클로저 외부에서
미리 캡처(REAL_ASYNC_CLIENT)해 재귀 교체를 방지한다.
"""

from __future__ import annotations

import sys

import httpx
import pytest

# 진짜 AsyncClient를 모듈 로드 시점에 캡처 — monkeypatch 이후 교체되어도 안전.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


# ── discord 패키지 무의존 정적 단언 ──────────────────────────────────────────
def test_no_discord_import() -> None:
    """rest_client 임포트 시 discord 패키지를 요구하지 않아야 한다."""
    sys.modules.pop("adapters.discord.rest_client", None)

    original = sys.modules.get("discord")

    class _Blocker:
        def __getattr__(self, name: str) -> object:
            raise ImportError("discord must not be imported by rest_client")

    sys.modules["discord"] = _Blocker()  # type: ignore[assignment]

    try:
        import adapters.discord.rest_client  # noqa: F401
    finally:
        if original is not None:
            sys.modules["discord"] = original
        else:
            sys.modules.pop("discord", None)
        sys.modules.pop("adapters.discord.rest_client", None)


# ── MockTransport 헬퍼 ────────────────────────────────────────────────────────


def _capture_transport(
    status_code: int, captured: list[httpx.Request]
) -> httpx.MockTransport:
    """요청을 captured 에 기록하고 status_code 를 반환하는 transport."""

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, content=b"{}")

    return httpx.MockTransport(_handler)


def _static_transport(status_code: int) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=b"{}")

    return httpx.MockTransport(_handler)


def _error_transport() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return httpx.MockTransport(_handler)


# ── AsyncClient 교체 헬퍼 ─────────────────────────────────────────────────────


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.MockTransport,
) -> None:
    """rest_client 모듈의 httpx.AsyncClient 를 mock 으로 교체.

    _REAL_ASYNC_CLIENT 를 사용해 재귀 교체를 방지한다.
    """
    import adapters.discord.rest_client as rc

    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            # 반드시 _REAL_ASYNC_CLIENT 사용 — 이미 교체된 rc.httpx.AsyncClient 재호출 방지
            self._real = _REAL_ASYNC_CLIENT(transport=transport)

        async def __aenter__(self) -> httpx.AsyncClient:
            return self._real

        async def __aexit__(self, *_: object) -> None:
            await self._real.aclose()

    monkeypatch.setattr(rc.httpx, "AsyncClient", _FakeAsyncClient)


# ── 테스트 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_correct_path_and_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approve()는 POST /api/inbox/{seq}/approve 경로로 정확히 호출해야 한다."""
    import adapters.discord.rest_client as rc

    captured: list[httpx.Request] = []
    _patch_async_client(monkeypatch, _capture_transport(200, captured))

    await rc.approve(seq=42, base_url="http://localhost:8000")

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/api/inbox/42/approve"


@pytest.mark.asyncio
async def test_reject_correct_path_and_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reject()는 POST /api/inbox/{seq}/reject 경로로 정확히 호출해야 한다."""
    import adapters.discord.rest_client as rc

    captured: list[httpx.Request] = []
    _patch_async_client(monkeypatch, _capture_transport(200, captured))

    await rc.reject(seq=7, base_url="http://localhost:8000")

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/api/inbox/7/reject"


@pytest.mark.asyncio
async def test_approve_200_returns_handled_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 응답 → handled=True, status=200, error=None."""
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _static_transport(200))

    result = await rc.approve(seq=1, base_url="http://localhost:8000")

    assert result.handled is True
    assert result.status == 200
    assert result.error is None


@pytest.mark.asyncio
async def test_approve_404_returns_handled_true_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 404 → handled=True(멱등). 예외 throw 없음.

    404는 "존재한 적 없는 seq"이지만 어댑터는 처리됨으로 표시
    (버튼 followup에 성공 응답, 오류 아님).
    """
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _static_transport(404))

    result = await rc.approve(seq=99999, base_url="http://localhost:8000")

    assert result.handled is True
    assert result.status == 404
    assert result.error is None


@pytest.mark.asyncio
async def test_reapprove_200_handled_true_not_misidentified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이미 처리된 seq 재approve → 200(RESOLVED 중복 적재, 무해) → handled=True.

    ★ 200을 '이미 처리됨 오류'로 오인하지 않아야 한다.
    RESOLVED 중복은 office 책임이며 어댑터가 차단 불가.
    """
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _static_transport(200))

    result1 = await rc.approve(seq=5, base_url="http://localhost:8000")
    result2 = await rc.approve(seq=5, base_url="http://localhost:8000")

    assert result1.handled is True
    assert result2.handled is True
    assert result1.error is None
    assert result2.error is None


@pytest.mark.asyncio
async def test_5xx_returns_handled_false_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 500 → handled=False, error 문자열 포함."""
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _static_transport(500))

    result = await rc.approve(seq=3, base_url="http://localhost:8000")

    assert result.handled is False
    assert result.status == 500
    assert result.error is not None
    assert "500" in result.error


@pytest.mark.asyncio
async def test_network_exception_returns_handled_false_no_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """네트워크 예외(ConnectError 등) → handled=False, error 포함, 크래시 없음."""
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _error_transport())

    result = await rc.approve(seq=2, base_url="http://localhost:8000")

    assert result.handled is False
    assert result.status is None
    assert result.error is not None
    assert len(result.error) > 0


# ── DC-2: post_inbound(POST /api/inbound) ────────────────────────────────────


@pytest.mark.asyncio
async def test_post_inbound_correct_path_and_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post_inbound()는 POST /api/inbound 경로로 호출하고 body에 text·message_id를 싣는다."""
    import json

    import adapters.discord.rest_client as rc

    captured: list[httpx.Request] = []
    _patch_async_client(monkeypatch, _capture_transport(200, captured))

    await rc.post_inbound(
        text="안녕하세요", message_id="123", base_url="http://localhost:8000"
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/api/inbound"
    body = json.loads(req.content)
    assert body["text"] == "안녕하세요"
    assert body["message_id"] == "123"
    # author 미전달 시 body에 author 키 없음(선택 필드).
    assert "author" not in body


@pytest.mark.asyncio
async def test_post_inbound_includes_author_when_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """author 전달 시 body에 포함된다(선택 필드)."""
    import json

    import adapters.discord.rest_client as rc

    captured: list[httpx.Request] = []
    _patch_async_client(monkeypatch, _capture_transport(202, captured))

    await rc.post_inbound(
        text="hi", message_id="9", base_url="http://localhost:8000", author="민준"
    )
    body = json.loads(captured[0].content)
    assert body["author"] == "민준"


@pytest.mark.asyncio
async def test_post_inbound_200_and_202_return_handled_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200(accepted/duplicate)·202 → handled=True."""
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _static_transport(200))
    r200 = await rc.post_inbound(text="t", message_id="1", base_url="http://h")
    assert r200.handled is True
    assert r200.status == 200
    assert r200.error is None

    _patch_async_client(monkeypatch, _static_transport(202))
    r202 = await rc.post_inbound(text="t", message_id="2", base_url="http://h")
    assert r202.handled is True
    assert r202.status == 202


@pytest.mark.asyncio
async def test_post_inbound_5xx_returns_handled_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 500 → handled=False, error에 상태 포함(크래시 0)."""
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _static_transport(500))
    result = await rc.post_inbound(text="t", message_id="3", base_url="http://h")
    assert result.handled is False
    assert result.status == 500
    assert result.error is not None
    assert "500" in result.error


@pytest.mark.asyncio
async def test_post_inbound_network_exception_handled_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """네트워크 예외 → handled=False, status=None, 크래시 0."""
    import adapters.discord.rest_client as rc

    _patch_async_client(monkeypatch, _error_transport())
    result = await rc.post_inbound(text="t", message_id="4", base_url="http://h")
    assert result.handled is False
    assert result.status is None
    assert result.error is not None
