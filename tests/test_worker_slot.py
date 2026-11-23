"""P1 동시성 step2 단위테스트 — 워커 per-slot 락 + 고아복구 dual-path + release 배선.

대상(비동결): office/worker.py 의 step2 변경.
- _slot_lock_path / _slot_pid_path: slot=None→기존 경로, slot=N→.{N} 분리
- SingleInstanceLock: 슬롯별 독립 획득 + FD_CLOEXEC(split-brain 봉합)
- Worker.run: claim(slot=self._slot) 각인 + terminal 후 release_slot 반납
- 고아복구 dual-path:
  · slot=None → 기존 list_by_status 스냅샷(behavior-preserving)
  · slot=int  → 원자 claim_suspended/claim_running(IS NULL 필터·라이브 보호) + release

per-task 복구 내부(_resume_one_suspended·_recover_one_running_2a)는 step2 무변이라
monkeypatch로 스텁(루프 배선만 검증) — 그 내부는 test_worker.py가 별도 커버.

설계·대안평가: .omc/plans/ralplan-p1-concurrency-prd.md
"""

from __future__ import annotations

import fcntl

import pytest

from office.task_queue import TaskQueue
from office.worker import (
    _DEFAULT_LOCK_PATH,
    _DEFAULT_PID_PATH,
    _slot_lock_path,
    _slot_pid_path,
    SingleInstanceLock,
    Worker,
)


# ── 슬롯 경로 파생 ────────────────────────────────────────────────────────────


def test_slot_lock_path_none_is_backward_compat():
    """slot=None이면 기존 state/worker.lock 그대로(단일워커 동치·롤백 경로)."""
    assert _slot_lock_path(None) == _DEFAULT_LOCK_PATH
    assert _slot_pid_path(None) == _DEFAULT_PID_PATH


def test_slot_lock_path_int_is_separated():
    """slot=N이면 worker.lock.{N}·worker.pid.{N}로 분리(슬롯별 독립)."""
    assert _slot_lock_path(0).name == "worker.lock.0"
    assert _slot_lock_path(2).name == "worker.lock.2"
    assert _slot_pid_path(1).name == "worker.pid.1"
    # 부모 디렉터리는 동일(state/) — 파일명만 슬롯 접미사
    assert _slot_lock_path(2).parent == _DEFAULT_LOCK_PATH.parent


# ── per-slot advisory 락 ─────────────────────────────────────────────────────


def test_per_slot_locks_are_independent(tmp_path):
    """서로 다른 슬롯 락은 동시에 획득 가능(N개 워커 공존)."""
    l0 = SingleInstanceLock(tmp_path / "worker.lock.0")
    l1 = SingleInstanceLock(tmp_path / "worker.lock.1")
    try:
        assert l0.acquire() is True
        assert l1.acquire() is True  # 다른 슬롯 — 독립 획득
    finally:
        l0.release()
        l1.release()


def test_same_slot_lock_second_acquire_fails(tmp_path):
    """같은 슬롯 락을 두 번째로 획득하면 실패(슬롯별 단일 인스턴스 보존)."""
    first = SingleInstanceLock(tmp_path / "worker.lock.0")
    dup = SingleInstanceLock(tmp_path / "worker.lock.0")
    try:
        assert first.acquire() is True
        assert dup.acquire() is False  # 이미 보유 — 즉시 실패(non-blocking)
    finally:
        first.release()
        dup.release()


def test_lock_fd_has_cloexec(tmp_path):
    """획득한 락 fd에 FD_CLOEXEC가 설정된다(supervisor fork→exec 미상속·split-brain 봉합)."""
    lk = SingleInstanceLock(tmp_path / "worker.lock.0")
    try:
        assert lk.acquire() is True
        flags = fcntl.fcntl(lk._fp.fileno(), fcntl.F_GETFD)
        assert flags & fcntl.FD_CLOEXEC  # close-on-exec 강제됨
    finally:
        lk.release()


# ── run(): claim 슬롯 각인 + terminal 후 release ─────────────────────────────


def _enqueue_queued(db_path) -> tuple[TaskQueue, int]:
    q = TaskQueue(db_path)
    tid = q.enqueue("crm_notify")
    return q, tid


def test_run_pooled_claims_with_slot_and_releases(tmp_path, monkeypatch):
    """풀(slot=4): run이 claim(slot=4)로 각인하고 terminal 후 release_slot로 반납한다."""
    db = tmp_path / "w.db"
    q, tid = _enqueue_queued(db)
    w = Worker(db_path=db, poll_interval=0.0, slot=4)

    seen: dict[str, object] = {}

    def fake_run_one(task, engine, max_polls):
        # 실행 중에는 claim(slot=4) 각인이 찍혀 있어야 한다.
        seen["slot_during"] = q.get(task["id"])["claimed_by_slot"]
        q.complete(task["id"])  # terminal

    monkeypatch.setattr(w, "_run_one", fake_run_one)
    w.run(max_ticks=1)

    assert seen["slot_during"] == 4  # claim(slot=4) 각인 확인
    row = q.get(tid)
    assert row["status"] == "done"
    assert row["claimed_by_slot"] is None  # 종료 후 release_slot 반납(소유가드)


def test_run_slot_zero_takes_pooled_path_and_stamps_zero(tmp_path, monkeypatch):
    """★회귀가드(리뷰 Nit-2a): slot=0은 falsy지만 `is None`이 False라 풀 경로로 간다.

    claim(slot=0)이 NULL이 아닌 0을 각인하고, terminal 후 release_slot(slot=0) 소유가드로
    반납한다. 누군가 `if self._slot is None`을 `if not self._slot`으로 오작성하면(0을
    단일워커로 오인) 이 테스트가 깨진다.
    """
    db = tmp_path / "w.db"
    q, tid = _enqueue_queued(db)
    w = Worker(db_path=db, poll_interval=0.0, slot=0)

    seen: dict[str, object] = {}

    def fake_run_one(task, engine, max_polls):
        seen["slot_during"] = q.get(task["id"])["claimed_by_slot"]
        q.complete(task["id"])

    monkeypatch.setattr(w, "_run_one", fake_run_one)
    w.run(max_ticks=1)

    assert seen["slot_during"] == 0  # NULL 아님 — 슬롯0 각인(풀 경로 확증)
    assert q.get(tid)["claimed_by_slot"] is None  # terminal 후 release(소유가드 slot=0)


def test_run_single_stamps_null_behavior_preserving(tmp_path, monkeypatch):
    """단일워커(slot=None): claim 각인이 NULL — 기존 경로 100% 보존."""
    db = tmp_path / "w.db"
    q, tid = _enqueue_queued(db)
    w = Worker(db_path=db, poll_interval=0.0)  # slot=None

    seen: dict[str, object] = {}

    def fake_run_one(task, engine, max_polls):
        seen["slot_during"] = q.get(task["id"])["claimed_by_slot"]
        q.complete(task["id"])

    monkeypatch.setattr(w, "_run_one", fake_run_one)
    w.run(max_ticks=1)

    assert seen["slot_during"] is None  # NULL 각인(behavior-preserving)
    assert q.get(tid)["status"] == "done"


# ── 고아복구 dual-path: suspended ────────────────────────────────────────────


def _make_suspended(q: TaskQueue) -> int:
    """suspended task 1건 생성(claimed_by_slot NULL).

    ★claim()은 '가장 오래된 queued'를 집는다 — 직전 enqueue분이 유일 queued라
    정확히 그 task를 running→suspend한다(앞선 task들은 이미 suspended라 queued 아님).
    """
    tid = q.enqueue("wf")
    q.claim()  # running (claimed_by_slot NULL)
    q.suspend(tid)  # suspended (NULL)
    return tid


def test_resume_suspended_orphans_pooled_atomic_and_skips_owned(tmp_path, monkeypatch):
    """풀: claim_suspended로 미점유(NULL) 고아만 1건씩 보유, 타슬롯 보유분은 건너뛴다."""
    db = tmp_path / "w.db"
    q = TaskQueue(db)
    # owned 먼저 생성 + 슬롯9 각인 — 이 시점 유일 suspended라 claim_suspended(9)가
    # 정확히 그것을 집는다(claim_suspended는 oldest-NULL을 집으므로 순서가 중요).
    owned = q.enqueue("wf")
    q.claim()
    q.suspend(owned)
    assert q.claim_suspended(9)["id"] == owned  # 슬롯9가 그 task를 보유
    free = [_make_suspended(q) for _ in range(3)]  # NULL-slot 3건

    w = Worker(db_path=db, poll_interval=0.0, slot=2)
    held: list[int] = []

    def fake_resume(task, engine, max_polls):
        held.append(task["id"])
        q.complete(task["id"])  # terminal → release 대상
        return True

    monkeypatch.setattr(w, "_resume_one_suspended", fake_resume)
    n = w._resume_suspended_orphans(engine=None, max_polls=None)

    assert n == 3
    assert set(held) == set(free)  # NULL 3건만 보유
    assert owned not in held  # 슬롯9 보유분은 IS NULL 필터로 건너뜀(AC2b)
    assert q.get(owned)["claimed_by_slot"] == 9  # 9 소유 보존(불가침)
    for t in free:
        assert q.get(t)["claimed_by_slot"] is None  # 처리 후 release 반납


def test_resume_suspended_orphans_single_uses_list_snapshot(tmp_path, monkeypatch):
    """단일워커: list_by_status로 전부 처리(stamp 무관·기존 경로)."""
    db = tmp_path / "w.db"
    q = TaskQueue(db)
    t1 = _make_suspended(q)
    w = Worker(db_path=db, poll_interval=0.0)  # slot=None
    held: list[int] = []

    def fake_resume(task, engine, max_polls):
        held.append(task["id"])
        return True

    monkeypatch.setattr(w, "_resume_one_suspended", fake_resume)
    n = w._resume_suspended_orphans(engine=None, max_polls=None)
    assert n == 1 and held == [t1]


def test_resume_suspended_pooled_no_infinite_loop_on_unresumable(tmp_path, monkeypatch):
    """재개불가(False) 고아는 각인 유지(quarantine)로 무한 재집음을 차단한다."""
    db = tmp_path / "w.db"
    q = TaskQueue(db)
    _make_suspended(q)  # NULL-slot 1건
    w = Worker(db_path=db, poll_interval=0.0, slot=1)
    calls = {"n": 0}

    def fake_resume_false(task, engine, max_polls):
        calls["n"] += 1
        return False  # suspended_state 부재 등 — 재개 불가

    monkeypatch.setattr(w, "_resume_one_suspended", fake_resume_false)
    n = w._resume_suspended_orphans(engine=None, max_polls=None)
    assert n == 0
    assert calls["n"] == 1  # 1회만 — 각인 유지로 재집음 0(무한루프 아님)


# ── 고아복구 dual-path: running ──────────────────────────────────────────────


def test_recover_running_orphans_pooled_skips_live_owned(tmp_path, monkeypatch):
    """풀: claim_running로 미점유(NULL) running 고아만, 라이브 슬롯 보유분은 불가침."""
    db = tmp_path / "w.db"
    q = TaskQueue(db)
    r1 = q.enqueue("wf")
    q.claim()  # running, NULL
    r2 = q.enqueue("wf")
    q.claim()  # running, NULL
    live = q.enqueue("wf")
    q.claim(slot=7)  # 라이브 슬롯7이 보유 중인 running(각인)

    w = Worker(db_path=db, poll_interval=0.0, slot=3)
    recovered: list[int] = []

    def fake_2a(task, engine, max_polls):
        recovered.append(task["id"])
        q.fail(task["id"])  # terminal
        return "failed"

    monkeypatch.setattr(w, "_recover_one_running_2a", fake_2a)
    out = w.recover_running_orphans()

    assert set(recovered) == {r1, r2}  # NULL running 2건만 복구
    assert live not in recovered  # 슬롯7 라이브는 IS NULL 아님 → 이중실행 차단
    assert q.get(live)["claimed_by_slot"] == 7  # 라이브 소유 보존
    assert set(out["failed"]) == {r1, r2}


def test_recover_running_orphans_single_uses_list_snapshot(tmp_path, monkeypatch):
    """단일워커: list_by_status로 running 고아 처리(기존 경로·behavior-preserving)."""
    db = tmp_path / "w.db"
    q = TaskQueue(db)
    r1 = q.enqueue("wf")
    q.claim()  # running, NULL
    w = Worker(db_path=db, poll_interval=0.0)  # slot=None
    recovered: list[int] = []

    def fake_2a(task, engine, max_polls):
        recovered.append(task["id"])
        return "resumed"

    monkeypatch.setattr(w, "_recover_one_running_2a", fake_2a)
    out = w.recover_running_orphans()
    assert recovered == [r1]
    assert out["resumed"] == [r1]
