"""재시작 내성 — 복구 책임이 웹→워커로 이전(Phase 6 6a Chunk 2).

★repoint(AC-P6-1f): Phase 5의 웹 복구(web_server.runner.resume_task/
recover_orphan_tasks)를 **office.worker의 복구**로 옮긴다. 같은 시나리오를 보존하되
실행/복구 주체가 워커다:
- suspended 고아 자동 재개(run_id 보존·email 정확히-1회) → Worker.run() 부팅 resume
  (_resume_suspended_orphans → wait_for_decision_and_resume, should_cancel=DB·heartbeat=DB).
- running 고아 fail+NodeFailed(2b) → office.worker.recover_running_orphans.
- 종료 task(done/failed) 배제 → 워커 복구가 안 건드림.
- 인프로세스 재시작 멱등(마커 선존 → B1 dedup) → 워커 재개도 email 1행 유지.

★_make_done_callback 영구409 테스트(Phase 5)는 웹 실행 모델 소멸로 무효 → 워커 등가
단언으로 대체: 워커가 advisory 락(fcntl.flock·non-blocking)으로 단일 인스턴스를 강제하고
claim 원자성(단일 UPDATE-RETURNING)으로 동시성 1을 보존함을 단언(웹 락 우회 0의 등가물).

★불변 매핑(이 파일이 지키는 계약):
- email 정확히-1회(B1+B2) → 워커 재개 경로에서도 보존(TestWorkerSuspendedResume·
  TestInProcessRestartIdempotency).
- 동시성 1 → advisory 락 + claim 원자성(TestWorkerConcurrencyOne — 웹 모델 등가).
- run_id 연속성 → suspended_state 박제 run_id로 재개(멱등키 = run_id:node_id).

오프라인 결정론(실서버·LLM 0 — email_send는 needs-approval라 도구 미실행 상태로 suspend).
tmp_path db 주입(office.db·business.db 모두 임시). 인프로세스(별도 OS 프로세스 0·좀비 0).
모든 워커 루프는 max_ticks/max_polls로 바운드(hang 금지·scripts/run_tests.sh 전용).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from office import run as run_module
from office.approve import _record_decision
from office.business import BusinessStore
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import ToolCallResult
from office.gating import GatingPolicy
from office.task_queue import TaskQueue
from office.tools import REAL_TOOLS, reset_business_store
from office.worker import SingleInstanceLock, Worker, recover_running_orphans
from workflows.crm_notify import NODES, WORKFLOW_NAME

# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 office.db 경로(이벤트·큐) — 실 파일 오염 없음."""
    return tmp_path / "office.db"


@pytest.fixture
def business_db(tmp_path: Path) -> Any:
    """tmp business.db를 전역 BusinessStore로 주입하고 테스트 후 원복한다.

    실도구(email_send)가 이 임시 outbox에 기록하므로 count_emails로 멱등을 단언한다.
    """
    store = BusinessStore(db_path=tmp_path / "business.db")
    reset_business_store(store)
    yield store
    reset_business_store(None)  # 다음 호출 시 기본 경로로 재생성(격리 해제)


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _make_suspended_task(db_path: Path) -> tuple[str, int]:
    """crm_notify를 N3 email_send에서 suspend시켜 (run_id, task_id)를 만든다.

    run_full의 enqueue→claim→run(→suspend)→persist→suspend 마킹을 모사한다(직전 워커
    크래시 잔재 = suspended 고아). email_send는 needs-approval+always_interrupt라 도구
    미실행 상태로 suspend되므로 이 시점 email_outbox는 0행이다. 실서버 0 — mock LLM.
    """
    store = EventStore(db_path=db_path)
    queue = TaskQueue(db_path=db_path)
    llm = run_module._FakeSmokeLLM(
        ["crm_get_customer", "order_get_latest", "chat", "email_send"]
    )
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )
    task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
    queue.claim()
    state = engine.run(
        nodes=list(NODES),
        workflow=WORKFLOW_NAME,
        task_id=task_id,
    )
    assert state.get("_workflow_status") == "suspended", (
        "테스트 전제: email_send에서 suspend해야 한다"
    )
    run_module._persist_suspended_state(task_id, state, db_path)
    queue.suspend(task_id)
    run_id = state.get("run_id", "")
    assert run_id, "suspended_state에 run_id가 박제돼야 한다(복구 멱등 전제)"
    return run_id, task_id


def _approve_pending(run_id: str, db_path: Path) -> None:
    """이 run의 대기 중 인터럽트를 승인 결정으로 DB에 기록한다(웹 Inbox 경로 모사).

    ★resume=False — 웹 Inbox 버튼 경로와 동일. INTERRUPT_RESOLVED만 기록하고 TaskQueue
    .resume(queued 전환)는 안 한다 → task가 suspended로 남아 워커 복구가 'suspended 고아
    + 승인됨'으로 인식해 재개(크래시 시나리오 정합).
    """
    store = EventStore(db_path=db_path)
    pending = store.pending_interrupts(run_id)
    assert pending, "승인할 대기 인터럽트가 없다(suspend 전제 실패)"
    iid = pending[0]["seq"]
    _record_decision(iid, "approve", db_path=db_path, resume=False)


def _mock_engine(db_path: Path) -> WorkflowEngine:
    """crm_notify 4노드 결정론 fake LLM + REAL_TOOLS 엔진(워커 주입용)."""
    store = EventStore(db_path=db_path)
    llm = run_module._FakeSmokeLLM(
        ["crm_get_customer", "order_get_latest", "chat", "email_send"]
    )
    executor = NodeExecutor(llm_client=llm, event_store=store)
    return WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )


def _outbox_count(business_db: Any) -> int:
    return business_db.count_emails()


# ── 1. 워커 suspended 고아 재개 — run_id 보존·email 정확히-1회 (AC-P6-1a/3b) ──


class TestWorkerSuspendedResume:
    """Worker.run() 부팅 resume이 승인된 suspended 고아를 재개(웹 resume_task 대체)."""

    def test_worker_resumes_suspended_orphan_and_sends_email_once(
        self, db_path: Path, business_db: Any
    ) -> None:
        """승인된 suspended 고아를 워커가 재개하면 email 싱크 1행 + task done."""
        run_id, task_id = _make_suspended_task(db_path)
        _approve_pending(run_id, db_path)
        assert _outbox_count(business_db) == 0  # 재개 전: 0행(도구 미실행)

        # 워커 부팅(in-process·max_ticks=1) → _resume_suspended_orphans가 재개.
        worker = Worker(db_path=db_path, poll_interval=0.0)
        engine = _mock_engine(db_path)
        worker.run(max_ticks=1, engine=engine, max_polls=200)

        # ★email 싱크 정확히 1행(비가역 정확히-1회) + 멱등키 = run_id:node_id.
        assert _outbox_count(business_db) == 1
        assert business_db.get_email_by_key(f"{run_id}:n3_email_send") is not None
        # task가 done으로 마킹됨(_resume_one_suspended 꼬리).
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "done"

    def test_worker_suspended_orphan_without_decision_fails_bounded(
        self, db_path: Path, business_db: Any
    ) -> None:
        """승인 결정이 없는 suspended 고아: 워커 보유 폴링이 max_polls 소진→fail, email 0행.

        (결정이 나중에 들어오면 다음 tick에 재개 — 여기선 미결정 폴링 보유가 바운드됨을
        단언. 무한 대기 0.)
        """
        _run_id, task_id = _make_suspended_task(db_path)
        # 결정 기록 안 함 — 폴링이 결정을 못 찾는다. max_polls로 바운드.

        worker = Worker(db_path=db_path, poll_interval=0.0)
        engine = _mock_engine(db_path)
        worker.run(max_ticks=1, engine=engine, max_polls=2)

        # 미결정으로 끝나면 fail로 흡수 → email 0행.
        assert _outbox_count(business_db) == 0
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "failed"


# ── 2. 워커 running 고아 — fail+NodeFailed(2b) (AC-P6 — 2b 유지) ──────────────


class TestWorkerRunningOrphan:
    """recover_running_orphans: running 고아 fail 마킹 + NodeFailed(자동 재개 안 함)."""

    def test_running_orphan_failed_and_notified(
        self, db_path: Path, business_db: Any
    ) -> None:
        """running 고아(직전 크래시 잔재)는 fail 마킹 + NodeFailed 발행(2b)."""
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.claim()  # → running (고아 시뮬)
        assert queue.get(task_id)["status"] == "running"

        failed = recover_running_orphans(db_path)
        assert task_id in failed

        # fail 마킹 + 자동 재개 0 → 비가역 부수효과 0(email 0행).
        assert queue.get(task_id)["status"] == "failed"
        assert _outbox_count(business_db) == 0
        types = [
            json.loads(pj).get("type")
            for _, pj in EventStore(db_path=db_path).read_since(0)
        ]
        assert "NODE_FAILED" in types

    def test_full_worker_boot_recovers_running_orphan_via_main_path(
        self, db_path: Path, business_db: Any
    ) -> None:
        """running 고아가 있는 상태로 워커 부팅(recover_running_orphans + run) → fail 안정.

        ★main() 부팅 순서(recover_running_orphans → run)를 in-process로 재현. running
        고아가 fail되고, 빈 큐라 워커 루프는 곧 종료(처리 0건).
        """
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.claim()  # running 고아

        # main() 부팅 경로 모사: 먼저 running 2b 복구, 그 다음 워커 루프.
        recover_running_orphans(db_path)
        worker = Worker(db_path=db_path, poll_interval=0.0)
        engine = _mock_engine(db_path)
        worker.run(max_ticks=1, engine=engine, max_polls=2)

        assert queue.get(task_id)["status"] == "failed"
        assert _outbox_count(business_db) == 0


# ── 3. ★동시성 1 — advisory 락 + claim 원자성 (웹 _make_done_callback 등가) ────


class TestWorkerConcurrencyOne:
    """웹 락 우회 0(_make_done_callback)의 워커 등가물 — advisory 락 + claim 원자성.

    Phase 5의 '웹이 락을 우회하지 않음(_make_done_callback 단일 해제)' 단언은 웹 실행
    모델 소멸로 무효 → 워커가 (a) advisory 락으로 단일 인스턴스를 강제하고 (b) claim
    원자성으로 한 task를 두 번 잡지 않음을 단언(동시성 1 = 워커 소유).
    """

    def test_advisory_lock_enforces_single_worker_instance(
        self, tmp_path: Path
    ) -> None:
        """advisory 락 보유 중 2번째 인스턴스 획득 실패(fcntl non-blocking — 동시성 1)."""
        lock_path = tmp_path / "worker.lock"
        first = SingleInstanceLock(lock_path)
        assert first.acquire() is True
        try:
            second = SingleInstanceLock(lock_path)
            assert second.acquire() is False  # 즉시 False(블로킹 0·hang 0)
        finally:
            first.release()
        # 해제 후 재획득 가능(프로세스 죽으면 OS 자동 해제 의미).
        third = SingleInstanceLock(lock_path)
        assert third.acquire() is True
        third.release()

    def test_claim_atomicity_one_task_claimed_once(self, db_path: Path) -> None:
        """claim 원자성: queued 1건을 두 번 claim하면 두 번째는 None(중복 실행 0).

        단일 UPDATE-RETURNING(WHERE status='queued')이라 한 행은 정확히 1회만 running
        전환된다 → 워커가 같은 task를 두 번 실행하지 않음(동시성 1의 구조적 토대).
        """
        queue = TaskQueue(db_path=db_path)
        tid = queue.enqueue(WORKFLOW_NAME, payload={})
        first = queue.claim()
        second = queue.claim()
        assert first is not None and first["id"] == tid
        assert second is None  # 이미 running — 재claim 0


# ── 4. 종료 task 배제 (done/failed는 복구 대상 아님) ──────────────────────────


class TestTerminatedTasksExcluded:
    """워커 복구가 done/failed task를 건드리지 않는다(종료 제외)."""

    def test_done_and_failed_tasks_not_recovered(
        self, db_path: Path, business_db: Any
    ) -> None:
        """done·failed task는 워커 부팅 resume/running 복구 대상이 아니다."""
        queue = TaskQueue(db_path=db_path)
        done_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.complete(done_id)
        failed_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.fail(failed_id)

        # running 복구: done/failed는 list_by_status('running')에 없어 무관.
        recover_running_orphans(db_path)
        # 워커 부팅 resume: suspended만 보므로 done/failed 무관.
        worker = Worker(db_path=db_path, poll_interval=0.0)
        engine = _mock_engine(db_path)
        worker.run(max_ticks=1, engine=engine, max_polls=2)

        # 상태 무변(복구가 종료 task를 안 건드림).
        assert queue.get(done_id)["status"] == "done"
        assert queue.get(failed_id)["status"] == "failed"
        assert _outbox_count(business_db) == 0


# ── 5. 인프로세스 재시작 멱등 — 마커 선존 시 재개 후 email 1행 (B1 dedup) ──────


class TestInProcessRestartIdempotency:
    """재시작 후 이미 실행된 비가역 도구가 재호출되지 않는다(마커 선존 → B1 skip)."""

    def test_worker_resume_after_marker_keeps_email_exactly_one(
        self, db_path: Path, business_db: Any
    ) -> None:
        """크래시 윈도(a): TOOL_CALL_RESULT 마커 + outbox 1행이 이미 있는 상태에서 워커
        재개 → B1 dedup이 도구를 재호출하지 않아 email 싱크 1행 유지(2회 안 됨).
        """
        run_id, task_id = _make_suspended_task(db_path)
        _approve_pending(run_id, db_path)

        store = EventStore(db_path=db_path)
        # ── 크래시 직전 상태 수동 조성: n3 도구가 이미 1회 실행됐다고 가정 ──
        business_db.record_email(
            to="minjun.kim@example.com",
            subject="주문 확인",
            body="안녕하세요 김민준 고객님.",
            idempotency_key=f"{run_id}:n3_email_send",
            sent_at="2026-06-09T00:00:00Z",
        )
        store.append(
            ToolCallResult(
                run_id=run_id,
                ts="2026-06-09T00:00:00Z",
                node_id="n3_email_send",
                name="email_send",
                result={"sent": True, "to": "minjun.kim@example.com"},
            )
        )
        assert _outbox_count(business_db) == 1

        # 워커 재개 — B1 dedup이 마커를 보고 도구 callable을 재호출하지 않아야 한다.
        worker = Worker(db_path=db_path, poll_interval=0.0)
        engine = _mock_engine(db_path)
        worker.run(max_ticks=1, engine=engine, max_polls=200)

        # 재개가 정상 종료(success)로 마킹됨(dedup skip 후 next=None 도달).
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "done"
        # ★email 싱크 정확히 1행(재호출 0 — B1 dedup) + TOOL_CALL_RESULT 1건 유지.
        assert _outbox_count(business_db) == 1, (
            "재개가 email을 두 번 기록함(dedup 실패)"
        )
        marker_count = sum(
            1
            for _, pj in store.read_since(0)
            if json.loads(pj).get("type") == "TOOL_CALL_RESULT"
            and json.loads(pj).get("node_id") == "n3_email_send"
        )
        assert marker_count == 1, "TOOL_CALL_RESULT(n3)가 1건이 아님(재발행됨)"
