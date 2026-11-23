"""작업큐 + 디스패처 단위 테스트 — US-003 수용 기준 3개.

테스트 범위 (계획 §5 Unit):
1. enqueue → claim → complete 상태전이
2. 실패 → failed 상태
3. suspend → resume (→ queued → 재claim)
4. suspended는 claim 대상 제외 단언
5. 동시성 코드 경로 부재 단언 — task_queue.py 소스에서
   asyncio|threading|concurrent.futures|ThreadPool|gather 패턴 grep
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from office.task_queue import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUSPENDED,
    Dispatcher,
    TaskQueue,
    WorkflowDisabledError,
)

# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def queue(tmp_path: Path) -> TaskQueue:
    """임시 DB를 사용하는 TaskQueue (실파일 오염 없음)."""
    return TaskQueue(db_path=tmp_path / "test.db")


# ── 1. enqueue → claim → complete 상태전이 ───────────────────────────────────


def test_enqueue_returns_task_id(queue: TaskQueue) -> None:
    """enqueue는 양의 정수 id를 반환한다."""
    task_id = queue.enqueue("test_workflow", {"key": "value"})
    assert isinstance(task_id, int)
    assert task_id > 0


# ── 라이브 비활성 워크플로 게이트 (OFFICE_DISABLED_WORKFLOWS) ──────────────────


def test_enqueue_disabled_workflow_raises(tmp_path: Path) -> None:
    """disabled_workflows에 든 워크플로 enqueue는 WorkflowDisabledError를 던진다."""
    q = TaskQueue(
        db_path=tmp_path / "t.db", disabled_workflows=frozenset({"crm_notify"})
    )
    with pytest.raises(WorkflowDisabledError):
        q.enqueue("crm_notify", {"mock": False})


def test_enqueue_non_disabled_ok(tmp_path: Path) -> None:
    """비활성 집합에 없는 워크플로는 정상 enqueue된다."""
    q = TaskQueue(
        db_path=tmp_path / "t.db", disabled_workflows=frozenset({"crm_notify"})
    )
    assert q.enqueue("research_summary", {"mock": False}) > 0


def test_enqueue_disable_via_env(tmp_path: Path, monkeypatch) -> None:
    """OFFICE_DISABLED_WORKFLOWS 환경변수(쉼표구분)로 비활성 집합이 결정된다."""
    monkeypatch.setenv("OFFICE_DISABLED_WORKFLOWS", "crm_notify, smoke_email")
    q = TaskQueue(db_path=tmp_path / "t.db")  # disabled_workflows=None → env
    with pytest.raises(WorkflowDisabledError):
        q.enqueue("smoke_email", {})
    assert q.enqueue("chat", {}) > 0


def test_enqueue_default_no_disable(tmp_path: Path, monkeypatch) -> None:
    """환경변수 미설정이면 전 워크플로 허용(기본 빈 집합) — 테스트/개발 무영향."""
    monkeypatch.delenv("OFFICE_DISABLED_WORKFLOWS", raising=False)
    q = TaskQueue(db_path=tmp_path / "t.db")
    assert q.enqueue("crm_notify", {}) > 0


def test_enqueue_claim_complete(queue: TaskQueue) -> None:
    """enqueue → claim → complete 전체 흐름 상태전이."""
    task_id = queue.enqueue("wf_a", {"x": 1})

    # enqueue 후 queued
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_QUEUED

    # claim → running
    claimed = queue.claim()
    assert claimed is not None
    assert claimed["id"] == task_id
    assert claimed["status"] == STATUS_RUNNING
    assert claimed["workflow"] == "wf_a"

    # complete → done
    queue.complete(task_id)
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_DONE


def test_claim_empty_queue_returns_none(queue: TaskQueue) -> None:
    """큐가 비어 있으면 claim은 None을 반환한다."""
    result = queue.claim()
    assert result is None


def test_claim_returns_oldest_first(queue: TaskQueue) -> None:
    """여러 작업이 있을 때 claim은 가장 먼저 들어온 작업을 반환한다."""
    id1 = queue.enqueue("wf_a")
    id2 = queue.enqueue("wf_b")  # noqa: F841

    claimed = queue.claim()
    assert claimed is not None
    assert claimed["id"] == id1


def test_payload_roundtrip(queue: TaskQueue) -> None:
    """payload가 JSON 직렬화→역직렬화 후 원본과 일치한다."""
    import json

    payload = {"name": "홍길동", "score": 42, "tags": ["a", "b"]}
    task_id = queue.enqueue("wf_payload", payload)
    task = queue.get(task_id)
    assert task is not None
    assert json.loads(task["payload_json"]) == payload


# ── 2. 실패 → failed ──────────────────────────────────────────────────────────


def test_fail_sets_status(queue: TaskQueue) -> None:
    """fail()은 작업 상태를 failed로 바꾼다."""
    task_id = queue.enqueue("wf_fail")
    queue.claim()
    queue.fail(task_id)
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_FAILED


def test_dispatcher_handler_exception_marks_failed(queue: TaskQueue) -> None:
    """핸들러가 예외를 던지면 디스패처가 작업을 failed로 표시한다."""
    queue.enqueue("wf_exc")

    def bad_handler(task):
        raise RuntimeError("의도적 오류")

    dispatcher = Dispatcher(queue, bad_handler)
    dispatcher.tick()

    tasks = queue.list_by_status(STATUS_FAILED)
    assert len(tasks) == 1


# ── 3. suspend → resume → queued → 재claim ───────────────────────────────────


def test_suspend_resume_flow(queue: TaskQueue) -> None:
    """suspend → resume → queued 상태전이 검증."""
    task_id = queue.enqueue("wf_suspend")

    # claim → running
    queue.claim()
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_RUNNING

    # suspend → suspended
    queue.suspend(task_id)
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_SUSPENDED

    # resume → queued
    queue.resume(task_id)
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_QUEUED


def test_resume_makes_task_claimable_again(queue: TaskQueue) -> None:
    """resume 후 같은 작업을 재claim할 수 있다."""
    task_id = queue.enqueue("wf_reclaim")

    # 첫 번째 claim
    claimed1 = queue.claim()
    assert claimed1 is not None
    assert claimed1["id"] == task_id

    # suspend → resume
    queue.suspend(task_id)
    queue.resume(task_id)

    # 두 번째 claim — 같은 작업을 다시 가져온다
    claimed2 = queue.claim()
    assert claimed2 is not None
    assert claimed2["id"] == task_id
    assert claimed2["status"] == STATUS_RUNNING


# ── 4. suspended는 claim 대상 제외 단언 ──────────────────────────────────────


def test_suspended_not_claimable(queue: TaskQueue) -> None:
    """suspended 작업은 claim 대상에서 제외된다."""
    task_id = queue.enqueue("wf_susp_only")
    queue.claim()  # running으로 전환
    queue.suspend(task_id)  # suspended

    # 큐에 queued 작업이 없으므로 claim은 None
    result = queue.claim()
    assert result is None


def test_suspended_skipped_when_queued_exists(queue: TaskQueue) -> None:
    """suspended 작업은 건너뛰고 queued 작업만 claim한다."""
    # 먼저 작업1을 enqueue → claim → suspend
    id1 = queue.enqueue("wf_susp")
    queue.claim()
    queue.suspend(id1)

    # 새 작업2 enqueue
    id2 = queue.enqueue("wf_new")

    # claim은 suspended(id1)를 건너뛰고 queued(id2)를 가져온다
    claimed = queue.claim()
    assert claimed is not None
    assert claimed["id"] == id2


# ── 5. 동시성 코드 경로 부재 단언 ─────────────────────────────────────────────


def test_no_concurrency_in_task_queue_source() -> None:
    """office/task_queue.py 소스에 동시성 관련 코드가 없음을 단언한다.

    금지 패턴:
    - asyncio  (asyncio.gather, asyncio.create_task 등)
    - threading (Thread, ThreadPool)
    - concurrent.futures (ThreadPoolExecutor, ProcessPoolExecutor)
    - ThreadPool (명시적 스레드풀)
    - gather (asyncio.gather 축약)
    """
    source_path = Path(__file__).parent.parent / "office" / "task_queue.py"
    assert source_path.exists(), f"소스 파일 없음: {source_path}"

    source = source_path.read_text(encoding="utf-8")

    # 금지 패턴 목록 (주석 포함 전체 텍스트 검색)
    # 주석에서 "금지"로 언급하는 내용은 실제 import/사용이 아니므로
    # 실제 import 구문 또는 호출 패턴만 검사한다
    forbidden_import_patterns = [
        r"^\s*import\s+asyncio",
        r"^\s*from\s+asyncio",
        r"^\s*import\s+threading",
        r"^\s*from\s+threading",
        r"^\s*import\s+concurrent",
        r"^\s*from\s+concurrent",
    ]
    forbidden_usage_patterns = [
        r"\basyncio\.gather\b",
        r"\basyncio\.create_task\b",
        r"\bThreadPoolExecutor\b",
        r"\bProcessPoolExecutor\b",
        r"\bThread\s*\(",
        r"\bthreading\.Thread\b",
    ]

    all_patterns = forbidden_import_patterns + forbidden_usage_patterns
    violations = []
    for pattern in all_patterns:
        matches = re.findall(pattern, source, re.MULTILINE)
        if matches:
            violations.append(f"패턴 '{pattern}' 발견: {matches}")

    assert not violations, "task_queue.py에 동시성 코드 경로가 존재한다:\n" + "\n".join(
        violations
    )


def test_dispatcher_is_sequential_no_concurrency_params(queue: TaskQueue) -> None:
    """Dispatcher 클래스에 동시성 파라미터(workers, concurrency 등)가 없다."""
    import inspect

    sig = inspect.signature(Dispatcher.__init__)
    param_names = list(sig.parameters.keys())

    # 동시성을 암시하는 파라미터 이름 금지
    concurrency_params = {
        "workers",
        "concurrency",
        "max_workers",
        "num_threads",
        "parallelism",
    }
    found = concurrency_params & set(param_names)
    assert not found, f"Dispatcher.__init__에 동시성 파라미터 발견: {found}"


# ── 6. Dispatcher tick / run 기본 동작 ──────────────────────────────────────


def test_dispatcher_tick_processes_one_task(queue: TaskQueue) -> None:
    """tick()은 작업 1건만 처리하고 True를 반환한다."""
    queue.enqueue("wf_tick1")
    queue.enqueue("wf_tick2")

    results: list[str] = []

    def handler(task):
        results.append(task["workflow"])
        return "done"

    dispatcher = Dispatcher(queue, handler)
    did_work = dispatcher.tick()

    assert did_work is True
    assert len(results) == 1  # 1건만 처리
    assert results[0] == "wf_tick1"


def test_dispatcher_tick_empty_returns_false(queue: TaskQueue) -> None:
    """큐가 비어 있으면 tick()은 False를 반환한다."""
    dispatcher = Dispatcher(queue, lambda t: "done")
    result = dispatcher.tick()
    assert result is False


def test_dispatcher_handler_suspend(queue: TaskQueue) -> None:
    """핸들러가 'suspend'를 반환하면 작업이 suspended가 된다."""
    task_id = queue.enqueue("wf_suspend_handler")

    dispatcher = Dispatcher(queue, lambda t: "suspend")
    dispatcher.tick()

    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_SUSPENDED


def test_dispatcher_run_processes_all_queued(queue: TaskQueue) -> None:
    """run(max_ticks=N)은 queued 작업을 순차적으로 모두 처리한다."""
    for i in range(3):
        queue.enqueue(f"wf_{i}")

    processed: list[int] = []

    def handler(task):
        processed.append(task["id"])
        return "done"

    dispatcher = Dispatcher(queue, handler)
    dispatcher.run(max_ticks=10)

    assert len(processed) == 3
    # 순차 처리 확인 — id 오름차순
    assert processed == sorted(processed)


def test_dispatcher_skips_suspended_during_run(queue: TaskQueue) -> None:
    """run() 중 suspended 작업은 건너뛰고 다음 queued를 처리한다."""
    id1 = queue.enqueue("wf_first")
    id2 = queue.enqueue("wf_second")

    call_order: list[int] = []

    def handler(task):
        call_order.append(task["id"])
        if task["id"] == id1:
            return "suspend"  # id1은 suspend
        return "done"

    dispatcher = Dispatcher(queue, handler)
    # tick 2회: 첫 tick(id1→suspend), 두 번째 tick(id2→done)
    dispatcher.tick()  # id1 → suspended
    dispatcher.tick()  # id2 → done

    assert call_order == [id1, id2]
    assert queue.get(id1)["status"] == STATUS_SUSPENDED
    assert queue.get(id2)["status"] == STATUS_DONE


# ── 7. claim 원자성 (Phase 5 US-P5-1 AC13 — 결정론, 타이밍 레이스 비의존) ─────


def test_claim_atomic_same_task_twice(queue: TaskQueue) -> None:
    """같은 task를 2회 claim 호출 → 1회만 dict, 2회째 None.

    claim이 단일 UPDATE-FROM-SELECT + RETURNING으로 원자적이라, 두 번째 호출은
    queued 행이 없어(첫 claim이 running 전환) None을 돌려준다. 타이밍 레이스에
    의존하지 않는 결정론 검증(순차 호출).
    """
    task_id = queue.enqueue("wf_atomic")

    first = queue.claim()
    assert first is not None
    assert first["id"] == task_id
    assert first["status"] == STATUS_RUNNING

    # 두 번째 claim — queued 행이 없으므로 None(이중 claim 불가).
    second = queue.claim()
    assert second is None


def test_claim_returns_full_row_shape(queue: TaskQueue) -> None:
    """claim 반환 dict가 기존 행 형태(id·workflow·payload·status·시각)를 보존한다."""
    queue.enqueue("wf_shape", {"k": "v"})
    claimed = queue.claim()
    assert claimed is not None
    for key in ("id", "workflow", "payload_json", "status", "created_at", "updated_at"):
        assert key in claimed, f"claim 반환에 {key} 키 누락"
    assert claimed["workflow"] == "wf_shape"
    assert claimed["status"] == STATUS_RUNNING


def test_claim_oldest_first_then_next(queue: TaskQueue) -> None:
    """연속 claim이 id 오름차순으로 정확히 1건씩 가져간다(원자화 후 순서 보존)."""
    id1 = queue.enqueue("wf_a")
    id2 = queue.enqueue("wf_b")
    id3 = queue.enqueue("wf_c")

    c1 = queue.claim()
    c2 = queue.claim()
    c3 = queue.claim()
    c4 = queue.claim()

    assert c1 is not None and c1["id"] == id1
    assert c2 is not None and c2["id"] == id2
    assert c3 is not None and c3["id"] == id3
    assert c4 is None


# ── 8. cancel 신호 (Phase 6 6a · X-1: tasks.cancel_requested 플래그) ──────────


def test_cancel_default_false(queue: TaskQueue) -> None:
    """새 task는 cancel_requested=False(기본값 0)."""
    task_id = queue.enqueue("wf_cancel")
    assert queue.is_cancel_requested(task_id) is False


def test_request_cancel_sets_flag(queue: TaskQueue) -> None:
    """request_cancel 후 is_cancel_requested=True."""
    task_id = queue.enqueue("wf_cancel")
    queue.request_cancel(task_id)
    assert queue.is_cancel_requested(task_id) is True


def test_clear_cancel_resets_flag(queue: TaskQueue) -> None:
    """clear_cancel 후 is_cancel_requested=False(set→clear 라운드트립)."""
    task_id = queue.enqueue("wf_cancel")
    queue.request_cancel(task_id)
    assert queue.is_cancel_requested(task_id) is True
    queue.clear_cancel(task_id)
    assert queue.is_cancel_requested(task_id) is False


def test_is_cancel_requested_missing_task_false(queue: TaskQueue) -> None:
    """존재하지 않는 task_id는 False(행 없음)."""
    assert queue.is_cancel_requested(99999) is False


def test_cancel_does_not_change_status(queue: TaskQueue) -> None:
    """cancel 플래그 set/clear는 status를 바꾸지 않는다(독립 컬럼)."""
    task_id = queue.enqueue("wf_cancel")
    queue.claim()  # running
    queue.request_cancel(task_id)
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == STATUS_RUNNING  # cancel은 status와 무관


# ── 9. heartbeat (Phase 6 6a · worker liveness) ─────────────────────────────


def test_heartbeat_age_none_before_update(queue: TaskQueue) -> None:
    """heartbeat 갱신 전에는 age가 None(행 없음)."""
    assert queue.heartbeat_age_seconds() is None


def test_update_heartbeat_sets_age(queue: TaskQueue) -> None:
    """update_heartbeat 후 age가 실수(>=0)로 반환된다."""
    queue.update_heartbeat()
    age = queue.heartbeat_age_seconds()
    assert age is not None
    assert age >= 0.0


def test_update_heartbeat_is_single_row(queue: TaskQueue) -> None:
    """update_heartbeat을 여러 번 호출해도 단일 행(id=1)만 유지된다(upsert)."""
    queue.update_heartbeat()
    queue.update_heartbeat()
    queue.update_heartbeat()
    from office.db import get_connection

    with get_connection(queue._db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM worker_heartbeat").fetchone()
    assert count["c"] == 1


def test_has_active_task(queue: TaskQueue) -> None:
    """running/suspended가 있으면 has_active_task=True, 없으면 False."""
    assert queue.has_active_task() is False
    task_id = queue.enqueue("wf_active")
    # queued만 있을 때는 활성 아님(running/suspended 기준).
    assert queue.has_active_task() is False
    queue.claim()  # running
    assert queue.has_active_task() is True
    queue.complete(task_id)  # done
    assert queue.has_active_task() is False


# ── 10. 마이그레이션 (cancel_requested 없는 기존 tasks 테이블 → ADD COLUMN) ──


def test_migration_adds_cancel_requested_column(tmp_path: Path) -> None:
    """cancel_requested 없는 구 tasks 테이블에 TaskQueue 초기화가 컬럼을 ADD한다.

    구 스키마(컬럼 부재)로 DB를 만든 뒤 TaskQueue(_init_table)가 PRAGMA table_info
    검사→ALTER로 마이그레이션하는지 검증한다. 기존 행은 보존되고 기본값 0이 채워진다.
    """
    import json

    from office.db import get_connection

    db_path = tmp_path / "legacy.db"
    # ① cancel_requested 없는 구 tasks 테이블 직접 생성 + 기존 행 1개.
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow    TEXT    NOT NULL,
                payload_json TEXT   NOT NULL DEFAULT '{}',
                status      TEXT    NOT NULL DEFAULT 'queued'
                                    CHECK(status IN ('queued','running','suspended','done','failed')),
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO tasks (workflow, payload_json, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            ("legacy_wf", json.dumps({"k": "v"}), "queued", "t0", "t0"),
        )
        conn.commit()
        cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "cancel_requested" not in cols_before  # 마이그레이션 전: 없음

    # ② TaskQueue 초기화 → _init_table이 ALTER로 컬럼 추가.
    queue = TaskQueue(db_path=db_path)

    with get_connection(db_path) as conn:
        cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "cancel_requested" in cols_after  # 마이그레이션 후: 있음

    # ③ 기존 행 보존 + 기본값 0(is_cancel_requested=False).
    rows = queue.list_by_status(STATUS_QUEUED)
    assert len(rows) == 1
    assert rows[0]["workflow"] == "legacy_wf"
    assert queue.is_cancel_requested(rows[0]["id"]) is False


def test_migration_idempotent(tmp_path: Path) -> None:
    """TaskQueue를 두 번 초기화해도 마이그레이션이 재실행되지 않는다(컬럼 중복 0)."""
    db_path = tmp_path / "twice.db"
    TaskQueue(db_path=db_path)  # 첫 초기화(신규 — CREATE에 컬럼 포함)
    # 두 번째 초기화: cancel_requested 이미 존재 → ALTER 건너뜀(에러 없이 통과).
    queue = TaskQueue(db_path=db_path)
    task_id = queue.enqueue("wf_twice")
    assert queue.is_cancel_requested(task_id) is False
