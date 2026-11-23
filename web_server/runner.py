"""web_server/runner.py — 워크플로 게이트웨이(Phase 6 6a Chunk 2 — 웹 축소).

이 모듈이 하는 일(계획 ralplan-phase6-worker-agent.md 결정축2 W-2·3 X-1·§6 US-P6-1/2):
- POST /api/run : 브라우저 [워크플로 시작] 버튼이 부른다. body의 workflow·mock을
  **task_queue에 enqueue만** 한다(응답 status="enqueued"). ★워크플로를 웹 프로세스에서
  실행하지 않는다 — claim+run은 별도 OS 프로세스(office.worker)가 담당(장애 격리).
- POST /api/run/cancel : cancel을 **DB 신호**(tasks.cancel_requested)로 set한다. 활성
  판정 = "DB running/suspended task 존재 AND 워커 liveness 신선(heartbeat N초 이내)".
  활성이면 202 cancelling, 아니면(없음 또는 stale 고아) 200 — 거짓202 0(AC-P6-2a).
- GET /api/run/status : 인메모리 락 대신 **DB 조회**(running 존재·tasks 목록·
  heartbeat 신선도)로 진행 상태를 돌려준다.

★왜 웹이 실행하지 않는가(Phase 6 F2 — 장애 격리):
- Phase 4a(F1′)는 워크플로 실행 함수를 uvicorn 프로세스 내부 전용 스레드풀로 돌렸다.
  Phase 6은 그 실행 주체를 **별도 워커 프로세스**로 이전한다 — 워크플로 크래시가 웹을
  죽이지 않음. 웹은 enqueue + SSE/Inbox/api(이벤트 DB 조회)만 하는 게이트웨이로 축소.
- 같은 office.db(WAL)를 공유하므로 웹의 FanoutPoller가 워커가 쓴 이벤트를 read_since로
  픽업해 SSE→Discord로 흘린다(계약 보존 — 이벤트 11종·SSE·Inbox 무중단).

★cancel 메커니즘 교체(US-009 의미 보존, 내부만 교체):
- Phase 4a는 인메모리 취소 Event를 set→같은 프로세스 폴링이 확인. 워커가 별도 프로세스라
  인메모리 신호 도달 불가 → cancel을 DB 신호(tasks.cancel_requested)로 재배선(워커 폴링이
  매 폴 확인·탈출). HTTP 외부 계약(202/200)은 동결, 내부 메커니즘만 교체.
- ★거짓202 방지(liveness): DB running 존재만으로는 워커 크래시로 남은 running 고아(끊을
  폴링 루프 없음)에 거짓202를 낼 수 있다 → 활성판정에 "워커 heartbeat 신선"을 AND로 보강.

★복구는 웹이 하지 않는다(워커 소유): 부팅 복구(running 2b·suspended 재개)는 office.worker
가 담당한다(웹은 복구 진입점을 제거). app.py lifespan은 poller(SSE fan-out)만 띄운다.

LLM은 직접 호출하지 않는다(엔진 단일 관문 — 워커 경유). web_server 금지 토큰 0
(preflight·정적 단언). 인메모리 락·전용 스레드풀도 안 쓴다(웹 축소 — 실행 주체는 워커).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from office.task_queue import TaskQueue, WorkflowDisabledError

# ── cancel liveness 임계(거짓202 방지 — AC-P6-2a) ────────────────────────────
# 활성판정 = "DB running/suspended 존재 AND heartbeat 신선(이 임계 이내)". 워커는 매
# tick·매 폴에서 heartbeat를 갱신하므로(office.worker.update_heartbeat), 신선 임계는
# 워커 poll_interval의 배수로 둔다. N = poll_interval(기본 2.0s) × k(=3) = 6.0s —
# 워커가 살아 있으면 한 폴 안에 갱신하므로 6s 신선은 정상 운영에서 항상 충족되고,
# 워커 크래시(heartbeat 정지)면 6s 후 stale로 판정해 거짓202 대신 200을 낸다(R2-2).
_WORKER_POLL_INTERVAL_S = 2.0
_LIVENESS_K = 3
_LIVENESS_FRESH_S = _WORKER_POLL_INTERVAL_S * _LIVENESS_K  # 6.0s


def _db_path_of(request: Request) -> Any:
    """app.state.store의 DB 경로를 돌려준다(웹·워커 동일 SQLite 공유)."""
    return request.app.state.store._db_path


def _worker_is_live(queue: TaskQueue) -> bool:
    """워커 heartbeat가 신선(_LIVENESS_FRESH_S 이내)하면 True.

    heartbeat 행이 아직 없으면(워커 미기동) False — 활성 task가 DB에 있어도 그것을
    끊어 줄 워커가 없으므로 cancel은 200(거짓202 방지). 워커가 살아 있으면 매 tick/폴
    갱신하므로 정상 운영에선 항상 신선.
    """
    age = queue.heartbeat_age_seconds()
    return age is not None and age <= _LIVENESS_FRESH_S


def _active_task_id(queue: TaskQueue) -> int | None:
    """현재 활성(running 우선, 없으면 suspended) task의 id를 돌려준다. 없으면 None.

    동시성 1이라 활성 task는 최대 1건. running을 우선(실행 중), 없으면 suspended(승인
    대기 — 워커가 블로킹 폴링으로 보유 중이라 cancel 신호로 탈출 가능)를 본다.
    """
    running = queue.list_by_status("running")
    if running:
        return int(running[0]["id"])
    suspended = queue.list_by_status("suspended")
    if suspended:
        return int(suspended[0]["id"])
    return None


def create_run_router() -> APIRouter:
    """워크플로 게이트웨이(run) 라우터를 만든다(create_app이 include_router로 마운트).

    같은 store(app.state.store)의 DB를 공유해 enqueue/cancel/status가 워커와 한 SQLite를
    본다. 웹은 워크플로를 실행하지 않는다(워커 소유) — 전역 락·executor·스레드 없음.
    """
    router = APIRouter(prefix="/api/run", tags=["run"])

    @router.post("")
    def start_run(
        request: Request, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """워크플로를 task_queue에 enqueue만 한다(실행은 워커 — AC-P6-1b).

        body: {"workflow": "crm_notify", "mock": false}. workflow 생략 시 crm_notify
        기본. mock=true면 워커가 결정론 fake LLM으로 실행(검증용 — payload로 전달).

        ★payload 스키마(워커가 읽음): {"mock": bool}. 웹은 워크플로 실행 함수를 호출하지
        않고(전용 스레드 submit 0·워크플로 웹 프로세스 실행 0), 이 mock 플래그를 payload에
        실어 둔다 → 워커(office.worker._engine_for_task)가 claim 후 그 행의 payload.mock을
        읽어 실행에 반영한다(mock이면 결정론 fake LLM 엔진).
        """
        body = body or {}
        workflow = str(body.get("workflow", "crm_notify"))
        mock = bool(body.get("mock", False))
        # ★동적질문 messages seam(Phase 8 C·AC-C0): body의 query를 payload에 실어
        # 워커가 읽어 initial_state["messages"]로 시딩하게 한다(engine.py:212 → N1 LLM).
        # query 없으면 payload에 싣지 않아 기존 워크플로(crm_notify 등) payload 무변.
        query = body.get("query")

        db_path = _db_path_of(request)
        queue = TaskQueue(db_path=db_path)
        # ★enqueue만 — 워크플로 실행 0. mock 플래그를 payload에 실어 워커가 읽게 한다.
        payload: dict[str, Any] = {"mock": mock}
        if isinstance(query, str) and query:
            payload["query"] = query
        try:
            task_id = queue.enqueue(workflow, payload=payload)
        except WorkflowDisabledError as exc:
            # 라이브 비활성 워크플로(예: crm_notify·실 CRM 부재) → 409로 거부.
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return {
            "status": "enqueued",
            "task_id": task_id,
            "workflow": workflow,
            "mock": mock,
        }

    @router.get("/status")
    def run_status(request: Request) -> dict[str, Any]:
        """진행 상태를 **DB 조회**로 돌려준다(인메모리 락 제거 — 웹은 실행 안 함).

        running: tasks에 running 또는 suspended 행이 있으면 True(워커가 처리 중/보유 중).
                 ★Phase 4a는 인메모리 락 보유 여부였으나 웹이 실행을 안 하므로 DB로 전환.
        tasks  : running/suspended/queued 목록(진행 가시화 — 계약 보존).
        worker_live: 워커 heartbeat 신선 여부(관측 보조).
        """
        db_path = _db_path_of(request)
        queue = TaskQueue(db_path=db_path)
        running_tasks = queue.list_by_status("running")
        suspended_tasks = queue.list_by_status("suspended")
        running = bool(running_tasks or suspended_tasks)
        return {
            "running": running,
            "tasks": {
                "running": running_tasks,
                "suspended": suspended_tasks,
                "queued": queue.list_by_status("queued"),
            },
            "worker_live": _worker_is_live(queue),
        }

    @router.post("/cancel")
    def cancel_run(request: Request) -> JSONResponse:
        """진행 중 워크플로를 취소한다 — DB 신호 + liveness 판정(AC-P6-2a).

        ★메커니즘(US-009 의미 보존, 내부만 DB 신호로 교체): 활성 task의 행에
        cancel_requested=1을 set한다. 워커 폴링이 매 폴 그 플래그를 확인→set이면
        RUN_ERROR 발행 후 탈출→fail 마킹(락 해제 의미를 워커 측 상태전이가 계승).

        활성판정(거짓202 0):
        - 활성 run(DB running/suspended 존재) AND 워커 liveness 신선(heartbeat
          _LIVENESS_FRESH_S 이내) → request_cancel(id) + **202 cancelling**.
        - 활성 없음 → **200 no_active_run**(취소할 것이 없는 무해한 no-op).
        - 활성 있으나 liveness stale(워커 크래시 고아 — 끊을 폴링 루프 없음) →
          **200 stale_running**(복구는 워커 부팅 스캔 담당) — ★거짓202 0.
        """
        db_path = _db_path_of(request)
        queue = TaskQueue(db_path=db_path)

        task_id = _active_task_id(queue)
        if task_id is None:
            # 활성 task 없음 — 취소할 것이 없다(무해한 no-op).
            return JSONResponse(status_code=200, content={"status": "no_active_run"})
        if not _worker_is_live(queue):
            # 활성 task는 있으나 워커가 stale(크래시 고아) — 끊어 줄 폴링 루프가 없다.
            # 거짓202 대신 200(복구는 워커 부팅 스캔이 fail+Notify로 담당).
            return JSONResponse(status_code=200, content={"status": "stale_running"})
        # 활성 + 워커 신선 → DB 취소 신호 set(워커가 다음 폴에 탈출).
        queue.request_cancel(task_id)
        return JSONResponse(status_code=202, content={"status": "cancelling"})

    return router
