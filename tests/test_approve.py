"""office/approve.py 단위 테스트 — US-007 수용 기준 3개.

테스트 범위 (계획 §5 Unit + AC):
1. pending list: 인터럽트 2개 주입(1개는 RESOLVED) → 미해결 1개만 + 경과시간 표시.
2. approve → INTERRUPT_RESOLVED 적재 + TaskQueue suspended→queued(resume) 단언.
3. reject 경로 동일 + decision=reject.
4. 파일 폴백: approvals/*.json 떨굼 → poll_file_decisions가 처리 + .consumed rename.
5. 2-프로세스 시뮬 (AC3): 같은 DB에 커넥션 2개 — database is locked 없이 완료(WAL).
   threading 금지 — 순차 교차 호출로 시뮬.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from office.approve import (
    _all_pending_interrupts,
    _record_decision,
    _write_approval_file,
    cmd_list,
    poll_file_decisions,
)
from office.db import get_connection
from office.event_store import EventStore
from office.events import (
    InterruptRequested,
    InterruptResolved,
    RunStarted,
)
from office.task_queue import STATUS_QUEUED, STATUS_SUSPENDED, TaskQueue


# ── 픽스처 ────────────────────────────────────────────────────────────────────


def _ts() -> str:
    return "2026-06-08T00:00:00+00:00"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 SQLite DB 경로 (실 파일 오염 없음)."""
    return tmp_path / "office.db"


@pytest.fixture
def store(db_path: Path) -> EventStore:
    """테스트용 EventStore."""
    return EventStore(db_path=db_path)


@pytest.fixture
def queue(db_path: Path) -> TaskQueue:
    """테스트용 TaskQueue (같은 DB 파일)."""
    return TaskQueue(db_path=db_path)


@pytest.fixture
def approvals_dir(tmp_path: Path) -> Path:
    """state/approvals/ 역할을 하는 임시 디렉토리."""
    d = tmp_path / "approvals"
    d.mkdir()
    return d


# ── 헬퍼: 인터럽트 이벤트 + task 준비 ─────────────────────────────────────────


def _make_interrupt(
    store: EventStore,
    queue: TaskQueue,
    run_id: str,
    node_id: str,
    tool: str,
    task_id: int | None = None,
) -> int:
    """INTERRUPT_REQUESTED를 발행하고 task를 suspended로 만든 뒤 interrupt_id(seq) 반환."""
    # task_id가 주어지면 payload에 포함 (US-006 게이팅 계약)
    payload: dict = {"args": {"to": "user@example.com"}}
    if task_id is not None:
        payload["task_id"] = task_id

    event = InterruptRequested(
        run_id=run_id,
        ts=_ts(),
        node_id=node_id,
        tool=tool,
        payload=payload,
    )
    seq = store.append(event)

    # task를 suspended로 전환 (게이팅이 했을 동작 모사)
    if task_id is not None:
        queue._update_status(task_id, STATUS_SUSPENDED)

    return seq  # seq = interrupt_id


# ── 1. pending list 테스트 ─────────────────────────────────────────────────────


class TestPendingList:
    """_all_pending_interrupts 및 cmd_list 테스트."""

    def test_pending_list_returns_only_unresolved(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """인터럽트 2개 주입, 1개 RESOLVED → 미해결 1개만 반환."""
        run_id = "run-list-01"
        store.append(RunStarted(run_id=run_id, ts=_ts()))

        # task 2개 enqueue (suspend 필요)
        tid1 = queue.enqueue("wf", {"x": 1})
        tid2 = queue.enqueue("wf", {"x": 2})

        # 인터럽트 1: 미해결
        iid1 = _make_interrupt(store, queue, run_id, "n3", "email_send", tid1)
        # 인터럽트 2: 바로 RESOLVED 처리
        iid2 = _make_interrupt(store, queue, run_id, "n4", "sms_send", tid2)  # noqa: F841
        store.append(
            InterruptResolved(
                run_id=run_id,
                ts=_ts(),
                node_id="n4",
                tool="sms_send",
                decision="approve",
            )
        )

        pending = _all_pending_interrupts(db_path=db_path)

        # 미해결 1개만
        assert len(pending) == 1
        item = pending[0]
        assert item["interrupt_id"] == iid1
        assert item["run_id"] == run_id
        assert item["tool"] == "email_send"
        assert item["risk"] == "needs-approval"

    def test_pending_list_contains_elapsed(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """pending 항목에 elapsed 필드가 문자열로 포함된다."""
        run_id = "run-elapsed-01"
        tid = queue.enqueue("wf")
        _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        pending = _all_pending_interrupts(db_path=db_path)
        assert len(pending) == 1
        elapsed = pending[0]["elapsed"]
        assert isinstance(elapsed, str)
        # 내용이 있어야 함 (빈 문자열 아님)
        assert len(elapsed) > 0

    def test_pending_list_empty_when_all_resolved(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """모두 RESOLVED면 pending은 빈 리스트."""
        run_id = "run-empty-01"
        tid = queue.enqueue("wf")
        _make_interrupt(store, queue, run_id, "n3", "email_send", tid)
        store.append(
            InterruptResolved(
                run_id=run_id,
                ts=_ts(),
                node_id="n3",
                tool="email_send",
                decision="approve",
            )
        )

        pending = _all_pending_interrupts(db_path=db_path)
        assert pending == []

    def test_cmd_list_prints_no_pending(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """pending 없으면 '없습니다' 메시지를 출력한다."""
        # EventStore 초기화만 (빈 DB)
        EventStore(db_path=db_path)
        cmd_list(db_path=db_path)
        captured = capsys.readouterr()
        assert "없습니다" in captured.out

    def test_cmd_list_prints_pending_rows(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """pending 항목이 있으면 interrupt_id·run_id·도구명·경과시간을 출력한다."""
        run_id = "run-print-01"
        tid = queue.enqueue("wf")
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        cmd_list(db_path=db_path)
        captured = capsys.readouterr()

        assert str(iid) in captured.out
        assert run_id in captured.out
        assert "email_send" in captured.out


# ── 2. approve → INTERRUPT_RESOLVED + resume 테스트 ──────────────────────────


class TestApproveFlow:
    """_record_decision(decision='approve') 테스트."""

    def test_approve_appends_interrupt_resolved(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """approve 시 INTERRUPT_RESOLVED 이벤트가 events 테이블에 적재된다."""
        run_id = "run-approve-01"
        tid = queue.enqueue("wf")
        queue.claim()  # running으로 전환
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        _record_decision(iid, "approve", db_path=db_path)

        # INTERRUPT_RESOLVED가 DB에 있어야 함
        events = store.read(run_id)
        types = [e.type for e in events]
        assert "INTERRUPT_RESOLVED" in types

        # decision=approve 확인
        from office.events import InterruptResolved as IR

        resolved = next(e for e in events if e.type == "INTERRUPT_RESOLVED")
        assert isinstance(resolved, IR)
        assert resolved.decision == "approve"
        assert resolved.node_id == "n3"

    def test_approve_resumes_task_to_queued(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """approve 시 suspended 작업이 queued로 전환된다 (resume)."""
        run_id = "run-approve-02"
        tid = queue.enqueue("wf")
        queue.claim()  # running
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        # suspended 확인
        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        _record_decision(iid, "approve", db_path=db_path)

        # resume 후 queued
        assert queue.get(tid)["status"] == STATUS_QUEUED

    def test_approve_pending_cleared_after_resolve(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """approve 후 pending_interrupts에서 해당 항목이 사라진다."""
        run_id = "run-approve-03"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        _record_decision(iid, "approve", db_path=db_path)

        pending = _all_pending_interrupts(db_path=db_path)
        assert all(p["interrupt_id"] != iid for p in pending)


# ── 3. reject 경로 테스트 ─────────────────────────────────────────────────────


class TestRejectFlow:
    """_record_decision(decision='reject') 테스트."""

    def test_reject_appends_interrupt_resolved_with_reject(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """reject 시 INTERRUPT_RESOLVED(decision=reject)가 적재된다."""
        run_id = "run-reject-01"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        _record_decision(iid, "reject", db_path=db_path)

        from office.events import InterruptResolved as IR

        events = store.read(run_id)
        resolved = next(e for e in events if e.type == "INTERRUPT_RESOLVED")
        assert isinstance(resolved, IR)
        assert resolved.decision == "reject"

    def test_reject_also_resumes_task(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """reject 시에도 작업은 queued로 resume된다.

        reject 처리 분기(RUN_FINISHED=rejected 등)는 엔진/게이팅 몫.
        CLI는 결정 기록 + resume 트리거만 담당.
        """
        run_id = "run-reject-02"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        _record_decision(iid, "reject", db_path=db_path)

        assert queue.get(tid)["status"] == STATUS_QUEUED

    def test_reject_clears_pending(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """reject 후 pending 목록에서 사라진다."""
        run_id = "run-reject-03"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        _record_decision(iid, "reject", db_path=db_path)

        pending = _all_pending_interrupts(db_path=db_path)
        assert all(p["interrupt_id"] != iid for p in pending)

    def test_record_decision_raises_for_unknown_interrupt(self, db_path: Path) -> None:
        """존재하지 않는 interrupt_id는 ValueError를 일으킨다."""
        EventStore(db_path=db_path)  # 테이블 초기화
        with pytest.raises(ValueError, match="INTERRUPT_REQUESTED"):
            _record_decision(99999, "approve", db_path=db_path)


# ── 4. 파일 폴백 테스트 ───────────────────────────────────────────────────────


class TestFileFallback:
    """poll_file_decisions 파일 폴백 헬퍼 테스트."""

    def test_poll_file_decisions_processes_json(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        approvals_dir: Path,
    ) -> None:
        """approvals/*.json이 있으면 인지·처리한다."""
        run_id = "run-file-01"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        # 결정 파일 생성
        (approvals_dir / f"{iid}.json").write_text(
            json.dumps({"decision": "approve", "interrupt_id": iid}),
            encoding="utf-8",
        )

        results = poll_file_decisions(approvals_dir, store, db_path=db_path)

        assert len(results) == 1
        assert results[0]["decision"] == "approve"
        assert results[0]["interrupt_id"] == iid

    def test_poll_file_decisions_renames_to_consumed(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        approvals_dir: Path,
    ) -> None:
        """처리된 파일은 .consumed로 rename된다."""
        run_id = "run-file-02"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        fpath = approvals_dir / f"{iid}.json"
        fpath.write_text(
            json.dumps({"decision": "approve", "interrupt_id": iid}),
            encoding="utf-8",
        )

        poll_file_decisions(approvals_dir, store, db_path=db_path)

        # 원본 .json 파일이 사라지고 .consumed가 생겼어야 함
        assert not fpath.exists()
        assert (approvals_dir / f"{iid}.consumed").exists()

    def test_poll_file_decisions_no_duplicate(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        approvals_dir: Path,
    ) -> None:
        """같은 파일을 두 번 poll해도 중복 처리하지 않는다."""
        run_id = "run-file-03"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        fpath = approvals_dir / f"{iid}.json"
        fpath.write_text(
            json.dumps({"decision": "approve", "interrupt_id": iid}),
            encoding="utf-8",
        )

        # 첫 번째 poll
        r1 = poll_file_decisions(approvals_dir, store, db_path=db_path)
        assert len(r1) == 1

        # 두 번째 poll — .consumed 파일은 glob("*.json")에 안 걸림
        r2 = poll_file_decisions(approvals_dir, store, db_path=db_path)
        assert len(r2) == 0

    def test_poll_file_decisions_empty_dir(
        self, store: EventStore, db_path: Path, approvals_dir: Path
    ) -> None:
        """빈 approvals 디렉토리는 빈 리스트를 반환한다."""
        results = poll_file_decisions(approvals_dir, store, db_path=db_path)
        assert results == []

    def test_poll_file_decisions_nonexistent_dir(
        self, store: EventStore, db_path: Path, tmp_path: Path
    ) -> None:
        """존재하지 않는 디렉토리는 빈 리스트를 반환한다."""
        missing = tmp_path / "no_such_dir"
        results = poll_file_decisions(missing, store, db_path=db_path)
        assert results == []

    def test_poll_file_decisions_resumes_task(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        approvals_dir: Path,
    ) -> None:
        """파일 폴백 처리 시 작업도 queued로 resume된다."""
        run_id = "run-file-04"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        (approvals_dir / f"{iid}.json").write_text(
            json.dumps({"decision": "reject", "interrupt_id": iid}),
            encoding="utf-8",
        )

        poll_file_decisions(approvals_dir, store, db_path=db_path)

        # reject여도 resume
        assert queue.get(tid)["status"] == STATUS_QUEUED

    def test_write_approval_file_creates_json(
        self, db_path: Path, tmp_path: Path
    ) -> None:
        """_write_approval_file이 올바른 JSON 파일을 생성한다."""
        adir = tmp_path / "approvals"
        fpath = _write_approval_file(42, "approve", approvals_dir=adir)

        assert fpath.exists()
        data = json.loads(fpath.read_text(encoding="utf-8"))
        assert data["decision"] == "approve"
        assert data["interrupt_id"] == 42


# ── 5. 2-프로세스 시뮬 (AC3) ─────────────────────────────────────────────────


class TestTwoProcessSimulation:
    """같은 DB에 커넥션 2개(디스패처 역할 + CLI 역할) 교차 write — WAL 검증.

    threading 금지 — 순차 교차 호출로 시뮬레이션.
    """

    def test_two_connections_no_lock(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """디스패처 커넥션(store)과 별도 CLI 커넥션이 교차로 써도 locked 없음."""
        run_id = "run-2proc-01"
        tid = queue.enqueue("wf")
        queue.claim()

        # 디스패처 역할: INTERRUPT_REQUESTED 발행
        _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        # CLI 역할: 별도 커넥션으로 INTERRUPT_RESOLVED 직접 삽입
        # (threading 금지 — 동일 스레드에서 순차 교차 호출)
        resolved_payload = json.dumps(
            {
                "run_id": run_id,
                "ts": _ts(),
                "type": "INTERRUPT_RESOLVED",
                "family": "Interrupt",
                "schema_v": 1,
                "node_id": "n3",
                "tool": "email_send",
                "decision": "approve",
            }
        )
        with get_connection(db_path) as conn2:
            conn2.execute(
                """
                INSERT INTO events (run_id, family, type, payload_json, ts, schema_v)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, "Interrupt", "INTERRUPT_RESOLVED", resolved_payload, _ts(), 1),
            )
            conn2.commit()

        # 디스패처 역할: 결과 확인 (별도 커넥션으로 read)
        events = store.read(run_id)
        types = [e.type for e in events]
        assert "INTERRUPT_REQUESTED" in types
        assert "INTERRUPT_RESOLVED" in types

    def test_two_connections_full_approve_cycle(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """디스패처 폴링 + 별도 커넥션 결정 기록 교차 시뮬 — 전 사이클 완주.

        순서:
        1. 디스패처: 작업 enqueue → claim → interrupt 발행 → suspend
        2. CLI(별도 커넥션): _record_decision으로 approve 기록
        3. 디스패처: pending_interrupts 재확인 → 빈 목록 (해소됨)
        4. 작업 status = queued (resume 확인)
        """
        run_id = "run-2proc-02"

        # 1. 디스패처 역할 (store/queue 커넥션)
        tid = queue.enqueue("crm_notify")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        # pending 확인
        pending_before = _all_pending_interrupts(db_path=db_path)
        assert len(pending_before) == 1

        # 2. CLI 역할 (별도 커넥션 — _record_decision이 내부적으로 새 커넥션 사용)
        # threading 사용 없이 순차 교차 호출
        result = _record_decision(iid, "approve", db_path=db_path)
        assert result["decision"] == "approve"
        assert result["task_id"] == tid

        # 3. 디스패처: pending 재확인
        pending_after = _all_pending_interrupts(db_path=db_path)
        assert len(pending_after) == 0

        # 4. 작업 resume 확인
        assert queue.get(tid)["status"] == STATUS_QUEUED

    def test_multiple_interrupts_two_connections(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """여러 인터럽트가 있을 때 교차 커넥션으로 순차 처리 — locked 없음."""
        run_id = "run-2proc-03"

        tid1 = queue.enqueue("wf1")
        queue.claim()
        iid1 = _make_interrupt(store, queue, run_id, "n3", "email_send", tid1)

        tid2 = queue.enqueue("wf2")
        queue.claim()
        iid2 = _make_interrupt(store, queue, run_id, "n4", "sms_send", tid2)

        # CLI 역할: 첫 번째 approve (별도 커넥션)
        _record_decision(iid1, "approve", db_path=db_path)

        # 디스패처 역할: pending 재확인 — iid2만 남아야 함
        pending = _all_pending_interrupts(db_path=db_path)
        assert len(pending) == 1
        assert pending[0]["interrupt_id"] == iid2

        # CLI 역할: 두 번째 reject
        _record_decision(iid2, "reject", db_path=db_path)

        # 모두 해소
        pending_final = _all_pending_interrupts(db_path=db_path)
        assert pending_final == []

        # 두 작업 모두 resume
        assert queue.get(tid1)["status"] == STATUS_QUEUED
        assert queue.get(tid2)["status"] == STATUS_QUEUED
