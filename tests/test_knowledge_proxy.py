"""US-G3-5: POST /api/knowledge/search 프록시 라우트 단위테스트.

8810 knowledge 서비스를 httpx MockTransport로 대체 — 실 서비스 불필요.
auth_required=True 앱에서 쿠키 인증 후 프록시 계약을 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from office.event_store import EventStore
from web_server import auth
from web_server.app import create_app

# ★실제 AsyncClient를 패치 전에 포착: monkeypatch가 web_server.app.httpx.AsyncClient(=
#   글로벌 httpx.AsyncClient)를 람다로 교체하므로, mock 헬퍼가 httpx.AsyncClient를 그대로
#   부르면 람다로 재진입해 transport 중복(TypeError)이 난다. 진짜 클래스를 써야 한다.
_REAL_ASYNC_CLIENT = httpx.AsyncClient

_PW = "testpw_knowledge!"
_COOKIE = "omc_session"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LOGIN_PASSWORD", _PW)
    # knowledge 서비스 URL을 mock 전용 주소로 고정(실제 네트워크 불필요).
    monkeypatch.setenv("KNOWLEDGE_SERVICE_URL", "http://knowledge-mock")
    auth._reset_for_tests()
    yield
    auth._reset_for_tests()


def _authed_client(tmp_path: Path) -> TestClient:
    """로그인된 TestClient를 돌려준다."""
    app = create_app(
        store=EventStore(db_path=tmp_path / "office.db"), auth_required=True
    )
    c = TestClient(app)
    r = c.post("/api/login", json={"password": _PW})
    assert r.status_code == 200 and r.json()["ok"] is True
    return c


# ── 정상 응답 ────────────────────────────────────────────────────────────────


def test_knowledge_search_returns_results(tmp_path, monkeypatch):
    """8810이 정상 응답할 때 {"results": [...]} 계약을 그대로 전달한다."""
    mock_body = {
        "results": [
            {"text": "hello world", "source": "http://example.com", "score": 0.9}
        ],
        "count": 1,
    }

    def _transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        payload = json.loads(request.content)
        assert payload["query"] == "test query"
        assert payload["k"] == 3
        return httpx.Response(200, json=mock_body)

    monkeypatch.setattr(
        "web_server.app.httpx.AsyncClient",
        lambda **kw: _make_async_client(_transport, **kw),
    )

    c = _authed_client(tmp_path)
    r = c.post("/api/knowledge/search", json={"query": "test query", "k": 3})
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["text"] == "hello world"
    assert data["results"][0]["source"] == "http://example.com"
    assert data["results"][0]["score"] == pytest.approx(0.9)


def test_knowledge_search_default_k(tmp_path, monkeypatch):
    """k 생략 시 기본값 5를 knowledge 서비스에 전달한다."""
    sent_k: list[int] = []

    def _transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent_k.append(payload.get("k"))
        return httpx.Response(200, json={"results": [], "count": 0})

    monkeypatch.setattr(
        "web_server.app.httpx.AsyncClient",
        lambda **kw: _make_async_client(_transport, **kw),
    )

    c = _authed_client(tmp_path)
    r = c.post("/api/knowledge/search", json={"query": "anything"})
    assert r.status_code == 200
    assert sent_k == [5]


# ── 오류 처리 ────────────────────────────────────────────────────────────────


def test_knowledge_search_502_when_service_down(tmp_path, monkeypatch):
    """knowledge 서비스 연결 실패 시 502 + {"results": []} graceful 반환."""

    def _transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        "web_server.app.httpx.AsyncClient",
        lambda **kw: _make_async_client(_transport, **kw),
    )

    c = _authed_client(tmp_path)
    r = c.post("/api/knowledge/search", json={"query": "down"})
    assert r.status_code == 502
    data = r.json()
    assert data["results"] == []
    assert "error" in data


def test_knowledge_search_502_on_http_error(tmp_path, monkeypatch):
    """knowledge 서비스가 5xx를 반환해도 502 graceful 반환."""

    def _transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal error"})

    monkeypatch.setattr(
        "web_server.app.httpx.AsyncClient",
        lambda **kw: _make_async_client(_transport, **kw),
    )

    c = _authed_client(tmp_path)
    r = c.post("/api/knowledge/search", json={"query": "broken"})
    assert r.status_code == 502
    assert r.json()["results"] == []


# ── 인증 게이트 ──────────────────────────────────────────────────────────────


def test_knowledge_search_requires_auth(tmp_path):
    """미인증 요청은 auth_gate가 401로 거부한다."""
    app = create_app(
        store=EventStore(db_path=tmp_path / "office.db"), auth_required=True
    )
    c = TestClient(app)
    r = c.post("/api/knowledge/search", json={"query": "x"})
    assert r.status_code == 401


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _make_async_client(transport_fn, **kw):
    """httpx.AsyncClient를 MockTransport로 대체하는 컨텍스트 매니저를 반환한다."""
    transport = httpx.MockTransport(transport_fn)
    return _REAL_ASYNC_CLIENT(
        transport=transport, **{k: v for k, v in kw.items() if k != "timeout"}
    )
