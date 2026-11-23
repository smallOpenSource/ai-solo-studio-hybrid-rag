"""★ SEQ DURABILITY TEST (Phase E) — orch's outbound seq survives a restart.

Pins the production must-fix from the Phase D cutover (renewal-phaseD-cutover.md
§Deviation / Anomaly 1): orch's ``OutboundEmitter`` seq was in-memory and reset to 0
on every restart, while the live bot's persisted SSE cursor (``state/discord/cursor.json``
``last_posted_seq``) is high — so after any orch restart the bot's ``frames_after(cursor)``
is empty and its ``should_post(seq)=seq>cursor`` gate double-skips every frame, and Discord
delivery SILENTLY drops.

The restart simulation: emit N frames against a persistence file (seq advances 1..N),
then construct a BRAND-NEW emitter against the SAME file (this is exactly what a process
restart does — a fresh ``OutboundEmitter`` instance, same on-disk state) and assert the
NEW emitter's first emit is N+1, NOT 1. The seq never regresses across the restart.

``seq_path=None`` keeps the legacy pure-in-memory behavior (the existing outbound/contract
tests construct ``OutboundEmitter()`` with no path and must stay GREEN).
"""

from __future__ import annotations

import json

from orch.outbound import (
    OutboundEmitter,
    _load_last_seq,
    encode_chat_run_id,
    seed_last_seq,
)


def _emitter(seq_path):
    """Construct an emitter bound to a persistence file (helper for clarity)."""
    return OutboundEmitter(seq_path=str(seq_path))


# ── THE restart-simulation proof ──────────────────────────────────────────────
def test_seq_persists_across_emitter_restart(tmp_path):
    """Emit N frames, build a NEW emitter on the same file, assert it continues at N+1."""
    seq_file = tmp_path / "emitter_seq.json"
    run_id = encode_chat_run_id("pm", "42")

    # First lifetime: emit N frames -> seq advances 1..N.
    e1 = _emitter(seq_file)
    n = 9  # mirror the live bot cursor (last_posted_seq=9) at deploy time.
    seqs_run1 = [
        e1.emit_text_message(run_id=run_id, content=f"m{i}").seq for i in range(n)
    ]
    assert seqs_run1 == list(range(1, n + 1))  # strictly 1..N

    # Simulate an orch RESTART: a brand-new emitter against the SAME persistence file.
    # (A process restart is exactly this — fresh instance, same on-disk last_seq.)
    e2 = _emitter(seq_file)

    # The crux: it must continue ABOVE N, never reset to 0/1 (no silent drop).
    next_frame = e2.emit_text_message(run_id=run_id, content="after-restart")
    assert next_frame.seq == n + 1, (
        "restarted emitter regressed -> bot would drop frames"
    )

    # And monotonicity holds forward from there.
    f2 = e2.emit_run_finished(run_id=run_id)
    assert f2.seq == n + 2


def test_persisted_last_seq_is_durable_high_water_on_disk(tmp_path):
    """After emits, the file holds the latest seq (atomic high-water mark)."""
    seq_file = tmp_path / "emitter_seq.json"
    e = _emitter(seq_file)
    for _ in range(3):
        e.emit_text_message(run_id=encode_chat_run_id(None, "x"), content="m")
    on_disk = json.loads(seq_file.read_text(encoding="utf-8"))
    assert on_disk == {"last_seq": 3}
    assert _load_last_seq(str(seq_file)) == 3


def test_absent_or_corrupt_file_degrades_to_zero(tmp_path):
    """A missing/garbage file starts the counter at 0 — in-memory parity, never wedges."""
    missing = tmp_path / "nope.json"
    assert _load_last_seq(str(missing)) == 0
    # First boot with no file: emit starts at 1 (then self-provisions the file).
    e = _emitter(missing)
    assert (
        e.emit_text_message(run_id=encode_chat_run_id(None, "x"), content="m").seq == 1
    )
    assert missing.exists()

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert _load_last_seq(str(corrupt)) == 0


def test_in_memory_emitter_unchanged_when_no_path(tmp_path):
    """seq_path=None keeps the legacy pure-in-memory emitter — each instance resets to 0.

    This is the contract the existing outbound/contract tests rely on (they construct
    OutboundEmitter() with no args); the persistence must be strictly additive.
    """
    e1 = OutboundEmitter()  # no seq_path
    assert e1.emit_text_message(run_id="chat-pm-1", content="m").seq == 1
    assert e1.emit_text_message(run_id="chat-pm-1", content="m").seq == 2
    # A second in-memory instance does NOT see the first's seq (no shared persistence).
    e2 = OutboundEmitter()
    assert e2.emit_text_message(run_id="chat-pm-1", content="m").seq == 1


def test_seed_lifts_above_cursor_and_never_lowers(tmp_path):
    """seed_last_seq is the cutover continuity step: lift to >= max(seed, current)."""
    seq_file = tmp_path / "emitter_seq.json"
    # Seed to the live cursor (9). A restarted emitter then continues at 10.
    assert seed_last_seq(str(seq_file), 9) == 9
    e = _emitter(seq_file)
    assert e.emit_text_message(run_id="chat-pm-1", content="m").seq == 10

    # Re-seeding with a LOWER value must not regress the persisted high-water mark.
    assert seed_last_seq(str(seq_file), 3) == 10  # stays at 10 (max), not 3
    e2 = _emitter(seq_file)
    assert e2.emit_text_message(run_id="chat-pm-1", content="m").seq == 11
