"""US-006 위험기반 게이팅 — 엔진 연동·resume·e2e 왕복 테스트.

게이트 B("위험 게이팅 동작 실증")의 심장.

이 파일은 test_gating.py(정책·분류 단위)를 보완해, 계획 §4.3·§4.4·§4.4.5와
PRD US-006 수용 기준 5·6을 **엔진 실행 경로**에서 단언한다:

  (수용 5) needs-approval → INTERRUPT_REQUESTED 발행(payload={task_id, args})
           + TaskQueue suspended 전환.
  (수용 6) approve → INTERRUPT_RESOLVED → resume(LLM 재호출 0, 도구만 실행) → 완료 /
           reject → RUN_FINISHED(status=rejected).
  block    → NODE_FAILED(reason=gating) + RUN_FINISHED(rejected) (Plan Mode로도 불가).
  Plan Mode → needs-approval 일괄 통과(block은 그대로 차단).
  e2e 근사 → office.approve._record_decision 와 결합해
           "suspend → 결정 기록 → resume_after_approval" 왕복 1회(전부 mock·tmp db).

설계 계약(US-007 approve.py가 의존 — 어기면 영구 suspended):
  INTERRUPT_REQUESTED.payload = {"task_id": int|None(resume용), "args": 정화된 도구 인자, ...}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import (
    FailReason,
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunFinished,
    TextMessage,
    ToolCallResult,
)
from office.gating import GatingPolicy
from office.nodes import Node
from office.task_queue import STATUS_SUSPENDED, TaskQueue

# ── 테스트용 정책 (4도구 + 미등록 default) ────────────────────────────────────

_TEST_POLICY: dict[str, Any] = {
    "tools": {
        "crm_get_customer": {"risk": "always-allow"},
        "order_get_latest": {"risk": "always-allow"},
        "email_send": {"risk": "needs-approval", "when": "always_interrupt"},
        "db_drop_table": {"risk": "block"},
    },
    "default_risk": "needs-approval",
}


# ── Fake LLM (호출 횟수 카운트 — resume LLM 재호출 0 단언용) ──────────────────


class FakeLLM:
    """호출 인자를 캡처하고 정해진 응답을 순서대로 반환하는 fake LLM.

    .calls 길이로 LLM 호출 횟수를 단언한다(M10b: resume는 LLM 재호출 0).
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses
        self._idx = 0

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        resp = self._responses[self._idx]
        if self._idx < len(self._responses) - 1:
            self._idx += 1
        return resp


def _stop(content: str) -> dict[str, Any]:
    """finish_reason=stop인 fake 응답."""
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


def _tc(name: str, args: dict[str, Any]) -> str:
    """prompt_json 도구호출 content JSON 문자열을 만든다."""
    import json

    return json.dumps({"name": name, "arguments": args}, ensure_ascii=False)


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    """인라인 정책을 임시 yaml로 저장해 경로를 반환한다."""
    p = tmp_path / "tools_policy.yaml"
    p.write_text(yaml.dump(_TEST_POLICY, allow_unicode=True), encoding="utf-8")
    return p


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    """tmp DB 이벤트 스토어."""
    return EventStore(db_path=tmp_path / "office.db")


def _crm_email_nodes() -> list[Node]:
    """N1(crm_get_customer, always-allow) → N3(email_send, needs-approval)."""
    n1 = Node(
        id="n1",
        kind="tool",
        prompt="고객 조회",
        thinking="off",
        tools=[{"function": {"name": "crm_get_customer"}}],
        next="n3",
    )
    n3 = Node(
        id="n3",
        kind="tool",
        prompt="이메일 발송",
        thinking="off",
        tools=[{"function": {"name": "email_send"}}],
    )
    return [n1, n3]


def _crm_email_tools() -> dict[str, Any]:
    """결정론 mock 도구."""
    return {
        "crm_get_customer": lambda args: {"customer_id": "C-1", "email": "a@b.com"},
        "email_send": lambda args: {"sent": True, "to": args.get("to")},
    }


# ── (수용 5) interrupt → INTERRUPT_REQUESTED payload 계약 + suspended ─────────


def test_interrupt_emits_requested_with_task_id_and_args(
    policy_path: Path, store: EventStore
) -> None:
    """needs-approval → INTERRUPT_REQUESTED 발행, payload에 task_id·args 포함.

    payload 계약(approve.py 의존): task_id(resume용) + args(정화된 도구 인자).
    args는 LLM이 추출한 실제 인자여야 한다(게이팅이 추출 후 동작하는 증거).
    """
    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com", "subject": "안내"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_path),
    )

    final = engine.run(_crm_email_nodes(), run_id="r-int", task_id=99)

    assert final.get("_workflow_status") == "suspended"

    events = store.read("r-int")
    requested = [e for e in events if isinstance(e, InterruptRequested)]
    assert len(requested) == 1, "INTERRUPT_REQUESTED가 정확히 1회 발행돼야 한다"
    ev = requested[0]
    assert ev.node_id == "n3"
    assert ev.tool == "email_send"
    assert ev.risk == "needs-approval"
    # payload 계약 — task_id(resume용)
    assert ev.payload.get("task_id") == 99
    # payload 계약 — args(정화된 도구 인자, LLM 추출값)
    assert ev.payload.get("args") == {"to": "a@b.com", "subject": "안내"}
    assert ev.payload.get("tool") == "email_send"


def test_interrupt_transitions_task_to_suspended(
    policy_path: Path, store: EventStore, tmp_path: Path
) -> None:
    """needs-approval interrupt 시 TaskQueue 작업이 suspended로 전환된다 (수용 5).

    엔진은 status="suspended"를 돌려주고, 디스패처 계약상 그 task를 suspend한다.
    여기서는 디스패처 핸들러를 모사해 interrupt → TaskQueue.suspend 전이를 단언.
    """
    queue = TaskQueue(db_path=tmp_path / "office.db")
    task_id = queue.enqueue("crm_notify", payload={})

    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_path),
    )

    claimed = queue.claim()
    assert claimed is not None and claimed["id"] == task_id

    final = engine.run(_crm_email_nodes(), run_id="r-sus", task_id=task_id)

    # 디스패처 계약: 엔진이 suspended를 반환하면 작업을 suspend한다.
    if final.get("_workflow_status") == "suspended":
        queue.suspend(task_id)

    row = queue.get(task_id)
    assert row is not None
    assert row["status"] == STATUS_SUSPENDED, "interrupt 후 작업이 suspended여야 한다"
    # suspended 작업은 claim 대상에서 제외
    assert queue.claim() is None


# ── (수용 6) resume approve → LLM 재호출 0, 도구만 실행 → 완료 ───────────────


def test_resume_approve_no_llm_recall_tool_only_completion(
    policy_path: Path, store: EventStore
) -> None:
    """approve resume는 LLM을 재호출하지 않고 영속 args로 도구만 실행해 완주한다.

    M10b 핵심:
    - run() 동안 LLM 호출 = 2회(N1 추출 + N3 추출).
    - resume_after_approval('approve') 동안 LLM 호출 = 0회(카운트 불변).
    - N3 도구가 영속 args로 실행되어 결과가 누적되고 RUN_FINISHED(success).
    """
    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_path),
    )

    suspended = engine.run(_crm_email_nodes(), run_id="r-approve", task_id=1)
    assert suspended.get("_workflow_status") == "suspended"
    calls_before = len(fake.calls)
    assert calls_before == 2, f"run 중 LLM 호출은 2회여야 한다(실제 {calls_before})"

    # 재개 — 승인
    final = engine.resume_after_approval(suspended, "approve")

    # M10b: resume 동안 LLM 재호출 0 (카운트 불변)
    assert len(fake.calls) == calls_before, (
        f"resume는 LLM을 재호출하면 안 된다. 호출 {calls_before}→{len(fake.calls)}"
    )
    assert final.get("_workflow_status") == "success"
    # 영속 args로 도구가 실행됨
    assert final["accumulated"].get("n3_result") == {"sent": True, "to": "a@b.com"}

    events = store.read("r-approve")
    # INTERRUPT_RESOLVED(approve) 발행
    resolved = [e for e in events if isinstance(e, InterruptResolved)]
    assert len(resolved) == 1 and resolved[0].decision == "approve"
    # N3 ToolCallResult 발행(도구 실제 실행)
    n3_results = [
        e for e in events if isinstance(e, ToolCallResult) and e.node_id == "n3"
    ]
    assert len(n3_results) == 1
    # RUN_FINISHED(success)
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished and finished[-1].status == "success"


# ── (수용 6) resume reject → RUN_FINISHED(rejected) ──────────────────────────


def test_resume_reject_finishes_rejected(policy_path: Path, store: EventStore) -> None:
    """reject resume는 도구를 실행하지 않고 RUN_FINISHED(status=rejected)로 끝낸다."""
    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_path),
    )

    suspended = engine.run(_crm_email_nodes(), run_id="r-reject", task_id=1)
    assert suspended.get("_workflow_status") == "suspended"
    calls_before = len(fake.calls)

    final = engine.resume_after_approval(suspended, "reject")

    # reject도 LLM 재호출 없음
    assert len(fake.calls) == calls_before
    assert final.get("_workflow_status") == "rejected"
    # 도구는 실행되지 않음 (n3_result 없음)
    assert "n3_result" not in final.get("accumulated", {})

    events = store.read("r-reject")
    resolved = [e for e in events if isinstance(e, InterruptResolved)]
    assert len(resolved) == 1 and resolved[0].decision == "reject"
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished and finished[-1].status == "rejected"


# ── block → NODE_FAILED(gating) + rejected ───────────────────────────────────


def test_block_emits_node_failed_gating_and_rejected(
    policy_path: Path, store: EventStore
) -> None:
    """block 도구는 NODE_FAILED(reason=gating) + RUN_FINISHED(rejected)로 끝난다.

    Plan Mode로도 통과 불가한 차단(아래 별도 테스트). 여기선 기본 모드.
    """
    drop = Node(
        id="n_drop",
        kind="tool",
        prompt="테이블 삭제",
        thinking="off",
        tools=[{"function": {"name": "db_drop_table"}}],
    )
    fake = FakeLLM([_stop(_tc("db_drop_table", {"table": "users"}))])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"db_drop_table": lambda a: {"dropped": True}},
        gating_policy=GatingPolicy(policy_path),
    )

    final = engine.run([drop], run_id="r-block")

    assert final.get("_workflow_status") == "rejected"
    # 도구는 실행되지 않음
    assert "n_drop_result" not in final.get("accumulated", {})

    events = store.read("r-block")
    failed = [e for e in events if isinstance(e, NodeFailed)]
    assert len(failed) == 1, "block은 NODE_FAILED를 1회 발행해야 한다"
    assert failed[0].reason == FailReason.gating
    assert failed[0].node_id == "n_drop"
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished and finished[-1].status == "rejected"


# ── Plan Mode — 일괄 통과 + block 불가 ───────────────────────────────────────


def test_plan_mode_emits_plan_text_message_and_passes_needs_approval(
    policy_path: Path, store: EventStore
) -> None:
    """Plan Mode: 시작 시 계획 TEXT_MESSAGE 1회 + needs-approval 일괄 통과해 완주."""
    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_path),
    )

    final = engine.run(_crm_email_nodes(), run_id="r-plan", task_id=1, plan_mode=True)

    # Plan Mode면 needs-approval(email_send)을 통과해 끝까지 진행
    assert final.get("_workflow_status") == "success"
    assert final["accumulated"].get("n3_result") == {"sent": True, "to": "a@b.com"}

    events = store.read("r-plan")
    # 계획 TEXT_MESSAGE 1회 (RUN_STARTED 직후)
    plan_msgs = [
        e for e in events if isinstance(e, TextMessage) and "Plan Mode" in e.content
    ]
    assert len(plan_msgs) == 1, "Plan Mode 계획 TEXT_MESSAGE는 1회 발행돼야 한다"
    assert "n3" in plan_msgs[0].content
    # Plan Mode여도 INTERRUPT_REQUESTED는 없음(일괄 승인 전제)
    assert not [e for e in events if isinstance(e, InterruptRequested)]


def test_plan_mode_cannot_pass_block(policy_path: Path, store: EventStore) -> None:
    """Plan Mode여도 block 도구는 통과 불가 — NODE_FAILED(gating) + rejected."""
    drop = Node(
        id="n_drop",
        kind="tool",
        prompt="테이블 삭제",
        thinking="off",
        tools=[{"function": {"name": "db_drop_table"}}],
    )
    fake = FakeLLM([_stop(_tc("db_drop_table", {"table": "users"}))])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"db_drop_table": lambda a: {"dropped": True}},
        gating_policy=GatingPolicy(policy_path),
    )

    final = engine.run([drop], run_id="r-plan-block", plan_mode=True)

    assert final.get("_workflow_status") == "rejected", (
        "Plan Mode로도 block은 통과 불가해야 한다"
    )
    events = store.read("r-plan-block")
    failed = [e for e in events if isinstance(e, NodeFailed)]
    assert failed and failed[0].reason == FailReason.gating


# ── e2e 근사 — suspend → 결정 기록(approve.py) → resume_after_approval ────────


def test_e2e_suspend_record_decision_resume_roundtrip(tmp_path: Path) -> None:
    """오프라인 e2e 왕복: suspend → office.approve 결정 기록 → resume_after_approval.

    전부 mock·tmp db. office.approve._record_decision(승인 소비측)과 결합해
    "멈춤→사람 승인→재개"의 전 사이클을 한 번 돈다(게이트 B 오프라인 예행).
    """
    from office import approve as approve_mod

    db_path = tmp_path / "office.db"
    store = EventStore(db_path=db_path)
    policy_p = tmp_path / "tools_policy.yaml"
    policy_p.write_text(yaml.dump(_TEST_POLICY, allow_unicode=True), encoding="utf-8")

    # 작업큐에 작업 등록 후 claim (디스패처 모사)
    queue = TaskQueue(db_path=db_path)
    task_id = queue.enqueue("crm_notify", payload={})
    queue.claim()

    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com", "subject": "상태 안내"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_p),
    )

    # 1) 실행 → N3에서 멈춤(suspend) + 디스패처가 작업 suspend
    suspended = engine.run(_crm_email_nodes(), run_id="r-e2e", task_id=task_id)
    assert suspended.get("_workflow_status") == "suspended"
    queue.suspend(task_id)
    calls_after_run = len(fake.calls)

    # 2) 사람 승인 — approve.py가 결정을 기록(INTERRUPT_RESOLVED append + 작업 resume)
    pending = approve_mod._all_pending_interrupts(db_path=db_path)
    assert len(pending) == 1, "대기 중인 인터럽트가 1건 있어야 한다"
    interrupt_id = pending[0]["interrupt_id"]
    # payload 계약 확인 — approve.py가 task_id를 읽어 resume할 수 있어야 한다
    assert pending[0]["payload"].get("task_id") == task_id
    assert pending[0]["payload"].get("args") == {
        "to": "a@b.com",
        "subject": "상태 안내",
    }

    record = approve_mod._record_decision(
        interrupt_id=interrupt_id,
        decision="approve",
        db_path=db_path,
        approvals_dir=tmp_path / "approvals",
    )
    assert record["task_id"] == task_id
    # approve.py가 작업을 queued로 resume했는지 확인
    assert queue.get(task_id)["status"] == "queued"

    # 3) 디스패처가 재개 — resume_after_approval(LLM 재호출 0, 도구만 실행)
    final = engine.resume_after_approval(suspended, "approve")
    assert len(fake.calls) == calls_after_run, "resume는 LLM 재호출 0이어야 한다"
    assert final.get("_workflow_status") == "success"
    assert final["accumulated"].get("n3_result") == {"sent": True, "to": "a@b.com"}
    queue.complete(task_id)
    assert queue.get(task_id)["status"] == "done"

    # 이벤트 단언: INTERRUPT_REQUESTED → INTERRUPT_RESOLVED(approve) → RUN_FINISHED(success)
    events = store.read("r-e2e")
    types = [e.to_dict()["type"] for e in events]
    assert "INTERRUPT_REQUESTED" in types
    assert "INTERRUPT_RESOLVED" in types
    assert types.index("INTERRUPT_REQUESTED") < types.index("INTERRUPT_RESOLVED")
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished and finished[-1].status == "success"


def test_e2e_full_event_sequence_order(policy_path: Path, store: EventStore) -> None:
    """풀 시퀀스 이벤트 발행 순서 단언 (suspend→approve→완료).

    기대 골격:
      RUN_STARTED → (N1) TOOL_CALL_START → TOOL_CALL_RESULT
                  → (N3) TOOL_CALL_START → INTERRUPT_REQUESTED
                  → INTERRUPT_RESOLVED → (N3) TOOL_CALL_RESULT → RUN_FINISHED
    """
    fake = FakeLLM(
        [
            _stop(_tc("crm_get_customer", {"email": "a@b.com"})),
            _stop(_tc("email_send", {"to": "a@b.com"})),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=_crm_email_tools(),
        gating_policy=GatingPolicy(policy_path),
    )

    suspended = engine.run(_crm_email_nodes(), run_id="r-seq", task_id=1)
    engine.resume_after_approval(suspended, "approve")

    types = [e.to_dict()["type"] for e in store.read("r-seq")]

    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    # INTERRUPT_REQUESTED는 RUN_FINISHED 전, INTERRUPT_RESOLVED 전
    assert "INTERRUPT_REQUESTED" in types
    assert types.index("INTERRUPT_REQUESTED") < types.index("INTERRUPT_RESOLVED")
    # 마지막 TOOL_CALL_RESULT(N3 도구 실행)는 INTERRUPT_RESOLVED 뒤
    assert types.index("INTERRUPT_RESOLVED") < (
        len(types) - 1 - types[::-1].index("TOOL_CALL_RESULT")
    )
