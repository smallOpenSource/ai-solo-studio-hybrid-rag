"""Phase B 로그인 세션 인증 통합 테스트 — FastAPI TestClient (engine env).

오프라인 결정론(실서버·LLM 0·tmp_path db). 인메모리 opaque 세션(web_server/auth.py)이
암호 env 구동·미인증 게이트(401)·WS 거부(close 1008)·만료/위조 토큰 거부를 보장하는지 검증.

★암호는 os.environ(LOGIN_PASSWORD)로 주입 — 하드코딩 아님을 증명(임의 테스트 값).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from office.event_store import EventStore
from web_server import auth
from web_server.app import create_app

_PW = "testpw_456!"  # ★임의 테스트 암호(env 주입 — 코드 하드코딩 아님 증명).
_COOKIE = "omc_session"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """LOGIN_PASSWORD를 env로 주입 + 세션 격리(테스트 간 leak 0)."""
    monkeypatch.setenv("LOGIN_PASSWORD", _PW)
    auth._reset_for_tests()
    yield
    auth._reset_for_tests()


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        store=EventStore(db_path=tmp_path / "office.db"), auth_required=True
    )
    return TestClient(app)


# ── 로그인/로그아웃/상태 ────────────────────────────────────────────────────


def test_login_wrong_password_returns_200_ok_false(tmp_path):
    """★US-G3-4: 틀린 비번은 200 {"ok": false} — 401은 브라우저 콘솔 error 자동기록."""
    c = _client(tmp_path)
    r = c.post("/api/login", json={"password": "nope"})
    assert r.status_code == 200
    assert r.json() == {"ok": False}
    assert _COOKIE not in r.cookies  # 쿠키 미발급.


def test_login_correct_sets_httponly_cookie(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/login", json={"password": _PW})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # Set-Cookie 발급 + httponly·SameSite 속성.
    setck = r.headers.get("set-cookie", "")
    assert _COOKIE in setck
    assert "httponly" in setck.lower()
    assert "samesite=strict" in setck.lower()


def test_status_reflects_login_then_logout(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/auth/status").json() == {"authed": False}  # 초기 미인증.
    c.post("/api/login", json={"password": _PW})
    assert c.get("/api/auth/status").json() == {"authed": True}  # 로그인 후.
    c.post("/api/logout")
    assert c.get("/api/auth/status").json() == {"authed": False}  # 로그아웃 후.


# ── 게이트(미인증 401) ──────────────────────────────────────────────────────


def test_api_requires_auth_401_without_cookie(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/inbox").status_code == 401  # 보호 API.


def test_events_stream_requires_auth_401(tmp_path):
    c = _client(tmp_path)
    assert c.get("/events/stream").status_code == 401  # SSE도 보호.


def test_api_passes_with_valid_cookie(tmp_path):
    c = _client(tmp_path)
    c.post("/api/login", json={"password": _PW})  # 쿠키 획득(클라 잼 유지).
    assert c.get("/api/inbox").status_code == 200  # 통과.


def test_tampered_cookie_rejected(tmp_path):
    c = _client(tmp_path)
    c.cookies.set(_COOKIE, "forged-unknown-token")  # 위조 토큰.
    assert c.get("/api/inbox").status_code == 401


def test_login_status_open_without_auth(tmp_path):
    """/api/login·/api/logout·/api/auth/status는 미인증에도 열림(게이트 예외).

    ★US-G3-4: 틀린 비번 login도 200({"ok": false}) — 게이트 예외이므로 401 아님.
    """
    c = _client(tmp_path)
    assert c.get("/api/auth/status").status_code == 200
    assert (
        c.post("/api/login", json={"password": "x"}).status_code == 200
    )  # 도달 가능·200.
    assert c.post("/api/logout").status_code == 200


# ── WS 거부(accept 이전 close 1008) ─────────────────────────────────────────


def test_ws_rejected_without_auth(tmp_path):
    c = _client(tmp_path)
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect("/ws"):
            pass  # accept 이전 close(1008)라 진입 즉시 거부.
    assert exc.value.code == 1008  # Policy Violation.


# ── auth.py 단위(만료·결정론·상수시간) ──────────────────────────────────────


def test_session_lifecycle_and_expiry():
    auth._reset_for_tests()
    # create at t=0, ttl은 config(기본 86400) — now 주입으로 결정론 검증.
    token = auth.create_session(now=0.0)
    assert auth.verify_session(token, now=1.0) is True  # 미만료.
    assert auth.verify_session(token, now=auth.ttl_seconds() + 1.0) is False  # 만료.
    # 만료 후 lazy 삭제 — 재검증도 False.
    assert auth.verify_session(token, now=1.0) is False


def test_unknown_and_empty_token_rejected():
    assert auth.verify_session(None) is False
    assert auth.verify_session("") is False
    assert auth.verify_session("never-issued") is False


def test_check_password_env_driven_constant_time():
    assert auth.check_password(_PW) is True
    assert auth.check_password("wrong") is False
    assert auth.check_password(None) is False


def test_destroy_session(tmp_path):
    token = auth.create_session(now=0.0)
    assert auth.verify_session(token, now=1.0) is True
    auth.destroy_session(token)
    assert auth.verify_session(token, now=1.0) is False
