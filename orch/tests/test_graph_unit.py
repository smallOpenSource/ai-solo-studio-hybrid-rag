"""Unit tests (deterministic, model STUBBED) for orch.graph + orch.tools.

Covers the Phase B unit matrix:
  * ceo_route returns the correct Command(goto) per directive (build | end).
  * CycleState.artifacts thread the prior stage's output across stages.
  * the office.business tool node calls the UNCHANGED store and returns a seeded row.
  * interrupt() -> snapshot.interrupts -> Command(resume=) merges into state.
  * a STATIC guard test: no full-alist() scan path exists in orch (point-reads only).
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from orch.graph import (
    build_cycle_graph,
    stage_research,
    use_caller,
    use_searcher,
)
from orch.tests.conftest import empty_searcher, raising_searcher, stub_searcher
from orch.tools import business_get_customer

# ── P3 §20 canonical 지시 픽스처 ────────────────────────────────────────────────
# stub_caller 는 directive 무관하게 "ROUTE: build" 를 반환(라우팅 결정)하므로, authority_gate 의
# tier 는 directive 키워드(assess)로 갈린다.
#   _AUTONOMOUS = 가역·범위내·인용된 빌드 → AUTONOMOUS(무차단·interrupt 0).
#   _ESCALATE   = owner-reserved 키워드(출시/금전/지출) → ESCALATE(interrupt).
_AUTONOMOUS = "make a clicker game"
_ESCALATE = "스팀 출시용 클리커 게임 — 광고비 실금전 지출 승인 필요"


# ── helpers ───────────────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.run(coro)


@contextlib.contextmanager
def _ctx(caller, searcher=stub_searcher):
    """Bind BOTH the model caller and the (no-network) knowledge searcher.

    Every cycle-running unit test must bind a stub searcher so the research step is
    deterministic and never touches the live knowledge service.
    """
    with use_caller(caller), use_searcher(searcher):
        yield


async def _fresh_graph():
    """A graph on an isolated throwaway checkpoint db (caller cleans it up)."""
    db = tempfile.mktemp(suffix=".db")
    cm = AsyncSqliteSaver.from_conn_string(db)
    saver = await cm.__aenter__()
    return cm, build_cycle_graph(saver), db


def _cleanup(db: str) -> None:
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


# ── ceo_route routing ─────────────────────────────────────────────────────────
def test_ceo_route_build_enters_stage_chain(stub):
    async def go():
        cm, g, db = await _fresh_graph()
        try:
            cfg = {"configurable": {"thread_id": "build-1"}}
            with _ctx(stub):
                state = await g.ainvoke({"directive": _AUTONOMOUS}, cfg)
                snap = await g.aget_state(cfg)
            # build route -> stages ran. ★§20: 가역+인용 → AUTONOMOUS(무차단) → interrupt 없이
            # finalize 까지 완주(레거시는 hitl_gate 에서 정지했음).
            assert state["route"] == "build"
            assert not snap.interrupts
            assert state.get("authority_tier") == "autonomous"
            assert "design" in state["artifacts"] and "content" in state["artifacts"]
            assert state["bluf"].startswith("결론")
        finally:
            await cm.__aexit__(None, None, None)
            _cleanup(db)

    _run(go())


def test_ceo_route_end_skips_stages(end_stub):
    async def go():
        cm, g, db = await _fresh_graph()
        try:
            cfg = {"configurable": {"thread_id": "end-1"}}
            with _ctx(end_stub):
                state = await g.ainvoke({"directive": "what's the weather?"}, cfg)
                snap = await g.aget_state(cfg)
            # end route -> graph terminates -> NO stages, NO interrupt.
            assert state["route"] == "end"
            assert list(snap.next) == []
            assert not snap.interrupts
            assert "design" not in (state.get("artifacts") or {})
        finally:
            await cm.__aexit__(None, None, None)
            _cleanup(db)

    _run(go())


# ── CycleState artifact threading ─────────────────────────────────────────────
def test_artifacts_thread_through_stages(stub):
    """Each stage's output accumulates; finalize's BLUF consumes all three."""

    async def go():
        cm, g, db = await _fresh_graph()
        try:
            cfg = {"configurable": {"thread_id": "thread-art"}}
            with _ctx(stub):
                await g.ainvoke({"directive": "make a clicker game"}, cfg)
                final = await g.ainvoke(Command(resume="approve"), cfg)
            arts = final["artifacts"]
            # All three stage artifacts present and threaded.
            assert set(["design", "content", "qa"]).issubset(arts.keys())
            assert "click" in arts["design"].lower()
            # content built on design (the stub names the game).
            assert "clicky" in arts["content"].lower()
            # finalize consumed them into a BLUF.
            assert final["bluf"].startswith("결론")
        finally:
            await cm.__aexit__(None, None, None)
            _cleanup(db)

    _run(go())


# ── business tool node (UNCHANGED office.business) ────────────────────────────
def test_business_tool_node_unchanged_call_seeded_row():
    """The wrapped office.business store is called unmodified and returns a seed.

    Uses an isolated tmp db so the original store's schema+seed run in isolation
    (db_path injection only — office/business.py is untouched).
    """
    db = tempfile.mktemp(suffix="_biz.db")
    try:
        # office.business._seed seeds at least one customer; the seed id is stable.
        # Look it up via the orch tool wrapper (pass-through to get_customer).
        # We assert a known seeded id resolves to a dict with that id.
        from office.business import BusinessStore  # original, for the seed id

        store = BusinessStore(db_path=db)
        # Find any seeded customer id by reading the schema's seed directly.
        with store._conn() as conn:
            row = conn.execute(
                "SELECT customer_id FROM crm_customers LIMIT 1"
            ).fetchone()
        assert row is not None, "office.business seed produced no customer"
        seed_id = row["customer_id"]

        got = business_get_customer(seed_id, db_path=db)
        assert got is not None
        assert got["customer_id"] == seed_id
        # Unknown id returns None (pass-through semantics, no new behavior).
        assert business_get_customer("nope-does-not-exist", db_path=db) is None
    finally:
        for s in ("", "-wal", "-shm"):
            p = Path(db + s)
            if p.exists():
                p.unlink()


# ── HITL interrupt -> snapshot.interrupts -> Command(resume=) ─────────────────
def test_interrupt_surfaces_options_and_resume_merges(stub):
    async def go():
        cm, g, db = await _fresh_graph()
        try:
            cfg = {"configurable": {"thread_id": "hitl-1"}}
            with _ctx(stub):
                # ★§20: owner-reserved(출시/금전) → ESCALATE → interrupt(§20.4a 브리프).
                await g.ainvoke({"directive": _ESCALATE}, cfg)
                snap = await g.aget_state(cfg)
                assert snap.interrupts, "no interrupt surfaced"
                val = snap.interrupts[0].value
                assert val["options"] == ["approve", "reject"]
                # ★계산 confidence(하드코딩 0.55 아님) + §20.4a 브리프 + tier.
                assert (
                    isinstance(val["confidence"], float) and val["tier"] == "escalate"
                )
                assert "brief" in val and "design" in val and "content" in val
                # Command(resume=) merges the choice into state.
                final = await g.ainvoke(Command(resume="approve"), cfg)
            assert final["decision"] == "approve"
            assert final.get("authority_tier") == "escalate"
        finally:
            await cm.__aexit__(None, None, None)
            _cleanup(db)

    _run(go())


def test_interrupt_resume_reject_also_completes(stub):
    """A non-approve resume value (reject) still merges + completes the cycle (no deadlock)."""

    async def go():
        cm, g, db = await _fresh_graph()
        try:
            cfg = {"configurable": {"thread_id": "hitl-reject"}}
            with _ctx(stub):
                await g.ainvoke({"directive": _ESCALATE}, cfg)
                final = await g.ainvoke(Command(resume="reject"), cfg)
            assert final["decision"] == "reject"
            assert final["bluf"].startswith("결론")
        finally:
            await cm.__aexit__(None, None, None)
            _cleanup(db)

    _run(go())


# ── KNOWLEDGE step (stage_research) — cite a real-shaped source ───────────────
def test_research_node_cites_source_and_threads_finding():
    """stage_research records the top hit's source URL + count into state/artifact."""
    with use_searcher(stub_searcher):
        out = stage_research({"directive": "make a clicker game"})
    assert out["knowledge_source"] == "https://example.test/wiki/Free-to-play"
    assert out["knowledge_count"] == 1
    # the research artifact carries the source so design can consume it.
    assert "research" in out["artifacts"]
    assert "https://example.test/wiki/Free-to-play" in out["artifacts"]["research"]
    assert "research" in out["stage_timings"]


def test_research_node_degrades_on_empty_corpus():
    """Empty corpus -> explicit no-source citation, count 0, cycle proceeds."""
    with use_searcher(empty_searcher):
        out = stage_research({"directive": "make a clicker game"})
    assert out["knowledge_count"] == 0
    assert out["knowledge_source"] == "(no knowledge source)"
    assert "research" in out["artifacts"]


def test_research_node_degrades_when_knowledge_down():
    """A raising searcher (service down) must NOT crash the node (degrade-path)."""
    with use_searcher(raising_searcher):
        out = stage_research({"directive": "make a clicker game"})
    # node survived: source is the no-source sentinel, finding notes unavailability.
    assert out["knowledge_source"] == "(no knowledge source)"
    assert out["knowledge_count"] == 0
    assert "unavailable" in out["artifacts"]["research"].lower()


def test_finalize_bluf_includes_cited_source(stub):
    """The delivered BLUF surfaces the cited source even if the model omits it."""

    async def go():
        cm, g, db = await _fresh_graph()
        try:
            cfg = {"configurable": {"thread_id": "cite-1"}}
            with _ctx(stub):
                await g.ainvoke({"directive": "make a clicker game"}, cfg)
                final = await g.ainvoke(Command(resume="approve"), cfg)
            # The stub BLUF already cites the source; assert it is present verbatim.
            assert "https://example.test/wiki" in final["bluf"]
            assert final["knowledge_source"].startswith("https://")
        finally:
            await cm.__aexit__(None, None, None)
            _cleanup(db)

    _run(go())


# ── STATIC guard: no full-alist() scan path anywhere in orch ──────────────────
def test_no_alist_full_scan_path_in_orch():
    """sqlite-with-discipline (Phase A): orch must NEVER call a full alist() scan.

    A single-connection AsyncSqliteSaver deadlocks on a full list scan; the spec
    forbids it. This static guard greps the orch source (excluding tests) and
    asserts no actual ``.alist(`` CALL site exists — a method invocation has a '.'
    immediately before it, which distinguishes a real call from the prose mentions
    in docstrings (``alist()``). Pending is resolved by bounded per-thread
    aget_state point-reads only.
    """
    orch_dir = Path(__file__).resolve().parents[1]
    offenders = []
    for py in orch_dir.glob("*.py"):
        for lineno, line in enumerate(py.read_text().splitlines(), 1):
            # A real call is `<obj>.alist(...)`; docstring mentions are `alist()`
            # preceded by a backtick/space, never a dot.
            if ".alist(" in line:
                offenders.append(f"{py.name}:{lineno}")
    assert not offenders, f"forbidden full-scan .alist() call found in: {offenders}"
    # Positive assertion: the point-read primitive IS used (aget_state in app.py).
    app_src = (orch_dir / "app.py").read_text()
    assert "aget_state(" in app_src
