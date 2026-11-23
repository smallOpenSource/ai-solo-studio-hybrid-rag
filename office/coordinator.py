"""자동 빌드 코디네이터 — 직렬 게임빌드 체이닝 + run_id 상관 2축 바운드 폴링.

계획 §2.A·§9 BINDING CONDITIONS(`.omc/plans/ralplan-coop-game-windows.md`)의 신규·
비동결 모듈. 기존 수동 직렬 파이프라인(game_design→game_balance→game_content→
game_assemble→game_qa·`tests/test_game_pipeline_e2e.py:508-566` 참조)을 **자동화**한다.

핵심 계약(§9 BINDING 1·2):
- **run_id 상관**: enqueue가 돌려주는 건 task_id(run_id 아님). 워커가 RUN_STARTED 발행
  **전** `_record_run_id_in_payload`로 payload에 run_id를 심으므로(`office/run.py:637`),
  코디네이터는 `load_run_id(task_id)`를 바운드 폴링해 run_id를 먼저 얻고, 그 run_id로
  `EventStore.read(run_id)`의 RUN_FINISHED를 바운드 폴링한다.
- **2축 바운드 폴링(단일 T 금지)**: T_claim(enqueue→load_run_id non-None)과 T_run
  (run_id→RUN_FINISHED)이 **독립** 타임아웃. 5단계 직렬 실LLM·단계당 ~36s+에서 한
  단계 지연이 전체 거짓실패가 되지 않게 분리한다.
- **바운드 절대**: 두 폴링 모두 경과시간 ≤ 한계까지만 돈다. 무한루프 0. foreground
  sleep 0(주입 sleep만 호출 — 테스트는 fake sleep로 실대기 0). web_server import 0.

이 모듈은 신규 이벤트/노드/워크플로를 만들지 않는다. enqueue·EventStore.read·
load_run_id만 사용(동결 7파일 미접촉).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional, Protocol

from office.events import RunFinished
from office.run import load_run_id as _default_load_run_id

# ── 게임빌드 직렬 단계(직렬 협업 = 배턴) ───────────────────────────────────────
# 순서가 곧 단계 계약: 각 단계는 이전 단계가 디스크 game/data에 남긴 산출을 입력으로
# 읽는다(`tools.py` game_data_read). 순서를 바꾸면 단계 계약이 깨진다.
STAGES: list[str] = [
    "game_design",
    "game_balance",
    "game_content",
    "game_assemble",
    "game_qa",
]

# RUN_FINISHED.status 분기 상수(`office/events.py:85-96` RunFinished.status).
_STATUS_SUCCESS = "success"

# 빌드 결과 state 값.
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_RUNNING = "running"

# 실패 사유(error 필드에 기록 — 어느 바운드가 끊겼는지/어느 단계인지 식별).
ERROR_CLAIM_TIMEOUT = "claim_timeout"
ERROR_RUN_TIMEOUT = "run_timeout"
ERROR_STAGE_FAILED = "stage_failed"


class _QueueLike(Protocol):
    """코디네이터가 의존하는 큐 계약 — enqueue(workflow, payload)->task_id만 쓴다."""

    def enqueue(self, workflow: str, payload: dict[str, Any] | None = ...) -> int: ...


class _EventStoreLike(Protocol):
    """코디네이터가 의존하는 이벤트스토어 계약 — read(run_id)->이벤트 리스트만 쓴다."""

    def read(self, run_id: str) -> list[Any]: ...


class BuildCoordinator:
    """게임빌드 5단계를 직렬 체이닝하는 동기 코디네이터.

    생성자 주입으로 테스트 가능(실워커·실LLM·실대기 0):
        coordinator = BuildCoordinator(
            queue=FakeQueue(), event_store=FakeEventStore(),
            load_run_id=fake_load_run_id, db_path=tmp_path / "x.db",
            t_claim=5.0, t_run=10.0, poll_interval=0.5,
            sleep=fake_clock.sleep, monotonic=fake_clock.monotonic,
        )
        result = coordinator.run_build("build-1", mock=True)
    """

    def __init__(
        self,
        *,
        queue: _QueueLike,
        event_store: _EventStoreLike,
        db_path: Any,
        load_run_id: Optional[Callable[[int], Optional[str]]] = None,
        t_claim: float = 30.0,
        t_run: float = 300.0,
        poll_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """코디네이터 초기화.

        Args:
            queue:        enqueue(workflow, payload)->task_id 제공(TaskQueue).
            event_store:  read(run_id)->이벤트 리스트 제공(EventStore).
            db_path:      load_run_id 기본 바인딩용 DB 경로.
            load_run_id:  (task_id)->run_id|None callable. None이면 office.run.load_run_id를
                          db_path로 바인딩한 기본 구현을 쓴다.
            t_claim:      enqueue→run_id 배정(claim) 바운드 타임아웃(초).
            t_run:        run_id→RUN_FINISHED 바운드 타임아웃(초).
            poll_interval: 두 폴링 루프의 폴 간격(초).
            sleep:        대기 함수(주입). 테스트는 fake sleep로 실대기 0.
            monotonic:    단조 시계(주입). 테스트는 fake counter로 경과 제어.
        """
        self._queue = queue
        self._event_store = event_store
        self._db_path = db_path
        # 기본 load_run_id는 office.run.load_run_id를 db_path로 바인딩(task_id만 받는 형태).
        if load_run_id is None:
            self._load_run_id: Callable[[int], Optional[str]] = lambda task_id: (
                _default_load_run_id(task_id, db_path)
            )
        else:
            self._load_run_id = load_run_id
        self._t_claim = t_claim
        self._t_run = t_run
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._monotonic = monotonic
        # build_id -> {state, stage, run_ids, error} 상태추적(US-3 상태조회용).
        self._builds: dict[str, dict[str, Any]] = {}

    # ── 공개 API ───────────────────────────────────────────────────────────

    def run_build(
        self,
        build_id: str,
        *,
        mock: bool,
        query: str | None = None,
        payload_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """5단계를 순차 체이닝 실행하고 최종 상태 dict를 반환한다.

        각 단계: (1) enqueue → task_id (2) T_claim 바운드 폴링으로 run_id 획득
        (3) T_run 바운드 폴링으로 RUN_FINISHED 획득 (4) status 분기. 어느 단계든
        claim_timeout·run_timeout·stage_failed면 즉시 체인 중단(다음 enqueue 0).

        Args:
            build_id: 이 빌드의 식별자(상태추적 키).
            mock:     payload에 실어 워커가 결정론 fake LLM으로 실행하게 한다(웹 관례).
            query:    있으면 payload["query"]로 실어 워커가 messages 시딩(기존 관례).
            payload_extra: 단계 payload에 병합할 추가 키(선택).

        Returns:
            self._builds[build_id]와 동일한 상태 dict(state/stage/run_ids/error).
        """
        status = {
            "state": STATE_RUNNING,
            "stage": None,
            "run_ids": {},
            "error": None,
        }
        self._builds[build_id] = status

        for stage in STAGES:
            status["stage"] = stage

            # (1) enqueue → task_id(run_id 아님). payload는 레퍼런스 관례(mock 포함).
            payload = self._build_payload(mock=mock, query=query, extra=payload_extra)
            task_id = self._queue.enqueue(stage, payload)

            # (2) T_claim 바운드 폴링: load_run_id(task_id) non-None까지.
            run_id = self._poll_run_id(task_id)
            if run_id is None:
                return self._finish_failed(status, ERROR_CLAIM_TIMEOUT, stage)
            status["run_ids"][stage] = run_id

            # (3) T_run 바운드 폴링: read(run_id)에서 RUN_FINISHED까지.
            finished = self._poll_run_finished(run_id)
            if finished is None:
                return self._finish_failed(status, ERROR_RUN_TIMEOUT, stage)

            # (4) status 분기: success → 다음 / failed·rejected → 체인 중단.
            if finished.status != _STATUS_SUCCESS:
                return self._finish_failed(status, ERROR_STAGE_FAILED, stage)

        # 전 단계 success → done.
        status["state"] = STATE_DONE
        status["stage"] = None
        return status

    def get_status(self, build_id: str) -> dict[str, Any] | None:
        """build_id의 현재 상태 dict를 반환한다(US-3 상태조회·백그라운드 진행 가시화).

        없으면 None. 반환 dict는 내부 상태와 동일 객체이므로 호출측은 읽기만 한다.
        """
        return self._builds.get(build_id)

    # ── 내부: 바운드 폴링 2축 ────────────────────────────────────────────────

    def _poll_run_id(self, task_id: int) -> str | None:
        """T_claim 바운드 폴링 — load_run_id(task_id) non-None까지(경과 ≤ t_claim).

        워커가 claim 후 RUN_STARTED 발행 전 payload에 run_id를 심는다(run.py:637) →
        여기서 먼저 non-None을 본다(RUN_FINISHED보다 앞섬·§9 순서보장). 한계 초과면 None.
        """
        deadline = self._monotonic() + self._t_claim
        while True:
            run_id = self._load_run_id(task_id)
            if run_id is not None:
                return run_id
            # 다음 폴 전 경과 검사 — 한계 초과면 더 자지 않고 종료(바운드 절대).
            if self._monotonic() >= deadline:
                return None
            self._sleep(self._poll_interval)

    def _poll_run_finished(self, run_id: str) -> RunFinished | None:
        """T_run 바운드 폴링 — read(run_id)에서 RUN_FINISHED까지(경과 ≤ t_run).

        EventStore.read는 seq 오름차순 이벤트 리스트. 그중 RUN_FINISHED 1건을 찾으면
        그 RunFinished를 돌려준다(status 분기는 호출측). 한계 초과면 None.
        """
        deadline = self._monotonic() + self._t_run
        while True:
            finished = self._find_run_finished(run_id)
            if finished is not None:
                return finished
            if self._monotonic() >= deadline:
                return None
            self._sleep(self._poll_interval)

    def _find_run_finished(self, run_id: str) -> RunFinished | None:
        """read(run_id) 이벤트 중 RUN_FINISHED 1건을 찾아 반환한다. 없으면 None.

        .type 문자열로 식별(이벤트 객체 형태 보존). 여러 건이면 첫 건(seq 오름차순이라
        가장 이른 종료)을 쓴다 — 정상 run엔 RUN_FINISHED가 1건.
        """
        for event in self._event_store.read(run_id):
            if getattr(event, "type", None) == "RUN_FINISHED":
                return event  # type: ignore[return-value]
        return None

    # ── 내부: 페이로드·종료 헬퍼 ─────────────────────────────────────────────

    def _build_payload(
        self,
        *,
        mock: bool,
        query: str | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """단계 enqueue payload를 만든다(웹 관례 미러 — {"mock": bool}[+query][+extra])."""
        payload: dict[str, Any] = {"mock": mock}
        if isinstance(query, str) and query:
            payload["query"] = query
        if extra:
            payload.update(extra)
        return payload

    def _finish_failed(
        self, status: dict[str, Any], error: str, stage: str
    ) -> dict[str, Any]:
        """상태를 failed로 마감하고(체인 중단), 끊긴 단계·사유를 기록해 반환한다."""
        status["state"] = STATE_FAILED
        status["stage"] = stage
        status["error"] = {"reason": error, "stage": stage}
        return status
