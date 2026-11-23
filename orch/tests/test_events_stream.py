"""SSE bridge contract tests (Phase C) for orch.app ``/events/stream``.

Proves orch's outbound SSE stream is byte-compatible with the EXISTING live
consumers so the React dashboard SSE client and the Discord sse_client parse it
unchanged:
  * the stream emits ``id: {seq}\\ndata: {json}\\n\\n`` frames (web_server format).
  * the EXISTING, UNCHANGED ``adapters.discord.sse_client._parse_sse_lines`` parses
    orch's raw stream bytes into (seq, payload) tuples — the same parser the live
    bot uses (httpx-only, no discord import).
  * after a full cycle, the stream surfaces the BLUF TEXT_MESSAGE + RUN_FINISHED
    (the AG-UI frames the dashboard/bot render), seqs strictly monotonic.
  * ``Last-Event-ID`` resumes after a cursor (idempotent reconnect, no loss).

Driven through the FastAPI TestClient with a stub caller/searcher + isolated tmp
db; ``idle_close_ms`` bounds the stream so reads terminate (no hang). auth=False
(the gate is covered by test_app_auth).
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# The EXISTING, UNCHANGED Discord SSE parser (sys.path inject only) — proves the
# live consumer reads orch's stream. httpx-only; no discord import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from adapters.discord.sse_client import _parse_sse_lines  # noqa: E402

from orch.app import create_app  # noqa: E402
from orch.tests.conftest import stub_caller, stub_searcher  # noqa: E402


def _client(tmp_db: str) -> TestClient:
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=False,
    )
    return TestClient(app)


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_ssetest.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


def _drive_full_cycle(client: TestClient) -> str:
    """Run one cycle to completion (emits the outbound frames); return thread_id."""
    run = client.post("/api/run", json={"workflow": "make a clicker game"}).json()
    thread_id = run["thread_id"]
    # Wait for the HITL interrupt to surface (cycle reached the gate).
    for _ in range(50):
        pend = client.get("/decisions/pending").json()["pending"]
        if any(p["thread_id"] == thread_id for p in pend):
            break
        time.sleep(0.05)
    # Resolve -> qa+finalize run -> BLUF TEXT_MESSAGE + RUN_FINISHED emitted.
    client.post(f"/decisions/{thread_id}/resolve", json={"choice": "approve"})
    return thread_id


def _read_stream(client: TestClient, last_event_id: int | None = None) -> str:
    """Read the bounded SSE stream as raw text (idle_close_ms terminates it)."""
    params = "idle_close_ms=400"
    if last_event_id is not None:
        params += f"&last_event_id={last_event_id}"
    with client.stream("GET", f"/events/stream?{params}") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        return "".join(chunk for chunk in resp.iter_text())


# ── orch SSE frames are parseable by the EXISTING discord parser ──────────────
def test_stream_frames_parse_with_existing_discord_parser(tmp_db):
    with _client(tmp_db) as client:
        _drive_full_cycle(client)
        raw = _read_stream(client)
        # Raw bytes use the web_server SSE format: id: / data: lines.
        assert "id: " in raw and "data: " in raw
        # The EXISTING discord parser turns the raw stream into (seq, payload).
        parsed = _parse_sse_lines(raw.split("\n"))
        assert parsed, "discord parser yielded no frames from orch's stream"
        types = [p[1].get("type") for p in parsed]
        # The cycle's outbound AG-UI frames are present (existing event types).
        assert "TEXT_MESSAGE" in types
        assert "RUN_FINISHED" in types
        # seqs strictly monotonic (idempotent cursor contract).
        seqs = [s for s, _ in parsed]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)


def test_stream_bluf_text_message_carries_assistant_content(tmp_db):
    with _client(tmp_db) as client:
        _drive_full_cycle(client)
        parsed = _parse_sse_lines(_read_stream(client).split("\n"))
        texts = [p for _, p in parsed if p.get("type") == "TEXT_MESSAGE"]
        assert texts, "no TEXT_MESSAGE frame on the stream"
        bluf = texts[0]
        assert bluf.get("role") == "assistant"
        assert bluf.get("content", "").startswith("결론")


# ── Last-Event-ID resumes after a cursor (no loss, no dup) ─────────────────────
def test_last_event_id_resumes_after_cursor(tmp_db):
    with _client(tmp_db) as client:
        _drive_full_cycle(client)
        full = _parse_sse_lines(_read_stream(client).split("\n"))
        assert len(full) >= 2
        cut = full[0][0]  # resume strictly after the first frame's seq
        resumed = _parse_sse_lines(_read_stream(client, last_event_id=cut).split("\n"))
        resumed_seqs = [s for s, _ in resumed]
        # Only frames newer than the cursor reappear (no frame <= cut).
        assert all(s > cut for s in resumed_seqs)
        # And the later frames are all still delivered (no loss).
        assert resumed_seqs == [s for s, _ in full if s > cut]


# ── React dashboard contract: ?last_seq= resume + isOfficeEvent-shaped payloads ─
def test_dashboard_contract_last_seq_query_and_payload_shape(tmp_db):
    """The React dashboard (web/src/api/sse.ts) resumes via ``?last_seq=`` and
    requires each payload to carry ``type`` (known), ``run_id``, ``ts`` (the
    isOfficeEvent guard). Prove orch satisfies both."""
    known_types = {
        "RUN_STARTED",
        "RUN_FINISHED",
        "RUN_ERROR",
        "TEXT_MESSAGE",
        "TOOL_CALL_START",
        "TOOL_CALL_RESULT",
        "STATE_SNAPSHOT",
        "STATE_DELTA",
        "INTERRUPT_REQUESTED",
        "INTERRUPT_RESOLVED",
        "NODE_FAILED",
    }
    with _client(tmp_db) as client:
        _drive_full_cycle(client)
        full = _parse_sse_lines(_read_stream(client).split("\n"))
        # isOfficeEvent field requirements on every frame the dashboard consumes.
        for _, payload in full:
            assert isinstance(payload.get("type"), str)
            assert payload["type"] in known_types
            assert isinstance(payload.get("run_id"), str) and payload["run_id"]
            assert isinstance(payload.get("ts"), str) and payload["ts"]
        # ?last_seq= (the React belt-and-suspenders query) resumes after a cursor.
        cut = full[0][0]
        with client.stream(
            "GET", f"/events/stream?idle_close_ms=400&last_seq={cut}"
        ) as resp:
            raw = "".join(chunk for chunk in resp.iter_text())
        resumed_seqs = [s for s, _ in _parse_sse_lines(raw.split("\n"))]
        assert all(s > cut for s in resumed_seqs)
