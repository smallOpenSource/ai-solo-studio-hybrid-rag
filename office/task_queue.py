"""작업큐 + 순차 디스패처 — SQLite 단일 테이블, 동시성 1.

설계 원칙 (계획 §4.6, 전제 #6):
- tasks 테이블(state/office.db, WAL) — queued/running/suspended/done/failed
- 디스패처는 단일 순차 루프: claim 1건 → 핸들러 호출 → 상태전이
- 동시성 파라미터 없음 — asyncio / threading / concurrent.futures 일절 금지
- suspended 작업은 claim 대상에서 제외 (승인 대기 중)
- enqueue 추상이 외부 진입점 — 후일 APScheduler 어댑터가 이 메서드를 호출
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from office.db import get_connection

# ── 상태 상수 ────────────────────────────────────────────────────────────────
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUSPENDED = "suspended"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_VALID_STATUSES = {
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUSPENDED,
    STATUS_DONE,
    STATUS_FAILED,
}


class WorkflowDisabledError(RuntimeError):
    """라이브에서 비활성화된 워크플로를 enqueue하려 할 때 발생한다.

    실 백엔드(예: CRM)가 없어 더미로만 동작하는 워크플로를 프로덕션 enqueue에서
    차단하는 게이트. 호출부(예: POST /api/run)가 4xx로 변환한다.
    """

    def __init__(self, workflow: str) -> None:
        super().__init__(f"workflow disabled in this environment: {workflow}")
        self.workflow = workflow


def _live_disabled_workflows() -> frozenset[str]:
    """라이브 비활성 워크플로 집합 (OFFICE_DISABLED_WORKFLOWS 환경변수·쉼표구분).

    기본(미설정)=빈 집합 — 테스트/개발은 전 워크플로 허용. 라이브 서비스는 app.env
    에서 OFFICE_DISABLED_WORKFLOWS=crm_notify,smoke_email 로 더미 의존 워크플로를
    프로덕션 enqueue에서 차단한다(실 백엔드 연결 전까지).
    """
    raw = os.environ.get("OFFICE_DISABLED_WORKFLOWS", "")
    return frozenset(w.strip() for w in raw.split(",") if w.strip())


def _now_iso() -> str:
    """현재 시각 ISO 8601 문자열 (UTC)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TaskQueue:
    """SQLite 기반 작업큐.

    모든 메서드는 동기 호출만 제공한다. 동시 호출을 가정하지 않는다.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        disabled_workflows: "frozenset[str] | None" = None,
    ) -> None:
        """작업큐 초기화.

        Args:
            db_path: SQLite 파일 경로. None이면 state/office.db.
                     테스트는 tmp_path로 임시 경로를 주입한다.
            disabled_workflows: enqueue를 거부할 워크플로 집합. None이면
                     OFFICE_DISABLED_WORKFLOWS 환경변수로 결정(기본 빈 집합).
        """
        self._db_path = db_path
        self._disabled = (
            disabled_workflows
            if disabled_workflows is not None
            else _live_disabled_workflows()
        )
        self._init_table()

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────

    def _conn(self):
        """WAL+busy_timeout이 적용된 연결을 새로 반환한다."""
        return get_connection(self._db_path)

    def _init_table(self) -> None:
        """tasks 테이블이 없으면 생성한다(+ 기존 DB 마이그레이션·heartbeat 테이블).

        Phase 6 6a(additive): cancel_requested 컬럼 + worker_heartbeat 테이블을
        함께 보장한다. 신규 DB는 CREATE에 포함, 기존 DB는 ALTER로 마이그레이션
        (SQLite엔 ADD COLUMN IF NOT EXISTS가 없어 PRAGMA table_info 검사 필수).
        기존 행·status CHECK 제약은 무파손(컬럼 추가는 기존 데이터 보존).
        """
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow    TEXT    NOT NULL,
                    payload_json TEXT   NOT NULL DEFAULT '{}',
                    status      TEXT    NOT NULL DEFAULT 'queued'
                                        CHECK(status IN ('queued','running','suspended','done','failed')),
                    created_at  TEXT    NOT NULL,
                    updated_at  TEXT    NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    claimed_by_slot INTEGER
                )
            """)
            # ── 기존 DB 마이그레이션: cancel_requested 부재 시 ADD COLUMN ──
            # 신규 DB는 위 CREATE에 이미 포함되므로 no-op. 기존 DB(컬럼 없음)만
            # 1회 ALTER. table_info로 컬럼 존재를 먼저 확인(IF NOT EXISTS 없음).
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "cancel_requested" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN"
                    " cancel_requested INTEGER NOT NULL DEFAULT 0"
                )
            # ── P1 동시성(step1·additive): claimed_by_slot 소유 컬럼 ──
            # 멀티 OS 프로세스 워커풀에서 suspended/running 고아의 '보유구간 전체'
            # 배타성 + crash 복구 소유 식별용 슬롯 각인. nullable(기본 NULL=미점유)
            # → 기존 행·단일워커(claim slot 미지정) 경로 무변(behavior-preserving).
            # ★상세 설계·대안평가: .omc/plans/ralplan-p1-concurrency-prd.md
            if "claimed_by_slot" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN claimed_by_slot INTEGER")
            # ── heartbeat 테이블: 워커 liveness 신호(단일 행 id=1) ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS worker_heartbeat (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    ts TEXT NOT NULL
                )
            """)
            conn.commit()

    def _update_status(self, task_id: int, status: str) -> None:
        """task_id 행의 status와 updated_at을 갱신한다."""
        assert status in _VALID_STATUSES, f"알 수 없는 상태: {status}"
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, _now_iso(), task_id),
            )
            conn.commit()

    # ── 공개 API ───────────────────────────────────────────────────────────

    def enqueue(self, workflow: str, payload: dict[str, Any] | None = None) -> int:
        """작업을 큐에 추가하고 task_id를 반환한다.

        외부 진입점 추상 — 후일 APScheduler 어댑터가 이 메서드를 호출한다.

        Args:
            workflow: 워크플로 이름 (예: "crm_notify")
            payload:  워크플로에 전달할 임의 데이터 (직렬화 가능 dict)

        Returns:
            새 작업의 정수 id

        Raises:
            WorkflowDisabledError: workflow가 라이브 비활성 집합에 속하면.
        """
        if workflow in self._disabled:
            raise WorkflowDisabledError(workflow)
        now = _now_iso()
        payload_str = json.dumps(payload or {}, ensure_ascii=False)
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (workflow, payload_json, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (workflow, payload_str, STATUS_QUEUED, now, now),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def claim(self, slot: int | None = None) -> dict[str, Any] | None:
        """queued 작업 1건을 running으로 전환하고 반환한다.

        suspended 작업은 대상에서 제외한다.
        처리할 작업이 없으면 None을 반환한다.

        Args:
            slot: 멀티 워커풀 슬롯 번호. 주면 claimed_by_slot에 각인(소유 표시) →
                  crash 시 어느 슬롯의 running task인지 식별(고아 복구 소유 구분).
                  None(기본)이면 NULL 각인 = 단일워커 기존 동작과 동일(behavior-preserving).

        Returns:
            작업 dict(id, workflow, payload, status, created_at, updated_at, ...)
            또는 None
        """
        with self._conn() as conn:
            now = _now_iso()
            # 원자적 claim (Phase 5 US-P5-1 AC13): SELECT→UPDATE 사이의 read-then-write
            # race를 구조적으로 닫는다. 가장 오래된 queued 1건을 단일 UPDATE-FROM-SELECT로
            # running 전환하고 WHERE status='queued' 재확인 → WAL writer 직렬화 하에서
            # 정확히 1행만 잡힌다. RETURNING으로 그 행을 원자적으로 돌려받아(재조회 race
            # 없음) 반환 형태(id·workflow·payload_json·status·created_at·updated_at)를
            # 보존한다. 잡은 행이 없으면(queued 0건 또는 동시 주체가 먼저 가져감) None.
            #
            # ★이식성(NIT-1): RETURNING은 SQLite ≥3.35(2021) 필요. 배포·테스트가 모두
            #   conda env(one_man_corp-engine, SQLite 3.51.2)라 충족하나, 구 SQLite
            #   (예: 시스템 Python 3.26.0)에선 OperationalError. conda env 의무가 이
            #   버전 바닥을 보장한다. 구 SQLite 배포가 불가피하면 BEGIN IMMEDIATE +
            #   SELECT→UPDATE 폴백이 필요(버전 바닥 없음) — 배포 런북에 기록.
            row = conn.execute(
                "UPDATE tasks SET status=?, updated_at=?, claimed_by_slot=?"
                " WHERE id=(SELECT id FROM tasks WHERE status=? ORDER BY id LIMIT 1)"
                "   AND status=?"
                " RETURNING *",
                (STATUS_RUNNING, now, slot, STATUS_QUEUED, STATUS_QUEUED),
            ).fetchone()
            conn.commit()
            if row is None:
                return None
            return dict(row)

    def complete(self, task_id: int) -> None:
        """작업을 done으로 표시한다."""
        self._update_status(task_id, STATUS_DONE)

    def fail(self, task_id: int) -> None:
        """작업을 failed로 표시한다."""
        self._update_status(task_id, STATUS_FAILED)

    def suspend(self, task_id: int) -> None:
        """작업을 suspended로 표시한다 (승인 대기 등)."""
        self._update_status(task_id, STATUS_SUSPENDED)

    def resume(self, task_id: int) -> None:
        """suspended 작업을 queued로 되돌린다 (다음 claim 대상이 됨)."""
        self._update_status(task_id, STATUS_QUEUED)

    # ── P1 동시성(step1·additive): 고아 원자 클레임 + 슬롯 소유 ─────────────────
    # 멀티 OS 프로세스 워커풀에서 고아복구(suspended/running) 2경로가 list_by_status로
    # 비원자 스캔하면 N워커가 같은 고아를 동시 집어 이중재개한다. claim()의 원자
    # UPDATE-RETURNING 패턴을 복제해 claimed_by_slot IS NULL인 1건만 원자 점유→슬롯
    # 각인한다(보유구간 전체 배타성). ★claimed_by_slot 컬럼만 UPDATE — payload_json·
    # status는 절대 무접촉(2a 재개의 run_id·suspended_state 보존). 단일워커(미사용)
    # 경로엔 영향 0. 설계·대안평가: .omc/plans/ralplan-p1-concurrency-prd.md

    def _claim_orphan(self, slot: int, status: str) -> dict[str, Any] | None:
        """status(suspended|running) 고아 중 미점유(claimed_by_slot IS NULL) 1건을
        원자적으로 점유(slot 각인)하고 반환한다. 없으면 None.

        WAL writer 직렬화 하에서 동시 호출 시 정확히 1슬롯만 점유(2번째는 None).
        status·payload_json은 무접촉 — claimed_by_slot·updated_at만 갱신.
        """
        assert status in (STATUS_SUSPENDED, STATUS_RUNNING), (
            f"고아 클레임 불가 상태: {status}"
        )
        with self._conn() as conn:
            row = conn.execute(
                "UPDATE tasks SET claimed_by_slot=?, updated_at=?"
                " WHERE id=(SELECT id FROM tasks"
                "           WHERE status=? AND claimed_by_slot IS NULL ORDER BY id LIMIT 1)"
                "   AND status=? AND claimed_by_slot IS NULL"
                " RETURNING *",
                (slot, _now_iso(), status, status),
            ).fetchone()
            conn.commit()
            return dict(row) if row else None

    def claim_suspended(self, slot: int) -> dict[str, Any] | None:
        """suspended 고아 1건을 원자 점유(slot 각인)하고 반환한다(보유구간 배타성)."""
        return self._claim_orphan(slot, STATUS_SUSPENDED)

    def claim_running(self, slot: int) -> dict[str, Any] | None:
        """running 고아 1건을 원자 점유(slot 각인)하고 반환한다(이중 복구 차단)."""
        return self._claim_orphan(slot, STATUS_RUNNING)

    def release_slot(self, task_id: int, slot: int | None = None) -> None:
        """claimed_by_slot 각인을 해제(NULL)한다(종료·재큐 시 소유 반납).

        ★supervisor가 죽은 슬롯 각인을 청소할 때가 아니라, 워커가 정상 종료
        경로(complete/fail/resume)에서 호출한다. 멱등(이미 NULL이어도 무해).

        Args:
            slot: 주면 ★소유 가드 — claimed_by_slot=slot 일 때만 해제(다른 슬롯이
                  재점유한 각인은 보존). 워커는 자기 slot을 줘서 호출해야 안전
                  (타워커 재점유분을 실수로 지워 이중실행하는 step2 오용 차단·리뷰 #5).
                  None(기본)이면 무조건 해제(supervisor 죽은slot 청소 등 관리 경로용).
        """
        with self._conn() as conn:
            if slot is None:
                conn.execute(
                    "UPDATE tasks SET claimed_by_slot=NULL, updated_at=? WHERE id=?",
                    (_now_iso(), task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET claimed_by_slot=NULL, updated_at=?"
                    " WHERE id=? AND claimed_by_slot=?",
                    (_now_iso(), task_id, slot),
                )
            conn.commit()

    def release_all_slot_stamps(self, slot: int) -> int:
        """slot이 각인된 모든 task의 claimed_by_slot을 NULL로 되돌린다(P1 step3·supervisor 청소).

        ★supervisor 죽은slot 청소 전용: worker.lock.{slot} LOCK_NB 획득(=그 슬롯 워커가
        진짜 죽음)을 확인한 뒤에만 호출해야 한다(split-brain 방지 — 살아있는 슬롯의 각인을
        지우면 그 워커가 보유 중인 task를 재기동 워커가 가로채 이중실행). 호출 후 그 슬롯의
        running/suspended 고아는 claimed_by_slot IS NULL이 되어, 재기동한 워커-slot이
        claim_running/claim_suspended로 원자 재점유한다(보유구간 배타성 복원). 멱등.

        Returns:
            NULL로 되돌린 행 수.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE tasks SET claimed_by_slot=NULL, updated_at=?"
                " WHERE claimed_by_slot=?",
                (_now_iso(), slot),
            )
            conn.commit()
            return cur.rowcount

    def get(self, task_id: int) -> dict[str, Any] | None:
        """task_id로 작업 dict를 조회한다. 없으면 None."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        """특정 status인 작업 목록을 반환한다."""
        assert status in _VALID_STATUSES
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY id",
                (status,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── cancel 신호 (Phase 6 6a · X-1: tasks.cancel_requested 플래그) ─────────
    # US-009 cancel을 인메모리 threading.Event → DB 신호로 재배선(워커 별도
    # 프로세스라 인메모리 Event 도달 불가). 웹이 set, 워커 폴링이 매 폴 확인.

    def request_cancel(self, task_id: int) -> None:
        """task의 cancel_requested 플래그를 1로 set한다(취소 요청)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET cancel_requested=1, updated_at=? WHERE id=?",
                (_now_iso(), task_id),
            )
            conn.commit()

    def is_cancel_requested(self, task_id: int) -> bool:
        """task의 cancel_requested 플래그가 set(=1)인지 반환한다. 행 없으면 False."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if row is None:
            return False
        return bool(row["cancel_requested"])

    def clear_cancel(self, task_id: int) -> None:
        """task의 cancel_requested 플래그를 0으로 리셋한다(complete/fail 종료 시)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET cancel_requested=0, updated_at=? WHERE id=?",
                (_now_iso(), task_id),
            )
            conn.commit()

    # ── 워커 heartbeat (Phase 6 6a · liveness 신호) ─────────────────────────
    # 워커가 살아있음을 단일 행(id=1)에 ISO ts로 기록. 웹의 cancel 활성판정이
    # "DB running/suspended 존재 AND liveness 신선"으로 거짓202(워커 크래시 고아)
    # 를 피하게 한다(계획 §3 결정축3 (b)). 이번 Chunk는 워커 측 갱신만 제공.

    def update_heartbeat(self) -> None:
        """워커 liveness 신호를 현재 ISO 시각으로 갱신한다(단일 행 id=1, upsert)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO worker_heartbeat (id, ts) VALUES (1, ?)",
                (_now_iso(),),
            )
            conn.commit()

    def heartbeat_age_seconds(self) -> float | None:
        """마지막 heartbeat 이후 경과 초를 반환한다. 행이 없으면 None."""
        with self._conn() as conn:
            row = conn.execute("SELECT ts FROM worker_heartbeat WHERE id=1").fetchone()
        if row is None:
            return None
        import datetime

        last = datetime.datetime.fromisoformat(row["ts"])
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - last).total_seconds()

    def has_active_task(self) -> bool:
        """running 또는 suspended task가 하나라도 있으면 True(활성 run 판정 보조)."""
        return bool(
            self.list_by_status(STATUS_RUNNING) or self.list_by_status(STATUS_SUSPENDED)
        )


class Dispatcher:
    """단일 순차 루프 디스패처.

    한 번에 작업 1건만 처리한다. 동시성 파라미터 없음.
    asyncio / threading / concurrent.futures 일절 사용하지 않는다.

    사용 예::

        def handle(task):
            # 작업 처리 로직 ...
            return "done"  # or "suspend" / "fail"

        dispatcher = Dispatcher(queue, handle)
        dispatcher.run(max_ticks=10)
    """

    def __init__(
        self,
        queue: TaskQueue,
        handler: Any,
        poll_interval: float = 1.0,
    ) -> None:
        """디스패처 초기화.

        Args:
            queue:         TaskQueue 인스턴스
            handler:       callable(task_dict) → "done" | "suspend" | "fail"
                           핸들러는 동기 함수여야 한다.
            poll_interval: 처리할 작업이 없을 때 다음 poll까지 대기 시간(초).
                           동시성 파라미터가 아닌 순수 폴링 간격이다.
        """
        self._queue = queue
        self._handler = handler
        self._poll_interval = poll_interval

    def tick(self) -> bool:
        """작업 1건을 처리한다.

        Returns:
            True  — 작업을 처리했음
            False — 처리할 작업 없음 (queued 0건)
        """
        task = self._queue.claim()
        if task is None:
            return False

        task_id: int = task["id"]
        try:
            result = self._handler(task)
        except Exception:
            self._queue.fail(task_id)
            return True

        if result == "suspend":
            self._queue.suspend(task_id)
        elif result == "fail":
            self._queue.fail(task_id)
        else:
            # "done" 또는 기타 → 완료
            self._queue.complete(task_id)
        return True

    def run(self, max_ticks: int | None = None) -> None:
        """순차 루프를 실행한다.

        Args:
            max_ticks: 최대 tick 횟수. None이면 무한 루프.
                       테스트에서는 유한값을 넘겨 루프를 종료시킨다.
        """
        ticks = 0
        while True:
            if max_ticks is not None and ticks >= max_ticks:
                break
            processed = self.tick()
            ticks += 1
            if not processed:
                # 처리할 작업 없음 → poll_interval 만큼 대기
                if max_ticks is None:
                    time.sleep(self._poll_interval)
                else:
                    # 테스트용 유한 루프: 빈 tick도 카운트 후 종료
                    break
