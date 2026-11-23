"""P1 동시성 step3 단위테스트 — WorkerSupervisor(멀티 OS 프로세스 감독자).

대상(신규·비동결): office/supervisor.py.
★안전 최우선(시스템다운 전례 2회): 모든 테스트는 **fake spawn(실 워커 0)** + fake
clock/sleep + max_cycles 바운드로 돈다 — 진짜 subprocess·진짜 워커를 절대 띄우지 않는다.

검증:
- start(): 정확히 N개 슬롯 워커 argv(conda python·--slot·--lock-path·--poll-interval)
- monitor(): 죽은 슬롯 감지→청소→재기동
- 죽은slot 청소: worker.lock.{i} LOCK_NB 게이팅 — 락 보유 중이면 청소·재기동 보류(split-brain)
- release_all_slot_stamps: 해당 슬롯 각인만 NULL(타슬롯 보존)
- crash-loop 상한: window 내 max회 재기동 후 포기(폭주 차단)·window 밖은 리셋
- 지수 백오프(cap)
- shutdown: 자기 자식만 SIGTERM→그레이스→SIGKILL(★pkill 없음)

설계: .omc/plans/ralplan-p1-concurrency-prd.md §step3
"""

from __future__ import annotations

import subprocess

import pytest

from office.supervisor import WorkerSupervisor
from office.task_queue import TaskQueue
from office.worker import SingleInstanceLock


# ── fake 자식 프로세스·spawn(실 subprocess 0) ────────────────────────────────


class FakeProc:
    """subprocess.Popen 모사 — poll/terminate/kill/wait. 실 프로세스 아님."""

    def __init__(self, pid: int = 0, stubborn: bool = False) -> None:
        self._pid = pid
        self._rc: int | None = None  # None=살아있음
        self.stubborn = stubborn  # terminate에도 안 죽음(SIGKILL 경로 테스트용)
        self.terminated = False
        self.killed = False

    @property
    def pid(self) -> int:
        return self._pid

    def die(self, rc: int = 1) -> None:
        self._rc = rc

    def poll(self) -> int | None:
        return self._rc

    def terminate(self) -> None:
        self.terminated = True
        if not self.stubborn:
            self._rc = 0  # graceful 종료

    def kill(self) -> None:
        self.killed = True
        self._rc = -9

    def wait(self, timeout: float | None = None) -> int:
        if self._rc is None:
            raise subprocess.TimeoutExpired(cmd="fake-worker", timeout=timeout or 0)
        return self._rc


class SpawnRecorder:
    """fake spawn — argv 기록 + 슬롯별 FakeProc 반환(실 워커 0)."""

    def __init__(self, stubborn_slots: tuple[int, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.by_slot: dict[int, FakeProc] = {}
        self.spawn_count = 0
        self._stubborn = set(stubborn_slots)

    def __call__(self, argv: list[str]) -> FakeProc:
        self.calls.append(argv)
        self.spawn_count += 1
        slot = int(argv[argv.index("--slot") + 1])
        p = FakeProc(pid=10000 + self.spawn_count, stubborn=(slot in self._stubborn))
        self.by_slot[slot] = p
        return p


class FixedClock:
    """제어 가능한 단조 시계(테스트 결정론·실 대기 0)."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_sup(tmp_path, recorder, *, concurrency=1, clock=None, sleeps=None, **kw):
    """공통 supervisor 생성 — fake spawn·fake sleep·lock_dir=tmp(실 워커/실 대기 0)."""
    clock = clock or FixedClock()
    sleeps = sleeps if sleeps is not None else []
    return WorkerSupervisor(
        concurrency,
        db_path=tmp_path / "sup.db",
        python_exe="/fake/conda/python",
        lock_dir=tmp_path,
        spawn=recorder,
        sleep=lambda s: sleeps.append(s),
        clock=clock,
        monitor_interval=0.0,
        **kw,
    )


# ── 생성·기동 ────────────────────────────────────────────────────────────────


def test_concurrency_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        WorkerSupervisor(0, db_path=tmp_path / "x.db", spawn=SpawnRecorder())


def test_start_spawns_n_workers_with_correct_argv(tmp_path):
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=2)
    sup.start()
    assert rec.spawn_count == 2
    assert set(rec.by_slot) == {0, 1}
    argv0 = rec.calls[0]
    assert argv0[0] == "/fake/conda/python"
    assert argv0[1:3] == ["-m", "office.worker"]
    assert "--slot" in argv0 and argv0[argv0.index("--slot") + 1] == "0"
    lock_arg = argv0[argv0.index("--lock-path") + 1]
    assert lock_arg.endswith("worker.lock.0")
    assert "--poll-interval" in argv0


# ── monitor: 죽음 감지 → 청소 → 재기동 ───────────────────────────────────────


def test_monitor_respawns_dead_slot_when_lock_free(tmp_path):
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=1, backoff_base=0.0)
    sup.start()
    assert rec.spawn_count == 1
    rec.by_slot[0].die(1)  # 슬롯0 자식 사망
    sup.monitor(max_cycles=1)  # 감지→청소(락 free)→재기동
    assert rec.spawn_count == 2  # 재기동됨
    assert rec.by_slot[0].poll() is None  # 새 자식 살아있음
    assert 0 not in sup._given_up


def test_monitor_no_respawn_while_alive(tmp_path):
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=2, backoff_base=0.0)
    sup.start()
    sup.monitor(max_cycles=3)  # 아무도 안 죽음
    assert rec.spawn_count == 2  # 재기동 0


# ── 죽은slot 청소: LOCK_NB 게이팅(split-brain) ───────────────────────────────


def test_dead_slot_cleanup_clears_stamps_when_lock_free(tmp_path):
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=1, backoff_base=0.0)
    q = TaskQueue(tmp_path / "sup.db")  # supervisor와 동일 db
    tid = q.enqueue("wf")
    q.claim(slot=0)  # running·claimed_by_slot=0(슬롯0 고아 시뮬레이션)
    sup.start()
    rec.by_slot[0].die(1)
    sup.monitor(max_cycles=1)  # 청소 → 재기동
    assert q.get(tid)["claimed_by_slot"] is None  # 죽은slot 각인 NULL 청소됨
    assert rec.spawn_count == 2  # 재기동


def test_dead_slot_cleanup_skipped_when_lock_held(tmp_path):
    """★split-brain 차단: worker.lock.{i} 보유 중이면 청소·재기동 둘 다 보류."""
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=1, backoff_base=0.0)
    q = TaskQueue(tmp_path / "sup.db")
    tid = q.enqueue("wf")
    q.claim(slot=0)  # 슬롯0이 보유 중인 running(살아있는 워커 시뮬레이션)
    sup.start()
    # 다른 프로세스(여기선 테스트)가 worker.lock.0을 보유 = 살아있는 워커
    held = SingleInstanceLock(tmp_path / "worker.lock.0")
    assert held.acquire() is True
    try:
        rec.by_slot[0].die(1)  # 자식 핸들은 죽었다 보고하나, 락은 누군가 보유
        sup.monitor(max_cycles=1)
        assert q.get(tid)["claimed_by_slot"] == 0  # ★각인 보존(청소 안 함)
        assert rec.spawn_count == 1  # ★재기동 보류(이중실행 0)
    finally:
        held.release()


def test_release_all_slot_stamps_targets_only_slot(tmp_path):
    q = TaskQueue(tmp_path / "q.db")
    a = q.enqueue("w")
    q.claim(slot=0)
    b = q.enqueue("w")
    q.claim(slot=0)
    c = q.enqueue("w")
    q.claim(slot=1)
    n = q.release_all_slot_stamps(0)
    assert n == 2
    assert q.get(a)["claimed_by_slot"] is None
    assert q.get(b)["claimed_by_slot"] is None
    assert q.get(c)["claimed_by_slot"] == 1  # 타슬롯 보존


# ── crash-loop 상한(폭주 차단) + window 리셋 ─────────────────────────────────


def test_crashloop_cap_gives_up(tmp_path):
    """window 내 crashloop_max회 재기동 후 추가 사망은 슬롯 포기(무한 재스폰 차단)."""
    rec = SpawnRecorder()
    clock = FixedClock()  # 고정 → 모든 사망이 동일 window 내
    sup = _make_sup(
        tmp_path,
        rec,
        concurrency=1,
        clock=clock,
        backoff_base=0.0,
        crashloop_max=3,
        crashloop_window=60.0,
    )
    sup.start()  # spawn #1
    for _ in range(3):  # 3회 재기동(spawn #2,#3,#4)
        sup._handle_death(0, 1)
    assert 0 not in sup._given_up
    assert rec.spawn_count == 4
    sup._handle_death(0, 1)  # 4번째 사망 → 포기
    assert 0 in sup._given_up
    assert rec.spawn_count == 4  # 더 이상 재기동 안 함


def test_monitor_exits_when_all_slots_given_up(tmp_path):
    """★F1(리뷰): 전 슬롯이 crash-loop 포기되면 monitor가 무한 idle 대신 즉시 탈출한다."""
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=1, backoff_base=0.0, crashloop_max=1)
    sup.start()
    sup._given_up.add(0)  # 전 슬롯(1개) 포기 상태
    cycles = sup.monitor(max_cycles=10)
    assert cycles == 1  # 첫 사이클 후 break(무한 idle 아님·silent 무작동 방지)


def test_crashloop_window_resets_when_spaced(tmp_path):
    """사망이 window보다 멀리 떨어지면 이력이 비워져 포기 안 함(정상 장기 운영)."""
    rec = SpawnRecorder()
    clock = FixedClock()
    sup = _make_sup(
        tmp_path,
        rec,
        concurrency=1,
        clock=clock,
        backoff_base=0.0,
        crashloop_max=2,
        crashloop_window=60.0,
    )
    sup.start()
    sup._handle_death(0, 1)  # t=0 재기동
    clock.advance(100.0)  # window(60) 초과
    sup._handle_death(0, 1)  # 이력 prune → 재기동(포기 안 함)
    assert 0 not in sup._given_up
    assert rec.spawn_count == 3  # start + 2 재기동


# ── 지수 백오프(cap) ─────────────────────────────────────────────────────────


def test_backoff_exponential_capped(tmp_path):
    rec = SpawnRecorder()
    sleeps: list[float] = []
    clock = FixedClock()
    sup = _make_sup(
        tmp_path,
        rec,
        concurrency=1,
        clock=clock,
        sleeps=sleeps,
        backoff_base=1.0,
        backoff_cap=4.0,
        crashloop_max=99,
        crashloop_window=600.0,
    )
    sup.start()
    for _ in range(4):
        sup._handle_death(0, 1)
    # 1·2·4·(8→cap 4) — 지수 증가 후 cap
    assert sleeps == [1.0, 2.0, 4.0, 4.0]


# ── shutdown: 자기 자식만 SIGTERM→그레이스→SIGKILL ──────────────────────────


def test_shutdown_terminates_all_children(tmp_path):
    rec = SpawnRecorder()
    sup = _make_sup(tmp_path, rec, concurrency=2)
    sup.start()
    procs = [rec.by_slot[0], rec.by_slot[1]]
    sup.shutdown(grace=5.0)
    assert all(p.terminated for p in procs)  # 전부 SIGTERM
    assert not any(p.killed for p in procs)  # graceful라 SIGKILL 불필요
    assert sup._procs == {}


def test_shutdown_kills_stubborn_child(tmp_path):
    """SIGTERM에도 안 죽는 자식은 그레이스 후 SIGKILL(자기 자식만·pkill 없음)."""
    rec = SpawnRecorder(stubborn_slots=(0,))
    clock = FixedClock()
    sup = _make_sup(tmp_path, rec, concurrency=1, clock=clock)
    sup.start()
    stubborn = rec.by_slot[0]
    sup.shutdown(grace=1.0)
    assert stubborn.terminated is True  # SIGTERM 시도
    assert stubborn.killed is True  # 그레이스 후 SIGKILL
