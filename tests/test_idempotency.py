"""멱등성(B1 이벤트 dedup) 테스트 — Phase 5 US-P5-2 (AC6·7·8·9·10).

US-P5-1(실도구·이메일 싱크 멱등키 B2)은 test_real_tools.py가 다룬다. 이 파일은
**B1(이벤트 dedup)**: engine `_run_tool`이 (run_id, node_id)의 TOOL_CALL_RESULT가
이미 있으면 도구를 재호출하지 않는다는 가드를 단언한다.

테스트 범위 (계획 §5 L1/L3 + §6 AC):
- AC6: EventStore.has_tool_result True/False (json_extract SQL 질의).
- AC6: B1 skip 시 도구 callable 미호출(mock 호출 카운트 0).
- AC7/AC9: 크래시 윈도 2종 분리 — (a) 마커 발행 후 크래시→B1 skip(도구 0회),
           (b) 마커 발행 전(`tool_fn→_emit` 윈도) 크래시→B1 못 막음·B2 UNIQUE가 싱크 1행.
- AC8: dedup skip 후 워크플로 success(crm_notify N3가 마지막 노드 next=None).
- AC9: TOOL_CALL_RESULT 정확히 1건 = email 1회 = 싱크 1행 3중 정합.
- AC10: dedup 가드는 폴링 아님(질의 1회) — 무한 루프 0(run_tests.sh 3중 가드가 카나리).
- resume 중복 트리거: 같은 run에 resume_after_approval 2회 → 도구 1회.

★전부 mock·tmp db. business 스토어는 reset_business_store로 tmp 경로 주입(격리).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from office.business import BusinessStore
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import RunFinished, ToolCallResult
from office.gating import GatingPolicy
from office.nodes import Node
from office.tools import REAL_TOOLS, reset_business_store


# ── 테스트용 정책 (crm always-allow + email needs-approval) ───────────────────

_TEST_POLICY: dict[str, Any] = {
    "tools": {
        "crm_get_customer": {"risk": "always-allow"},
        "email_send": {"risk": "needs-approval", "when": "always_interrupt"},
    },
    "default_risk": "needs-approval",
}


# ── Fake LLM (호출 응답 순서 고정) ────────────────────────────────────────────


class FakeLLM:
    """호출 인자를 캡처하고 정해진 응답을 순서대로 반환하는 fake LLM."""

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
    """prompt_json 도구호출 content JSON 문자열."""
    return json.dumps({"name": name, "arguments": args}, ensure_ascii=False)


class CountingTool:
    """호출 횟수를 세는 도구 callable (context 미수용 1-인자 — B1 skip 입증용)."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.count = 0
        self._result = result

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        self.count += 1
        return dict(self._result)


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


@pytest.fixture
def biz_store(tmp_path: Path) -> Iterator[BusinessStore]:
    """tmp business.db를 전역 스토어로 주입하고 끝나면 리셋한다(격리)."""
    bstore = BusinessStore(db_path=tmp_path / "business.db")
    reset_business_store(bstore)
    try:
        yield bstore
    finally:
        reset_business_store(None)


def _email_node() -> Node:
    """email_send tool 노드 — crm_notify N3와 동일하게 마지막 노드(next=None)."""
    return Node(
        id="n3_email_send",
        kind="tool",
        prompt="이메일 발송",
        thinking="off",
        tools=[{"function": {"name": "email_send"}}],
        next=None,
    )


# ── AC6: EventStore.has_tool_result True/False (json_extract SQL) ─────────────


def test_has_tool_result_false_when_absent(store: EventStore) -> None:
    """TOOL_CALL_RESULT가 없으면 has_tool_result는 False."""
    assert store.has_tool_result("run-1", "n3_email_send") is False


def test_has_tool_result_true_after_emit(store: EventStore) -> None:
    """해당 (run_id, node_id) TOOL_CALL_RESULT가 있으면 True (json_extract 매칭)."""
    store.append(
        ToolCallResult(
            run_id="run-1",
            ts="2026-06-09T00:00:00+00:00",
            node_id="n3_email_send",
            name="email_send",
            result={"sent": True},
        )
    )
    assert store.has_tool_result("run-1", "n3_email_send") is True
    # 다른 node_id·run_id는 매칭 안 됨(필터 정확성)
    assert store.has_tool_result("run-1", "n1_crm_get") is False
    assert store.has_tool_result("run-other", "n3_email_send") is False


# ── AC6: B1 skip 시 도구 callable 미호출 (mock 호출 카운트 0) ─────────────────


def test_b1_skip_does_not_call_tool_when_marker_exists(
    policy_path: Path, store: EventStore
) -> None:
    """이미 TOOL_CALL_RESULT가 있으면 _run_tool이 도구를 재호출하지 않는다(AC6).

    같은 run_id로 email_send를 2회 resume → 도구 callable 호출은 정확히 1회.
    (2회째 resume은 마커가 이미 있어 B1 dedup이 도구 호출을 skip.)
    """
    email_tool = CountingTool({"sent": True, "to": "a@b.com"})
    fake = FakeLLM([_stop(_tc("email_send", {"to": "a@b.com"}))])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"email_send": email_tool},
        gating_policy=GatingPolicy(policy_path),
    )

    # 1) run → email_send에서 suspend(도구 미실행)
    suspended = engine.run([_email_node()], run_id="r-dup", task_id=1)
    assert suspended.get("_workflow_status") == "suspended"
    assert email_tool.count == 0, "suspend 단계에선 도구가 실행되지 않아야 한다"

    # 2) 1차 resume(approve) → 도구 1회 실행 + TOOL_CALL_RESULT 마커 1건
    final1 = engine.resume_after_approval(suspended, "approve")
    assert final1.get("_workflow_status") == "success"
    assert email_tool.count == 1, "1차 resume에서 도구가 정확히 1회 실행돼야 한다"

    # 3) 2차 resume(중복 트리거 — 재시작·중복 resume 시뮬) → B1 dedup이 skip
    final2 = engine.resume_after_approval(suspended, "approve")
    assert email_tool.count == 1, "2차 resume은 B1 dedup으로 도구를 재호출하지 않는다"
    # 워크플로는 여전히 정상 종료(success)에 도달
    assert final2.get("_workflow_status") == "success"

    # TOOL_CALL_RESULT는 정확히 1건 유지(재발행 안 함, AC9)
    events = store.read("r-dup")
    n3_results = [
        e
        for e in events
        if isinstance(e, ToolCallResult) and e.node_id == "n3_email_send"
    ]
    assert len(n3_results) == 1, "TOOL_CALL_RESULT는 정확히 1건이어야 한다(AC9)"


# ── AC8: dedup skip 후 워크플로 success (N3 마지막 노드) ──────────────────────


def test_dedup_skip_preserves_workflow_success(
    policy_path: Path, store: EventStore
) -> None:
    """dedup skip 경로가 _workflow_status(success) + RUN_FINISHED 1회를 보존한다(AC8).

    crm_notify는 email이 마지막 노드(next=None)라 skip 후 후속 캐리포워드 복원
    불요 — skip이면 _run_from이 next=None을 만나 정상 종료에 도달.
    """
    email_tool = CountingTool({"sent": True})
    fake = FakeLLM([_stop(_tc("email_send", {"to": "a@b.com"}))])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"email_send": email_tool},
        gating_policy=GatingPolicy(policy_path),
    )
    suspended = engine.run([_email_node()], run_id="r-skip", task_id=1)
    engine.resume_after_approval(suspended, "approve")  # 1차: 마커 생성

    # 2차 resume → 도구 skip되지만 워크플로는 success
    final = engine.resume_after_approval(suspended, "approve")
    assert final.get("_workflow_status") == "success"

    # 크래시 0 — RunFinished(success)가 발행됨(2차 resume도 정상 종료)
    events = store.read("r-skip")
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished, "RUN_FINISHED가 발행돼야 한다"
    assert finished[-1].status == "success"


# ── AC7/AC9: 크래시 윈도 (a) 마커 발행 후 크래시 → B1 skip ────────────────────


def test_crash_window_a_after_marker_b1_skips(
    policy_path: Path, store: EventStore, biz_store: BusinessStore
) -> None:
    """윈도 (a): 싱크 1행 + TOOL_CALL_RESULT 1건이 DB에 있는 상태에서 재개 →
    B1 dedup이 도구 callable 재호출을 skip → 싱크 1행 유지(AC7).

    실 email_send(REAL_TOOLS) + 실 business store로 부수효과를 관측한다.
    """
    fake = FakeLLM(
        [_stop(_tc("email_send", {"to": "a@b.com", "subject": "s", "body": "b"}))]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(policy_path),
    )
    suspended = engine.run([_email_node()], run_id="r-wina", task_id=1)

    # 1차 resume → 실 도구가 싱크 1행 기록 + TOOL_CALL_RESULT 마커 발행("마커 후" 상태)
    engine.resume_after_approval(suspended, "approve")
    assert biz_store.count_emails() == 1
    assert store.has_tool_result("r-wina", "n3_email_send") is True

    # 재개(크래시 시뮬) → B1이 마커를 보고 skip → 싱크 여전히 1행
    final = engine.resume_after_approval(suspended, "approve")
    assert final.get("_workflow_status") == "success"
    assert biz_store.count_emails() == 1, "윈도(a): B1 skip으로 싱크 1행 유지(AC7)"
    # TOOL_CALL_RESULT도 정확히 1건(AC9 3중 정합)
    events = store.read("r-wina")
    n3_results = [
        e
        for e in events
        if isinstance(e, ToolCallResult) and e.node_id == "n3_email_send"
    ]
    assert len(n3_results) == 1


# ── AC7: 크래시 윈도 (b) 마커 발행 전 크래시 → B2 UNIQUE만으로 싱크 1행 ──────


def test_crash_window_b_before_marker_b2_blocks(
    policy_path: Path, store: EventStore, biz_store: BusinessStore
) -> None:
    """윈도 (b): 싱크 1행은 있으나 TOOL_CALL_RESULT 없음(마커 발행 전 `tool_fn→_emit`
    윈도 크래시) 상태에서 재개 → B1은 마커가 없어 skip 못 하고 도구를 재호출하나,
    B2(이메일 싱크 idempotency_key UNIQUE, US-P5-1)가 싱크 1행으로 차단(AC7).

    인프로세스 DB 수동 조작으로 "도구 실행됐으나 마커 발행 전 죽음"을 흉내낸다:
    같은 멱등키로 싱크에 1행을 미리 넣어 둔(=도구가 한 번 부수효과를 남긴) 뒤,
    마커(TOOL_CALL_RESULT) 없이 resume → 도구 재호출 → B2 UNIQUE가 이중 기록 차단.
    """
    run_id = "r-winb"
    node_id = "n3_email_send"
    idem_key = f"{run_id}:{node_id}"

    # "마커 발행 전 크래시" 상태 재현: 싱크엔 이미 1행(도구 부수효과 발생됨), 마커는 없음.
    biz_store.record_email(
        to="a@b.com",
        subject="s",
        body="b",
        idempotency_key=idem_key,
        sent_at="2026-06-09T00:00:00+00:00",
    )
    assert biz_store.count_emails() == 1
    assert store.has_tool_result(run_id, node_id) is False  # 마커 없음(윈도 b 전제)

    fake = FakeLLM(
        [_stop(_tc("email_send", {"to": "a@b.com", "subject": "s", "body": "b"}))]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(policy_path),
    )
    suspended = engine.run([_email_node()], run_id=run_id, task_id=1)

    # resume → B1은 마커 없어 skip 못 함 → 실 도구 재호출 → B2 UNIQUE가 차단
    final = engine.resume_after_approval(suspended, "approve")
    assert final.get("_workflow_status") == "success"
    assert biz_store.count_emails() == 1, "윈도(b): B2 UNIQUE가 싱크 1행 유지(AC7)"
    # 이번 재호출의 도구 결과는 deduplicated=True(멱등키 충돌)
    n3_result = final["accumulated"].get("n3_email_send_result")
    assert n3_result is not None
    assert n3_result.get("deduplicated") is True


# ── AC9: email 1회 = TOOL_CALL_RESULT 1건 = 싱크 1행 3중 정합 ─────────────────


def test_triple_consistency_email_marker_sink(
    policy_path: Path, store: EventStore, biz_store: BusinessStore
) -> None:
    """정상 1회 실행 + 중복 resume 후에도 email 1회=TOOL_CALL_RESULT 1건=싱크 1행(AC9)."""
    fake = FakeLLM(
        [_stop(_tc("email_send", {"to": "a@b.com", "subject": "s", "body": "b"}))]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(policy_path),
    )
    suspended = engine.run([_email_node()], run_id="r-triple", task_id=1)
    engine.resume_after_approval(suspended, "approve")
    engine.resume_after_approval(suspended, "approve")  # 중복 트리거

    events = store.read("r-triple")
    n3_results = [
        e
        for e in events
        if isinstance(e, ToolCallResult) and e.node_id == "n3_email_send"
    ]
    # 3중 정합: 싱크 1행 = TOOL_CALL_RESULT 1건 = email 1회
    assert biz_store.count_emails() == 1
    assert len(n3_results) == 1
    assert biz_store.get_email_by_key("r-triple:n3_email_send") is not None


# ── AC6 보강: allow 경로(일반 실행)에서도 B1 가드가 도구 재호출을 막는다 ──────


def test_b1_guard_on_allow_path(store: EventStore) -> None:
    """always-allow 도구라도 같은 (run_id, node_id) 마커가 있으면 _run_tool이 skip한다.

    게이팅 없는 직접 _run_tool 호출(allow 경로)에서 B1 가드를 단언한다 — resume뿐
    아니라 일반 실행 경로도 같은 _run_tool 단일 가드가 커버함을 입증(AC6).
    """
    crm_tool = CountingTool({"customer_id": "C-1"})
    fake = FakeLLM([_stop(_tc("crm_get_customer", {"customer_id": "C-1"}))])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools={"crm_get_customer": crm_tool},
        gating_policy=None,  # 게이팅 없음 → 모든 도구 allow
    )
    node = Node(
        id="n1_crm_get",
        kind="tool",
        prompt="고객 조회",
        thinking="off",
        tools=[{"function": {"name": "crm_get_customer"}}],
        next=None,
    )
    # 1) 정상 run(allow 경로) → 도구 1회 + 마커 1건 → success
    final1 = engine.run([node], run_id="r-allow", task_id=1)
    assert final1.get("_workflow_status") == "success"
    assert crm_tool.count == 1

    # 2) 같은 run_id로 _run_tool 직접 재호출(마커 존재) → B1 skip(도구 미호출)
    state = {
        "run_id": "r-allow",
        "messages": [],
        "accumulated": {},
        "_last_extracted": {"name": "crm_get_customer", "args": {"customer_id": "C-1"}},
    }
    engine._run_tool(node, state, "r-allow")
    assert crm_tool.count == 1, (
        "마커 존재 시 allow 경로 _run_tool도 도구를 재호출하지 않는다"
    )
