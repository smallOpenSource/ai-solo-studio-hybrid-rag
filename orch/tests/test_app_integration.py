"""Integration tests (FastAPI TestClient, model STUBBED) for orch.app.

Covers the Phase B integration matrix:
  * POST /api/run (the EXISTING Discord contract body) -> 200 + {task_id}.
  * GET /threads/by_task/{task_id} resolves task_id -> thread_id.
  * GET /decisions/pending is a point-read (aget_state) that surfaces the HITL
    interrupt with no deadlock while the cycle is in-flight.
  * POST /decisions/{thread_id}/resolve (approve AND revise) resumes the graph in
    the same process and completes (BLUF produced).

The app is built with a deterministic stub caller via create_app(caller=...) and an
isolated tmp checkpoint db, so no live gemma is touched. Tracing is disabled to
avoid global tracer-provider churn across tests.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

# P3 §20: stub 은 directive 무관 "build" 라우팅 → interrupt 여부는 authority_gate(directive 키워드).
# owner-reserved(출시/금전/지출) → ESCALATE → interrupt(HITL 경로 테스트용).
_ESCALATE = "스팀 출시용 클리커 게임 — 광고비 실금전 지출 승인 필요"


def _client(tmp_db: str) -> TestClient:
    # Phase B integration matrix: auth gate OFF (covered separately by the Phase C
    # token tests); inject the no-network knowledge searcher so the research step
    # is deterministic.
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=False,
    )
    return TestClient(app)


def _poll_pending(client: TestClient, thread_id: str, tries: int = 50):
    """Bounded poll for the HITL interrupt to surface on the given thread."""
    for _ in range(tries):
        resp = client.get("/decisions/pending")
        assert resp.status_code == 200
        match = [p for p in resp.json()["pending"] if p["thread_id"] == thread_id]
        if match:
            return match[0]
        time.sleep(0.05)
    return None


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_itest.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


# ── /api/run honors the existing rest_client.post_run body ────────────────────
def test_api_run_existing_contract_returns_task_id(tmp_db):
    with _client(tmp_db) as client:
        # post_run sends exactly this body: {"workflow", "mock"}.
        resp = client.post(
            "/api/run", json={"workflow": "make a clicker game", "mock": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data and isinstance(data["task_id"], int)
        assert "thread_id" in data
        # correlation endpoint resolves task_id -> thread_id.
        corr = client.get(f"/threads/by_task/{data['task_id']}")
        assert corr.status_code == 200
        assert corr.json()["thread_id"] == data["thread_id"]


def test_threads_by_task_unknown_is_404(tmp_db):
    with _client(tmp_db) as client:
        assert client.get("/threads/by_task/999999").status_code == 404


# ── /decisions/pending point-read surfaces the interrupt (no deadlock) ────────
def test_pending_point_read_surfaces_interrupt(tmp_db):
    with _client(tmp_db) as client:
        run = client.post("/api/run", json={"workflow": _ESCALATE}).json()
        pending = _poll_pending(client, run["thread_id"])
        assert pending is not None, "Escalate interrupt never surfaced"
        val = pending["value"]
        assert val["options"] == ["approve", "reject"]  # §20.4a Escalate 페이로드
        assert pending["next"] == ["authority_gate"]  # hitl_gate → authority_gate


# ── /decisions/resolve approve -> resume -> complete ──────────────────────────
def test_resolve_approve_completes_cycle(tmp_db):
    with _client(tmp_db) as client:
        run = client.post("/api/run", json={"workflow": _ESCALATE}).json()
        thread_id = run["thread_id"]
        assert _poll_pending(client, thread_id) is not None
        resolved = client.post(
            f"/decisions/{thread_id}/resolve", json={"choice": "approve"}
        )
        assert resolved.status_code == 200
        out = resolved.json()
        assert out["decision"] == "approve"
        assert out["route"] == "build"
        assert (out["artifacts"] or {}).get("qa")
        # KNOWLEDGE dimension: the research step cited the stub source + threaded it.
        assert (out["artifacts"] or {}).get("research")
        assert out["knowledge_source"] == "https://example.test/wiki/Free-to-play"
        assert out["knowledge_count"] == 1
        assert out["bluf"].startswith("결론")
        # stored outcome retrievable.
        oc = client.get(f"/outcome/{thread_id}")
        assert oc.status_code == 200
        assert oc.json()["bluf"].startswith("결론")


def test_resolve_reject_also_resumes_to_complete(tmp_db):
    with _client(tmp_db) as client:
        run = client.post("/api/run", json={"workflow": _ESCALATE}).json()
        thread_id = run["thread_id"]
        assert _poll_pending(client, thread_id) is not None
        resolved = client.post(
            f"/decisions/{thread_id}/resolve", json={"choice": "reject"}
        )
        assert resolved.status_code == 200
        assert resolved.json()["decision"] == "reject"
        assert resolved.json()["bluf"].startswith("결론")


def test_resolve_without_pending_interrupt_is_409(tmp_db):
    with _client(tmp_db) as client:
        # A thread that never started has no interrupt -> 409.
        resp = client.post(
            "/decisions/nonexistent-thread/resolve", json={"choice": "approve"}
        )
        assert resp.status_code == 409
