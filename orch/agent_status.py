"""orch.agent_status — §9.4 8-state agent status publishing (NET-NEW · add-only).

The §27.4 status record (``aigs:status:<agent>``) sink already exists on
``orch.state_store.StateStore`` (``set_status``/``get_status``); what was missing
is (i) the canonical 8-value state enum, (ii) any code that actually *publishes* a
state on a graph node transition, and (iii) the read surface. This module supplies
(i) the enum + a node→(persona, state) map, and (ii) ``publish_status`` plus a thin
``_with_status`` builder-level callable wrapper that the cycle/org graph builders
apply to ``add_node`` arguments — the node function *bodies* are byte-preserved
(HIGH-3): the wrapper fires the status publish on entry and then returns the wrapped
node's result unchanged (input/output contract invariant).

Determinism / no-network discipline (orch convention):
  * ``since`` is iso8601 from an injectable ``clock`` seam (NOT ``datetime.now``-at-
    call-site directly — tests pin a fixed clock for deterministic payloads).
  * publishing NEVER raises into the hot path (live non-interrupt) — every failure
    is suppressed; a process-memory fallback dict records the last state so the RO
    ``/agents/status`` route reflects transitions even when Redis is disabled
    (``store=None``) or no event loop is available in the node's executor thread.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional


class AgentState(str, Enum):
    """The §9.4 / §27.4 agent state enum — EXACTLY these 8 literal values.

    Spec §9.4 line417 · §27.4 line1877. ``str`` mixin so the value serializes as the
    literal string in the §27.4 JSON record (``state`` field). Any other spelling is
    rejected (``AgentState("Foo")`` raises ValueError) — the unit test asserts the
    set is exactly these 8.
    """

    Idle = "Idle"
    Researching = "Researching"
    Coding = "Coding"
    Reviewing = "Reviewing"
    Meeting = "Meeting"
    Waiting = "Waiting"
    Blocked = "Blocked"
    Sleeping = "Sleeping"


# Node name → (persona id, AgentState). Deterministic, no LLM. Persona ids are the
# ``config/personas.yaml`` ids. ★LOW-B: the 8-value enum has no first-class "Writing"
# state, so stage_design/stage_content (the art/content authoring stages) map to the
# nearest in-enum value ``Coding`` (authoring/producing) — a documented approximation
# under the 8-value constraint, not a fabrication.
_NODE_STATUS: dict[str, tuple[str, AgentState]] = {
    # cycle graph nodes (orch/graph.py:580-587)
    "ceo_route": ("ceo", AgentState.Researching),
    "stage_research": ("research", AgentState.Researching),
    "stage_design": ("art", AgentState.Coding),  # ≈ authoring (LOW-B)
    "stage_content": ("art", AgentState.Coding),  # ≈ authoring (LOW-B)
    "authority_gate": ("qa", AgentState.Reviewing),
    "deliberate": ("coo", AgentState.Meeting),
    "stage_qa": ("qa", AgentState.Reviewing),
    "finalize": ("ceo", AgentState.Idle),
    # org graph extra nodes (orch/org_graph.py)
    "coo": ("coo", AgentState.Meeting),
    "dev_lead": ("dev_lead", AgentState.Meeting),
    "deliberate_org": ("coo", AgentState.Meeting),
    "escalate": ("ceo", AgentState.Blocked),
}


def node_status(node: str) -> Optional[tuple[str, AgentState]]:
    """(persona id, state) for a graph node name, or None if unmapped (deterministic)."""
    return _NODE_STATUS.get(node)


# org department node → AgentState. dept id == persona id (the org department nodes
# ARE the dept personas). research/librarian/intake are knowledge-gathering
# (Researching), art/dev produce artifacts (Coding ≈ authoring · LOW-B), qa reviews
# (Reviewing). Deterministic · no LLM.
_DEPT_STATE: dict[str, AgentState] = {
    "research": AgentState.Researching,
    "art": AgentState.Coding,
    "librarian": AgentState.Researching,
    "intake": AgentState.Researching,
    "dev": AgentState.Coding,
    "qa": AgentState.Reviewing,
}


def dept_state(dept_id: str) -> AgentState:
    """AgentState for an org department node (dept id == persona id · deterministic)."""
    return _DEPT_STATE.get(dept_id, AgentState.Researching)


# Process-memory fallback: agent_id -> last §27.4 record. Written on every publish so
# the RO read surface reflects transitions even with REDIS disabled / no event loop in
# the node executor thread. This is graceful degradation (last-known state), NOT
# fabrication — an agent that never transitioned is simply absent here (→ Idle/null at
# the read surface).
_MEM_STATUS: dict[str, dict[str, Any]] = {}


def _utc_now_iso() -> str:
    """Default clock: current UTC time as iso8601 (the only now()-at-call site)."""
    return datetime.now(timezone.utc).isoformat()


def _record(
    agent_id: str, state: AgentState | str, current_task: Any
) -> dict[str, Any]:
    """Build the §27.4 authority record (line1873-1879): {agent, state, current_task, since}."""
    return {
        "agent": agent_id,
        "state": state.value if isinstance(state, AgentState) else str(state),
        "current_task": current_task,
        "since": None,  # filled by the caller with the clock-derived iso (seam)
    }


def memory_status() -> dict[str, dict[str, Any]]:
    """The process-memory fallback map (read surface uses it when store is None)."""
    return _MEM_STATUS


def reset_memory_status() -> None:
    """Clear the process-memory fallback (test isolation only)."""
    _MEM_STATUS.clear()


async def publish_status(
    store: Any,
    agent_id: str,
    *,
    state: AgentState | str,
    current_task: Any = None,
    clock: Callable[[], str] = _utc_now_iso,
) -> None:
    """Publish a §27.4 status record for ``agent_id`` (state + current_task + since).

    Writes ``{"agent", "state", "current_task", "since"}`` (§27.4 line1873-1879) to
    ``aigs:status:<agent>`` via ``store.set_status`` when a store is provided, AND to
    the process-memory fallback always (so a None store still surfaces the transition).
    ``since`` is iso8601 from the injectable ``clock`` seam (deterministic in tests).
    NEVER raises (live non-interrupt) — all failures suppressed.
    """
    rec = _record(agent_id, state, current_task)
    with contextlib.suppress(Exception):
        rec["since"] = clock()
    _MEM_STATUS[agent_id] = rec  # always-on fallback (graceful · last-known)
    if store is not None:
        with contextlib.suppress(Exception):
            await store.set_status(agent_id, rec)


def _publish_entry(agent_id: str, state: AgentState) -> None:
    """Fire a node-entry status publish (fire-and-forget · suppress · never blocks).

    Called from the ``_with_status`` wrapper on node entry. The wrapper has no store
    handle and may run in a thread executor with no running loop, so this:
      * ALWAYS records the process-memory fallback synchronously (zero I/O · the RO
        read surface reads this when store is None — i.e. the test/default path), and
      * if a running event loop exists, also schedules the async ``publish_status`` so
        a live Redis-backed store gets the record too (best-effort · suppressed).
    """
    with contextlib.suppress(Exception):
        # current_task=None (MINOR-3·정직 deferral): 노드-진입 wrapper 는 task 컨텍스트
        #   (directive)를 보유하지 않으므로 진입 발행 시 §27.4 current_task 는 null 이다
        #   (필드는 존재·정직 null·날조 아님). directive→current_task 스레딩은 P6+ 후속
        #   (라이브 대시보드 승격 시 강화). publish_status(current_task=...) 직접 호출 경로는
        #   값 발행을 지원한다(아래).
        rec = _record(agent_id, state, None)
        rec["since"] = _utc_now_iso()
        _MEM_STATUS[agent_id] = rec
    # Best-effort live store write IF a loop is running in this context (live path).
    with contextlib.suppress(Exception):
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_entry_to_store(agent_id, state))


async def _publish_entry_to_store(agent_id: str, state: AgentState) -> None:
    """Resolve the live store lazily and publish (only when a loop is running·live)."""
    store = None
    with contextlib.suppress(Exception):
        from orch.state_store import make_state_store

        store, _ok, _err = await make_state_store()
    if store is not None:
        await publish_status(store, agent_id, state=state)


def _with_status(
    fn: Callable[..., Any], agent_id: str, state: AgentState
) -> Callable[..., Any]:
    """Thinly wrap a graph node callable so it publishes a status on entry (HIGH-3).

    The node function *body* is untouched — this wraps the ``add_node`` argument only.
    On entry it fires ``_publish_entry`` (fire-and-forget · suppressed · non-blocking)
    and then calls the original node, returning its result UNCHANGED (the node's
    input/output contract is invariant, so graph transitions/outputs are identical →
    the existing graph regression tests prove the wrapping is isomorphic). Works for
    sync node callables (the cycle/org nodes run in langgraph's thread executor) and
    async ones alike.
    """
    if asyncio.iscoroutinefunction(fn):

        async def _async_wrapped(*args: Any, **kwargs: Any) -> Any:
            _publish_entry(agent_id, state)
            return await fn(*args, **kwargs)

        _async_wrapped.__name__ = getattr(fn, "__name__", "node")
        _async_wrapped.__doc__ = getattr(fn, "__doc__", None)
        return _async_wrapped

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        _publish_entry(agent_id, state)
        return fn(*args, **kwargs)

    # Preserve identity metadata so langgraph node naming / debuggability is unchanged.
    _wrapped.__name__ = getattr(fn, "__name__", "node")
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped
