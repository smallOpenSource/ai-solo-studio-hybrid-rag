"""Phase D-prep unit tests — login/cookie-session, inbox approve|reject, inbound.

Covers each NEW endpoint orch must serve so the live Discord adapter becomes a full
drop-in (a later WEB_BASE_URL flip with ZERO adapter edits), plus the seq->thread_id
mapping and the cookie/x-api-key dual auth. Model + searcher are STUBBED and the
checkpoint db is an isolated tmp file, so the suite is deterministic and never touches
live gemma / the knowledge service. Tracing is disabled.

The login password is pinned via the LOGIN_PASSWORD env (orch.session reads os.environ
before the .env file), so these tests do not depend on the repo's real secret.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch import session as _session
from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_RW = "rw-secret-test"
_RO = "ro-secret-test"
_PASSWORD = "phase-d-prep-pw"
_COOKIE = "omc_session"


@pytest.fixture(autouse=True)
def _pin_password_and_reset():
    """Pin the login password deterministically + reset the in-memory session store."""
    prev = os.environ.get("LOGIN_PASSWORD")
    os.environ["LOGIN_PASSWORD"] = _PASSWORD
    _session._reset_for_tests()
    try:
        yield
    finally:
        _session._reset_for_tests()
        if prev is None:
            os.environ.pop("LOGIN_PASSWORD", None)
        else:
            os.environ["LOGIN_PASSWORD"] = prev


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_dprep.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


def _client(tmp_db: str) -> TestClient:
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=True,
        rw_key=_RW,
        ro_key=_RO,
    )
    return TestClient(app)


# ── /api/login + cookie session ───────────────────────────────────────────────
def test_login_sets_omc_session_cookie(tmp_db):
    with _client(tmp_db) as client:
        resp = client.post("/api/login", json={"password": _PASSWORD})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # Set-Cookie carries the opaque omc_session token (what auth_client reads).
        assert _COOKIE in resp.cookies
        assert resp.cookies[_COOKIE]


def test_login_wrong_password_is_200_ok_false_no_cookie(tmp_db):
    with _client(tmp_db) as client:
        resp = client.post("/api/login", json={"password": "wrong"})
        assert resp.status_code == 200  # never 401 on the login route (US-G3-4 parity)
        assert resp.json() == {"ok": False}
        assert _COOKIE not in resp.cookies


def test_auth_status_reflects_session(tmp_db):
    with _client(tmp_db) as client:
        assert client.get("/api/auth/status").json() == {"authed": False}
        client.post("/api/login", json={"password": _PASSWORD})
        assert client.get("/api/auth/status").json() == {"authed": True}


# ── cookie auth authorizes the adapter routes (no x-api-key needed) ───────────
def test_cookie_authorizes_write_route_without_api_key(tmp_db):
    with _client(tmp_db) as client:
        client.post(
            "/api/login", json={"password": _PASSWORD}
        )  # TestClient keeps cookie
        # /api/run is a write route; the session cookie alone authorizes it (no key).
        resp = client.post("/api/run", json={"workflow": "make a clicker game"})
        assert resp.status_code == 200
        assert "task_id" in resp.json()


def test_no_cookie_no_key_is_401(tmp_db):
    with _client(tmp_db) as client:
        assert client.get("/api/inbox").status_code == 401
        assert client.post("/api/inbound", json={}).status_code == 401


def test_xapikey_still_works_alongside_cookie(tmp_db):
    with _client(tmp_db) as client:
        # RO key reads inbox; RW key writes run — both without a cookie.
        assert client.get("/api/inbox", headers={"x-api-key": _RO}).status_code == 200
        assert (
            client.post(
                "/api/run",
                json={"workflow": "make a clicker game"},
                headers={"x-api-key": _RW},
            ).status_code
            == 200
        )


# ── seq -> thread_id mapping + inbox approve resumes the cycle ────────────────
def _login(client: TestClient) -> None:
    assert client.post("/api/login", json={"password": _PASSWORD}).status_code == 200


def _drive_to_interrupt(client: TestClient) -> int:
    """Start a cycle, wait for the INTERRUPT_REQUESTED frame, return its seq.

    ★P3 §20: cycle hitl 무조건 interrupt 은퇴 → authority_gate. interrupt 를 유발하려면
    owner-reserved(출시/금전/지출) 지시로 ESCALATE 시킨다(stub 은 directive 무관 build 라우팅).
    """
    resp = client.post(
        "/api/run",
        json={"workflow": "스팀 출시용 클리커 게임 — 광고비 실금전 지출 승인 필요"},
    )
    assert resp.status_code == 200
    # The cycle runs as a background task that ESCALATEs at the authority gate and emits the
    # INTERRUPT_REQUESTED frame. Poll /api/inbox until the review item appears.
    for _ in range(200):
        review = client.get("/api/inbox").json()["review"]
        if review:
            return int(review[0]["interrupt_id"])
        import time

        time.sleep(0.05)
    raise AssertionError("no INTERRUPT_REQUESTED frame surfaced")


def test_inbox_review_carries_interrupt_id_seq(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        seq = _drive_to_interrupt(client)
        assert isinstance(seq, int) and seq >= 1
        review = client.get("/api/inbox").json()["review"]
        item = review[0]
        # The bot backfill (bot.py) reads exactly these keys.
        assert item["interrupt_id"] == seq
        assert item["thread_id"]
        assert item["run_id"].startswith("chat-")


def test_inbox_approve_resumes_and_emits_bluf(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        seq = _drive_to_interrupt(client)
        resp = client.post(f"/api/inbox/{seq}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["decision"] == "approve"
        # A BLUF TEXT_MESSAGE (role=assistant) + RUN_FINISHED were emitted.
        frames = client.get("/outbound/frames").json()["frames"]
        types = [f["payload"]["type"] for f in frames]
        assert "TEXT_MESSAGE" in types and "RUN_FINISHED" in types
        bluf = next(
            f["payload"] for f in frames if f["payload"]["type"] == "TEXT_MESSAGE"
        )
        assert bluf["role"] == "assistant" and bluf["content"].startswith("결론")


def test_inbox_reject_resumes_with_reject(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        seq = _drive_to_interrupt(client)
        resp = client.post(f"/api/inbox/{seq}/reject")
        assert resp.status_code == 200
        assert resp.json()["decision"] == "reject"


def test_inbox_double_approve_is_404_handled(tmp_db):
    # rest_client treats 404 as handled/idempotent; a re-approve must be 404, not 5xx.
    with _client(tmp_db) as client:
        _login(client)
        seq = _drive_to_interrupt(client)
        assert client.post(f"/api/inbox/{seq}/approve").status_code == 200
        assert client.post(f"/api/inbox/{seq}/approve").status_code == 404


def test_inbox_unknown_seq_is_404(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        assert client.post("/api/inbox/999999/approve").status_code == 404


# ── /api/inbound ──────────────────────────────────────────────────────────────
def test_inbound_accepts_and_encodes_chat_run_id(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        resp = client.post(
            "/api/inbound",
            json={"text": "안녕하세요", "message_id": "m-1", "target_role": "pm"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert "task_id" in body
        # chat-{role}-{message_id} encoding (inbound.py:112) — drives multi-bot dispatch.
        assert body["run_id"] == "chat-pm-m-1"


def test_inbound_validates_required_fields(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        assert client.post("/api/inbound", json={"message_id": "x"}).status_code == 400
        assert client.post("/api/inbound", json={"text": "hi"}).status_code == 400


def test_inbound_is_idempotent_on_message_id(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        first = client.post(
            "/api/inbound", json={"text": "hi", "message_id": "dup-1"}
        ).json()
        second = client.post(
            "/api/inbound", json={"text": "hi again", "message_id": "dup-1"}
        )
        assert second.status_code == 200
        body = second.json()
        assert body["status"] == "duplicate"
        assert body["run_id"] == first["run_id"]
        assert body["task_id"] == first["task_id"]


def test_inbound_default_role_when_unaddressed(tmp_db):
    with _client(tmp_db) as client:
        _login(client)
        body = client.post(
            "/api/inbound", json={"text": "hello", "message_id": "m-2"}
        ).json()
        assert body["run_id"] == "chat-default-m-2"
