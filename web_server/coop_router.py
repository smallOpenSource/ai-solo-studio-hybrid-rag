"""web_server/coop_router.py — US-3: 협업 게임빌드 진입 라우터(202 + 상태조회).

이 모듈이 하는 일(계획 `.omc/plans/ralplan-coop-game-windows.md` §2.A·§9 BINDING 4):
- POST /api/coop/build : body {"mock": bool, "query"?: str}를 받아 build_id(uuid)를
  발급하고, BuildCoordinator.run_build를 **백그라운드 데몬 스레드**에서 시작한 뒤
  **즉시 202**({"build_id": ...})를 반환한다. ★web-inline 금지(app.py 게이트웨이 철학):
  run_build는 5단계 직렬 폴링(실LLM 단계당 ~36s+)이라 동기로 부르면 HTTP 응답이 수 분간
  블로킹된다 → 데몬 스레드로 떼어 내고 응답은 build_id만 즉시 돌려준다.
- GET /api/coop/status/{build_id} : coordinator.get_status(build_id)를 그대로 노출한다
  (state/stage/run_ids/error). 미등록 build_id면 404. run_build가 진행 중에도
  코디네이터가 self._builds[build_id]를 갱신하므로(state=running·현재 stage·누적
  run_ids) 폴링으로 진행을 관측할 수 있다(US-2 인계 — get_status 백그라운드 가시화).

★코디네이터 인스턴스(app/팩토리 스코프 1개):
- create_app이 만든 store(app.state.store)의 DB를 공유한다 — 라이브 office.db에 enqueue
  하면 가동 중 worker 풀(slot0/1)이 그 큐를 드레인해 실제로 게임을 만든다(라이브 경로).
- 첫 build 요청 때 app.state에 1개를 만들어 캐시하고 이후 재사용한다(cancel_all_router가
  store._db_path를 reach-in하는 동형 — app.state.store 단일 출처). 동결 runner.py 미접촉
  (별 모듈·별 prefix /api/coop).

★라이브 타이밍(t_claim/t_run/poll_interval): 실LLM 단계 시간 여유로 넉넉히 둔다(기본
t_claim=60s·t_run=900s·poll_interval=2.0s). enqueue→claim은 워커 폴 간격(2s) 안에 일어나고
한 단계 실LLM 풀런은 수십 초~분이라 t_run을 크게 잡는다. 테스트는 작은 값을 주입한다.

신규 이벤트/노드/워크플로 0. enqueue·EventStore.read·BuildCoordinator만 쓴다.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from office.coordinator import BuildCoordinator
from office.task_queue import TaskQueue

# ── 라이브 기본 타이밍(실LLM 단계 시간 여유) ────────────────────────────────────
# enqueue→run_id 배정(claim)은 워커 폴 간격(2s) 안에 일어나므로 t_claim은 작아도 되나,
# 워커가 다른 task로 바쁘면 늦어질 수 있어 여유를 둔다. t_run은 한 단계 실LLM 풀런
# (수십 초~분)을 담을 만큼 크게. 테스트는 이 값을 작게 주입해 빠르게 결판낸다.
_LIVE_T_CLAIM = 60.0
_LIVE_T_RUN = 900.0
_LIVE_POLL_INTERVAL = 2.0


def _coordinator_for(
    request: Request,
    *,
    t_claim: float,
    t_run: float,
    poll_interval: float,
) -> BuildCoordinator:
    """app.state에 캐시된 BuildCoordinator를 돌려준다(팩토리 스코프 1개·없으면 생성).

    app.state.store의 DB(웹·워커 공유 SQLite)로 queue·event_store·db_path를 묶어
    코디네이터를 만든다. 한 app에 1개만 두어 build_id→상태 추적(_builds)이 status
    조회와 같은 인스턴스를 본다(다른 인스턴스면 get_status가 None이 됨).
    """
    existing = getattr(request.app.state, "coop_coordinator", None)
    if existing is not None:
        return existing  # type: ignore[return-value]
    store = request.app.state.store
    db_path = store._db_path  # 웹·워커 동일 SQLite(cancel_all_router 동형 reach-in).
    coordinator = BuildCoordinator(
        queue=TaskQueue(db_path=db_path),
        event_store=store,
        db_path=db_path,
        t_claim=t_claim,
        t_run=t_run,
        poll_interval=poll_interval,
    )
    request.app.state.coop_coordinator = coordinator
    return coordinator


def create_coop_router(
    *,
    t_claim: float = _LIVE_T_CLAIM,
    t_run: float = _LIVE_T_RUN,
    poll_interval: float = _LIVE_POLL_INTERVAL,
) -> APIRouter:
    """협업 게임빌드 진입 라우터를 만든다(create_app이 include_router로 마운트).

    같은 store(app.state.store)의 DB를 공유한다(워커와 한 SQLite). 동결 runner.py
    미접촉 — 별 모듈/별 prefix(/api/coop). 타이밍은 라이브 기본값을 쓰되 인자로
    주입 가능(테스트가 작은 값으로 빠르게 결판).
    """
    router = APIRouter(prefix="/api/coop", tags=["coop"])

    @router.post("/build")
    def start_build(
        request: Request, body: dict[str, Any] | None = None
    ) -> JSONResponse:
        """게임빌드 5단계 직렬 체인을 백그라운드로 시작하고 즉시 202를 반환한다.

        body: {"mock": bool, "query"?: str}. mock=true면 코디네이터가 enqueue payload에
        mock을 실어 워커가 결정론 fake LLM으로 실행한다(검증용). query가 있으면 payload에
        실어 워커가 messages 시딩(기존 관례).

        ★web-inline 금지: run_build는 5단계 직렬 폴링(블로킹)이라 동기로 부르면 응답이
        수 분간 막힌다 → 데몬 스레드로 떼어 내고 응답은 build_id만 즉시 202로 돌려준다.
        진행은 GET /api/coop/status/{build_id}로 폴링한다.
        """
        body = body or {}
        mock = bool(body.get("mock", False))
        query = body.get("query")
        query_str = query if isinstance(query, str) and query else None

        coordinator = _coordinator_for(
            request,
            t_claim=t_claim,
            t_run=t_run,
            poll_interval=poll_interval,
        )
        build_id = str(uuid.uuid4())

        # ★백그라운드 데몬 스레드: run_build는 동기·블로킹(폴링 5단계). 응답을 막지 않게
        #   떼어 내고 즉시 202. 데몬이라 프로세스 종료를 막지 않는다(웹 셧다운 시 정리).
        thread = threading.Thread(
            target=coordinator.run_build,
            args=(build_id,),
            kwargs={"mock": mock, "query": query_str},
            name=f"coop-build-{build_id}",
            daemon=True,
        )
        thread.start()

        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "build_id": build_id, "mock": mock},
        )

    @router.get("/status/{build_id}")
    def build_status(request: Request, build_id: str) -> JSONResponse:
        """build_id의 현재 상태(state/stage/run_ids/error)를 돌려준다(없으면 404).

        run_build 진행 중에도 코디네이터가 _builds[build_id]를 갱신하므로(state=running·
        현재 stage·누적 run_ids) 폴링으로 진행을 관측한다. 종료 시 state=done 또는 failed.
        """
        coordinator = _coordinator_for(
            request,
            t_claim=t_claim,
            t_run=t_run,
            poll_interval=poll_interval,
        )
        status = coordinator.get_status(build_id)
        if status is None:
            return JSONResponse(
                status_code=404,
                content={"status": "not_found", "build_id": build_id},
            )
        return JSONResponse(
            status_code=200,
            content={
                "build_id": build_id,
                "state": status["state"],
                "stage": status["stage"],
                "run_ids": status["run_ids"],
                "error": status["error"],
            },
        )

    return router
