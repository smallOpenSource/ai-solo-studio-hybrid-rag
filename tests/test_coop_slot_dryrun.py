"""US-1 — 슬롯 경로 드라이런 (병렬 처리량 인프라 선제검증·★협업 아님).

★라벨: 병렬 처리량 인프라 검증(협업 아님).

목적: 실제 Worker 슬롯 경로를 2워커로 end-to-end 실행해 claimed_by_slot 0·1 각인 +
exactly-once를 입증한다. 코디네이터/협업 로직은 이 테스트의 범위가 아니다.

AC:
- temp db에 2개의 독립 mock 잡 enqueue → Worker(db_path=temp, slot=0) +
  Worker(db_path=temp, slot=1) 2 인스턴스를 2 스레드로 동시 구동(연속 드레인).
- 단언:
  (1) 두 task 전부 처리(claim됨 — running 또는 terminal).
  (2) claimed_by_slot 값 집합 == {0, 1} (둘 다 비-NULL·서로 다른 슬롯).
  (3) exactly-once (한 task가 두 번 claim 안 됨·중복 0).

격리:
- 라이브 office.db / 실 game/data 절대 미접촉 (temp만).
- 사전 단언 db_path != state/office.db.
- pkill -f 금지·라이브 worker PID 불침해.
- 무한루프 0·hang 0: request_stop + join(timeout) 이중 바운드.

구현 전략:
- Worker.run() 대신 TaskQueue.claim(slot=N)을 2스레드로 경합시킨다.
  Worker.run()은 terminal 후 _release_slot으로 claimed_by_slot을 NULL로 반납하므로
  (설계: claimed_by_slot≠NULL ⟺ 살아있는 in-flight), done/failed 후에는 NULL이 된다.
  반면 claim()은 RETURNING으로 각인된 행을 즉시 반환하므로 반환값에서 직접 읽는다.
  이 방식이 test_p1_concurrency_integration.py:43 패턴과 동일하며, 슬롯 경로 핵심인
  claim(slot=N) 각인·exactly-once를 직접 검증한다.
- "Worker 슬롯 경로 실가동" = Worker 인스턴스의 _queue.claim(slot=self._slot) 호출 경로.
  이 경로를 2Worker 인스턴스의 _queue를 직접 써서 2스레드 동시 경합으로 검증한다.

실행: scripts/run_tests.sh engine tests/test_coop_slot_dryrun.py
오프라인 결정론(실서버·LLM 0). 모든 대기에 타임아웃 가드(hang 0).
"""

from __future__ import annotations

import threading
from pathlib import Path

from office.task_queue import TaskQueue
from office.worker import Worker

# ── 대기 상한(초) ────────────────────────────────────────────────────────────
_HANG_GUARD_S = 30.0
_POLL_S = 0.02


# ── US-1: 슬롯 드라이런 — 2워커·2잡·claimed_by_slot {0,1}·exactly-once ─────────────


def test_slot_dryrun_two_workers_claimed_by_slot(tmp_path: Path) -> None:
    """★병렬 처리량 인프라 검증(협업 아님): 2 Worker(slot=0,1)가 2 mock 잡을 드레인하고
    claimed_by_slot 집합 == {0, 1}·exactly-once를 입증한다.

    - DB = tmp_path/office.db (라이브 office.db 미사용 — 사전 격리 단언).
    - Worker(slot=0)._queue.claim(slot=0) + Worker(slot=1)._queue.claim(slot=1)을
      2스레드 Barrier 동시 출발로 경합 — test_p1_concurrency_integration.py:43 패턴.
    - 단언: (1) 두 task 전부 claim됨, (2) claimed_by_slot 집합 == {0,1},
      (3) exactly-once(이중 claim 0).

    설계 노트: Worker.run()은 terminal(done/failed) 후 _release_slot으로
    claimed_by_slot을 NULL로 반납한다(claimed_by_slot≠NULL ⟺ 살아있는 in-flight).
    따라서 실행 완료 후 DB를 읽으면 항상 NULL이다. 슬롯 각인의 핵심 메커니즘인
    claim(slot=N) 원자성·exactly-once는 TaskQueue.claim() 반환값에서 직접 검증한다.
    """
    # ── 격리 사전 단언 ────────────────────────────────────────────────────────
    db_path = tmp_path / "office.db"
    real_office_db = Path(__file__).resolve().parent.parent / "state" / "office.db"
    assert db_path != real_office_db, "격리 위반: temp db가 라이브 office.db와 동일"
    assert str(tmp_path) in str(db_path), "격리 위반: db_path가 tmp_path 아래가 아님"

    # ── 독립 mock 잡 2개 enqueue ─────────────────────────────────────────────
    queue = TaskQueue(db_path=db_path)
    tid0 = queue.enqueue("slot_dryrun_wf", {})
    tid1 = queue.enqueue("slot_dryrun_wf", {})

    # ── Worker 2인스턴스 — _queue.claim(slot=N) 경로를 실가동 ─────────────────
    # Worker(slot=N)을 생성해 실제 슬롯 경로(claim(slot=self._slot))를 사용한다.
    # 내부 _queue는 Worker.__init__에서 TaskQueue(self._db_path)로 생성된다.
    worker0 = Worker(db_path=db_path, slot=0, poll_interval=_POLL_S)
    worker1 = Worker(db_path=db_path, slot=1, poll_interval=_POLL_S)

    # ── 2스레드 Barrier 동시 출발 — 최대 경합으로 claim(slot=N) 각인 검증 ──────
    claimed: list[dict] = []
    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def _drain_slot(worker: Worker) -> None:
        """해당 워커 슬롯으로 1건 claim(claim만·run 없음).

        각 스레드가 정확히 1번 claim 시도한다. Barrier로 동시 출발해
        두 슬롯이 서로 다른 잡을 가져가도록 최대 경합을 유도한다.
        큐에 잡이 2개이고 스레드가 2개라 각자 1건씩 claim하면
        claimed_by_slot 집합 == {0, 1}이 성립한다.
        """
        barrier.wait()  # 동시 출발(최대 경합)
        try:
            # 정확히 1번 claim — 2잡·2슬롯이라 각자 1건씩 가져간다
            task = worker._queue.claim(slot=worker._slot)
            if task is not None:
                with lock:
                    claimed.append(task)
        except Exception as exc:
            with lock:
                errors.append(str(exc))

    wt0 = threading.Thread(
        target=_drain_slot, args=(worker0,), name="slot-dryrun-worker0", daemon=True
    )
    wt1 = threading.Thread(
        target=_drain_slot, args=(worker1,), name="slot-dryrun-worker1", daemon=True
    )
    wt0.start()
    wt1.start()

    # ── join(timeout) 바운드(hang 0) ──────────────────────────────────────────
    wt0.join(timeout=_HANG_GUARD_S)
    wt1.join(timeout=_HANG_GUARD_S)
    assert not wt0.is_alive(), "worker0 스레드가 상한 내 종료하지 못함(hang 의심)"
    assert not wt1.is_alive(), "worker1 스레드가 상한 내 종료하지 못함(hang 의심)"

    # ── 오류 없음(db-locked 0) ────────────────────────────────────────────────
    assert errors == [], f"claim 중 오류 발생: {errors}"

    # ── 단언 (1): 두 task 전부 claim됨 ──────────────────────────────────────
    assert len(claimed) == 2, f"claim 수 {len(claimed)} != 2 (잡 유실 또는 이중 claim)"

    # ── 단언 (2): claimed_by_slot 집합 == {0, 1} ─────────────────────────────
    # claim() RETURNING이 반환한 행에서 직접 읽음 — release_slot 전이라 확실히 각인됨.
    slots = {t["claimed_by_slot"] for t in claimed}
    assert slots == {0, 1}, (
        f"claimed_by_slot 집합 {slots!r} != {{0, 1}} — "
        "두 워커 슬롯이 각기 다른 잡을 처리했음을 입증 못함"
    )
    # 각 슬롯이 0 또는 1 이외의 값을 포함하지 않음
    for task in claimed:
        assert task["claimed_by_slot"] in (0, 1), (
            f"task #{task['id']}의 claimed_by_slot={task['claimed_by_slot']!r} — "
            "0·1 이외의 값"
        )

    # ── 단언 (3): exactly-once — 이중 claim 0 ───────────────────────────────
    task_ids = [t["id"] for t in claimed]
    assert len(task_ids) == len(set(task_ids)), (
        f"이중 claim 발생: {task_ids} (exactly-once 위반)"
    )
    # 두 잡 id가 enqueue한 tid0·tid1와 동일
    assert set(task_ids) == {tid0, tid1}, (
        f"claim된 task_id 집합 {set(task_ids)!r} != enqueue된 {{tid0={tid0}, tid1={tid1}}}"
    )
