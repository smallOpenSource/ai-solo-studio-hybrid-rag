"""US-003(T3) Inbox API + 승인 배선 통합 테스트 — FastAPI TestClient.

오프라인 결정론(실서버·LLM 0). tmp_path db 주입으로 state/office.db 무오염.
slow 마커 없음.

테스트 범위(PRD US-003 수용 6·7·8 / 계획 §4.5·§4.6):
- FailReason question 3값(gating|escalation|question) + 자유 문자열 ValueError(수용 7).
- _record_decision(resume=False): INTERRUPT_RESOLVED·파일만 기록, TaskQueue.resume
  미호출(task 상태 불변). resume=True(기본)는 종전대로 resume까지 — 회귀 가드(수용 8).
- GET /api/inbox 3분류 매핑: INTERRUPT_REQUESTED 미해소→Review, NODE_FAILED(escalation)·
  RUN_FINISHED·RUN_ERROR·NODE_FAILED(gating)→Notify, NODE_FAILED(question)→Question(수용 6).
- POST approve/reject → _record_decision(resume=False) 호출 단언(수용 8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from office.approve import _all_pending_interrupts, _record_decision
from office.event_store import EventStore
from office.events import (
    FailReason,
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunError,
    RunFinished,
    event_from_dict,
)
from office.task_queue import STATUS_SUSPENDED, TaskQueue
from web_server.app import create_app


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _ts() -> str:
    return "2026-06-08T00:00:00+00:00"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 SQLite DB 경로 (실 파일 오염 없음)."""
    return tmp_path / "office.db"


@pytest.fixture
def store(db_path: Path) -> EventStore:
    return EventStore(db_path=db_path)


@pytest.fixture
def queue(db_path: Path) -> TaskQueue:
    return TaskQueue(db_path=db_path)


def _make_interrupt(
    store: EventStore,
    queue: TaskQueue,
    run_id: str,
    node_id: str,
    tool: str,
    task_id: int | None = None,
) -> int:
    """INTERRUPT_REQUESTED 발행 + task를 suspended로 만든 뒤 interrupt_id(seq) 반환."""
    payload: dict = {"args": {"to": "user@example.com"}}
    if task_id is not None:
        payload["task_id"] = task_id
    seq = store.append(
        InterruptRequested(
            run_id=run_id, ts=_ts(), node_id=node_id, tool=tool, payload=payload
        )
    )
    if task_id is not None:
        queue._update_status(task_id, STATUS_SUSPENDED)
    return seq


# ── 1. FailReason question 3값 + 자유 문자열 ValueError (수용 7) ───────────────


class TestFailReasonQuestion:
    """FailReason에 question이 추가되어 3값이고 자유 문자열은 거부됨."""

    def test_fail_reason_has_three_values(self) -> None:
        """gating·escalation·question 3값(question은 Phase 4a 추가)."""
        assert FailReason.question.value == "question"
        assert {r.value for r in FailReason} == {"gating", "escalation", "question"}

    def test_node_failed_question_roundtrip(self) -> None:
        """reason=question NODE_FAILED가 직렬화/역직렬화 라운드트립된다."""
        e = NodeFailed(
            run_id="r",
            ts=_ts(),
            node_id="n1",
            reason=FailReason.question,
            detail="되묻기",
        )
        d = e.to_dict()
        assert d["reason"] == "question"  # 문자열로 직렬화
        restored = event_from_dict(d)
        assert isinstance(restored, NodeFailed)
        assert restored.reason == FailReason.question

    def test_free_string_reason_still_rejected(self) -> None:
        """question 추가 후에도 자유 문자열 reason은 ValueError."""
        data = {
            "run_id": "r",
            "ts": _ts(),
            "type": "NODE_FAILED",
            "family": "Interrupt",
            "schema_v": 1,
            "node_id": "n1",
            "reason": "not-a-real-reason",
            "detail": "",
        }
        with pytest.raises(ValueError):
            event_from_dict(data)


# ── 2. _record_decision(resume=False) — 기록만, resume 미호출 (수용 8) ─────────


class TestRecordDecisionResumeFlag:
    """resume 파라미터: False면 TaskQueue.resume 건너뜀, True(기본)면 종전대로."""

    def test_resume_false_records_but_does_not_resume(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """resume=False: INTERRUPT_RESOLVED·파일 기록 O, task 상태 불변(suspended 유지)."""
        run_id = "run-rf-01"
        tid = queue.enqueue("wf")
        queue.claim()  # running
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)
        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        approvals = db_path.parent / "approvals"
        result = _record_decision(
            iid, "approve", db_path=db_path, approvals_dir=approvals, resume=False
        )

        # ① INTERRUPT_RESOLVED 발행됨
        types = [e.type for e in store.read(run_id)]
        assert "INTERRUPT_RESOLVED" in types
        # ② 파일 기록됨
        assert (approvals / f"{iid}.json").exists()
        # ③ TaskQueue.resume 미호출 → task 상태 불변(suspended 유지, queued 아님)
        assert queue.get(tid)["status"] == STATUS_SUSPENDED
        assert result["task_id"] == tid

    def test_resume_true_is_unchanged_default(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """resume=True(기본): 종전대로 resume까지 → task가 queued로 전환(회귀 가드)."""
        from office.task_queue import STATUS_QUEUED

        run_id = "run-rt-01"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)
        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        # resume 인자 미지정 = 기본 True → 종전 동작
        _record_decision(iid, "approve", db_path=db_path)

        assert queue.get(tid)["status"] == STATUS_QUEUED

    def test_resume_false_reject_same_path(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """reject도 resume=False면 동일 — 기록만, task 상태 불변."""
        run_id = "run-rf-rej"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        _record_decision(iid, "reject", db_path=db_path, resume=False)

        resolved = next(e for e in store.read(run_id) if e.type == "INTERRUPT_RESOLVED")
        assert isinstance(resolved, InterruptResolved)
        assert resolved.decision == "reject"
        assert queue.get(tid)["status"] == STATUS_SUSPENDED


# ── 3. GET /api/inbox 3분류 매핑 (수용 6) ─────────────────────────────────────


class TestInboxClassification:
    """이벤트 → Review/Notify/Question 3분류 매핑 정확성."""

    def test_interrupt_requested_goes_to_review(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """미해소 INTERRUPT_REQUESTED는 Review에, _all_pending_interrupts와 일치."""
        run_id = "run-rev-01"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/inbox").json()

        assert [r["interrupt_id"] for r in body["review"]] == [iid]
        # Review가 _all_pending_interrupts와 일치
        pending_ids = [
            p["interrupt_id"] for p in _all_pending_interrupts(db_path=db_path)
        ]
        assert [r["interrupt_id"] for r in body["review"]] == pending_ids
        assert body["notify"] == []
        assert body["question"] == []

    def test_resolved_interrupt_leaves_review(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """INTERRUPT_RESOLVED 후 그 항목은 Review에서 사라진다."""
        run_id = "run-rev-02"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)
        store.append(
            InterruptResolved(
                run_id=run_id,
                ts=_ts(),
                node_id="n3",
                tool="email_send",
                decision="approve",
            )
        )

        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/inbox").json()

        assert all(r["interrupt_id"] != iid for r in body["review"])

    def test_escalation_and_gating_and_lifecycle_go_to_notify(
        self, store: EventStore, db_path: Path
    ) -> None:
        """NODE_FAILED(escalation)·NODE_FAILED(gating)·RUN_FINISHED·RUN_ERROR → Notify."""
        run_id = "run-notify-01"
        s_esc = store.append(
            NodeFailed(
                run_id=run_id, ts=_ts(), node_id="n2", reason=FailReason.escalation
            )
        )
        s_gat = store.append(
            NodeFailed(run_id=run_id, ts=_ts(), node_id="n3", reason=FailReason.gating)
        )
        s_fin = store.append(RunFinished(run_id=run_id, ts=_ts(), status="success"))
        s_err = store.append(RunError(run_id=run_id, ts=_ts(), error="boom"))

        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/inbox").json()

        notify_seqs = {n["seq"] for n in body["notify"]}
        assert notify_seqs == {s_esc, s_gat, s_fin, s_err}
        assert body["question"] == []
        assert body["review"] == []

    def test_question_reason_goes_to_question(
        self, store: EventStore, db_path: Path
    ) -> None:
        """NODE_FAILED(question)은 Question 칸에만 들어간다(Notify 아님)."""
        run_id = "run-q-01"
        s_q = store.append(
            NodeFailed(
                run_id=run_id,
                ts=_ts(),
                node_id="n4",
                reason=FailReason.question,
                detail="확인?",
            )
        )

        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/inbox").json()

        assert [q["seq"] for q in body["question"]] == [s_q]
        assert body["question"][0]["reason"] == "question"
        assert body["notify"] == []

    def test_all_three_classes_together(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """세 칸이 동시에 채워질 때 각 이벤트가 정확한 칸으로 간다."""
        run_id = "run-mix-01"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)  # Review
        store.append(
            NodeFailed(
                run_id=run_id, ts=_ts(), node_id="n2", reason=FailReason.escalation
            )
        )  # Notify
        store.append(
            NodeFailed(
                run_id=run_id, ts=_ts(), node_id="n5", reason=FailReason.question
            )
        )  # Question

        app = create_app(store=store)
        with TestClient(app) as client:
            body = client.get("/api/inbox").json()

        assert [r["interrupt_id"] for r in body["review"]] == [iid]
        assert [n["reason"] for n in body["notify"]] == ["escalation"]
        assert [q["reason"] for q in body["question"]] == ["question"]

    def test_bad_reason_does_not_break_inbox(
        self, store: EventStore, db_path: Path
    ) -> None:
        """비표준 reason NODE_FAILED가 DB에 있어도 inbox가 5xx로 깨지지 않는다.

        원문 읽기(read_since)라 event_from_dict ValueError를 안 탄다. 비표준
        reason은 어느 칸에도 안 들어가고 조용히 무시된다(분류 안전).
        """
        from office.db import get_connection

        run_id = "run-bad-01"
        bad_payload = json.dumps(
            {
                "run_id": run_id,
                "ts": _ts(),
                "type": "NODE_FAILED",
                "family": "Interrupt",
                "schema_v": 1,
                "node_id": "n1",
                "reason": "totally-invalid",
                "detail": "",
            },
            ensure_ascii=False,
        )
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO events (run_id, family, type, payload_json, ts, schema_v)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, "Interrupt", "NODE_FAILED", bad_payload, _ts(), 1),
            )
            conn.commit()

        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.get("/api/inbox")
            assert resp.status_code == 200
            body = resp.json()

        # 비표준 reason은 Notify·Question 어디에도 없다.
        assert body["notify"] == []
        assert body["question"] == []


# ── 4. POST approve/reject → _record_decision(resume=False) 호출 (수용 8) ──────


class TestApproveRejectEndpoints:
    """POST가 _record_decision을 resume=False로 호출함을 단언."""

    def test_post_approve_calls_record_decision_resume_false(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST .../approve가 _record_decision(..., decision='approve', resume=False) 호출."""
        run_id = "run-post-01"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        calls: list[dict] = []
        import web_server.inbox as inbox_mod

        real = inbox_mod._record_decision

        def _spy(interrupt_id, decision, **kwargs):
            calls.append({"interrupt_id": interrupt_id, "decision": decision, **kwargs})
            return real(interrupt_id, decision, **kwargs)

        monkeypatch.setattr(inbox_mod, "_record_decision", _spy)

        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post(f"/api/inbox/{iid}/approve")

        assert resp.status_code == 200
        assert len(calls) == 1
        assert calls[0]["interrupt_id"] == iid
        assert calls[0]["decision"] == "approve"
        assert calls[0]["resume"] is False  # ★ 핵심 단언

    def test_post_reject_calls_record_decision_resume_false(
        self,
        store: EventStore,
        queue: TaskQueue,
        db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST .../reject가 decision='reject', resume=False로 호출."""
        run_id = "run-post-02"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)

        calls: list[dict] = []
        import web_server.inbox as inbox_mod

        real = inbox_mod._record_decision

        def _spy(interrupt_id, decision, **kwargs):
            calls.append({"interrupt_id": interrupt_id, "decision": decision, **kwargs})
            return real(interrupt_id, decision, **kwargs)

        monkeypatch.setattr(inbox_mod, "_record_decision", _spy)

        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post(f"/api/inbox/{iid}/reject")

        assert resp.status_code == 200
        assert calls[0]["decision"] == "reject"
        assert calls[0]["resume"] is False

    def test_post_approve_records_resolved_but_task_unchanged(
        self, store: EventStore, queue: TaskQueue, db_path: Path
    ) -> None:
        """POST approve(resume=False): INTERRUPT_RESOLVED 기록 + task 상태 불변(suspended)."""
        run_id = "run-post-03"
        tid = queue.enqueue("wf")
        queue.claim()
        iid = _make_interrupt(store, queue, run_id, "n3", "email_send", tid)
        assert queue.get(tid)["status"] == STATUS_SUSPENDED

        app = create_app(store=store)
        with TestClient(app) as client:
            client.post(f"/api/inbox/{iid}/approve")

        # INTERRUPT_RESOLVED 발행됨
        resolved = next(e for e in store.read(run_id) if e.type == "INTERRUPT_RESOLVED")
        assert isinstance(resolved, InterruptResolved)
        assert resolved.decision == "approve"
        # task 상태 불변 — resume=False라 queued로 안 감(재개는 폴링 루프 담당)
        assert queue.get(tid)["status"] == STATUS_SUSPENDED
        # Review에서도 사라짐(해소됨)
        with TestClient(app) as client:
            body = client.get("/api/inbox").json()
        assert all(r["interrupt_id"] != iid for r in body["review"])

    def test_post_unknown_interrupt_returns_404(
        self, store: EventStore, db_path: Path
    ) -> None:
        """없는 interrupt_id에 POST하면 404(스트림·앱이 죽지 않음)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/inbox/99999/approve")
        assert resp.status_code == 404
