"""US-2 — 자동 코디네이터(BuildCoordinator) 단위테스트(fake 주입·실워커/실LLM 0).

계획 §9 BINDING 1·2 검증. 전부 fake 주입(FakeQueue·fake load_run_id·FakeEventStore·
FakeClock)으로 실워커·실LLM·실대기를 0으로 만든다. monotonic·sleep 주입으로 바운드
타임아웃 경로를 결정론적으로(실시간 경과 없이) 검증한다.

4종:
  (a) 정상 5단계   — enqueue 순서=STAGES·success→done·mock 페이로드 반영.
  (b) run_id 영구 None — T_claim 종료(다음 enqueue 0).
  (c) RUN_FINISHED 미등장 — T_run 종료.
  (d) 2단계 status=failed — 체인 중단(3단계 enqueue 0).

실행: scripts/run_tests.sh engine tests/test_coordinator.py
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from office.coordinator import (
    ERROR_CLAIM_TIMEOUT,
    ERROR_RUN_TIMEOUT,
    ERROR_STAGE_FAILED,
    STAGES,
    STATE_DONE,
    STATE_FAILED,
    BuildCoordinator,
)
from office.events import RunFinished


# ── fake 구현체 ──────────────────────────────────────────────────────────────


class FakeQueue:
    """enqueue 호출을 기록하고 증가하는 task_id를 돌려준다(실 DB 0)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id = 0

    def enqueue(self, workflow: str, payload: dict[str, Any] | None = None) -> int:
        self._next_id += 1
        self.calls.append((workflow, payload or {}))
        return self._next_id

    @property
    def workflows(self) -> list[str]:
        """enqueue된 워크플로명 순서."""
        return [wf for wf, _ in self.calls]


class FakeLoadRunId:
    """task_id별로 K번 None을 돌려준 뒤 run_id를 돌려주는 fake load_run_id.

    K=0이면 즉시 run_id(정상). K=None이면 영구 None(claim 미배정 시뮬레이션).
    호출 횟수를 task_id별로 센다.
    """

    def __init__(
        self,
        *,
        none_count: int | None = 0,
        run_id_prefix: str = "run",
    ) -> None:
        self._none_count = none_count
        self._prefix = run_id_prefix
        self._calls: dict[int, int] = {}

    def __call__(self, task_id: int) -> Optional[str]:
        n = self._calls.get(task_id, 0)
        self._calls[task_id] = n + 1
        if self._none_count is None:
            return None  # 영구 None
        if n < self._none_count:
            return None
        return f"{self._prefix}-{task_id}"


class FakeEventStore:
    """run_id별 이벤트 리스트를 보유하고, RUN_FINISHED 등장 시점을 read 호출 횟수로 제어.

    appear_after_reads: run_id별로 "read가 N번 호출된 뒤" RUN_FINISHED가 등장(폴링 시뮬).
                        0이면 즉시 등장. None이면 영구 미등장(T_run 종료 시뮬레이션).
    status_by_run:      run_id별 RUN_FINISHED.status(success/failed/rejected).
    """

    def __init__(
        self,
        *,
        appear_after_reads: dict[str, int | None] | None = None,
        status_by_run: dict[str, str] | None = None,
        default_status: str = "success",
    ) -> None:
        self._appear = appear_after_reads or {}
        self._status = status_by_run or {}
        self._default_status = default_status
        self._read_calls: dict[str, int] = {}

    def read(self, run_id: str) -> list[Any]:
        n = self._read_calls.get(run_id, 0)
        self._read_calls[run_id] = n + 1
        appear = self._appear.get(run_id, 0)  # 미지정 run_id는 즉시 등장
        if appear is None:
            return []  # 영구 미등장
        if n < appear:
            return []  # 아직 등장 전(폴 더 필요)
        status = self._status.get(run_id, self._default_status)
        return [RunFinished(run_id=run_id, ts="2026-06-17T00:00:00", status=status)]


class FakeClock:
    """주입 monotonic/sleep — sleep이 단조 카운터를 전진시켜 실대기 0으로 경과 시뮬레이션.

    monotonic()은 현재 카운터를 반환. sleep(dt)는 카운터를 dt만큼 전진(실제로 자지 않음).
    이로써 바운드 타임아웃을 결정론적으로(실시간 0) 검증한다.
    """

    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    def sleep(self, dt: float) -> None:
        self._t += dt


def _make_coordinator(
    *,
    queue: FakeQueue,
    event_store: FakeEventStore,
    load_run_id: Any,
    clock: FakeClock,
    t_claim: float = 5.0,
    t_run: float = 10.0,
    poll_interval: float = 1.0,
) -> BuildCoordinator:
    """공통 코디네이터 빌더(fake 전부 주입·db_path는 미사용 sentinel)."""
    return BuildCoordinator(
        queue=queue,
        event_store=event_store,
        db_path="<unused>",  # load_run_id 주입 시 기본 바인딩 미사용.
        load_run_id=load_run_id,
        t_claim=t_claim,
        t_run=t_run,
        poll_interval=poll_interval,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


# ── (a) 정상 5단계 ────────────────────────────────────────────────────────────


def test_happy_path_chains_all_five_stages_in_order() -> None:
    """정상: 5단계가 STAGES 순서로 enqueue되고 success→done·mock 페이로드 반영."""
    queue = FakeQueue()
    # 즉시 run_id, 즉시 RUN_FINISHED(success) — 폴 0회로 통과.
    event_store = FakeEventStore(default_status="success")
    load_run_id = FakeLoadRunId(none_count=0)
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue, event_store=event_store, load_run_id=load_run_id, clock=clock
    )

    result = coordinator.run_build("build-happy", mock=True)

    # enqueue 순서가 STAGES와 정확히 일치.
    assert queue.workflows == STAGES
    # 최종 상태 done.
    assert result["state"] == STATE_DONE
    assert result["error"] is None
    # mock 페이로드가 모든 단계에 반영.
    assert all(payload.get("mock") is True for _, payload in queue.calls)
    # 단계별 run_id가 기록됨(상태추적).
    assert set(result["run_ids"].keys()) == set(STAGES)
    # get_status로도 동일 상태 조회 가능.
    assert coordinator.get_status("build-happy") is result


def test_happy_path_passes_query_in_payload() -> None:
    """정상: query 주면 payload["query"]로 모든 단계에 실린다(웹 관례)."""
    queue = FakeQueue()
    event_store = FakeEventStore(default_status="success")
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue,
        event_store=event_store,
        load_run_id=FakeLoadRunId(none_count=0),
        clock=clock,
    )

    coordinator.run_build("build-q", mock=False, query="테마: 우주 로그라이크")

    assert all(p.get("query") == "테마: 우주 로그라이크" for _, p in queue.calls)
    assert all(p.get("mock") is False for _, p in queue.calls)


# ── (b) run_id 영구 None → T_claim 종료 ──────────────────────────────────────


def test_claim_timeout_when_run_id_never_assigned() -> None:
    """run_id 영구 None(워커 미기동) → T_claim 종료·다음 enqueue 0."""
    queue = FakeQueue()
    event_store = FakeEventStore(default_status="success")
    load_run_id = FakeLoadRunId(none_count=None)  # 영구 None
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue,
        event_store=event_store,
        load_run_id=load_run_id,
        clock=clock,
        t_claim=5.0,
        poll_interval=1.0,
    )

    result = coordinator.run_build("build-claim-to", mock=True)

    # 실패·사유 claim_timeout·1단계(game_design)에서.
    assert result["state"] == STATE_FAILED
    assert result["error"]["reason"] == ERROR_CLAIM_TIMEOUT
    assert result["error"]["stage"] == STAGES[0]
    # 첫 단계만 enqueue·다음 단계 enqueue 0(체인 중단).
    assert queue.workflows == [STAGES[0]]
    # 바운드 절대: 무한루프 0(테스트가 timeout 없이 반환됨이 곧 증거).
    # T_claim(5.0)·poll(1.0) → 폴은 유한 횟수만 돈다.


def test_claim_timeout_does_not_read_event_store() -> None:
    """run_id 미배정이면 EventStore.read는 호출되지 않는다(run_id 없이 read 불가)."""
    queue = FakeQueue()
    event_store = FakeEventStore(default_status="success")
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue,
        event_store=event_store,
        load_run_id=FakeLoadRunId(none_count=None),
        clock=clock,
    )

    coordinator.run_build("build-noread", mock=True)

    # read가 한 번도 호출되지 않음.
    assert event_store._read_calls == {}


# ── (c) RUN_FINISHED 미등장 → T_run 종료 ─────────────────────────────────────


def test_run_timeout_when_run_finished_never_appears() -> None:
    """run_id는 배정됐으나 RUN_FINISHED 영구 미등장 → T_run 종료·다음 enqueue 0."""
    queue = FakeQueue()
    # run_id는 즉시 배정되지만(claim 통과), RUN_FINISHED는 영구 미등장.
    load_run_id = FakeLoadRunId(none_count=0, run_id_prefix="r")
    event_store = FakeEventStore(
        appear_after_reads={"r-1": None},  # task_id=1 → run_id "r-1" 영구 미등장
        default_status="success",
    )
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue,
        event_store=event_store,
        load_run_id=load_run_id,
        clock=clock,
        t_claim=5.0,
        t_run=10.0,
        poll_interval=1.0,
    )

    result = coordinator.run_build("build-run-to", mock=True)

    # 실패·사유 run_timeout·1단계에서.
    assert result["state"] == STATE_FAILED
    assert result["error"]["reason"] == ERROR_RUN_TIMEOUT
    assert result["error"]["stage"] == STAGES[0]
    # 첫 단계만 enqueue(claim은 통과했으므로 run_id는 기록됨).
    assert queue.workflows == [STAGES[0]]
    assert result["run_ids"][STAGES[0]] == "r-1"
    # read가 여러 번 호출됨(T_run 폴링이 실제로 돌았다는 증거)·유한 종료.
    assert event_store._read_calls["r-1"] >= 1


# ── (d) 2단계 status=failed → 체인 중단 ──────────────────────────────────────


def test_chain_breaks_on_failed_status_at_stage_two() -> None:
    """2단계 RUN_FINISHED.status=failed → 체인 중단(3단계 enqueue 0)."""
    queue = FakeQueue()
    load_run_id = FakeLoadRunId(none_count=0, run_id_prefix="r")
    # 1단계(r-1)=success, 2단계(r-2)=failed → 2단계에서 중단.
    event_store = FakeEventStore(
        status_by_run={"r-1": "success", "r-2": "failed"},
        default_status="success",
    )
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue, event_store=event_store, load_run_id=load_run_id, clock=clock
    )

    result = coordinator.run_build("build-fail2", mock=True)

    # 실패·사유 stage_failed·2단계(game_balance)에서.
    assert result["state"] == STATE_FAILED
    assert result["error"]["reason"] == ERROR_STAGE_FAILED
    assert result["error"]["stage"] == STAGES[1]
    # 1·2단계만 enqueue·3단계(game_content) 이후 enqueue 0(체인 중단).
    assert queue.workflows == [STAGES[0], STAGES[1]]
    # 1단계 run_id는 기록(success), 2단계도 기록(폴링은 했으나 status로 중단).
    assert result["run_ids"][STAGES[0]] == "r-1"
    assert result["run_ids"][STAGES[1]] == "r-2"


def test_chain_breaks_on_rejected_status() -> None:
    """RUN_FINISHED.status=rejected도 failed와 동일하게 체인 중단(success만 진행)."""
    queue = FakeQueue()
    load_run_id = FakeLoadRunId(none_count=0, run_id_prefix="r")
    event_store = FakeEventStore(status_by_run={"r-1": "rejected"})
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue, event_store=event_store, load_run_id=load_run_id, clock=clock
    )

    result = coordinator.run_build("build-reject", mock=True)

    assert result["state"] == STATE_FAILED
    assert result["error"]["reason"] == ERROR_STAGE_FAILED
    assert result["error"]["stage"] == STAGES[0]
    assert queue.workflows == [STAGES[0]]


# ── 바운드 검증(보조): 폴링이 실제로 K번 None 후 통과 ────────────────────────


def test_claim_polls_until_run_id_appears_within_bound() -> None:
    """run_id가 K번 None 후 등장하면 T_claim 안에서 통과한다(바운드 내 폴 동작)."""
    queue = FakeQueue()
    # 각 단계 task마다 2번 None 후 run_id 등장. poll_interval=1, t_claim=5 → 통과.
    load_run_id = FakeLoadRunId(none_count=2, run_id_prefix="r")
    event_store = FakeEventStore(default_status="success")
    clock = FakeClock()
    coordinator = _make_coordinator(
        queue=queue,
        event_store=event_store,
        load_run_id=load_run_id,
        clock=clock,
        t_claim=5.0,
        poll_interval=1.0,
    )

    result = coordinator.run_build("build-poll", mock=True)

    assert result["state"] == STATE_DONE
    assert queue.workflows == STAGES


def test_get_status_returns_none_for_unknown_build() -> None:
    """미등록 build_id는 None을 반환한다."""
    coordinator = _make_coordinator(
        queue=FakeQueue(),
        event_store=FakeEventStore(),
        load_run_id=FakeLoadRunId(),
        clock=FakeClock(),
    )
    assert coordinator.get_status("nope") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
