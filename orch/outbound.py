"""orch.outbound — the outbound event-emit path (RC-1).

The new core's THREE outbound obligations (renewal §25 Phase 6/7, RC-1):

  (i)  emit AG-UI-shaped events into a fanout-readable store/stream —
       ``TEXT_MESSAGE`` (role="assistant" + content), ``RUN_FINISHED``,
       ``INTERRUPT_REQUESTED``. **Keep the existing 11-event contract — add NO
       new event types.** We REUSE the repo's UNCHANGED event dataclasses
       (office.events) so every emitted payload is byte-for-byte what the live
       fanout/mapping already consume (``event.to_dict()``).
  (ii) reproduce the ``chat-{role}-{id}`` run_id encoding (web_server/inbound.py
       :112) so the multi-bot dispatcher (bot.py:_role_from_run_id) routes the
       BLUF to the addressed employee's bot identity.
  (iii) preserve ``seq`` monotonicity so the bot's cursor stays idempotent
       (bot.py:455-470 advance_cursor). A single monotonic counter stamps every
       emitted frame; the store hands frames out in (seq, payload) order.

This module does NOT import discord and does NOT modify office/ — it constructs
the existing event dataclasses and serializes them, exactly as the worker does.
``post_text`` (mapping.py:165) is a CONSUMER-side toggle: this emitter always
writes the assistant TEXT_MESSAGE; whether the bot posts it is decided downstream
by ``event_to_message(seq, payload, toggles)``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Inject the repo root so the UNCHANGED office.events dataclasses import cleanly.
_REPO_ROOT = Path(__file__).resolve().parents[1]  # /opt/studio
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from office.events import (  # noqa: E402  (after sys.path inject; originals untouched)
    InterruptRequested,
    RunFinished,
    TextMessage,
)


def encode_chat_run_id(role: str | None, message_id: str) -> str:
    """Reproduce the ``chat-{role}-{id}`` run_id encoding (inbound.py:112).

    role=None/"" => "default" (primary assistant bot posts). A concrete role
    (single hyphen-free token) routes to that employee's bot via
    bot.py::_role_from_run_id, which splits on '-' and reads parts[1].
    """
    conv_role = role if isinstance(role, str) and role else "default"
    return f"chat-{conv_role}-{message_id}"


def _now_iso() -> str:
    """ISO-8601 UTC timestamp (matches office.events ts convention)."""
    return datetime.now(timezone.utc).isoformat()


# ── seq durability (Phase E): atomic file persistence of the high-water counter ──
def _load_last_seq(seq_path: str) -> int:
    """Read the persisted ``{"last_seq": N}`` high-water mark (0 if absent/corrupt).

    A missing file (first boot) or any read/parse error degrades to 0 — identical to the
    pre-Phase-E in-memory start — so persistence is purely additive and can never wedge
    the emitter. A present, well-formed file resumes the monotonic counter at N.
    """
    try:
        with open(seq_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        value = int(raw.get("last_seq", 0))
        return value if value > 0 else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _persist_last_seq(seq_path: str, last_seq: int) -> None:
    """Atomically persist the high-water ``last_seq`` via write-temp + ``os.replace``.

    ``os.replace`` is atomic on POSIX, so a reader (or a crash) never observes a partial
    file: the counter file is either the old value or the new one. The parent dir is
    created on demand so the first emit self-provisions ``state/orch/``.
    """
    path = Path(seq_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"last_seq": int(last_seq)}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def seed_last_seq(seq_path: str, value: int) -> int:
    """Seed the persistence file to ``max(value, current)`` — the cutover continuity step.

    Used once at deploy time to lift the restarted emitter's start ABOVE the live bot's
    cursor (``max(orch current seq, bot last_posted_seq)``) so the FIRST restart of the
    durable code does not re-emit seqs the bot already consumed. Never lowers an existing
    high-water mark. Returns the value actually written.
    """
    target = max(int(value), _load_last_seq(seq_path))
    _persist_last_seq(seq_path, target)
    return target


@dataclass
class EmittedFrame:
    """One fanout-readable frame: a monotonic seq + the event's to_dict payload.

    Mirrors what an SSE consumer sees (``id: <seq>`` + ``data: <json>``) and what
    ``event_to_message(seq, payload, toggles)`` is fed.
    """

    seq: int
    payload: dict[str, Any]


@dataclass
class OutboundEmitter:
    """In-process, fanout-readable event store for the new core's outbound path.

    Thread-safe monotonic ``seq`` stamping (the live fanout is a single cursor;
    monotonicity is the idempotency contract). ``frames`` is append-only and read
    in order, exactly like the EventStore the live SSE poller drains.

    ★ Phase E — ``seq`` DURABILITY across restarts. The bot persists its SSE cursor
    (``state/discord/cursor.json`` ``last_posted_seq``); a fresh in-memory ``_seq``
    that resets to 0 on every orch restart sits BELOW that cursor, so the bot's
    ``frames_after(cursor)`` is empty and ``should_post(seq)=seq>cursor`` double-skips
    every post-restart frame — Discord delivery silently drops (Phase D §Deviation,
    Anomaly 1). When ``seq_path`` is given, ``_seq`` is LOADED on construction and the
    NEW high-water mark is atomically persisted (``os.replace``) on every emit, so a
    restarted emitter continues ABOVE the bot's cursor and NEVER regresses. The
    ``frames`` list itself is in-process (the SSE poller replays from its cursor); only
    the monotonic ``_seq`` counter must survive a restart, which is exactly what the
    bot's cursor reads. ``seq_path=None`` keeps the pure in-memory behavior (tests).

    It also records the ``seq -> thread_id`` mapping for every INTERRUPT_REQUESTED
    frame it stamps. This is the bridge the Discord approve button needs: the adapter
    reads the frame's ``seq`` (sse_client) / the inbox ``interrupt_id`` and POSTs
    ``/api/inbox/{seq}/approve`` — orch must turn that ``seq`` back into the graph
    thread to resume (``Command(resume=)``). The live web_server gets this for free
    because its ``seq`` IS the EventStore row id of the interrupt; orch reconstructs
    the same correlation here.
    """

    seq_path: str | None = None
    _seq: int = 0
    _frames: list[EmittedFrame] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # seq (of an INTERRUPT_REQUESTED frame) -> the graph thread_id paused at that gate.
    _interrupt_seq_to_thread: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Load the persisted high-water ``last_seq`` so emit continues at last_seq+1.

        Durable + monotonic: a corrupt/absent file degrades to 0 (in-memory parity), but
        a present file with ``last_seq=N`` resumes the counter at N (next emit => N+1).
        ``max(self._seq, ...)`` guards against an explicit constructor ``_seq`` being
        silently lowered by an older/missing file (never regress).
        """
        if self.seq_path:
            self._seq = max(self._seq, _load_last_seq(self.seq_path))

    def _emit(self, event: Any) -> EmittedFrame:
        """Stamp the next monotonic seq and append the event's to_dict payload.

        Persists the new high-water ``_seq`` BEFORE releasing the lock (atomic
        ``os.replace``), so a crash right after an emit cannot lose the seq the bot may
        already have consumed — the restart resumes strictly above it (no regression,
        no silent drop).
        """
        with self._lock:
            self._seq += 1
            frame = EmittedFrame(seq=self._seq, payload=event.to_dict())
            self._frames.append(frame)
            if self.seq_path:
                _persist_last_seq(self.seq_path, self._seq)
            return frame

    # ── The three outbound obligations (existing event types only) ────────────

    def emit_text_message(
        self,
        run_id: str,
        content: str,
        role: str = "assistant",
    ) -> EmittedFrame:
        """Emit a TEXT_MESSAGE (the BLUF the bot posts as the employee).

        Uses the UNCHANGED office.events.TextMessage dataclass, so the payload is
        exactly what mapping.event_to_message expects (role + content). The bot
        only posts assistant messages when ``post_text`` is on (mapping.py:165).
        """
        return self._emit(
            TextMessage(run_id=run_id, ts=_now_iso(), role=role, content=content)
        )

    def emit_run_finished(
        self,
        run_id: str,
        workflow: str = "",
        status: str = "success",
    ) -> EmittedFrame:
        """Emit a RUN_FINISHED lifecycle event (cycle completion)."""
        return self._emit(
            RunFinished(run_id=run_id, ts=_now_iso(), workflow=workflow, status=status)
        )

    def emit_interrupt_requested(
        self,
        run_id: str,
        tool: str = "",
        risk: str = "needs-approval",
        payload: dict[str, Any] | None = None,
        node_id: str = "hitl_gate",
        thread_id: str | None = None,
    ) -> EmittedFrame:
        """Emit an INTERRUPT_REQUESTED event (HITL owner-approval gate).

        When ``thread_id`` is given, the frame's monotonic ``seq`` is recorded as the
        key into that graph thread, so a later ``/api/inbox/{seq}/approve`` (the adapter
        button) resolves the right interrupt (``Command(resume=)``).
        """
        frame = self._emit(
            InterruptRequested(
                run_id=run_id,
                ts=_now_iso(),
                node_id=node_id,
                tool=tool,
                risk=risk,
                payload=payload or {},
            )
        )
        if thread_id is not None:
            with self._lock:
                self._interrupt_seq_to_thread[frame.seq] = thread_id
        return frame

    # ── seq -> thread_id bridge (the Discord approve button needs this) ────────

    def thread_for_seq(self, seq: int) -> str | None:
        """Return the graph thread_id recorded for an INTERRUPT_REQUESTED ``seq``.

        None when the seq was never an interrupt frame (the adapter treats that as a
        handled/404 no-op, matching rest_client's 404 == handled policy).
        """
        with self._lock:
            return self._interrupt_seq_to_thread.get(seq)

    def pending_interrupts(self) -> list[dict[str, Any]]:
        """List recorded interrupt frames as inbox ``review`` items (newest insert order).

        Each item carries ``interrupt_id`` (= the frame seq the adapter POSTs to
        approve/reject) plus the interrupt payload, mirroring the live
        ``web_server/inbox.py`` ``review`` shape the bot's backfill reads
        (bot.py:_backfill_persistent_views reads ``item['interrupt_id']``).
        """
        with self._lock:
            seq_to_thread = dict(self._interrupt_seq_to_thread)
            by_seq = {
                f.seq: f.payload
                for f in self._frames
                if f.payload.get("type") == "INTERRUPT_REQUESTED"
            }
        review: list[dict[str, Any]] = []
        for seq, thread_id in seq_to_thread.items():
            payload = by_seq.get(seq, {})
            review.append(
                {
                    "interrupt_id": seq,
                    "thread_id": thread_id,
                    "run_id": payload.get("run_id", ""),
                    "tool": payload.get("tool", ""),
                    "risk": payload.get("risk", ""),
                    "node_id": payload.get("node_id", "hitl_gate"),
                    "payload": payload.get("payload", {}),
                    "ts": payload.get("ts", ""),
                }
            )
        return review

    def forget_interrupt_seq(self, seq: int) -> None:
        """Drop a resolved interrupt's seq mapping (idempotent re-approve -> 404/handled)."""
        with self._lock:
            self._interrupt_seq_to_thread.pop(seq, None)

    # ── Fanout-side reads (what the SSE poller / mapping consume) ──────────────

    def frames(self) -> list[EmittedFrame]:
        """Return all emitted frames in (monotonic) seq order — a snapshot copy."""
        with self._lock:
            return list(self._frames)

    def frames_after(self, cursor: int) -> list[EmittedFrame]:
        """Return frames with seq > cursor (the bot's at-least-once cursor read)."""
        with self._lock:
            return [f for f in self._frames if f.seq > cursor]
