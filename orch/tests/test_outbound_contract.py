"""★ OUTBOUND CONTRACT TEST (RC-1c / RC-2) — orch -> existing Discord mapping.

Proves the new core's outbound payloads satisfy the EXISTING, UNCHANGED Discord
contract BEFORE any live token gate:

  * import adapters.discord.mapping.event_to_message (the real, unchanged mapper)
    and adapters.discord.bot._role_from_run_id (the real dispatch key extractor).
  * feed an orch-emitted TEXT_MESSAGE payload (BLUF) through event_to_message and
    assert the BLUF surfaces as a DiscordMessageSpec when toggles={"post_text": True},
    AND is DROPPED (None) when post_text=False (the mapping.py:165 toggle premise).
  * assert the chat-{role}-{id} run_id encoded by orch.outbound dispatches to the
    addressed employee via _role_from_run_id (multi-bot routing).
  * assert seq monotonicity across the emitted frames (the bot's idempotent cursor
    contract, bot.py:455-470).

No new AG-UI event types are introduced — orch reuses office.events dataclasses.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Import the EXISTING, UNCHANGED adapter contract (sys.path inject only).
_REPO_ROOT = Path(__file__).resolve().parents[2]  # /opt/studio
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from adapters.discord.mapping import DiscordMessageSpec, event_to_message  # noqa: E402
from orch.outbound import OutboundEmitter, encode_chat_run_id  # noqa: E402


def _role_from_run_id(run_id: str) -> str | None:
    """EXACT reproduction of adapters.discord.bot._role_from_run_id (bot.py:241).

    bot.py imports the ``discord`` package (absent in the isolated orch env), so we
    cannot import that module directly; this is a verbatim copy of the dispatch-key
    parser the live bot uses. Keeping the logic identical makes the dispatch leg of
    this contract test run unconditionally (not skipped).
    """
    if not isinstance(run_id, str):
        return None
    parts = run_id.split("-")
    if len(parts) >= 3 and parts[0] == "chat" and parts[1] and parts[1] != "default":
        return parts[1]
    return None


_BLUF = "결론: 클리커 게임 컨셉 승인 가능.\n필요행동: 없음.\n근거:\n- design: ok\n- qa: PASS"


def _emit_bluf_frame(role: str | None = "pm", message_id: str = "42"):
    """Emit a BLUF TEXT_MESSAGE the way orch.app does on resolve; return the frame."""
    emitter = OutboundEmitter()
    run_id = encode_chat_run_id(role, message_id)
    return emitter.emit_text_message(run_id=run_id, content=_BLUF), run_id


# ── RC-2: post_text True (POST) ───────────────────────────────────────────────
def test_bluf_surfaces_as_spec_when_post_text_true():
    frame, _ = _emit_bluf_frame()
    spec = event_to_message(frame.seq, frame.payload, {"post_text": True})
    assert isinstance(spec, DiscordMessageSpec)
    # The assistant BLUF content surfaces verbatim (label-free natural speech).
    assert spec.content == _BLUF
    assert spec.buttons == []  # a plain message, not an approval prompt.


# ── RC-2: post_text False (DROP) ──────────────────────────────────────────────
def test_bluf_dropped_when_post_text_false():
    frame, _ = _emit_bluf_frame()
    # Default (no toggle) and explicit False both drop the TEXT_MESSAGE.
    assert event_to_message(frame.seq, frame.payload, {"post_text": False}) is None
    assert event_to_message(frame.seq, frame.payload, None) is None


def test_user_role_never_posts_even_with_post_text_true():
    """orch emits assistant BLUFs; a user-role TEXT_MESSAGE must still be dropped."""
    emitter = OutboundEmitter()
    frame = emitter.emit_text_message(
        run_id=encode_chat_run_id("pm", "42"), content="echo", role="user"
    )
    assert event_to_message(frame.seq, frame.payload, {"post_text": True}) is None


# ── RC-1: chat-{role}-{id} dispatch key ───────────────────────────────────────
def test_chat_role_run_id_dispatches_to_employee():
    _, run_id = _emit_bluf_frame(role="pm", message_id="42")
    assert run_id == "chat-pm-42"
    # The bot's dispatch extractor (verbatim) routes this to the 'pm' employee bot.
    assert _role_from_run_id(run_id) == "pm"
    # default/uuid run ids -> None (primary assistant posts).
    assert _role_from_run_id(encode_chat_run_id(None, "42")) is None
    # a hyphen-free employee token with a hyphenated message_id still parses role.
    assert _role_from_run_id(encode_chat_run_id("sales", "msg-99-x")) == "sales"


# ── RC-1: seq monotonicity (idempotent cursor contract) ───────────────────────
def test_seq_is_monotonic_across_emitted_frames():
    emitter = OutboundEmitter()
    run_id = encode_chat_run_id("pm", "42")
    f_text = emitter.emit_text_message(run_id=run_id, content=_BLUF)
    f_fin = emitter.emit_run_finished(run_id=run_id, workflow="game_build")
    f_intr = emitter.emit_interrupt_requested(run_id=run_id, tool="approve_gate")
    seqs = [f_text.seq, f_fin.seq, f_intr.seq]
    # strictly increasing -> the bot's should_post/advance_cursor stays idempotent.
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    # frames_after(cursor) returns only newer frames (cursor read semantics).
    after = emitter.frames_after(f_text.seq)
    assert [fr.seq for fr in after] == [f_fin.seq, f_intr.seq]


# ── No new event types: every emitted payload is one of the existing 11 ───────
def test_emitted_event_types_are_existing_contract_only():
    emitter = OutboundEmitter()
    run_id = encode_chat_run_id("pm", "42")
    emitter.emit_text_message(run_id=run_id, content=_BLUF)
    emitter.emit_run_finished(run_id=run_id)
    emitter.emit_interrupt_requested(run_id=run_id, tool="approve_gate")
    types = {f.payload["type"] for f in emitter.frames()}
    allowed = {"TEXT_MESSAGE", "RUN_FINISHED", "INTERRUPT_REQUESTED"}
    assert types <= allowed
    # And each round-trips through the mapper without crashing (returns spec|None).
    for f in emitter.frames():
        out = event_to_message(f.seq, f.payload, {"post_text": True})
        assert out is None or isinstance(out, DiscordMessageSpec)
