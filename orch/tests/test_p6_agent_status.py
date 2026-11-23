"""P6b — §9.4 8-상태 발행 배관 (NET-NEW) 단위 + 통합 테스트.

8 enum 정확(초과/오타 거부) · publish_status §27.4 4필드(store=None 폴백) · 노드 매핑
결정론 · 실 cycle invoke 후 /agents/status 가 그 전이 §27.4 4필드 반영(stub caller) ·
negative-mutation(미전이 직원이 active 로 나오면 FAIL) · personas.yaml 전원(secretary
제외·수 파일파생) 반환.

model STUBBED·네트워크 0. _MEM_STATUS 는 모듈 전역이므로 각 테스트가 reset(격리).
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch import agent_status as A
from orch.agent_status import (
    AgentState,
    dept_state,
    memory_status,
    node_status,
    publish_status,
    reset_memory_status,
)
from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_8 = {
    "Idle",
    "Researching",
    "Coding",
    "Reviewing",
    "Meeting",
    "Waiting",
    "Blocked",
    "Sleeping",
}
_AUTONOMOUS = (
    "make a clicker game"  # 가역·인용 → 빌드 경로(research/design/content/qa 전이)
)


@pytest.fixture(autouse=True)
def _isolate_mem():
    reset_memory_status()
    yield
    reset_memory_status()


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_p6status.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


# ── 8 enum 정확성 ──────────────────────────────────────────────────────────────
def test_agent_state_exactly_8_values():
    assert {s.value for s in AgentState} == _8
    assert len(list(AgentState)) == 8


def test_agent_state_rejects_typo_and_extra():
    # 오타/초과 값 거부(str-enum 멤버십).
    for bad in ("Researchng", "researching", "Working", "Busy", ""):
        with pytest.raises(ValueError):
            AgentState(bad)


# ── publish_status §27.4 4필드(store=None 폴백) ────────────────────────────────
def test_publish_status_27_4_fields_store_none():
    async def _go():
        await publish_status(
            None,
            "research",
            state=AgentState.Researching,
            current_task="시장조사",
            clock=lambda: "2026-06-22T00:00:00+00:00",
        )

    asyncio.run(_go())
    rec = memory_status()["research"]
    assert set(rec) == {"agent", "state", "current_task", "since"}  # §27.4 4필드 정확
    assert rec["agent"] == "research"
    assert rec["state"] == "Researching"
    assert rec["current_task"] == "시장조사"
    assert rec["since"] == "2026-06-22T00:00:00+00:00"  # clock seam(결정론)


def test_publish_status_never_raises_on_bad_store():
    class _Boom:
        async def set_status(self, *a, **k):
            raise RuntimeError("redis down")

    async def _go():
        # 예외 suppress(라이브 비중단) — 폴백 dict 는 여전히 기록됨.
        await publish_status(
            _Boom(), "qa", state=AgentState.Reviewing, current_task="x"
        )

    asyncio.run(_go())
    assert memory_status()["qa"]["state"] == "Reviewing"  # 폴백은 살아있음


def test_publish_status_writes_live_store():
    writes = {}

    class _Store:
        async def set_status(self, agent_id, rec):
            writes[agent_id] = rec

    async def _go():
        await publish_status(
            _Store(), "art", state=AgentState.Coding, current_task="에셋"
        )

    asyncio.run(_go())
    assert writes["art"]["state"] == "Coding"
    assert set(writes["art"]) == {"agent", "state", "current_task", "since"}


# ── 노드 매핑 결정론 ────────────────────────────────────────────────────────────
def test_node_status_mapping_deterministic():
    assert node_status("stage_research") == ("research", AgentState.Researching)
    assert node_status("authority_gate") == ("qa", AgentState.Reviewing)
    assert node_status("stage_qa") == ("qa", AgentState.Reviewing)
    assert node_status("finalize") == ("ceo", AgentState.Idle)
    assert node_status("escalate") == ("ceo", AgentState.Blocked)
    assert node_status("deliberate") == ("coo", AgentState.Meeting)
    # stage_design/content → art/Coding(8값 근사·LOW-B)
    assert node_status("stage_design") == ("art", AgentState.Coding)
    assert node_status("stage_content") == ("art", AgentState.Coding)
    assert node_status("nonexistent") is None  # 미매핑 → None(결정론)


def test_dept_state_mapping_deterministic():
    assert dept_state("research") == AgentState.Researching
    assert dept_state("dev") == AgentState.Coding
    assert dept_state("qa") == AgentState.Reviewing
    assert dept_state("librarian") == AgentState.Researching


# ── _with_status: 입출력 계약 불변(동형) ────────────────────────────────────────
def test_with_status_wrapper_preserves_return():
    reset_memory_status()

    def _node(state):
        return {"artifacts": {"x": state.get("directive", "")}}

    wrapped = A._with_status(_node, "research", AgentState.Researching)
    out = wrapped({"directive": "hello"})
    assert out == {"artifacts": {"x": "hello"}}  # 원 결과 그대로
    assert memory_status()["research"]["state"] == "Researching"  # 진입 발행됨
    assert wrapped.__name__ == "_node"  # identity 메타 보존


# ── 통합: 실 cycle invoke → /agents/status 가 그 전이 반영(stub caller) ──────────
def _client(tmp_db: str):
    return TestClient(
        create_app(
            ckpt_db=tmp_db,
            caller=stub_caller,
            searcher=stub_searcher,
            tracing=False,
            auth=False,
        )
    )


def test_agents_status_reflects_real_cycle_transition(tmp_db):
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _AUTONOMOUS})

        # 빌드 경로가 stage_research → stage_design/content → authority_gate 를 통과하면
        # research/art/qa 가 active 로 발행된다. bounded-poll(fire-and-forget _run 레이스).
        def _states():
            r = client.get("/agents/status")
            assert r.status_code == 200
            return {a["agent"]: a["state"] for a in r.json()["agents"]}

        seen = {}
        for _ in range(80):
            seen = _states()
            if seen.get("research") == "Researching":
                break
            time.sleep(0.05)

        assert seen.get("research") == "Researching", (
            "실 cycle 전이가 /agents/status 에 반영 안 됨(발행 배관 미작동)"
        )
        # §27.4 4필드 정확.
        rec = next(
            a
            for a in client.get("/agents/status").json()["agents"]
            if a["agent"] == "research"
        )
        assert set(rec) == {"agent", "state", "current_task", "since"}


def test_agents_status_negative_mutation_unvisited_is_idle(tmp_db):
    # ★negative-mutation: 전이 안 한 직원(librarian·intake·dev — 빌드 cycle 미경유)은
    # active 가 아니라 Idle/null 이어야 한다(날조면 FAIL). cycle 은 research/art/qa 만 거침.
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _AUTONOMOUS})
        # research 전이 대기.
        for _ in range(80):
            states = {
                a["agent"]: a["state"]
                for a in client.get("/agents/status").json()["agents"]
            }
            if states.get("research") == "Researching":
                break
            time.sleep(0.05)
        agents = {a["agent"]: a for a in client.get("/agents/status").json()["agents"]}
        # 미전이 직원 → Idle·current_task=null(정직·날조 0).
        for pid in ("librarian", "intake", "dev"):
            assert agents[pid]["state"] == "Idle", f"{pid} 미전이인데 active(날조)"
            assert agents[pid]["current_task"] is None


def test_agents_status_returns_all_personas_secretary_excluded(tmp_db):
    # personas.yaml 로드 전원·secretary 제외·수 파일파생(하드코딩 0).
    from office.personas import load_personas

    expected_ids = {p["id"] for p in load_personas() if not p.get("skip_nick")}
    assert "secretary" not in expected_ids  # skip_nick=true → 제외
    with _client(tmp_db) as client:
        r = client.get("/agents/status")
        assert r.status_code == 200
        got_ids = {a["agent"] for a in r.json()["agents"]}
        assert got_ids == expected_ids  # 전원·파일 파생(9 직원)


def test_agents_status_store_none_graceful_200(tmp_db):
    # store=None(REDIS off·테스트 기본) → 200 + 폴백 반환(503 아님).
    with _client(tmp_db) as client:
        r = client.get("/agents/status")
        assert r.status_code == 200
        # 아무 전이 없으면 전원 Idle.
        states = {a["state"] for a in r.json()["agents"]}
        assert states == {"Idle"}
