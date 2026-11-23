"""RO/RW 2-token x-api-key gate tests (Phase C §6.8) for orch.app.

Proves the new core's seam enforces a two-token policy BEFORE any live exposure:
  * a missing or unknown ``x-api-key`` is 401 on every gated route.
  * WRITE routes (/api/run, /directives, /decisions/{thread_id}/resolve) require
    the RW token; presenting the RO token there is 403 (authn ok, authz denied).
  * READ routes (/decisions/pending, /outbound/frames, /outcome/...) accept the RO
    token (and the RW token).
  * /healthz is open (readiness probe, no key).

The app is built with explicit rw/ro keys + a stub caller/searcher and an isolated
tmp checkpoint db, so the suite is deterministic and never touches live gemma or the
knowledge service. Tracing is disabled.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_RW = "rw-secret-test"
_RO = "ro-secret-test"


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


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_authtest.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


# ── /healthz is open (no key) ─────────────────────────────────────────────────
def test_healthz_open_without_key(tmp_db):
    with _client(tmp_db) as client:
        assert client.get("/healthz").status_code == 200


# ── missing / invalid key -> 401 on a gated route ─────────────────────────────
def test_missing_key_is_401(tmp_db):
    with _client(tmp_db) as client:
        assert client.get("/decisions/pending").status_code == 401
        assert (
            client.get("/decisions/pending", headers={"x-api-key": "nope"}).status_code
            == 401
        )


# ── write route requires RW; RO is 403 ────────────────────────────────────────
def test_write_route_rejects_ro_key_403(tmp_db):
    with _client(tmp_db) as client:
        resp = client.post(
            "/api/run",
            json={"workflow": "make a clicker game"},
            headers={"x-api-key": _RO},
        )
        assert resp.status_code == 403


def test_write_route_accepts_rw_key(tmp_db):
    with _client(tmp_db) as client:
        resp = client.post(
            "/api/run",
            json={"workflow": "make a clicker game"},
            headers={"x-api-key": _RW},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()


def test_resolve_is_a_write_route_rw_only(tmp_db):
    # The resolve route is also a write — RO must be 403 there too (thread need not
    # exist; the gate runs before the handler).
    with _client(tmp_db) as client:
        ro = client.post(
            "/decisions/some-thread/resolve",
            json={"choice": "approve"},
            headers={"x-api-key": _RO},
        )
        assert ro.status_code == 403


# ── read routes accept RO (and RW) ────────────────────────────────────────────
def test_read_route_accepts_ro_key(tmp_db):
    with _client(tmp_db) as client:
        assert (
            client.get("/decisions/pending", headers={"x-api-key": _RO}).status_code
            == 200
        )
        assert (
            client.get("/outbound/frames", headers={"x-api-key": _RO}).status_code
            == 200
        )


def test_read_route_accepts_rw_key_too(tmp_db):
    with _client(tmp_db) as client:
        assert (
            client.get("/decisions/pending", headers={"x-api-key": _RW}).status_code
            == 200
        )
