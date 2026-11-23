"""★ DROP-IN CONTRACT TEST — the key Phase D-prep proof.

Drives the EXISTING, UNCHANGED Discord adapter modules against a LIVE orch server
(uvicorn on 127.0.0.1, ephemeral port, explicit shutdown) to prove the FULL loop works
with the adapter byte-unchanged — i.e. a later ``WEB_BASE_URL`` flip to orch needs ZERO
adapter edits:

    auth_client.login            -> cookie (omc_session)
    rest_client.post_run         -> starts a cycle (cookie auth)
    sse_client.consume_events    -> consumes orch's /events/stream frames; the seq of the
                                    INTERRUPT_REQUESTED frame is what the approve button uses
    rest_client.approve          -> /api/inbox/{seq}/approve resumes the cycle -> BLUF frame
    rest_client.post_inbound     -> /api/inbound starts a chat cycle -> assistant reply frame

The orch app is built with a STUB caller/searcher and an isolated tmp checkpoint db, so the
proof is deterministic (no live gemma / no knowledge service). The login password is pinned
via the LOGIN_PASSWORD env. The server binds 127.0.0.1 only and is shut down explicitly.

Marked ``slow`` ONLY because it boots a real socket server (still deterministic, no model
network); it runs in the default suite here but can be deselected with -m 'not slow'.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

# The UNCHANGED adapter modules — imported exactly as the live bot imports them.
from adapters.discord import auth_client, rest_client
from adapters.discord.sse_client import consume_events

from orch import session as _session
from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_PASSWORD = "dropin-contract-pw"
_HOST = "127.0.0.1"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((_HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_orch():
    """Boot a real orch server (stubbed models) on 127.0.0.1:<port>; yield base_url.

    Explicit lifecycle: a uvicorn.Server in a daemon thread, bounded readiness wait,
    explicit ``should_exit`` shutdown + join. No pkill, no live-service touch.
    """
    prev_pw = os.environ.get("LOGIN_PASSWORD")
    os.environ["LOGIN_PASSWORD"] = _PASSWORD
    _session._reset_for_tests()

    db = tempfile.mktemp(suffix="_dropin.db")
    port = _free_port()
    app = create_app(
        ckpt_db=db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=True,
    )
    config = uvicorn.Config(app, host=_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{_HOST}:{port}"
    # Bounded readiness wait (<=10s) — poll the open /healthz.
    ready = False
    for _ in range(100):
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200 and r.json().get("ok"):
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    assert ready, "orch server did not become ready"

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        _session._reset_for_tests()
        if prev_pw is None:
            os.environ.pop("LOGIN_PASSWORD", None)
        else:
            os.environ["LOGIN_PASSWORD"] = prev_pw
        for s in ("", "-wal", "-shm"):
            p = Path(db + s)
            if p.exists():
                p.unlink()


async def _consume_until_interrupt(base_url: str, cookie: str) -> tuple[int, list]:
    """Consume orch's SSE stream (UNCHANGED sse_client) until INTERRUPT_REQUESTED.

    Returns (interrupt_seq, all_frames_seen). Bounded: idle_close via max_retries +
    an asyncio timeout so a stuck stream cannot hang the suite.
    """
    seen: list[tuple[int, dict]] = []
    interrupt_seq = -1

    async def _drain():
        nonlocal interrupt_seq
        # max_retries bounds reconnects; the stream stays open (long-lived) so we break
        # out as soon as we see the interrupt frame.
        async for seq, payload in consume_events(
            base_url, last_event_id=0, max_retries=3, cookie=cookie
        ):
            seen.append((seq, payload))
            if payload.get("type") == "INTERRUPT_REQUESTED":
                interrupt_seq = seq
                return

    await asyncio.wait_for(_drain(), timeout=20)
    return interrupt_seq, seen


@pytest.mark.slow
def test_full_dropin_loop_with_unchanged_adapter(live_orch):
    base_url = live_orch

    async def _run():
        # 1) login -> cookie (the UNCHANGED auth_client builds its own httpx client).
        token = await auth_client.login(base_url, _PASSWORD)
        assert token, "auth_client.login did not return an omc_session token"
        cookie = f"omc_session={token}"

        # 2) post_run -> starts a cycle (cookie auth, ZERO adapter edits).
        run = await rest_client.post_run("make a clicker game", base_url, cookie=cookie)
        assert run.handled and run.status == 200
        assert run.task_id is not None

        # 3) consume the SSE stream with the UNCHANGED sse_client until the interrupt
        #    frame; its seq is what the approve button POSTs.
        interrupt_seq, frames = await _consume_until_interrupt(base_url, cookie)
        assert interrupt_seq >= 1, f"no INTERRUPT_REQUESTED frame (saw {frames})"

        # 4) approve via the UNCHANGED rest_client -> /api/inbox/{seq}/approve resumes.
        appr = await rest_client.approve(interrupt_seq, base_url, cookie=cookie)
        assert appr.handled and appr.status == 200

        # The resume emits the BLUF as a TEXT_MESSAGE(role=assistant) + RUN_FINISHED.
        # Verify via the read endpoint (the same frames the SSE bridge/mapping deliver).
        async with httpx.AsyncClient() as c:
            fr = await c.get(
                f"{base_url}/outbound/frames", headers={"Cookie": cookie}, timeout=5
            )
        payloads = [f["payload"] for f in fr.json()["frames"]]
        types = [p["type"] for p in payloads]
        assert "TEXT_MESSAGE" in types and "RUN_FINISHED" in types
        bluf = next(p for p in payloads if p["type"] == "TEXT_MESSAGE")
        assert bluf["role"] == "assistant" and bluf["content"].startswith("결론")

        # 5) post_inbound (human chat) via the UNCHANGED rest_client -> chat cycle ->
        #    assistant reply frame with the chat-{role}-{id} run_id.
        inb = await rest_client.post_inbound(
            "다음 단계 알려줘", "msg-42", base_url, cookie=cookie, target_role="pm"
        )
        assert inb.handled and inb.status == 200

        # Poll the read endpoint for the chat reply frame (bounded).
        reply = None
        for _ in range(100):
            async with httpx.AsyncClient() as c:
                fr = await c.get(
                    f"{base_url}/outbound/frames", headers={"Cookie": cookie}, timeout=5
                )
            for f in fr.json()["frames"]:
                p = f["payload"]
                if p["type"] == "TEXT_MESSAGE" and p.get("run_id") == "chat-pm-msg-42":
                    reply = p
                    break
            if reply is not None:
                break
            await asyncio.sleep(0.1)
        assert reply is not None, "chat reply frame (chat-pm-msg-42) never emitted"
        assert reply["role"] == "assistant" and reply["content"]

    asyncio.run(_run())


@pytest.mark.slow
def test_dropin_reject_and_idempotent_reapprove(live_orch):
    """reject works, and a re-approve of a resolved seq is 404==handled (no 5xx)."""
    base_url = live_orch

    async def _run():
        token = await auth_client.login(base_url, _PASSWORD)
        cookie = f"omc_session={token}"
        run = await rest_client.post_run("make a clicker game", base_url, cookie=cookie)
        assert run.handled
        interrupt_seq, _ = await _consume_until_interrupt(base_url, cookie)
        assert interrupt_seq >= 1

        rej = await rest_client.reject(interrupt_seq, base_url, cookie=cookie)
        assert rej.handled and rej.status == 200
        # Re-deciding the now-resolved seq is 404, which rest_client maps to handled=True.
        again = await rest_client.approve(interrupt_seq, base_url, cookie=cookie)
        assert again.handled and again.status == 404

    asyncio.run(_run())
