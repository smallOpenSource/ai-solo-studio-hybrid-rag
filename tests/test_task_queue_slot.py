"""P1 동시성 step1 단위테스트 — claimed_by_slot 소유 + 원자 고아 클레임.

대상(비동결): office/task_queue.py 의 step1 additive 프리미티브.
- claimed_by_slot 컬럼 마이그레이션(additive·기존행 NULL·behavior-preserving)
- claim(slot) 슬롯 각인 / 기본 None=무변
- claim_suspended/claim_running 원자성(보유구간 배타성·IS NULL 필터)
- ★AC2d: claim_* 후 payload_json·status 바이트 무변(2a 재개 run_id/suspended_state 보존)
- release_slot 해제(멱등)

설계·대안평가: .omc/plans/ralplan-p1-concurrency-prd.md
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from office.task_queue import TaskQueue


@pytest.fixture
def queue(tmp_path):
    """임시 DB 작업큐(프로덕션 office.db 무접촉)."""
    return TaskQueue(db_path=tmp_path / "tq_slot.db")


# ── 마이그레이션 (additive·behavior-preserving) ──────────────────────────────


def test_fresh_db_has_claimed_by_slot_column(queue, tmp_path):
    """신규 DB의 tasks 테이블에 claimed_by_slot 컬럼이 존재한다."""
    tid = queue.enqueue("wf")
    task = queue.get(tid)
    assert "claimed_by_slot" in task
    assert task["claimed_by_slot"] is None  # 기본 미점유


def test_migration_adds_column_to_existing_db(tmp_path):
    """claimed_by_slot 없는 기존 DB도 TaskQueue init 시 1회 ALTER로 무파손 마이그레이션."""
    db = tmp_path / "legacy.db"
    # claimed_by_slot 없는 구 스키마 + 기존 행 1건을 수기 생성
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow TEXT NOT NULL,"
        " payload_json TEXT NOT NULL DEFAULT '{}',"
        " status TEXT NOT NULL DEFAULT 'queued'"
        " CHECK(status IN ('queued','running','suspended','done','failed')),"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
        " cancel_requested INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO tasks (workflow, payload_json, status, created_at, updated_at)"
        " VALUES ('legacy', '{\"k\":1}', 'queued', 't', 't')"
    )
    conn.commit()
    conn.close()

    q = TaskQueue(db_path=db)  # 마이그레이션 발동
    row = q.get(1)
    assert "claimed_by_slot" in row
    assert row["claimed_by_slot"] is None  # 기존 행은 NULL
    assert json.loads(row["payload_json"]) == {"k": 1}  # 기존 데이터 무파손
    assert row["workflow"] == "legacy" and row["status"] == "queued"


# ── claim(slot) 각인 / 기본 무변 ─────────────────────────────────────────────


def test_claim_default_slot_is_null_behavior_preserving(queue):
    """claim() 기본(slot 미지정)은 claimed_by_slot=NULL — 단일워커 기존 동작 동일."""
    queue.enqueue("wf")
    claimed = queue.claim()
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["claimed_by_slot"] is None


def test_claim_with_slot_stamps_ownership(queue):
    """claim(slot=3)은 claimed_by_slot=3을 각인한다(running 소유 식별)."""
    queue.enqueue("wf")
    claimed = queue.claim(slot=3)
    assert claimed["claimed_by_slot"] == 3
    assert queue.get(claimed["id"])["claimed_by_slot"] == 3


# ── claim_suspended 원자성 + 보유구간 배타성 ─────────────────────────────────


def _make_suspended(queue, payload=None):
    tid = queue.enqueue("wf", payload or {})
    queue.claim()
    queue.suspend(tid)
    return tid


def test_claim_suspended_returns_and_stamps(queue):
    tid = _make_suspended(queue)
    got = queue.claim_suspended(0)
    assert got is not None and got["id"] == tid
    assert got["claimed_by_slot"] == 0
    assert got["status"] == "suspended"  # status 무변


def test_claim_suspended_second_call_gets_none(queue):
    """이미 점유(claimed_by_slot set)된 suspended는 다른 슬롯이 못 집는다(배타성)."""
    _make_suspended(queue)
    first = queue.claim_suspended(0)
    assert first is not None
    second = queue.claim_suspended(1)
    assert second is None  # IS NULL 필터로 재집음 0


def test_claim_suspended_two_tasks_two_slots(queue):
    """suspended 2건 → 서로 다른 슬롯이 각각 1건씩(이중 점유 0)."""
    t1 = _make_suspended(queue)
    t2 = _make_suspended(queue)
    a = queue.claim_suspended(0)
    b = queue.claim_suspended(1)
    assert {a["id"], b["id"]} == {t1, t2}
    assert a["id"] != b["id"]
    third = queue.claim_suspended(2)
    assert third is None


# ── ★AC2d: payload_json·status 바이트 무변 ───────────────────────────────────


def test_claim_suspended_preserves_payload_and_status_bytewise(queue):
    """claim_suspended는 claimed_by_slot만 UPDATE — payload_json·status 무접촉.

    2a 재개의 run_id·suspended_state가 payload에 박제돼 있으므로 클로버 금지.
    """
    tid = _make_suspended(
        queue, {"run_id": "r-keep", "suspended_state": {"node": "n2", "acc": [1, 2]}}
    )
    before = queue.get(tid)
    queue.claim_suspended(7)
    after = queue.get(tid)
    assert after["payload_json"] == before["payload_json"]  # 바이트 동일
    assert after["status"] == before["status"] == "suspended"
    assert json.loads(after["payload_json"])["run_id"] == "r-keep"


# ── claim_running ────────────────────────────────────────────────────────────


def test_claim_running_atomic_and_exclusive(queue):
    queue.enqueue("wf")
    queue.claim()  # → running (claimed_by_slot NULL)
    got = queue.claim_running(0)
    assert (
        got is not None and got["claimed_by_slot"] == 0 and got["status"] == "running"
    )
    assert queue.claim_running(1) is None  # 이미 점유


def test_claim_running_skips_live_owned(queue):
    """claim(slot=5)로 이미 슬롯 소유된 running은 claim_running이 안 집는다(라이브 보호)."""
    queue.enqueue("wf")
    queue.claim(slot=5)  # 라이브 워커가 슬롯5로 점유한 running
    assert queue.claim_running(9) is None  # IS NULL 아님 → 고아로 오인 안 함


# ── release_slot ─────────────────────────────────────────────────────────────


def test_release_slot_clears_and_is_idempotent(queue):
    tid = _make_suspended(queue)
    queue.claim_suspended(0)
    assert queue.get(tid)["claimed_by_slot"] == 0
    queue.release_slot(tid)
    assert queue.get(tid)["claimed_by_slot"] is None
    queue.release_slot(tid)  # 멱등(이미 NULL)
    assert queue.get(tid)["claimed_by_slot"] is None
    # 해제 후 다시 점유 가능
    assert queue.claim_suspended(1) is not None


def test_release_slot_ownership_guard(queue):
    """release_slot(slot) 소유가드: 다른 슬롯이 재점유한 각인은 보존(리뷰 #5).

    워커A(slot0)가 자기 slot으로 해제 시도하나 task를 슬롯9가 보유 중이면
    A의 해제는 no-op → 9의 소유 보존(이중실행 차단). slot 미지정은 무조건 해제.
    """
    tid = _make_suspended(queue)
    queue.claim_suspended(9)  # 슬롯9가 점유
    queue.release_slot(tid, slot=0)  # 비소유 슬롯0의 가드 해제 → no-op
    assert queue.get(tid)["claimed_by_slot"] == 9  # 9 소유 보존
    queue.release_slot(tid, slot=9)  # 소유자 해제 → 성공
    assert queue.get(tid)["claimed_by_slot"] is None


# ── 빈 큐 안전 ───────────────────────────────────────────────────────────────


def test_claim_orphan_empty_returns_none(queue):
    assert queue.claim_suspended(0) is None
    assert queue.claim_running(0) is None
