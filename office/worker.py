"""F2 별도 워커 프로세스 진입점 — task_queue(office.db)를 소비해 워크플로 실행.

Phase 6 6a(계획 ralplan-phase6-worker-agent.md §3 결정축2 W-2). 웹(web_server)은
enqueue + SSE/Inbox/api(이벤트 DB 조회)만 하고, 이 워커가 claim + run을 담당한다
(장애 격리: 워크플로 크래시가 웹을 안 죽임). 같은 office.db(WAL)를 공유하므로 웹의
FanoutPoller가 워커가 쓴 이벤트를 그대로 SSE→Discord로 흘린다(계약 보존).

사용법:
    python -m office.worker                      # 무한 루프(프로덕션)
    python -m office.worker --max-ticks 1        # 유한(테스트·1회)
    python -m office.worker --poll-interval 0.5  # 폴 간격 조정

설계(계획 §1·§3):
- ★단일 인스턴스 advisory 락(AC-P6-1d): state/worker.lock을 fcntl.flock(LOCK_EX|
  LOCK_NB)로 보유(프로세스 생애 동안). 이미 워커가 떠 있으면 즉시 exit(1). 선택 근거
  (BEGIN EXCLUSIVE 대비): (a) 프로세스 죽으면 OS가 락을 자동 해제 → 좀비 잔재 0,
  (b) non-blocking이라 hang 0, (c) advisory·파일 락이라 큐의 SQLite 연결과 독립
  (BEGIN EXCLUSIVE를 워커 생애 내내 쥐면 큐의 모든 write가 막혀 치명적).
- ★P1 동시성 step2(풀 확장·비동결): --slot N이면 락을 state/worker.lock.{N}으로 분리
  (슬롯별 독립) + claim(slot=N)/claim_*(고아복구) 슬롯 각인 + 종료경로 release_slot
  소유가드. slot 미지정(단일워커·머지 기본 concurrency:1)이면 state/worker.lock·
  claimed_by_slot NULL·list_by_status 복구로 기존 동작 100% 보존(behavior-preserving
  롤백 경로). supervisor(step3)가 N개 슬롯 워커를 fork→exec로 띄운다(락 fd O_CLOEXEC로
  split-brain 봉합). 설계·대안평가: .omc/plans/ralplan-p1-concurrency-prd.md
- 동시성 1: 워커 1개 전제(advisory) + claim 원자성(task_queue 단일 UPDATE-RETURNING).
  asyncio/threading/concurrent.futures 0 — heartbeat도 백그라운드 스레드 없이 동기로
  매 tick·매 폴에서 갱신한다.
- hang 가드: run(max_ticks)·run_claimed_task(max_polls)로 바운드. graceful shutdown은
  SIGTERM 플래그 set → 현재 tick 마치고 탈출(진행 중 워크플로 보호).
- 부팅 복구(복구 책임 완전 보유): 기동 시 (a) running 고아 복구 → ★Phase 6 6b 2a:
  Worker.recover_running_orphans가 run_id 식별→이벤트 재생(A-R2·recovery.py)→가역이면
  재개, 비가역/미식별이면 fail+NodeFailed(escalation) Notify(B-HYBRID·main()에서 1회).
  엔진 없는 모듈 함수 recover_running_orphans(db_path)는 2b 전건 fail+Notify 폴백으로
  보존(기존 호출처 호환). (b) suspended 고아 재개 → 보유·블로킹 폴링으로 끝까지
  (complete/fail) — Worker.run()이 루프 진입 전 1회 + 매 tick.
  ★W-2 모델: 워커가 suspended task를 블로킹 폴링으로 보유하나 동시성 1(≤1 in-flight)
  이라 무해하고 cancel은 DB 신호로 탈출 가능(cancel-escapable). 이로써 웹의 resume_task/
  recover_orphan_tasks는 제거 대상(웹은 enqueue+SSE/Inbox 게이트웨이로 축소).
  ★★starvation 런북(의도된 설계 — NIT-2 박제): 미결정 suspended 고아를 워커가 블로킹
  폴링으로 보유하면 그 동안 신규 queued task는 claim되지 못하고 **굶는다**(starvation).
  이는 버그가 아니라 W-2의 의도된 트레이드오프 — 동시성 1을 단순 모델로 보존하기 위함이다.
  ★P1 step2(풀·concurrency:N): 한 슬롯이 미결정 suspended를 블로킹 보유해도 다른 N-1개
  슬롯이 queued를 계속 claim하므로 starvation은 슬롯 수에 비례해 완화된다(완전 제거는
  아님 — 모든 슬롯이 동시에 미결정 보유에 묶이면 여전히 굶는다). 단일워커(slot=None·
  머지 기본)에선 본문 그대로다. 어느 경우든 탈출구는 동일하게 cancel(DB 신호)이다.
  굶주린 신규 task를 풀어 줄 **유일한 탈출구는 cancel**(DB 신호 → 보유 폴링 첫 폴에서
  탈출 → fail 종결 → 다음 tick에 신규 task claim). 결정(승인/거부) 도착도 탈출이나, 영영
  도착 안 하는 미결정 고아는 cancel만이 푼다. 운영: 보유가 길어지면 /api/run/cancel로
  해당 task를 취소해 큐를 흐르게 한다(테스트 입증: test_recovery_2a.TestStarvationCancelEscape).

신규 외부 의존성 0(stdlib: fcntl·signal·os·argparse·sys + 기존 office import).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from office.db import DEFAULT_DB_PATH
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import FailReason, NodeFailed
from office.gating import GatingPolicy
from office.recovery import infer_resume_node_id, is_resumable, replay_accumulated
from office.run import (
    _build_chat_history,
    _chat_persona_seed,
    _DEFAULT_MOCK_NODE_SEQUENCE,
    _FakeSmokeLLM,
    _is_game_workflow,
    _load_workflow,
    _MOCK_NODE_SEQUENCES,
    OFFICE_DEFAULT_MODEL,
    load_run_id,
    load_suspended_state,
    run_claimed_task,
    wait_for_decision_and_resume,
)
from office.task_queue import TaskQueue, _now_iso
from office.tools import REAL_TOOLS

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_LOCK_PATH = _PROJECT_ROOT / "state" / "worker.lock"
_DEFAULT_PID_PATH = _PROJECT_ROOT / "state" / "worker.pid"


def _slot_lock_path(slot: int | None) -> Path:
    """슬롯별 advisory 락 파일 경로(P1 step2).

    slot=None(단일워커·머지 기본 concurrency:1)이면 기존 state/worker.lock 그대로 —
    프로덕션 단일 워커 동작 동치(롤백 경로). slot=N(supervisor 풀)이면 state/worker.lock.{N}
    — 슬롯마다 독립 advisory 락이라 N개 워커가 공존한다(슬롯별 단일 인스턴스 보존).
    """
    if slot is None:
        return _DEFAULT_LOCK_PATH
    return _DEFAULT_LOCK_PATH.parent / f"worker.lock.{slot}"


def _slot_pid_path(slot: int | None) -> Path:
    """슬롯별 PID 파일 경로(관측 보조). slot=None이면 기존 state/worker.pid."""
    if slot is None:
        return _DEFAULT_PID_PATH
    return _DEFAULT_PID_PATH.parent / f"worker.pid.{slot}"


# ── 단일 인스턴스 advisory 락 (fcntl.flock — non-blocking, 자동 해제) ────────────


class SingleInstanceLock:
    """state/worker.lock에 fcntl.flock(LOCK_EX|LOCK_NB)으로 단일 인스턴스를 강제한다.

    획득 실패(이미 다른 워커 보유)면 acquire()가 False. 프로세스가 죽으면 OS가
    락을 자동 해제하므로 좀비 잔재가 없다(PID 파일의 stale 위험 회피). 컨텍스트
    매니저로도 쓸 수 있다.
    """

    def __init__(self, lock_path: str | Path | None = None) -> None:
        self._lock_path = (
            Path(lock_path) if lock_path is not None else _DEFAULT_LOCK_PATH
        )
        self._fp: Any = None

    def acquire(self) -> bool:
        """락 획득을 즉시 시도한다(non-blocking). 성공 True, 이미 보유 중이면 False."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        # 'w'가 아닌 'a'로 열어 기존 내용(있다면) 보존 — 락 자체는 파일 내용과 무관.
        fp = open(self._lock_path, "a")
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # 이미 다른 프로세스가 배타 락 보유 → 즉시 실패(블로킹 안 함).
            fp.close()
            return False
        # ★P1 step2 split-brain 봉합: 락 fd에 close-on-exec를 강제한다. supervisor(step3)가
        # 워커를 fork→exec(conda python)로 띄울 때 부모가 쥔 다른 슬롯의 락 fd가 자식에
        # 상속되면 "LOCK_NB 성공 = 그 슬롯 워커 죽음" 동치가 깨진다(자식이 fd를 들고 있어
        # 락이 안 풀린 듯 보임 → 죽은slot 청소 오판). FD_CLOEXEC로 exec 시 자동 close →
        # 각 슬롯 락은 그 슬롯 워커 프로세스만 소유한다. 단일워커(미감독) 경로엔 무해.
        flags = fcntl.fcntl(fp.fileno(), fcntl.F_GETFD)
        fcntl.fcntl(fp.fileno(), fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        self._fp = fp
        return True

    def release(self) -> None:
        """락을 해제한다(파일 핸들 닫음 → flock 자동 해제)."""
        if self._fp is not None:
            try:
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fp.close()
            self._fp = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


# ── 부팅 복구(2b만): running 고아 fail + NodeFailed(escalation) Notify ─────────


def _fail_and_notify_orphan(
    task: dict[str, Any],
    store: EventStore,
    queue: TaskQueue,
    run_id: str = "",
    detail: str | None = None,
) -> bool:
    """running 고아 1건을 fail 마킹 + NodeFailed(escalation) Notify 한다(2b 폴백 원시).

    2a 재개가 불가(run_id 미식별·비가역 경로·재생/그래프 실패)할 때의 보수 폴백.
    crm_notify에선 항상 가역 노드(N1/N2/N2.5)라 부수효과 손실 0(email_send는
    needs-approval라 초기 패스 항상 suspend). 발행 실패해도 fail 마킹은 시도된다.

    Returns:
        True면 fail 마킹 성공(NodeFailed 발행까지). 예외면 False.
    """
    task_id = task["id"]
    try:
        queue.fail(task_id)
        store.append(
            NodeFailed(
                run_id=run_id,
                ts=_now_iso(),
                node_id="",
                reason=FailReason.escalation,
                detail=(
                    detail
                    if detail is not None
                    else (
                        f"워커 부팅 복구: running 고아 task #{task_id}"
                        f"(workflow={task.get('workflow', '')}) — 직전 크래시 잔재로"
                        " fail 마킹(자동 재개 안 함, 가역 노드라 손실 0)."
                    )
                ),
            )
        )
        return True
    except BaseException:
        # NodeFailed 발행마저 실패해도 fail 마킹은 이미 시도됨 — 호출자가 다음 task로.
        return False


def recover_running_orphans(db_path: str | Path) -> list[int]:
    """기동 시 1회 running 고아 스캔 → fail 마킹 + Inbox Notify(NodeFailed)(2b — 폴백).

    ★Phase 6 6b: 2a 재개는 엔진이 필요하므로 Worker.recover_running_orphans(인스턴스
    메서드)가 담당하고, 이 모듈 함수는 엔진 없는 보수 폴백(2b 전건 fail+Notify)으로 남는다
    (기존 호출처·시그니처 보존). main()은 Worker.recover_running_orphans(2a)를 부른다.

    running 고아 = 직전 워커 크래시 잔재. 자동 재개 안 함(2b) — Notify만. 스캔 자체는
    예외 격리(일시 DB 락 등으로 기동이 죽지 않게).

    Returns:
        fail 마킹한 running 고아 task_id 목록.
    """
    store = EventStore(db_path=db_path)
    queue = TaskQueue(db_path=db_path)
    failed_orphans: list[int] = []

    try:
        running = queue.list_by_status("running")
    except BaseException:
        running = []
    for task in running:
        if _fail_and_notify_orphan(task, store, queue):
            failed_orphans.append(task["id"])
    return failed_orphans


# ── 워커 본체 ────────────────────────────────────────────────────────────────


class Worker:
    """task_queue를 폴링하며 claim된 task를 순차 실행하는 단일 워커.

    동시성 1: 한 번에 task 1건만. claim→run_claimed_task→clear_cancel.
    무한 루프(max_ticks=None)는 프로덕션, 유한은 테스트.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        poll_interval: float = 2.0,
        slot: int | None = None,
    ) -> None:
        self._db_path = db_path if db_path is not None else DEFAULT_DB_PATH
        self._poll_interval = poll_interval
        self._queue = TaskQueue(self._db_path)
        self._store = EventStore(self._db_path)
        # B-HYBRID 가역성 판정용(config/tools_policy.yaml) — 2a 복구가 다음 노드 risk를
        # risk_of로 본다. 복구 시에만 사용(정상 실행 경로엔 무관).
        self._gating_policy = GatingPolicy()
        self._stop_requested = False
        # ★P1 동시성(step2): 워커풀 슬롯 번호. None=단일워커(머지 기본 concurrency:1·기존
        # 경로 100% 보존: claim(slot=None)→claimed_by_slot NULL·고아복구 list_by_status
        # 스냅샷·락 state/worker.lock). int=supervisor 풀의 한 슬롯(claim(slot=N) 각인·
        # 고아복구 원자 claim_*+release_slot 소유가드·락 state/worker.lock.{N}).
        self._slot = slot

    def request_stop(self) -> None:
        """graceful shutdown 플래그를 set한다(SIGTERM 핸들러가 호출).

        현재 tick은 끝까지 진행하고(진행 중 워크플로 보호) 다음 루프 진입 전에 탈출한다.
        """
        self._stop_requested = True

    def _release_slot(self, task_id: int) -> None:
        """이 워커가 보유하던 task의 슬롯 각인을 해제한다(종료경로·소유가드·P1 step2).

        풀(slot int)에서만 의미가 있다 — claim(slot=N)/claim_*로 각인한 소유를 task가
        terminal(complete/fail)에 도달한 뒤 반납해 "claimed_by_slot≠NULL ⟺ 살아있는
        슬롯이 보유 중인 in-flight task" 불변식을 유지한다(supervisor 죽은slot 청소가
        in-flight만 정확히 겨냥·AC2c). release_slot(slot=self._slot)의 소유가드라 다른
        슬롯이 재점유한 각인은 건드리지 않는다. 단일워커(slot=None)면 no-op(기존 경로 —
        claimed_by_slot 미사용).
        """
        if self._slot is None:
            return
        self._queue.release_slot(task_id, slot=self._slot)

    # mock _FakeSmokeLLM 결정론 시퀀스(워크플로별 노드 순서)는 run.py의
    # _MOCK_NODE_SEQUENCES 단일 출처를 공유한다(run_full과 어긋날 위험 0 — code-review Minor).

    def _build_engine(self, mock: bool = False, wf_name: str = "") -> WorkflowEngine:
        """run_full과 동일 설정의 엔진을 만든다(REAL_TOOLS + GatingPolicy).

        ★워커는 기본적으로 실서버 LLM을 쓴다(프로덕션). 그러나 enqueue payload에
        mock=True가 실려 오면(웹 /api/run body의 mock 플래그가 task payload로 전달됨,
        Chunk 2 W-2 배선) run_full(mock=True)과 동일하게 _FakeSmokeLLM(워크플로별
        node_sequence)로 엔진을 만든다 — 실서버 없이 결정론 검증 경로. tools는 run_full
        의 mock 경로와 동일하게 REAL_TOOLS(business.db 실조회·email 멱등키)를 쓴다.
        wf_name으로 시퀀스를 고른다(미지정/미등록은 crm_notify 기본 — 기존 동작 보존).

        테스트는 run(engine=...)로 mock 엔진을 직접 주입해 이 메서드를 우회한다(주입
        엔진이 있으면 mock 여부와 무관하게 그 엔진 재사용 — 별도 OS 프로세스 아님).
        """
        if mock:
            # run_full(mock=True)과 동일 시퀀스. 워크플로별 node_sequence. LLM 서버·키 불요.
            sequence = _MOCK_NODE_SEQUENCES.get(wf_name, _DEFAULT_MOCK_NODE_SEQUENCE)
            llm: Any = _FakeSmokeLLM(sequence)
        else:
            from core.llm_client import LLMClient

            llm = LLMClient()
        executor = NodeExecutor(llm_client=llm, event_store=self._store)
        # ★g5-4-5-review Major-1: run_full과 동일하게 game 워크플로만 error_fails_node=
        #   True로 띄운다 — n1 required read가 입력 부재 시 노드 실패(NODE_FAILED)로
        #   드러내 단계 계약을 프로덕션(워커)에서도 성립시킨다. 비-game은 False(무변).
        #   wf_name이 빈 문자열(주입 엔진 경로 등 미지정)이면 비-game으로 취급(기존 동작).
        return WorkflowEngine(
            executor=executor,
            event_store=self._store,
            tools=REAL_TOOLS,
            gating_policy=GatingPolicy(),
            error_fails_node=_is_game_workflow(wf_name),
        )

    def _engine_for_task(
        self, task: dict[str, Any], injected: WorkflowEngine | None
    ) -> WorkflowEngine:
        """이 task에 쓸 엔진을 고른다 — 주입 엔진이 있으면 그것(테스트), 없으면 payload
        mock 플래그에 따라 _build_engine(mock=...)으로 매 task 새로 만든다(프로덕션).

        ★payload mock 배선(Chunk 2): 웹 start_run이 enqueue payload에 {"mock": bool}을
        실으므로(워커가 run 안 함 — enqueue만), 워커가 그 행의 payload를 읽어 mock이면
        결정론 fake LLM 엔진으로 실행한다. 동시성 1이라 task마다 엔진 1개는 무해.
        """
        if injected is not None:
            return injected
        mock = self._payload_mock(task)
        wf_name = str(task.get("workflow", ""))
        return self._build_engine(mock=mock, wf_name=wf_name)

    @staticmethod
    def _payload_mock(task: dict[str, Any]) -> bool:
        """task 행의 payload_json에서 mock 플래그를 안전하게 읽는다(없으면 False)."""
        import json

        raw = task.get("payload_json")
        if not raw:
            return False
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
        return bool(payload.get("mock", False)) if isinstance(payload, dict) else False

    @staticmethod
    def _payload_query(task: dict[str, Any]) -> str:
        """task 행의 payload_json에서 동적질문 query를 안전하게 읽는다(없으면 "").

        ★동적질문 messages seam(Phase 8 C·AC-C0): 웹 /api/run body의 query가 enqueue
        payload에 실려 오면(runner.py) 워커가 여기서 읽어 initial_state["messages"]에
        user 메시지로 시딩한다 → engine.py:212가 그 messages를 N1 LLM에 직접 주입한다
        (엔진 핫패스 무수정). _payload_mock과 동형의 방어적 파서(파손/부재면 "").
        """
        import json

        raw = task.get("payload_json")
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        query = payload.get("query", "")
        return query if isinstance(query, str) else ""

    @staticmethod
    def _payload_str_field(task: dict[str, Any], key: str) -> str:
        """payload_json에서 문자열 필드를 안전하게 읽는다(없거나 파손이면 ""·_payload_query 동형)."""
        import json

        raw = task.get("payload_json")
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        val = payload.get(key, "")
        return val if isinstance(val, str) else ""

    def _payload_target_role(self, task: dict[str, Any]) -> str:
        """★멀티봇: enqueue payload의 target_role(지목 직원·인바운드가 실음). 없으면 ""."""
        return self._payload_str_field(task, "target_role")

    def _payload_run_id(self, task: dict[str, Any]) -> str:
        """★멀티봇: payload의 run_id(chat-{role}-{id}·응답 게시 디스패치 키). 없으면 ""."""
        return self._payload_str_field(task, "run_id")

    def _run_one(
        self,
        task: dict[str, Any],
        engine: WorkflowEngine | None,
        max_polls: int | None,
    ) -> None:
        """claim된 task 1건을 run_claimed_task로 실행하고 clear_cancel까지 한다.

        engine: 주입 엔진(테스트). None이면 payload mock 플래그에 맞춰 매 task 새로 빌드
        (프로덕션 — 실 LLM 또는 결정론 fake). cancel 채널 = should_cancel(DB 신호 폴),
        heartbeat = update_heartbeat(suspended 폴링 중에도 liveness 신선). 종료
        (complete/fail/cancel) 후 cancel_requested 플래그를 리셋(AC-P6-2c — 잔재 0).
        """
        task_id: int = task["id"]
        wf_name: str = task["workflow"]
        # ★P6 poison-pill 방어: 미등록 워크플로가 enqueue되면 그 task만 fail-fast로 격리
        # 한다(워커 생존). _load_workflow는 run.py에서 ValueError를 던지는데, 이 호출이 try
        # 밖이면 메인루프까지 전파돼 단일워커가 죽는다(잘못된 enqueue 1건이 전체 정지).
        # _fail_and_notify_orphan이 queue.fail + NodeFailed(escalation) Notify까지 수행한다.
        try:
            nodes, _ = _load_workflow(wf_name)
        except ValueError:
            _fail_and_notify_orphan(
                task,
                self._store,
                self._queue,
                detail=(
                    f"미등록 워크플로 {wf_name!r} — task #{task_id} fail-fast 격리"
                    "(워커 생존·다음 task 계속)."
                ),
            )
            self._queue.clear_cancel(task_id)
            return
        run_engine = self._engine_for_task(task, engine)

        initial_state: dict[str, Any] = {"model": OFFICE_DEFAULT_MODEL}
        # ★동적질문 messages seam(Phase 8 C·AC-C0): payload query가 있으면 user
        # 메시지로 시딩한다 → engine.py:212가 첫 노드 LLM에 직접 주입(엔진 무수정).
        # query 없으면(기존 워크플로) messages 미시딩 — 기존 동작 100% 보존.
        query = self._payload_query(task)
        run_id_override: str | None = None
        if query:
            # ★chat 대화기억(S1) + 멀티봇 페르소나: chat 워크플로만 이전 대화 회수 + 지목
            # 직원 페르소나로 시딩하고, 응답 run_id를 chat-{role}-{id}로 잡아 그 직원 봇이
            # 게시(@멘션 정체성)하게 한다. 비-chat(동적질문)은 현재 턴만·run_id 기본(uuid) — 무변.
            if wf_name == "chat":
                target_role = self._payload_target_role(task)
                history = _build_chat_history(self._store)
                initial_state["messages"] = _chat_persona_seed(
                    target_role, history, query
                )
                run_id_override = self._payload_run_id(task) or None
            else:
                initial_state["messages"] = [{"role": "user", "content": query}]
        try:
            run_claimed_task(
                task_id,
                nodes=nodes,
                wf_name=wf_name,
                engine=run_engine,
                store=self._store,
                queue=self._queue,
                db_path=self._db_path,
                initial_state=initial_state,
                poll_interval=self._poll_interval,
                max_polls=max_polls,
                should_cancel=lambda: self._queue.is_cancel_requested(task_id),
                heartbeat=self._queue.update_heartbeat,
                run_id=run_id_override,
            )
        finally:
            # complete든 fail이든 cancel이든 취소 플래그 리셋(잔재 0).
            self._queue.clear_cancel(task_id)

    def _resume_one_suspended(
        self,
        task: dict[str, Any],
        engine: WorkflowEngine,
        max_polls: int | None,
    ) -> bool:
        """suspended task 1건을 보유(블로킹 폴링)해 끝까지 재개→complete/fail 한다.

        ★W-2 복구 모델(Chunk 2 §1): 워커가 suspended 고아/보유 task를 load_suspended_state
        →wait_for_decision_and_resume(should_cancel=DB·heartbeat=DB)로 **블로킹 폴링**으로
        보유한다. 동시성 1(한 번에 task 1건)이라 한 task에 묶여도 정확성 무해하고, cancel은
        DB 신호로 폴링 첫 폴에 탈출 가능(cancel-escapable)이라 데드락 없음. 무한 루프는
        max_polls 바운드(테스트)·프로덕션은 결정/취소 대기(승인 도착 또는 cancel).

        run_full의 suspended-branch 꼬리(⑥)와 동일하게 success면 complete, 아니면(미결정
        max_polls 소진·cancelled·error) fail로 흡수한다. run_id가 suspended_state에
        박제돼 있어 같은 run_id로 resume → B1 dedup·B2 멱등키로 email 정확히-1회.

        Returns:
            True  — 이 task를 보유·재개 시도했음(complete/fail 마킹까지).
            False — suspended_state가 없어(payload={} 등) 재개 불가 → 호출자가 처리.
        """
        task_id: int = task["id"]
        suspended_state = load_suspended_state(task_id, self._db_path)
        if suspended_state is None:
            # 재개할 state가 없다(running 고아가 잘못 섞임 등) — skip.
            return False
        try:
            final_state = wait_for_decision_and_resume(
                engine=engine,
                suspended_state=suspended_state,
                task_id=task_id,
                store=self._store,
                db_path=self._db_path,
                poll_interval=self._poll_interval,
                max_polls=max_polls,
                should_cancel=lambda: self._queue.is_cancel_requested(task_id),
                heartbeat=self._queue.update_heartbeat,
            )
            final_status = final_state.get("_workflow_status", "unknown")
            if final_status == "success":
                self._queue.complete(task_id)
            else:
                # 미결정(max_polls 소진)·cancelled·rejected·error는 fail로 흡수(종료).
                self._queue.fail(task_id)
        finally:
            # complete든 fail이든 cancel이든 취소 플래그 리셋(잔재 0 — AC-P6-2c).
            self._queue.clear_cancel(task_id)
        return True

    def _resume_suspended_orphans(
        self,
        engine: WorkflowEngine | None,
        max_polls: int | None,
    ) -> int:
        """현재 suspended 상태인 task들을 순차 보유·재개한다(부팅 1회 + 매 tick).

        ★복구 책임이 웹→워커로 완전 이전(Chunk 2): suspended 고아 = 직전 워커 크래시
        잔재이거나, 이 워커가 이전 tick에 suspend시킨 뒤 결정 대기 중인 task. 한 번에
        1건씩 블로킹 보유하며 끝낸다(cancel-escapable). 각 task는 payload mock 플래그에
        맞는 엔진(주입 엔진 우선)으로 재개한다.

        ★P1 step2 dual-path:
        - slot=None(단일워커·기존 경로): list_by_status 스냅샷 순회. 단일 인스턴스 락이
          배타성을 보장하므로 비원자 스캔도 race 없음(behavior-preserving).
        - slot=int(풀): claim_suspended(slot)로 미점유(claimed_by_slot IS NULL) 고아 1건을
          원자 점유→슬롯 각인하고 보유한다. 다른 슬롯이 보유 중인(각인된) suspended는
          IS NULL 필터로 건너뛰어 보유구간 이중집음 0(AC2b). terminal 후 release_slot 반납.

        Returns:
            재개 시도(보유)한 suspended task 수.
        """
        resumed = 0
        if self._slot is None:
            # ── 단일워커(기존 경로·behavior-preserving): list_by_status 스냅샷 ──
            try:
                suspended = self._queue.list_by_status("suspended")
            except BaseException:
                suspended = []
            for task in suspended:
                if self._stop_requested:
                    break
                task_engine = self._engine_for_task(task, engine)
                if self._resume_one_suspended(task, task_engine, max_polls):
                    resumed += 1
            return resumed
        # ── 풀(slot 각인·원자 보유): claim_suspended로 미점유 고아만 1건씩 ──
        # ★종료 보장(리뷰 Nit-1·무한루프 아님): 매 iter는 terminal+release(suspended에서
        # 이탈) 또는 quarantine(각인→IS NULL 집합에서 이탈) 중 하나라 'claimed_by_slot IS
        # NULL인 suspended' 집합이 단조 감소한다. 다른 워커는 자기 slot으로 각인하므로 NULL
        # 고아를 새로 만들지 않는다 → 유한 집합 소진 시 claim_suspended가 None을 줘 break.
        while not self._stop_requested:
            task = self._queue.claim_suspended(self._slot)
            if task is None:
                break
            task_engine = self._engine_for_task(task, engine)
            if self._resume_one_suspended(task, task_engine, max_polls):
                # terminal(complete/fail) 도달 → 슬롯 각인 반납(소유가드). 영영 대기
                # (미결정·max_polls=None)면 _resume_one_suspended가 안 돌아오므로 각인
                # 유지(in-flight 소유 정상) — 크래시 시 supervisor 청소.
                resumed += 1
                self._release_slot(task["id"])
            # False(suspended_state 부재·재개 불가)면 각인 유지(quarantine) → claim_suspended
            # IS NULL 필터가 다음 iter에 건너뛰어 무한 재집음 차단. 비정상 고아라 드묾.
        return resumed

    def _recover_one_running_2a(
        self,
        task: dict[str, Any],
        engine: WorkflowEngine | None,
        max_polls: int | None,
    ) -> str:
        """running 고아 1건을 2a(이벤트 재생 재개) 또는 2b(fail+Notify)로 복구한다.

        ★A-R2 + B-HYBRID(계획 §6 US-P6B-1 · recovery.py 위임):
          1) run_id 식별(load_run_id — US-P6B-0 payload). 없으면 2b 폴백.
          2) _load_workflow(wf_name) 그래프 재로드. ValueError(미등록)면 2b 폴백.
          3) replay_accumulated로 accumulated 복원 + infer_resume_node_id로 재개 노드 추론.
          4) B-HYBRID: is_resumable(재개 노드가 비가역/agent)이면 2b 폴백(fail+Notify).
          5) 가역이면 run_claimed_task(run_id=동일·initial_state=accumulated 복원)로 재개 —
             이미 실행된 tool 노드는 B1 skip(부수효과 0)·비가역은 B2 멱등키로 이중발송 0.
             재개가 다시 needs-approval(N3)에 닿으면 suspended로 보유·폴링(run_claimed_task
             꼬리가 처리). messages 복원 불요(carryforward — engine.py:209 재생성).

        Returns:
            "resumed" — 2a 재개 시도(complete/fail/suspend 마킹까지).
            "failed"  — 2b 폴백(fail+Notify)으로 종결.
        """
        task_id: int = task["id"]
        wf_name: str = task.get("workflow", "")

        run_id = load_run_id(task_id, self._db_path)
        if not run_id:
            # claim~RUN_STARTED 윈도 식별 실패(payload run_id 부재) — 2b 보수 폴백.
            _fail_and_notify_orphan(
                task,
                self._store,
                self._queue,
                detail=(
                    f"워커 부팅 복구: running 고아 task #{task_id}"
                    f"(workflow={wf_name}) — run_id 미식별(payload 부재)로 2a 재개 불가,"
                    " fail 마킹(자동 재개 안 함)."
                ),
            )
            return "failed"

        # 그래프 재로드(running 고아엔 _nodes 부재 — _load_workflow 유일 경로).
        try:
            nodes, _ = _load_workflow(wf_name)
        except BaseException:
            _fail_and_notify_orphan(
                task,
                self._store,
                self._queue,
                run_id=run_id,
                detail=(
                    f"워커 부팅 복구: running 고아 task #{task_id}"
                    f"(workflow={wf_name}) — 워크플로 그래프 재로드 실패로 2a 불가, fail."
                ),
            )
            return "failed"

        # A-R2 재구성 + B-HYBRID 판정(재구성/판정 실패는 보수적으로 2b 폴백).
        try:
            accumulated = replay_accumulated(self._store, run_id)
            resume_node_id = infer_resume_node_id(nodes, accumulated)
            resumable = is_resumable(nodes, resume_node_id, self._gating_policy)
        except BaseException:
            resumable = False
            accumulated = {}
            resume_node_id = None

        # 모든 노드 완료(resume_node_id=None) → run은 이미 성공 종료했고 complete() 마킹만
        # 빠진 고아다. 그래프를 재실행하면 마지막 needs-approval 노드에서 불필요 재suspend가
        # 나므로, 재실행 없이 complete로 종결한다(부수효과 0 — 이미 끝난 run).
        if resumable and resume_node_id is None:
            self._queue.complete(task_id)
            self._queue.clear_cancel(task_id)
            return "resumed"

        if not resumable:
            # 재개 노드가 비가역(needs-approval/block)·agent거나 재구성 실패 → 2b 폴백.
            _fail_and_notify_orphan(
                task,
                self._store,
                self._queue,
                run_id=run_id,
                detail=(
                    f"워커 부팅 복구: running 고아 task #{task_id}"
                    f"(workflow={wf_name}) — 다음 노드가 비가역/agent(B-HYBRID)라 2a 재개"
                    " 안 함, fail+escalation Notify(사람 개입 필요)."
                ),
            )
            return "failed"

        # ── 2a 재개: 같은 run_id + 복원 accumulated로 run_claimed_task 재진입 ──
        run_engine = self._engine_for_task(task, engine)
        initial_state: dict[str, Any] = {
            "model": OFFICE_DEFAULT_MODEL,
            "accumulated": accumulated,
        }
        try:
            run_claimed_task(
                task_id,
                nodes=nodes,
                wf_name=wf_name,
                engine=run_engine,
                store=self._store,
                queue=self._queue,
                db_path=self._db_path,
                initial_state=initial_state,
                poll_interval=self._poll_interval,
                max_polls=max_polls,
                should_cancel=lambda: self._queue.is_cancel_requested(task_id),
                heartbeat=self._queue.update_heartbeat,
                run_id=run_id,
            )
        finally:
            self._queue.clear_cancel(task_id)
        return "resumed"

    def recover_running_orphans(
        self,
        engine: WorkflowEngine | None = None,
        max_polls: int | None = None,
    ) -> dict[str, list[int]]:
        """기동 시 1회 running 고아를 2a(재개)/2b(fail+Notify)로 복구한다(Phase 6 6b).

        ★모듈 함수 recover_running_orphans(db_path)(2b 전건)과 달리, 이 인스턴스 메서드는
        엔진을 가져 2a 재개를 시도한다(A-R2 + B-HYBRID). main()이 부팅 시 1회 호출한다.
        동시성 1: worker.py 단일 루프라 다중 running 고아도 순차 복구(AC-P6B-1e).

        스캔 자체는 예외 격리(일시 DB 락 등으로 기동이 죽지 않게). 각 task는 가역이면
        2a 재개, 비가역/미식별이면 2b 폴백(fail+Notify).

        ★P1 step2 dual-path:
        - slot=None(단일워커·기존 경로): list_by_status 스냅샷 순회(behavior-preserving).
        - slot=int(풀): claim_running(slot)로 미점유(IS NULL) running 고아만 1건씩 원자
          점유→복구→release. ★전제(step3 supervisor): 죽은 슬롯의 running 각인은
          supervisor가 재기동 직전 worker.lock.{slot} LOCK_NB 획득(=진짜 죽음) 후
          claimed_by_slot=NULL로 청소한다. 그래야 부팅한 워커-N이 그 고아(IS NULL)를
          원자 재점유한다. 살아있는 다른 슬롯이 보유 중인 running(각인됨)은 IS NULL
          필터로 건너뛴다(이중복구 0·라이브 워커 보호).

        Returns:
            {"resumed": [task_id...], "failed": [task_id...]}.
        """
        resumed: list[int] = []
        failed: list[int] = []
        if self._slot is None:
            # ── 단일워커(기존 경로·behavior-preserving): list_by_status 스냅샷 ──
            try:
                running = self._queue.list_by_status("running")
            except BaseException:
                running = []
            for task in running:
                if self._stop_requested:
                    break
                try:
                    outcome = self._recover_one_running_2a(task, engine, max_polls)
                except BaseException:
                    # 개별 task 복구 실패가 전체 부팅을 멈추지 않게 — 다음 task로.
                    continue
                if outcome == "resumed":
                    resumed.append(task["id"])
                else:
                    failed.append(task["id"])
            return {"resumed": resumed, "failed": failed}
        # ── 풀(slot 각인·원자 복구): claim_running으로 미점유 고아만 1건씩 ──
        while not self._stop_requested:
            task = self._queue.claim_running(self._slot)
            if task is None:
                break
            try:
                outcome = self._recover_one_running_2a(task, engine, max_polls)
            except BaseException:
                # 개별 복구 실패는 부팅을 멈추지 않는다 — 각인 유지(quarantine)해 무한
                # 재집음 차단(IS NULL 아님). 다음 NULL 고아로.
                continue
            if outcome == "resumed":
                resumed.append(task["id"])
            else:
                failed.append(task["id"])
            self._release_slot(task["id"])
        return {"resumed": resumed, "failed": failed}

    def run(
        self,
        max_ticks: int | None = None,
        engine: WorkflowEngine | None = None,
        max_polls: int | None = None,
    ) -> int:
        """워커 루프를 실행한다(복구 책임 완전 보유 — Chunk 2).

        부팅 1회(루프 진입 전): suspended 고아 재개 시도(보유·블로킹 폴링). 그 다음 매 tick:
        ① update_heartbeat ② suspended 보유분(결정 대기 중) 재개 ③ claim queued →
        있으면 _run_one(run_claimed_task — suspend 시 같은 호출 내 블로킹 폴링까지)
        ④ 아무 것도 없으면 poll_interval sleep. graceful stop 플래그면 다음 루프 전 탈출.

        ★단순 모델(W-2): ≤1 in-flight(동시성 1)라 suspended 고아 블로킹 보유는 cancel-
        escapable(DB 신호)이면 안전. 무한 루프는 max_ticks/max_polls 바운드.

        Args:
            max_ticks: 최대 tick 수. None이면 무한(프로덕션). 유한은 테스트.
            engine   : 주입 엔진(테스트의 mock LLM 엔진). None이면 매 task payload mock
                       플래그에 맞춰 빌드(실서버 LLM 또는 결정론 fake). 주입 시 그 엔진을
                       claim·resume 양쪽에 재사용.
            max_polls: 승인 폴링 상한(run_claimed_task·suspended 재개로 관통 — hang 가드).
                       None이면 무기한(프로덕션 승인 대기).

        Returns:
            처리한(claim 성공 + suspended 보유 재개) task 수.
        """
        ticks = 0
        processed = 0

        # ── 부팅 1회: suspended 고아 재개(running 2b는 main()이 별도 1회 처리) ──
        # ★복구 책임이 워커로 이전 — 결정 도착분은 즉시, 미도착분은 폴링 보유(cancel
        #   또는 max_polls로 탈출). stop 플래그면 진입 자체를 건너뛴다.
        if not self._stop_requested:
            processed += self._resume_suspended_orphans(engine, max_polls)

        while True:
            if self._stop_requested:
                break
            if max_ticks is not None and ticks >= max_ticks:
                break
            ticks += 1

            # ① heartbeat 갱신(매 tick — claim 사이 idle에도 liveness 신선)
            self._queue.update_heartbeat()

            # ② suspended 보유분 재개(이전 tick에 suspend됐거나 새 고아 — 결정/취소 대기)
            processed += self._resume_suspended_orphans(engine, max_polls)
            if self._stop_requested:
                break

            # ③ claim queued (★P1 step2: 슬롯 각인 — slot=None이면 NULL=기존 경로)
            task = self._queue.claim(slot=self._slot)
            if task is not None:
                # task 실행(run_claimed_task — suspend 시 블로킹 폴링까지 같은 호출 내)
                # ★P6 가용성 방어: _run_one의 예기치 못한 오류(엔진·도구·재로드 등)가 단일
                # 워커를 죽이지 않게 격리한다. graceful shutdown 신호(KeyboardInterrupt/
                # SystemExit)만 재전파하고, 그 외 BaseException은 task만 fail+Notify 후 계속.
                try:
                    self._run_one(task, engine, max_polls)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    _fail_and_notify_orphan(
                        task,
                        self._store,
                        self._queue,
                        detail=(
                            f"task #{task['id']}(workflow={task.get('workflow', '')}) "
                            "실행 중 예기치 못한 오류 — fail 격리(워커 생존)."
                        ),
                    )
                    self._queue.clear_cancel(task["id"])
                finally:
                    # ★P1 step2: claim(slot=N)으로 각인한 소유를 task terminal 후 반납
                    # (소유가드). _run_one이 영영 안 돌아오면(미결정 suspend·max_polls=None)
                    # finally 미도달=각인 유지(in-flight 소유 정상) — 크래시 시 supervisor
                    # 청소. slot=None이면 no-op(기존 경로).
                    # ★불변식(리뷰 Minor-1): KI/SystemExit가 보유 중(non-terminal) task를
                    # 끊으면 finally가 각인을 NULL로 푼 뒤 re-raise한다. 재기동 시 그 고아는
                    # claim_running이 집어 2a replay로 재개되는데 — 이는 임의 크래시 고아와
                    # 동일 경로라 idempotent(B1 이미실행 노드 skip·B2 멱등키·비가역은 2b
                    # fail+Notify). 즉 NULL-후-재기동은 안전(새 위험 표면 아님). SIGTERM은
                    # request_stop 플래그라 현재 tick을 마치고 빠져 이 경로를 안 탄다.
                    self._release_slot(task["id"])
                processed += 1
            else:
                # ④ 처리할 작업 없음 → poll_interval 대기(유한 루프면 빈 tick 후 계속)
                if max_ticks is None and self._poll_interval > 0:
                    time.sleep(self._poll_interval)
        return processed


# ── CLI 진입점 ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """워커 진입점 — advisory 락 획득 → 부팅 복구 → 무한 루프.

    이미 워커가 떠 있으면(advisory 락 실패) stderr 안내 후 exit(1)(동시성 1 보존).
    SIGTERM 수신 시 현재 tick 마치고 graceful 종료.
    """
    parser = argparse.ArgumentParser(description="F2 워커 (task_queue 소비)")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="처리할 작업이 없을 때 다음 폴까지 대기(초)",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="최대 tick 수(테스트 주입용). 미지정이면 무한(프로덕션)",
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=None,
        help=(
            "멀티 워커풀 슬롯 번호(supervisor가 주입). 미지정이면 단일워커"
            "(머지 기본 concurrency:1·claimed_by_slot NULL·기존 경로)."
        ),
    )
    parser.add_argument(
        "--lock-path",
        type=str,
        default=None,
        help="advisory 락 파일 경로(미지정이면 슬롯 기반 state/worker.lock[.{slot}])",
    )
    args = parser.parse_args(argv)

    slot = args.slot
    # 락 경로: --lock-path 명시 시 그대로(테스트 주입), 아니면 슬롯 기반 파생
    # (slot=None→worker.lock 기존 동치, slot=N→worker.lock.{N} 슬롯별 독립).
    lock_path = args.lock_path or str(_slot_lock_path(slot))

    # ① 단일 인스턴스 advisory 락 — 이 슬롯에 이미 워커가 떠 있으면 즉시 종료.
    lock = SingleInstanceLock(lock_path)
    if not lock.acquire():
        print(
            f"[worker] 이미 다른 워커가 이 슬롯(slot={slot})에 실행 중입니다"
            "(advisory 락 보유). 종료합니다.",
            file=sys.stderr,
        )
        return 1

    # PID 파일(관측 보조 — stale 위험이 있어 1급 방어 아님, advisory가 1급). 슬롯별 분리.
    pid_path = _slot_pid_path(slot)
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    worker = Worker(poll_interval=args.poll_interval, slot=slot)

    # ② graceful shutdown — SIGTERM/SIGINT 시 플래그 set(현재 tick 마치고 탈출).
    def _on_term(signum: int, frame: Any) -> None:
        print(f"[worker] 신호 {signum} 수신 — 현재 tick 마친 뒤 종료합니다.")
        worker.request_stop()

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    try:
        # ③ 부팅 복구(2a): running 고아 — 가역이면 이벤트 재생 재개, 비가역/미식별이면
        #   fail+Notify(B-HYBRID). 엔진 주입 없음(payload mock 플래그로 매 task 빌드).
        recovered = worker.recover_running_orphans()
        if recovered["resumed"] or recovered["failed"]:
            print(
                f"[worker] 부팅 복구: running 고아 재개 {recovered['resumed']} "
                f"/ fail+Notify {recovered['failed']}"
            )

        # ④ 워커 루프(무한 — 프로덕션). max_ticks=None.
        print(f"[worker] 기동(poll_interval={args.poll_interval}s). 큐 폴링 시작.")
        worker.run(max_ticks=args.max_ticks)
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
