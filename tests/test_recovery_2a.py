"""running 고아 2a 복구 + US-P6B-0 run_id 매핑 + 6a NIT — Phase 6 6b Chunk A.

계획 ralplan-phase6b-2a-agent.md §6 US-P6B-0/1/4. agent(US-P6B-2/3)는 Chunk B라 미포함.

검증 범위:
- US-P6B-0: 워커 claim 직후 task payload에 run_id 1줄 기록 → load_run_id로 식별
  (claim~RUN_STARTED 윈도 크래시 식별 포함).
- US-P6B-1: A-R2(이벤트 재생 + 그래프 재로드) 재구성 정확성(accumulated 복원·그래프
  next 체인 current_id 추론·messages 복원 불요) + B-HYBRID 분기(가역 재개/비가역
  fail+Notify/chat 전진 판정) + 같은 run_id 재개 멱등(email 정확히-1행) + 다중 고아 순차.
- US-P6B-4: NIT-1(WAL 동시 write 직렬화) + NIT-2(starvation cancel escape).

★전부 max_ticks/max_polls 바운드 + 스레드 테스트는 join 바운드(hang 0). 오프라인
결정론(실서버·LLM 0 — mock _FakeSmokeLLM). tmp_path db 주입(office.db·business.db).
실행은 오직 scripts/run_tests.sh engine(3중 가드). bare pytest 금지(hang 전례).
"""

from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from office import run as run_module
from office.approve import _record_decision
from office.business import BusinessStore
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import StateDelta, ToolCallResult
from office.gating import GatingPolicy
from office.recovery import (
    infer_resume_node_id,
    is_resumable,
    replay_accumulated,
)
from office.task_queue import TaskQueue
from office.tools import REAL_TOOLS, reset_business_store
from office.worker import Worker
from workflows.crm_notify import NODES, WORKFLOW_NAME

# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 office.db 경로(이벤트·큐)."""
    return tmp_path / "office.db"


@pytest.fixture
def business_db(tmp_path: Path) -> Any:
    """tmp business.db를 전역 BusinessStore로 주입·원복(email 멱등 단언용)."""
    store = BusinessStore(db_path=tmp_path / "business.db")
    reset_business_store(store)
    yield store
    reset_business_store(None)


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _mock_engine(db_path: Path) -> WorkflowEngine:
    """crm_notify 4노드 결정론 fake LLM + REAL_TOOLS 엔진(워커 주입용)."""
    store = EventStore(db_path=db_path)
    llm = run_module._FakeSmokeLLM(
        ["crm_get_customer", "order_get_latest", "chat", "email_send"]
    )
    return WorkflowEngine(
        executor=NodeExecutor(llm_client=llm, event_store=store),
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )


def _make_running_orphan(
    db_path: Path,
    *,
    run_id: str,
    upto: str,
    record_run_id: bool = True,
) -> int:
    """crm_notify를 upto 노드까지만 실행한 running 고아를 만든다(직전 크래시 모사).

    upto in {"n1","n2","n2_5","all"}: 그 노드까지 실행 후 next=None으로 끊어 종료시키되
    task는 running으로 남긴다(complete/fail 마킹 안 함 = 고아). run_id를 payload에 기록
    (US-P6B-0 경로). all이면 plan_mode로 N3까지 자동승인 실행(email 1행 발생).
    """
    store = EventStore(db_path=db_path)
    queue = TaskQueue(db_path=db_path)
    seq_map = {
        "n1": ["crm_get_customer"],
        "n2": ["crm_get_customer", "order_get_latest"],
        "n2_5": ["crm_get_customer", "order_get_latest", "chat"],
        "all": ["crm_get_customer", "order_get_latest", "chat", "email_send"],
    }
    node_map = {
        "n1": [dataclasses.replace(NODES[0], next=None)],
        "n2": [NODES[0], dataclasses.replace(NODES[1], next=None)],
        "n2_5": [NODES[0], NODES[1], dataclasses.replace(NODES[2], next=None)],
        "all": list(NODES),
    }
    llm = run_module._FakeSmokeLLM(seq_map[upto])
    engine = WorkflowEngine(
        executor=NodeExecutor(llm_client=llm, event_store=store),
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=GatingPolicy(),
    )
    task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
    queue.claim()
    if record_run_id:
        run_module._record_run_id_in_payload(task_id, run_id, db_path)
    engine.run(
        nodes=node_map[upto],
        run_id=run_id,
        workflow=WORKFLOW_NAME,
        task_id=task_id,
        plan_mode=(upto == "all"),
    )
    assert queue.get(task_id)["status"] == "running", "고아 전제: running 상태 유지"
    return task_id


# ── US-P6B-0: run_id 매핑 기록 ────────────────────────────────────────────────


class TestRunIdMapping:
    """claim 직후 payload run_id 기록 → load_run_id 식별(US-P6B-0)."""

    def test_run_id_recorded_in_payload_merges_mock_flag(self, db_path: Path) -> None:
        """run_id 기록이 기존 {"mock":True}를 보존하며 run_id를 병합(AC-P6B-0a)."""
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
        queue.claim()
        run_module._record_run_id_in_payload(task_id, "rid-xyz", db_path)

        payload = json.loads(queue.get(task_id)["payload_json"])
        assert payload["run_id"] == "rid-xyz"
        assert payload["mock"] is True  # mock 플래그 보존(병합)
        assert run_module.load_run_id(task_id, db_path) == "rid-xyz"

    def test_window_crash_identified_by_payload_run_id(self, db_path: Path) -> None:
        """RUN_STARTED 미발행(claim~윈도)이라도 payload run_id로 식별(AC-P6B-0b).

        run_id만 기록하고 engine.run을 아예 안 돌린(이벤트 0건) 상태 = claim 직후
        ~ RUN_STARTED 발행 전 크래시. 그래도 load_run_id가 run_id를 돌려준다.
        """
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
        queue.claim()
        run_module._record_run_id_in_payload(task_id, "rid-window", db_path)
        # 이벤트 0건(RUN_STARTED 미발행) 확인.
        assert EventStore(db_path=db_path).read("rid-window") == []
        assert run_module.load_run_id(task_id, db_path) == "rid-window"

    def test_load_run_id_none_when_absent(self, db_path: Path) -> None:
        """payload에 run_id 없으면 None(2b 폴백 트리거 — AC-P6B-1c)."""
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
        queue.claim()
        assert run_module.load_run_id(task_id, db_path) is None


# ── US-P6B-1: A-R2 재구성 정확성(unit) ────────────────────────────────────────


class TestAR2Reconstruction:
    """이벤트 재생 + 그래프 재로드 재구성(AC-P6B-1a/1d · U-2a-1)."""

    def test_replay_accumulated_from_state_delta(self, db_path: Path) -> None:
        """합성 STATE_DELTA 재생 → accumulated 키 정확 복원(messages 미사용)."""
        store = EventStore(db_path=db_path)
        rid = "rid-replay"
        store.append(
            StateDelta(run_id=rid, ts="t1", accumulated={"n1_crm_get_result": {"a": 1}})
        )
        store.append(
            StateDelta(
                run_id=rid, ts="t2", accumulated={"n2_order_get_result": {"b": 2}}
            )
        )
        acc = replay_accumulated(store, rid)
        assert acc == {"n1_crm_get_result": {"a": 1}, "n2_order_get_result": {"b": 2}}

    def test_replay_accumulated_real_run_keys(self, db_path: Path) -> None:
        """실제 crm_notify N1+N2+N2.5 실행 events 재생 → 노드별 prefix 키 복원."""
        _make_running_orphan(db_path, run_id="rid-real", upto="n2_5")
        acc = replay_accumulated(EventStore(db_path=db_path), "rid-real")
        # tool 노드 결과 + chat 콘텐츠 키 존재(완료 마커).
        assert "n1_crm_get_result" in acc
        assert "n2_order_get_result" in acc
        assert "n2_5_summary_content" in acc
        # N3는 아직 미실행 — 결과 키 부재.
        assert "n3_email_send_result" not in acc

    def test_infer_resume_node_via_graph_next_chain(self, db_path: Path) -> None:
        """그래프 next 체인 + accumulated 마커로 current_id(재개 노드) 추론."""
        # N1만 완료(result 키 1개) → 재개 노드는 N2.
        acc_n1 = {"n1_crm_get_result": {}, "n1_crm_get_call": {}}
        assert infer_resume_node_id(list(NODES), acc_n1) == "n2_order_get"
        # N1+N2 완료 → 재개 노드는 N2.5(chat).
        acc_n2 = {"n1_crm_get_result": {}, "n2_order_get_result": {}}
        assert infer_resume_node_id(list(NODES), acc_n2) == "n2_5_summary"
        # 전부 완료 → None.
        acc_all = {
            "n1_crm_get_result": {},
            "n2_order_get_result": {},
            "n2_5_summary_content": {},
            "n3_email_send_result": {},
        }
        assert infer_resume_node_id(list(NODES), acc_all) is None

    def test_messages_restore_not_needed(self, db_path: Path) -> None:
        """★messages 복원 불요(carryforward): STATE_DELTA에 messages가 안 실리고
        재구성도 accumulated만 쓴다(재개 노드가 _build_carry_note로 재생성).
        """
        _make_running_orphan(db_path, run_id="rid-carry", upto="n2")
        rows = EventStore(db_path=db_path).read_rows("rid-carry")
        for row in rows:
            if row["type"] == "STATE_DELTA":
                payload = json.loads(row["payload_json"])
                # STATE_DELTA payload엔 messages 키가 없다(accumulated만).
                assert "messages" not in payload
        # 재구성 결과에도 messages 없음.
        acc = replay_accumulated(EventStore(db_path=db_path), "rid-carry")
        assert "messages" not in acc


# ── US-P6B-1: B-HYBRID 분기 ───────────────────────────────────────────────────


class TestBHybridBranch:
    """가역=2a 재개 / 비가역=2b fail+Notify / chat 전진 판정(AC-P6B-1c · U-2a-3)."""

    def test_reversible_resume_node_is_resumable(self) -> None:
        """재개 노드 N2(order_get_latest·always-allow)=가역 → resumable True."""
        acc = {"n1_crm_get_result": {}}
        rid = infer_resume_node_id(list(NODES), acc)
        assert rid == "n2_order_get"
        assert is_resumable(list(NODES), rid, GatingPolicy()) is True

    def test_chat_node_advances_to_next_tool_node(self) -> None:
        """★도구 없는 chat(N2.5) 재개 노드 → 전진 판정으로 다음 도구(N3 email) 봄.

        N3가 needs-approval(비가역)이라 결국 not resumable. chat을 default_risk로
        오판해 즉시 비가역 처리하지 않고 다음 도구까지 전진함을 입증(crm_notify
        N2.5→N3 케이스).
        """
        acc = {"n1_crm_get_result": {}, "n2_order_get_result": {}}
        rid = infer_resume_node_id(list(NODES), acc)
        assert rid == "n2_5_summary"  # chat 노드
        # chat은 전진 → N3(email_send needs-approval) → 비가역 → False.
        assert is_resumable(list(NODES), rid, GatingPolicy()) is False

    def test_irreversible_next_node_not_resumable(self) -> None:
        """재개 노드 N3(email_send·needs-approval) 자체 → 비가역 → not resumable."""
        acc = {
            "n1_crm_get_result": {},
            "n2_order_get_result": {},
            "n2_5_summary_content": {},
        }
        rid = infer_resume_node_id(list(NODES), acc)
        assert rid == "n3_email_send"
        assert is_resumable(list(NODES), rid, GatingPolicy()) is False

    def test_all_complete_is_resumable(self) -> None:
        """전부 완료(resume None) → resumable True(잔여 비가역 없음)."""
        assert is_resumable(list(NODES), None, GatingPolicy()) is True


# ── US-P6B-1: 통합 — 2a 재개 / 2b 폴백 / 멱등 / 다중 고아 ──────────────────────


class TestRunningOrphan2aRecovery:
    """Worker.recover_running_orphans(2a) — 재개·폴백·멱등·다중(AC-P6B-1a/b/c/e)."""

    def test_reversible_orphan_resumed_then_suspends_at_n3(
        self, db_path: Path, business_db: Any
    ) -> None:
        """N1만 끝난 가역 고아 → 2a 재개 → N2~N3 진행 → N3 needs-approval suspend.

        결정 없어 max_polls 소진→fail, email 0행(N3 미승인). 재개 시도됨(resumed).
        """
        task_id = _make_running_orphan(db_path, run_id="rid-rev", upto="n1")
        worker = Worker(db_path=db_path, poll_interval=0.0)
        out = worker.recover_running_orphans(engine=_mock_engine(db_path), max_polls=2)

        assert task_id in out["resumed"]
        assert business_db.count_emails() == 0  # N3 미승인 — 비가역 미실행
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "failed"

    def test_irreversible_next_orphan_failed_and_notified(
        self, db_path: Path, business_db: Any
    ) -> None:
        """N2.5까지 끝난 고아(다음=N3 비가역) → 2a 재개 안 함 → fail+NodeFailed(I-2a-2)."""
        task_id = _make_running_orphan(db_path, run_id="rid-irr", upto="n2_5")
        worker = Worker(db_path=db_path, poll_interval=0.0)
        out = worker.recover_running_orphans(engine=_mock_engine(db_path), max_polls=2)

        assert task_id in out["failed"]
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "failed"
        assert business_db.count_emails() == 0
        types = [
            json.loads(pj).get("type")
            for _, pj in EventStore(db_path=db_path).read_since(0)
        ]
        assert "NODE_FAILED" in types  # escalation Notify

    def test_all_complete_orphan_marked_done_email_once(
        self, db_path: Path, business_db: Any
    ) -> None:
        """★멱등(I-2a-1·핵심): email 이미 1행인 완료 고아 → 같은 run_id 재개 → done,
        email 정확히 1행(B1+B2 — 이중발송 0). 재실행 없이 complete 단축.
        """
        task_id = _make_running_orphan(db_path, run_id="rid-idem", upto="all")
        assert business_db.count_emails() == 1  # plan_mode로 N3 자동승인 → 1행

        worker = Worker(db_path=db_path, poll_interval=0.0)
        out = worker.recover_running_orphans(engine=_mock_engine(db_path), max_polls=5)

        assert task_id in out["resumed"]
        assert TaskQueue(db_path=db_path).get(task_id)["status"] == "done"
        # ★email 정확히 1행 유지(재개가 두 번 안 보냄).
        assert business_db.count_emails() == 1

    def test_unidentified_run_id_falls_back_to_2b(
        self, db_path: Path, business_db: Any
    ) -> None:
        """run_id 미기록 running 고아 → 식별 실패 → 2b 폴백(fail+Notify · AC-P6B-1c)."""
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.claim()  # running 고아(run_id payload 부재)

        worker = Worker(db_path=db_path, poll_interval=0.0)
        out = worker.recover_running_orphans(engine=_mock_engine(db_path), max_polls=2)

        assert task_id in out["failed"]
        assert queue.get(task_id)["status"] == "failed"
        assert business_db.count_emails() == 0

    def test_multiple_orphans_recovered_sequentially(
        self, db_path: Path, business_db: Any
    ) -> None:
        """다중 running 고아 순차 복구(AC-P6B-1e · 동시성1 단일 루프).

        고아 2건(가역 N1 + 비가역 N2.5) → 하나는 resumed, 하나는 failed. 순차 처리.
        """
        t_rev = _make_running_orphan(db_path, run_id="rid-m-rev", upto="n1")
        t_irr = _make_running_orphan(db_path, run_id="rid-m-irr", upto="n2_5")

        worker = Worker(db_path=db_path, poll_interval=0.0)
        out = worker.recover_running_orphans(engine=_mock_engine(db_path), max_polls=2)

        assert t_rev in out["resumed"]
        assert t_irr in out["failed"]
        # 둘 다 종료 상태(running 잔존 0).
        q = TaskQueue(db_path=db_path)
        assert q.get(t_rev)["status"] == "failed"  # N3 미승인
        assert q.get(t_irr)["status"] == "failed"


# ── US-P6B-1: 모듈 2b 폴백 함수 보존(기존 호출처 호환) ─────────────────────────


class TestModule2bFallbackPreserved:
    """모듈 recover_running_orphans(db_path)는 2b 전건 fail+Notify로 보존(호환)."""

    def test_module_fn_still_fails_running_orphan(
        self, db_path: Path, business_db: Any
    ) -> None:
        """엔진 없는 모듈 함수는 2b(전건 fail+Notify) — 기존 시그니처·동작 보존."""
        from office.worker import recover_running_orphans

        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(WORKFLOW_NAME, payload={})
        queue.claim()
        failed = recover_running_orphans(db_path)
        assert task_id in failed
        assert queue.get(task_id)["status"] == "failed"


# ── US-P6B-4 NIT-1: WAL 동시(병렬) write 직렬화 ───────────────────────────────


class TestWalConcurrentWrite:
    """2-스레드 동시 write가 busy_timeout(5s) 내 둘 다 성공(AC-P6B-4a · U-nit1)."""

    def test_concurrent_event_append_and_interrupt_resolved_serialize(
        self, db_path: Path
    ) -> None:
        """스레드1=워커 EventStore.append / 스레드2=inbox INTERRUPT_RESOLVED write가
        SQLITE_BUSY 재시도를 busy_timeout 내 흡수해 둘 다 성공한다.

        ★스레드는 join(timeout=15s) 바운드 + 예외 수집(hang 0·실패 가시화).
        """
        # 선행: suspend된 run을 만들어 _record_decision이 쓸 INTERRUPT_REQUESTED를 둔다.
        store = EventStore(db_path=db_path)
        queue = TaskQueue(db_path=db_path)
        llm = run_module._FakeSmokeLLM(
            ["crm_get_customer", "order_get_latest", "chat", "email_send"]
        )
        engine = WorkflowEngine(
            executor=NodeExecutor(llm_client=llm, event_store=store),
            event_store=store,
            tools=REAL_TOOLS,
            gating_policy=GatingPolicy(),
        )
        task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
        queue.claim()
        state = engine.run(nodes=list(NODES), workflow=WORKFLOW_NAME, task_id=task_id)
        run_id = state["run_id"]
        run_module._persist_suspended_state(task_id, state, db_path)
        queue.suspend(task_id)
        iid = store.pending_interrupts(run_id)[0]["seq"]

        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def writer_events() -> None:
            try:
                barrier.wait(timeout=10)
                s = EventStore(db_path=db_path)
                for i in range(20):
                    s.append(
                        ToolCallResult(
                            run_id=run_id,
                            ts=f"t{i}",
                            node_id=f"probe{i}",
                            name="probe",
                            result={"i": i},
                        )
                    )
            except BaseException as exc:
                errors.append(exc)

        def writer_decision() -> None:
            try:
                barrier.wait(timeout=10)
                # INTERRUPT_RESOLVED write(inbox.py:155 경로 등가 — resume=False).
                _record_decision(iid, "approve", db_path=db_path, resume=False)
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=writer_events)
        t2 = threading.Thread(target=writer_decision)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        assert not t1.is_alive() and not t2.is_alive(), "스레드 hang(15s 내 미종료)"
        assert errors == [], f"동시 write 직렬화 실패(SQLITE_BUSY 미흡수): {errors}"

        # 두 write 모두 반영: probe 이벤트 20건 + INTERRUPT_RESOLVED 1건.
        types = [json.loads(pj).get("type") for _, pj in store.read_since(0)]
        assert types.count("TOOL_CALL_RESULT") >= 20
        assert "INTERRUPT_RESOLVED" in types


# ── US-P6B-4 NIT-2: starvation은 W-2 설계 + cancel이 유일 탈출 ─────────────────


class TestStarvationCancelEscape:
    """suspended 고아 블로킹 보유 = 의도된 W-2 starvation, cancel이 유일 탈출(AC-P6B-4b)."""

    def test_cancel_is_sole_escape_from_suspended_hold(
        self, db_path: Path, business_db: Any
    ) -> None:
        """미결정 suspended 고아 보유 중 cancel DB 신호 → 폴링 탈출 → 신규 queued 진행.

        ★cancel이 유일 탈출임을 명시: cancel 없이 결정도 없으면 max_polls로만 끝난다
        (의도된 W-2 starvation). 여기선 cancel을 set해 polling 첫 폴 탈출을 단언한다.
        """
        # suspended 고아 생성(승인 결정 없음 — 영구 대기 후보).
        store = EventStore(db_path=db_path)
        queue = TaskQueue(db_path=db_path)
        llm = run_module._FakeSmokeLLM(
            ["crm_get_customer", "order_get_latest", "chat", "email_send"]
        )
        engine = WorkflowEngine(
            executor=NodeExecutor(llm_client=llm, event_store=store),
            event_store=store,
            tools=REAL_TOOLS,
            gating_policy=GatingPolicy(),
        )
        task_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})
        queue.claim()
        state = engine.run(nodes=list(NODES), workflow=WORKFLOW_NAME, task_id=task_id)
        run_module._persist_suspended_state(task_id, state, db_path)
        queue.suspend(task_id)

        # 신규 queued task(굶주림 관측 대상).
        new_id = queue.enqueue(WORKFLOW_NAME, payload={"mock": True})

        # cancel 신호 set → suspended 보유 폴링이 첫 폴에 탈출(cancel-escapable).
        queue.request_cancel(task_id)

        worker = Worker(db_path=db_path, poll_interval=0.0)
        # max_polls 충분(200)해도 cancel이 즉시 탈출시키므로 hang 0.
        worker.run(max_ticks=2, engine=_mock_engine(db_path), max_polls=200)

        # suspended 고아는 cancel로 탈출→fail. email 0행(미승인·취소).
        assert queue.get(task_id)["status"] == "failed"
        assert business_db.count_emails() == 0
        # cancel 탈출 후 신규 queued가 claim·진행됨(굶주림 해소).
        assert queue.get(new_id)["status"] in ("done", "failed", "suspended")
