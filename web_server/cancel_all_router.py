"""web_server/cancel_all_router.py — P1 step3.5: 전체 활성 워크플로 일괄 취소.

★동결 runner.py 미접촉: runner.py(동결)의 POST /api/run/cancel은 '단일 활성 task'만
취소한다(_active_task_id 1건·liveness 게이트). 멀티 OS 프로세스 워커풀(concurrency:N)에선
여러 task가 동시에 running/suspended일 수 있어 사용자에게 '전부 취소' 경로가 필요하다.
runner.py가 동결이라 거기 추가할 수 없으므로, 같은 prefix(/api/run)에 별 라우터로
POST /api/run/cancel-all을 신설한다(경로 cancel ≠ cancel-all 이라 비충돌).

★부분 facade 한계(정직 박제): 이 엔드포인트는 running·suspended 전 task에 DB 취소 신호
(cancel_requested=1)를 set하는 단순 facade다. runner.py /cancel의 정교한 task별 liveness
판정(202 cancelling vs 200 stale_running)을 복제하지 않는다 — 활성 task가 하나라도 있으면
202(cancelling-all), 없으면 200(no_active_run)만 구분한다. 워커 크래시 고아(stale)에 신호를
set해도 무해하다(cancel_requested는 워커 폴링이 소비하며 종료 시 clear_cancel로 잔재 0,
미소비 stale은 워커 부팅 스캔이 복구하므로 거짓 취소 부작용 0). 워커별 즉시성·정확한 stale
판정이 필요하면 runner.py /cancel을 task별로 호출한다(단일 경로).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from office.task_queue import TaskQueue


def _db_path_of(request: Request) -> Any:
    """app.state.store의 DB 경로를 돌려준다(웹·워커 동일 SQLite 공유·inbox.py 동형).

    ★동결 runner.py의 _db_path_of를 import하지 않고 동형 복제 — 동결 모듈 의존을 피한다
    (inbox.py도 같은 한 줄을 자체 보유). store._db_path는 EventStore의 DB 경로.
    """
    return request.app.state.store._db_path


def create_cancel_all_router() -> APIRouter:
    """전체 활성 취소 라우터를 만든다(create_app이 include_router로 마운트).

    같은 store(app.state.store)의 DB를 공유한다(runner와 동일 SQLite). 동결 runner.py
    미접촉 — 별 모듈/별 경로(/api/run/cancel-all).
    """
    router = APIRouter(prefix="/api/run", tags=["run"])

    @router.post("/cancel-all")
    def cancel_all(request: Request) -> JSONResponse:
        """running·suspended 전 task에 DB 취소 신호를 set한다(일괄 취소).

        활성(running/suspended) 전부에 request_cancel(cancel_requested=1)을 set한다. 워커
        (들)의 폴링이 매 폴 그 플래그를 확인→set이면 RUN_ERROR 발행 후 탈출→fail 마킹.
        멀티 워커풀에선 각 슬롯이 자기 보유 task의 신호를 독립 소비한다(보유구간 배타성).

        반환:
        - 활성 있음 → 202 {"status":"cancelling-all","count":N,"task_ids":[...]}.
        - 활성 없음 → 200 {"status":"no_active_run","count":0}.
        ★부분 facade(모듈 docstring): task별 liveness 판정은 복제 안 함(stale 신호 무해).
        """
        db_path = _db_path_of(request)
        queue = TaskQueue(db_path=db_path)
        active = queue.list_by_status("running") + queue.list_by_status("suspended")
        task_ids = [t["id"] for t in active]
        if not task_ids:
            return JSONResponse(
                status_code=200, content={"status": "no_active_run", "count": 0}
            )
        for tid in task_ids:
            queue.request_cancel(tid)
        return JSONResponse(
            status_code=202,
            content={
                "status": "cancelling-all",
                "count": len(task_ids),
                "task_ids": task_ids,
            },
        )

    return router
