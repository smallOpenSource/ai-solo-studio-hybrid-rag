"""P1 동시성 step4 — 다중 슬롯 동시성 통합/부하 테스트(스레드 기반·바운드).

★안전(시스템다운 전례 2회): 실 subprocess·실 LLM·무한루프 0. 스레드 N개가 공유 tmp DB
(WAL+busy_timeout 5s)에 동시 claim/orphan-claim/event-write를 가해 SQLite 원자성·보유구간
배타성·db-locked 0을 실증한다. 각 스레드는 유한 루프(claim None이면 종료)라 바운드.

스레드 vs 프로세스: claim 배타성의 메커니즘은 SQLite WAL writer 직렬화 + UPDATE-RETURNING
원자성이라 스레드/프로세스 무관하게 동일하게 성립한다(각 메서드가 get_connection으로 독립
연결). 멀티프로세스 supervisor 라이프사이클은 test_supervisor.py(fake spawn)가, 실
end-to-end c=2 처리량/H-13 qdrant 동시검색은 step5 라이브가 담당(범위 분리). 여기선
'동시 DB 접근 정확성'을 본다.

AC 매핑(상세 .omc/research/p1-step4-ac-coverage.md):
- AC1   동시 claim 이중 0      → test_concurrent_claim_exactly_once
- AC2a/b 고아 이중집음0·보유구간 → test_concurrent_orphan_claim_exactly_once
- AC2c  소유좀비 청소→재점유    → test_supervisor_cleanup_then_worker_reclaims
- AC2d  payload 무클로버(경합)  → test_concurrent_claim_suspended_preserves_payload
- AC8   WAL db-locked 0        → test_concurrent_writers_and_readers_no_db_locked (+ 위 전부 errors==[])
"""

from __future__ import annotations

import sqlite3
import threading

from office.supervisor import WorkerSupervisor
from office.task_queue import TaskQueue


def _run_threads(fn, n_threads: int) -> None:
    """n_threads개를 Barrier로 동시 출발시켜 실행·합류한다(최대 경합)."""
    barrier = threading.Barrier(n_threads)
    threads = [threading.Thread(target=fn, args=(i, barrier)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ── AC1: 동시 claim 이중 0 ────────────────────────────────────────────────────


def test_concurrent_claim_exactly_once(tmp_path):
    """N개 queued를 8스레드가 동시 claim — 각 task 정확히 1회·총 N·db-locked 0."""
    db = tmp_path / "race.db"
    q = TaskQueue(db)
    n_tasks = 60
    for _ in range(n_tasks):
        q.enqueue("wf")
    claimed: list[int] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(slot: int, barrier: threading.Barrier) -> None:
        local: list[int] = []
        barrier.wait()  # 동시 출발(최대 경합)
        try:
            while True:
                t = q.claim(slot=slot)
                if t is None:
                    break
                local.append(t["id"])
        except sqlite3.OperationalError as e:  # database is locked 등
            with lock:
                errors.append(str(e))
        with lock:
            claimed.extend(local)

    _run_threads(worker, 8)
    assert errors == []  # AC8: db-locked 0(WAL+busy_timeout 5s)
    assert len(claimed) == n_tasks  # 모든 task claim됨(유실 0)
    assert len(set(claimed)) == n_tasks  # ★이중 claim 0(정확히 1회)


# ── AC2a/2b: 고아 이중집음 0·보유구간 배타성 ─────────────────────────────────


def test_concurrent_orphan_claim_exactly_once(tmp_path):
    """K개 suspended 고아를 8슬롯이 동시 claim_suspended — 각 1슬롯·이중집음 0·각인 일관."""
    db = tmp_path / "orphan.db"
    q = TaskQueue(db)
    n_orphans = 40
    for _ in range(n_orphans):
        tid = q.enqueue("wf")
        q.claim()  # → running (claimed_by_slot NULL)
        q.suspend(tid)  # → suspended (NULL)
    grabbed: list[tuple[int, int]] = []
    errors: list[str] = []
    lock = threading.Lock()

    def slot_worker(slot: int, barrier: threading.Barrier) -> None:
        local: list[tuple[int, int]] = []
        barrier.wait()
        try:
            while True:
                t = q.claim_suspended(slot)
                if t is None:
                    break
                local.append((t["id"], slot))
        except sqlite3.OperationalError as e:
            with lock:
                errors.append(str(e))
        with lock:
            grabbed.extend(local)

    _run_threads(slot_worker, 8)
    assert errors == []
    ids = [g[0] for g in grabbed]
    assert len(ids) == n_orphans
    assert len(set(ids)) == n_orphans  # ★각 고아 정확히 1슬롯(이중집음 0·AC2b)
    # 각 고아의 claimed_by_slot이 실제 grab한 슬롯과 일치(소유 각인 일관성)
    for tid, slot in grabbed:
        assert q.get(tid)["claimed_by_slot"] == slot


# ── AC2d: payload 무클로버(경합 하) ──────────────────────────────────────────


def test_concurrent_claim_suspended_preserves_payload(tmp_path):
    """동시 claim_suspended 중에도 payload_json·status 바이트 무변(AC2d·경합 하)."""
    db = tmp_path / "payload.db"
    q = TaskQueue(db)
    n = 30
    before: dict[int, str] = {}
    for i in range(n):
        tid = q.enqueue(
            "wf", {"run_id": f"r-{i}", "suspended_state": {"acc": [i, i + 1]}}
        )
        q.claim()
        q.suspend(tid)
        before[tid] = q.get(tid)["payload_json"]
    errors: list[str] = []
    lock = threading.Lock()

    def slot_worker(slot: int, barrier: threading.Barrier) -> None:
        barrier.wait()
        try:
            while q.claim_suspended(slot) is not None:
                pass
        except sqlite3.OperationalError as e:
            with lock:
                errors.append(str(e))

    _run_threads(slot_worker, 6)
    assert errors == []
    for tid, before_json in before.items():
        after = q.get(tid)
        assert after["payload_json"] == before_json  # ★바이트 무변(클로버 0)
        assert after["status"] == "suspended"  # status 무변


# ── AC8: WAL 동시 writer+reader db-locked 0 ──────────────────────────────────


def test_concurrent_writers_and_readers_no_db_locked(tmp_path):
    """동시 writer(enqueue/claim/heartbeat) + reader(list/get) — db-locked 0(WAL+busy_timeout)."""
    db = tmp_path / "wal.db"
    q = TaskQueue(db)
    errors: list[tuple[str, str]] = []
    lock = threading.Lock()

    def writer(slot: int, barrier: threading.Barrier) -> None:
        barrier.wait()
        try:
            for _ in range(20):
                q.enqueue("wf")
                q.claim(slot=slot)
                q.update_heartbeat()
        except sqlite3.OperationalError as e:
            with lock:
                errors.append(("writer", str(e)))

    def reader(slot: int, barrier: threading.Barrier) -> None:
        barrier.wait()
        try:
            for _ in range(40):
                q.list_by_status("running")
                q.list_by_status("queued")
                q.heartbeat_age_seconds()
        except sqlite3.OperationalError as e:
            with lock:
                errors.append(("reader", str(e)))

    barrier = threading.Barrier(8)
    threads = [threading.Thread(target=writer, args=(i, barrier)) for i in range(4)] + [
        threading.Thread(target=reader, args=(i, barrier)) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []  # ★AC8: WAL+busy_timeout으로 database-is-locked 0


# ── AC2c: 소유좀비 청소 → 워커 재점유 핸드셰이크 ─────────────────────────────


def test_supervisor_cleanup_then_worker_reclaims(tmp_path):
    """소유좀비(claimed_by_slot=i) → supervisor 청소(lock free) → claim_running 원자 재점유.

    step3 supervisor(release_all_slot_stamps)와 step2 worker(claim_running) 통합 — 죽은
    슬롯의 running 고아가 청소로 IS NULL이 되어 재기동 워커가 원자 재점유하는 전 경로.
    """
    db = tmp_path / "handshake.db"
    q = TaskQueue(db)
    # 슬롯3이 보유하던 running 고아 2건(직전 크래시 잔재)
    r1 = q.enqueue("wf")
    q.claim(slot=3)
    r2 = q.enqueue("wf")
    q.claim(slot=3)
    assert q.get(r1)["claimed_by_slot"] == 3
    assert q.get(r2)["claimed_by_slot"] == 3

    # supervisor가 죽은 슬롯3 청소(worker.lock.3 free → LOCK_NB 성공 → 각인 NULL)
    sup = WorkerSupervisor(1, db_path=db, lock_dir=tmp_path, spawn=lambda argv: None)
    assert sup._cleanup_dead_slot(3) is True
    assert q.get(r1)["claimed_by_slot"] is None  # 청소됨
    assert q.get(r2)["claimed_by_slot"] is None

    # 재기동 워커-3가 claim_running으로 원자 재점유(running 고아 복구 경로)
    got1 = q.claim_running(3)
    got2 = q.claim_running(3)
    assert {got1["id"], got2["id"]} == {r1, r2}
    assert got1["claimed_by_slot"] == 3 and got2["claimed_by_slot"] == 3
    assert q.claim_running(3) is None  # 더 없음(전부 재점유)


def test_supervisor_cleanup_blocked_preserves_live_slot(tmp_path):
    """★split-brain: 슬롯 락 보유 중(살아있는 워커)이면 청소 거부 → 각인 보존(이중실행 0)."""
    from office.worker import SingleInstanceLock

    db = tmp_path / "live.db"
    q = TaskQueue(db)
    tid = q.enqueue("wf")
    q.claim(slot=5)  # 슬롯5가 보유 중인 running
    held = SingleInstanceLock(tmp_path / "worker.lock.5")
    assert held.acquire() is True  # 살아있는 워커가 락 보유
    try:
        sup = WorkerSupervisor(
            1, db_path=db, lock_dir=tmp_path, spawn=lambda argv: None
        )
        assert sup._cleanup_dead_slot(5) is False  # 청소 거부
        assert q.get(tid)["claimed_by_slot"] == 5  # ★각인 보존(가로채기 0)
    finally:
        held.release()
