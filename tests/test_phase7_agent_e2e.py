"""US-P7A-1 NIT-2 — agent_smoke worker-driven e2e(생명주기·동일 프로세스).

계획 ralplan-phase7-agent-production.md §3.4 7a · §8. ★전부 **in-process Worker 경유**
(Worker.run / recover_running_orphans — 별도 OS 프로세스 아님)로 agent 노드의 프로덕션
생명주기를 닫는다. 6b의 agent 단위 검증(test_agent_node.py)을 워커 구동 e2e로 승격한다.

검증 대상(AC-1a/1b/1c/1d):
  · AC-1a 종료불능→상한: AGENT_DONE 안 내는 agent_smoke → Worker.run claim·run →
    _AGENT_STEP_LIMIT=12 도달 → NODE_FAILED(escalation)·RUN_FINISHED(failed)·워커 fail.
  · AC-1b cancel-during-agent: agent_action(비가역) suspend → DB cancel → should_cancel
    탈출 → cancelled→fail·비가역 미실행.
  · AC-1c agent running 고아: recover_running_orphans → is_resumable False(recovery.py
    agent 제외) → fail+NodeFailed(escalation) Notify·자동재개 0.
  · AC-1d mock HITL 왕복: 주입 tools dict(agent_action callable) 엔진을 Worker에 주입 →
    agent_action suspend → 승인(INTERRUPT_RESOLVED) → Worker.run 보유 폴링 resume → complete.

★바운드: max_ticks/max_polls + agent 스텝상한(_AGENT_STEP_LIMIT=12). 오프라인 결정론
(실서버·LLM 0 — ScriptLLM). 실행은 오직 scripts/run_tests.sh engine(3중 가드). bare
pytest 금지(hang 전례).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from office import run as run_module
from office.approve import _record_decision
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import EVENT_REGISTRY, FailReason
from office.gating import GatingPolicy
from office.task_queue import TaskQueue
from office.worker import Worker
from workflows.agent_smoke import NODES as AGENT_NODES
from workflows.agent_smoke import WORKFLOW_NAME as AGENT_WF

# ── 드라이버 가능한 mock LLM(test_agent_node.py ScriptLLM 패턴 재사용) ──────────


def _tc(name: str, args: dict[str, Any] | None = None) -> str:
    """prompt_json 도구호출 content JSON."""
    return json.dumps({"name": name, "arguments": args or {}}, ensure_ascii=False)


class ScriptLLM:
    """스크립트 응답을 순서대로 반환하는 mock LLM(소진되면 마지막 반복).

    test_agent_node.py의 ScriptLLM과 동일 계약 — agent 루프를 결정론으로 구동한다.
    종료 안 하는 LLM은 마지막 응답이 도구 호출/마커 없는 추론이라 스텝 상한이 잡는다.
    """

    def __init__(self, contents: list[str]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._contents = contents
        self._idx = 0

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        content = self._contents[min(self._idx, len(self._contents) - 1)]
        if self._idx < len(self._contents) - 1:
            self._idx += 1
        return {
            "message": {
                "role": "assistant",
                "content": content,
                "tool_calls": [],
                "reasoning_content": "",
            },
            "content": content,
            "finish_reason": "stop",
            "usage": {},
            "latency_ms": 1.0,
        }


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 office.db 경로(이벤트·큐 공유)."""
    return tmp_path / "office.db"


def _agent_engine(
    db_path: Path, contents: list[str], tools: dict[str, Any]
) -> WorkflowEngine:
    """agent_smoke 구동용 엔진 — ScriptLLM + 주입 tools dict.

    ★agent_action ∉ REAL_TOOLS(MOCK 전용)라 HITL 왕복엔 agent_action callable을 포함한
    tools dict를 직접 주입한다(worker.py:273 injected 경로). GatingPolicy()는 기본
    config/tools_policy.yaml(agent_action=needs-approval·crm/order=always-allow)을 읽는다.
    """
    store = EventStore(db_path=db_path)
    return WorkflowEngine(
        executor=NodeExecutor(llm_client=ScriptLLM(contents), event_store=store),
        event_store=store,
        tools=tools,
        gating_policy=GatingPolicy(),
    )


def _counting_tools() -> tuple[dict[str, Any], dict[str, list[str]]]:
    """호출 카운트 결정론 mock 도구 + 호출 로그(비가역 미실행 단언용)."""
    log: dict[str, list[str]] = {
        "crm_get_customer": [],
        "order_get_latest": [],
        "agent_action": [],
    }

    def crm(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        log["crm_get_customer"].append((context or {}).get("node_id", ""))
        return {"customer_id": "C-1", "name": "테스터"}

    def order(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        log["order_get_latest"].append((context or {}).get("node_id", ""))
        return {"order_id": "O-1", "status": "배송완료"}

    def action(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        log["agent_action"].append((context or {}).get("node_id", ""))
        return {"done": True}

    return (
        {"crm_get_customer": crm, "order_get_latest": order, "agent_action": action},
        log,
    )


def _all_events(db_path: Path) -> list[dict[str, Any]]:
    """모든 run의 이벤트 payload(type 포함)를 seq 순으로 읽는다."""
    return [json.loads(pj) for _, pj in EventStore(db_path=db_path).read_since(0)]


# ── AC-1a: 종료불능 agent → 스텝 상한 → fail (워커 구동) ───────────────────────


class TestAgentStepLimitWorkerDriven:
    """AGENT_DONE 없는 agent_smoke를 Worker가 구동 → 상한 → escalation·워커 fail."""

    def test_nonterminating_agent_hits_step_limit_and_fails(
        self, db_path: Path
    ) -> None:
        """enqueue→Worker.run(max_ticks 바운드)→claim→run→_AGENT_STEP_LIMIT→fail.

        도구 호출 없이 마커 없는 추론만 무한 반복 → 종료 불가 → 상한이 최후 방어.
        워커가 task를 fail로 종결한다(생명주기 닫힘).
        """
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(AGENT_WF, payload={"mock": True})
        # done 마커 없는 순수 추론만 반복(도구 호출 아님) → 상한 도달.
        tools, log = _counting_tools()
        engine = _agent_engine(db_path, ["계속 생각 중..."], tools)

        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=engine, max_polls=2)

        # ★워커가 fail로 종결(생명주기 닫힘).
        assert queue.get(task_id)["status"] == "failed"
        # NODE_FAILED(escalation) + RUN_FINISHED(failed) 발행.
        events = _all_events(db_path)
        node_failed = [e for e in events if e.get("type") == "NODE_FAILED"]
        assert len(node_failed) == 1
        assert node_failed[0]["reason"] == FailReason.escalation.value
        assert "상한" in node_failed[0]["detail"]
        run_finished = [e for e in events if e.get("type") == "RUN_FINISHED"]
        assert run_finished and run_finished[-1]["status"] == "failed"
        # 도구는 한 번도 안 불림(순수 추론 루프).
        assert log["crm_get_customer"] == []
        # 발행 타입 전부 11종 ⊆(새 타입 0).
        assert {e["type"] for e in events} <= set(EVENT_REGISTRY.keys())


# ── AC-1b: cancel-during-agent → 비가역 미실행 fail ───────────────────────────


class TestCancelDuringAgent:
    """agent_action(비가역) suspend 중 DB cancel → should_cancel 탈출·비가역 미실행."""

    def test_cancel_escapes_agent_suspend_irreversible_not_run(
        self, db_path: Path
    ) -> None:
        """agent_action suspend → cancel set → 폴링 첫 폴 탈출 → cancelled→fail.

        ★비가역(agent_action)은 승인 전 실행 0(suspend-before-exec). 워커가 task를
        fail로 종결. cancel은 max_polls 소진 전 탈출(should_cancel) — hang 0.
        """
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(AGENT_WF, payload={"mock": True})
        # agent가 곧장 agent_action(비가역) 호출 → suspend. 마커 미도달.
        tools, log = _counting_tools()
        engine = _agent_engine(db_path, [_tc("agent_action", {"note": "go"})], tools)

        # ★cancel을 미리 set → run_claimed_task가 suspend 후 wait_for_decision_and_resume
        #   첫 폴에서 should_cancel()=True로 즉시 탈출(cancelled). max_polls 큰 값이어도
        #   cancel이 먼저 탈출시키므로 hang 0.
        queue.request_cancel(task_id)

        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=engine, max_polls=200)

        # ★cancelled → 워커 fail로 흡수.
        assert queue.get(task_id)["status"] == "failed"
        # ★비가역(agent_action)은 승인 전 실행 0(게이팅 단일 관문 suspend).
        assert log["agent_action"] == []
        # cancel 잔재 0(clear_cancel).
        assert queue.is_cancel_requested(task_id) is False
        # RUN_ERROR(cancelled by operator) 발행.
        events = _all_events(db_path)
        run_errors = [e for e in events if e.get("type") == "RUN_ERROR"]
        assert any("cancel" in (e.get("error", "").lower()) for e in run_errors)


# ── AC-1c: agent running 고아 → 2a fail + Notify(자동재개 0) ───────────────────


class TestAgentRunningOrphan2aFail:
    """agent running 고아 → recover_running_orphans → is_resumable False(agent) → fail."""

    def _make_agent_running_orphan(self, db_path: Path, *, run_id: str) -> int:
        """agent_smoke를 running 상태로 남긴 고아를 만든다(직전 크래시 모사).

        engine.run을 agent_action suspend 직전까지 돌리지 않고, run_id만 payload에 기록한
        뒤(이벤트 일부 발행) running으로 둔다. recover_running_orphans는 그래프 재로드
        (agent_smoke·agent 노드) → infer_resume_node_id → is_resumable False(agent 보수).
        """
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(AGENT_WF, payload={"mock": True})
        queue.claim()
        run_module._record_run_id_in_payload(task_id, run_id, db_path)
        # 일부 진행 이벤트(없어도 무방하나, 재생 경로를 실제로 밟게 STATE_DELTA 1건 둔다).
        store = EventStore(db_path=db_path)
        from office.events import StateDelta

        store.append(
            StateDelta(run_id=run_id, ts="t1", accumulated={"agent_main_content": "x"})
        )
        assert queue.get(task_id)["status"] == "running"
        return task_id

    def test_agent_running_orphan_failed_and_notified(self, db_path: Path) -> None:
        """agent 노드 고아 → recover_running_orphans → fail+NodeFailed(escalation).

        ★is_resumable이 agent 노드를 비가역 보수 취급(recovery.py:151)이라 2a 자동재개 0,
        2b 폴백(fail+Notify)으로 종결. 사람 개입 필요 신호.
        """
        task_id = self._make_agent_running_orphan(db_path, run_id="rid-agent-orphan")
        engine = _agent_engine(db_path, [_tc("agent_action")], _counting_tools()[0])

        worker = Worker(db_path=db_path, poll_interval=0.0)
        out = worker.recover_running_orphans(engine=engine, max_polls=2)

        # ★2a 재개 안 함 — failed 집합(자동재개 0).
        assert task_id in out["failed"]
        assert task_id not in out["resumed"]
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "failed"
        # NODE_FAILED(escalation) Notify 발행.
        events = _all_events(db_path)
        node_failed = [e for e in events if e.get("type") == "NODE_FAILED"]
        assert node_failed and node_failed[-1]["reason"] == FailReason.escalation.value


# ── AC-1d: mock HITL 왕복(주입 tools dict 엔진 → 승인 → resume → complete) ─────


class TestAgentHitlRoundtripWorkerDriven:
    """agent_action suspend → 승인 → Worker 보유 폴링 resume → complete(재승인 루프 0)."""

    def test_inject_tools_dict_hitl_resume_completes(self, db_path: Path) -> None:
        """주입 tools dict(agent_action callable) 엔진을 Worker에 주입 → HITL 왕복.

        ★agent_action ∉ REAL_TOOLS라 worker.py:273 injected 경로로 agent_action callable을
        포함한 엔진을 주입한다. 흐름: ① engine.run(agent_smoke) → agent_action suspend →
        suspended 영속+suspend 마킹. ② 승인(INTERRUPT_RESOLVED 기록·resume=False라 status
        suspended 유지). ③ Worker.run 부팅 → _resume_suspended_orphans → 보유 폴링이 결정
        감지 → resume_after_approval(G-RESTART) → complete. 재승인 루프 0(INTERRUPT 1건).
        """
        contents = [
            _tc("crm_get_customer"),
            _tc("agent_action", {"note": "go"}),
            "완료 AGENT_DONE",
        ]
        tools, log = _counting_tools()
        store = EventStore(db_path=db_path)
        queue = TaskQueue(db_path=db_path)

        # ── ① suspend 단계: agent_action에서 멈춘 suspended task 생성 ──
        eng_suspend = WorkflowEngine(
            executor=NodeExecutor(
                llm_client=ScriptLLM(list(contents)), event_store=store
            ),
            event_store=store,
            tools=tools,
            gating_policy=GatingPolicy(),
        )
        task_id = queue.enqueue(AGENT_WF, payload={"mock": True})
        queue.claim()
        state = eng_suspend.run(
            nodes=list(AGENT_NODES),
            workflow=AGENT_WF,
            task_id=task_id,
        )
        assert state["_workflow_status"] == "suspended"
        assert log["crm_get_customer"] == ["agent_main#0"]  # 가역 1회(sub-id)
        assert log["agent_action"] == []  # 비가역은 승인 전 실행 0
        run_id = state["run_id"]
        run_module._persist_suspended_state(task_id, state, db_path)
        queue.suspend(task_id)

        # ── ② 승인 기록(resume=False — task는 suspended 유지, 워커가 폴링으로 재개) ──
        iid = store.pending_interrupts(run_id)[0]["seq"]
        _record_decision(iid, "approve", db_path=db_path, resume=False)

        # ── ③ Worker.run 부팅 → suspended 고아 보유 폴링 → 결정 감지 → resume → complete ──
        eng_resume = WorkflowEngine(
            executor=NodeExecutor(
                llm_client=ScriptLLM(list(contents)), event_store=store
            ),
            event_store=store,
            tools=tools,
            gating_policy=GatingPolicy(),
        )
        worker = Worker(db_path=db_path, poll_interval=0.0)
        worker.run(max_ticks=1, engine=eng_resume, max_polls=5)

        # ★complete로 종결(생명주기 닫힘).
        assert queue.get(task_id)["status"] == "done"
        # 가역은 재시작 B1 skip(여전히 1회), 비가역은 승인된 base-id로 정확히 1회.
        assert len(log["crm_get_customer"]) == 1
        assert log["agent_action"] == ["agent_main"]  # 비가역 base-id 1행
        # ★재승인 루프 0 — INTERRUPT_REQUESTED 최초 1건뿐.
        events = _all_events(db_path)
        interrupts = [e for e in events if e.get("type") == "INTERRUPT_REQUESTED"]
        assert len(interrupts) == 1
        # RUN_ERROR 0(1b 봉합 — resume node_map.get 성공).
        assert not any(e.get("type") == "RUN_ERROR" for e in events)
