"""web_server/app.py — Phase 4a FastAPI 앱(얇은 게이트웨이).

이 모듈이 하는 일(US-002 / T2 범위):
- GET /events/stream : SSE StreamingResponse. EventFanout 인메모리 큐에서
  (seq, payload_json)을 받아 `id: {seq}\\ndata: {payload_json}\\n\\n` 형식으로
  흘린다. payload_json은 DB에 저장된 원문 그대로 패스스루한다(event_from_dict
  미경유 — 잘못된 reason 같은 값이 들어 있어도 스트림이 5xx로 안 떨어진다).
  Last-Event-ID 헤더(또는 query)가 있으면 그 seq 이후부터 재생하고 이어간다.
  주기적 `: ping` 코멘트(SSE keep-alive)로 프록시·연결 타임아웃을 막는다.
- 정적 서빙 : web/dist(프론트 빌드 산출, 없으면 빈 자리만) + work/(직원 이미지)
  StaticFiles 마운트.

설계(계획 §4.2·§4.3):
- create_app(store=None, fanout=None) 팩토리 — 테스트가 mock store/fanout을
  주입한다. host/port·정적 경로는 config/endpoint.yaml의 web 블록에서 읽는다
  (코드 하드코딩 0, 수용 18).
- 앱 시작/종료(lifespan)에 FanoutPoller(0.5초 폴링)를 등록/취소한다. 이
  asyncio 폴링은 단순 DB 증분 읽기일 뿐 워크플로 실행이 아니다(LLM 호출 0).
  워크플로(run_full)는 US-004가 전용 스레드로 띄운다 — 혼동 금지.

LLM은 직접 호출하지 않는다(워크플로 = office 경유). 이 모듈에는 라우터를
추가할 자리(US-003 inbox·US-004 run)가 있으나, T2는 stream·정적만 배선한다.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from core.config import get_web_config
from office.event_store import EventStore
from web_server.auth import (
    check_password,
    cookie_name,
    create_session,
    destroy_session,
    ttl_seconds,
    verify_session,
)
from web_server.inbound import create_inbound_router
from web_server.inbox import create_inbox_router
from web_server.runner import create_run_router
from web_server.cancel_all_router import create_cancel_all_router
from web_server.coop_router import create_coop_router
from web_server.sse import EventFanout, FanoutPoller
from web_server.ws import WsConnectionManager, serve_office_ws

# 미인증에도 열린 인증 엔드포인트(게이트 예외) — 나머지 /api/* 와 /events/stream 은 401.
_AUTH_OPEN_PATHS = frozenset({"/api/login", "/api/logout", "/api/auth/status"})


class _LoginBody(BaseModel):
    """POST /api/login 본문 — 암호 1개(no username·공유 비밀번호 게이트)."""

    password: str = ""


class _KnowledgeSearchBody(BaseModel):
    """POST /api/knowledge/search 본문 — 질의 + 결과 수 k.

    ★모듈 레벨 정의 필수: FastAPI는 지역(클로저) 클래스를 body 모델로 인식하지
    못해 쿼리 파라미터로 오인(422)한다. _LoginBody와 동일 레벨로 둔다.
    """

    query: str
    k: int = 5


# 프로젝트 루트 — 정적 경로(상대)를 절대경로로 변환하는 기준.
_PROJECT_ROOT = Path(__file__).parent.parent

# SSE 스트림이 새 이벤트를 확인하는 간격(초). fan-out 폴링(0.5초)보다 촘촘히
# 큐를 확인해 적재된 이벤트를 지연 없이 내보낸다. keep-alive ping은 이 간격을
# 여러 번 누적해 보낸다(_PING_INTERVAL).
_STREAM_TICK = 0.25
# keep-alive `: ping` 코멘트를 보내는 주기(초). 이 시간 동안 새 이벤트가
# 없으면 핑을 보내 연결이 살아 있음을 알린다.
_PING_INTERVAL = 15.0


def _resolve_static(rel_or_abs: str) -> Path:
    """정적 경로(설정값)를 프로젝트 루트 기준 절대경로로 변환한다."""
    p = Path(rel_or_abs)
    return p if p.is_absolute() else (_PROJECT_ROOT / p)


def _parse_last_event_id(request: Request) -> int:
    """재연결 커서(Last-Event-ID)를 헤더 또는 query에서 읽어 int로 돌려준다.

    SSE 표준은 재연결 시 마지막으로 받은 id를 `Last-Event-ID` 헤더로 보낸다.
    수동 호출·테스트 편의를 위해 query 파라미터 last_event_id 도 허용한다.
    값이 없거나 정수가 아니면 0(처음부터)으로 안전 폴백한다.
    """
    raw = request.headers.get("Last-Event-ID")
    if raw is None:
        raw = request.query_params.get("last_event_id")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _parse_idle_close_ms(request: Request) -> float | None:
    """idle_close_ms query 파라미터를 읽어 초 단위 float로 돌려준다(없으면 None).

    SSE는 본래 무기한으로 열려 있고, 브라우저가 떠나면(disconnect) 닫힌다
    (프로덕션 경로). 그러나 폴링형 클라이언트·통합 테스트는 "밀린 이벤트를
    다 받고 나서 더 안 오면 스스로 닫기"가 편하다. idle_close_ms를 주면 그
    밀리초 동안 신규 이벤트가 없을 때 스트림을 정상 종료한다. 안 주면 None
    (무기한 — disconnect로만 닫힘).

    값이 정수가 아니면 None으로 안전 폴백한다.
    """
    raw = request.query_params.get("idle_close_ms")
    if raw is None:
        return None
    try:
        return max(0, int(raw)) / 1000.0
    except (TypeError, ValueError):
        return None


def _format_sse(seq: int, payload_json: str) -> str:
    """하나의 이벤트를 SSE 프레임 문자열로 만든다(원문 패스스루).

    형식: `id: {seq}\\ndata: {payload_json}\\n\\n`
    payload_json은 DB 저장 원문 그대로 — 여기서 파싱·재직렬화하지 않는다(M6).
    """
    return f"id: {seq}\ndata: {payload_json}\n\n"


async def _event_stream(
    fanout: EventFanout,
    last_seq: int,
    request: Request | None = None,
    idle_close_ms: float | None = None,
) -> AsyncIterator[str]:
    """SSE 본문 제너레이터 — last_seq 이후 이벤트를 순서대로 흘린다.

    1) 먼저 큐에 이미 쌓인 last_seq 이후 이벤트를 재생(replay_from)한다
       (재연결 시 결손 0). 2) 그 다음 커서를 전진시키며 새 이벤트가 큐에
       적재되는 대로 내보낸다. 새 이벤트가 한동안 없으면 `: ping` 코멘트로
       keep-alive를 보낸다.

    종료 조건(둘 다 정상 종료):
    - request가 주어지고 클라이언트가 끊기면(browser disconnect) 닫는다
      — 프로덕션 경로(uvicorn이 is_disconnected를 알림).
    - idle_close_ms가 주어지고 그 시간 동안 신규 이벤트가 없으면 닫는다
      — 폴링 클라이언트·통합 테스트 경로(밀린 이벤트 다 받고 스스로 종료).
    둘 다 없으면 무기한(프로덕션에서 disconnect로만 닫힘).

    실제 DB 증분 적재는 FanoutPoller가 별도로 돌린다 — 이 제너레이터는 큐를
    읽기만 한다(중복 0 — 커서가 seq 오름차순으로만 전진).
    """
    cursor = last_seq
    since_ping = 0.0
    idle = 0.0
    while True:
        new_events = fanout.replay_from(cursor)
        if new_events:
            for seq, payload_json in new_events:
                yield _format_sse(seq, payload_json)
                cursor = seq
            since_ping = 0.0
            idle = 0.0
        else:
            since_ping += _STREAM_TICK
            idle += _STREAM_TICK
            if since_ping >= _PING_INTERVAL:
                # SSE 코멘트(`:`로 시작) — 클라이언트는 무시하지만 연결은 유지.
                yield ": ping\n\n"
                since_ping = 0.0
            if idle_close_ms is not None and idle >= idle_close_ms:
                return  # 유휴 종료(폴링·테스트) — 더 안 오면 정상 닫기.
        if request is not None and await request.is_disconnected():
            return  # 클라이언트가 떠남(프로덕션) — 정상 닫기.
        await asyncio.sleep(_STREAM_TICK)


def create_app(
    store: EventStore | None = None,
    fanout: EventFanout | None = None,
    auth_required: bool = False,
) -> FastAPI:
    """FastAPI 앱을 만든다(팩토리).

    Args:
        store: 이벤트 스토어. None이면 기본 state/office.db EventStore. 테스트는
               tmp_path db EventStore를 주입한다.
        fanout: 인메모리 fan-out. None이면 store로 새로 만든다. 테스트는 미리
                poll_once 해 둔 fanout을 주입할 수 있다.
        auth_required: True면 로그인 세션 게이트(미인증 /api·/events·/ws 거부)를 켠다.
               기본 False(기존 테스트·임베드 용도 후방호환) — 프로덕션(__main__)이 True로 켠다.
               로그인/로그아웃/상태 라우트는 auth_required와 무관히 항상 존재한다.

    Returns:
        라우트(stream·정적)와 lifespan(폴링 태스크)이 배선된 FastAPI 앱.

    참고(US-003/004가 라우터 추가할 자리):
        - app.state.store / app.state.fanout 로 주입물에 접근 가능.
        - inbox 라우터(US-003)·run 라우터(US-004)는 같은 store/fanout을 공유해
          이 함수에 include_router 로 붙인다.
    """
    if store is None:
        store = EventStore()
    if fanout is None:
        fanout = EventFanout(store)

    poller = FanoutPoller(fanout)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """앱 수명: 시작 시 폴링 태스크 기동, 종료 시 취소.

        ★Phase 6 6a Chunk 2: 부팅 복구(running 2b·suspended 재개)는 **office.worker가
        소유**한다 — 웹은 워크플로를 실행하지 않는 게이트웨이로 축소됐으므로 복구도 안
        한다(웹의 resume_task/recover_orphan_tasks 제거). 여기선 FanoutPoller만 띄운다
        (SSE fan-out은 여전히 웹 책임 — 워커가 office.db에 쓴 이벤트를 read_since로 픽업).
        """
        await poller.start()
        try:
            yield
        finally:
            await poller.stop()

    app = FastAPI(title="one_man_corp web", lifespan=lifespan)
    # 주입물·구동부를 app.state에 보관(US-003/004 라우터가 공유).
    app.state.store = store
    app.state.fanout = fanout
    app.state.poller = poller

    # Inbox API(US-003) 라우터 마운트 — 같은 store/DB를 공유한다(app.state.store).
    # 정적 마운트(_mount_static)는 "/"를 잡으므로 그 전에 붙인다.
    app.include_router(create_inbox_router())

    # 워크플로 게이트웨이(run) API 라우터 마운트 — enqueue/cancel/status만(실행은 워커).
    # 같은 store/DB를 공유한다(app.state.store). 정적 마운트보다 먼저 붙인다.
    app.include_router(create_run_router())

    # ★P1 step3.5: 전체 활성 일괄 취소(/api/run/cancel-all) — 멀티 워커풀(concurrency:N)에서
    # 동시 다중 활성 task를 한 번에 취소. 동결 runner.py 미접촉(별 라우터·별 경로). 같은 store/DB.
    app.include_router(create_cancel_all_router())

    # ★US-3: 협업 게임빌드 진입 라우터(/api/coop/build 202 + /api/coop/status/{id}).
    # BuildCoordinator(app.state.store DB 공유)를 백그라운드 데몬 스레드로 돌려 5단계
    # 직렬 체인을 자동화하고, 응답은 build_id만 즉시 202로 돌려준다(web-inline 금지).
    # 동결 runner.py 미접촉(별 라우터·별 prefix). 같은 store/DB(워커가 큐 드레인).
    app.include_router(create_coop_router())

    # DC-2 디스코드 인바운드 API 라우터 마운트 — POST /api/inbound(화면 에코 +
    # chat enqueue·run_id 영속 멱등). 같은 store/DB 공유. 정적 마운트보다 먼저 붙인다.
    app.include_router(create_inbound_router())

    # ── 로그인 세션 인증 (item5·인메모리 opaque 세션) ────────────────────────────
    # 게이트: 미인증 시 /api/*(login/logout/status 제외) + /events/stream → 401.
    # 정적 SPA(/)·자산(/work)은 통과(프론트가 로그인 화면 표시). /ws는 핸들러에서 검증.
    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        if auth_required:
            path = request.url.path
            needs_auth = (
                path.startswith("/api/") and path not in _AUTH_OPEN_PATHS
            ) or path == "/events/stream"
            if needs_auth and not verify_session(request.cookies.get(cookie_name())):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.post("/api/login")
    async def login(body: _LoginBody, request: Request) -> JSONResponse:
        """암호 검증(상수시간) → 성공 시 opaque 세션 쿠키 발급.

        ★US-G3-4: 틀린 비번도 200으로 응답({"ok": false}) — 401은 브라우저 콘솔 error를
        자동 기록하므로 억제 불가. login 라우트에서만 200으로 통일; 보호 라우트 401은 유지.
        """
        if not check_password(body.password):
            return JSONResponse({"ok": False}, status_code=200)
        token = create_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            key=cookie_name(),
            value=token,
            httponly=True,
            secure=(request.url.scheme == "https"),  # HTTPS 바인드에서만 Secure.
            samesite="strict",
            path="/",
            max_age=ttl_seconds(),
        )
        return resp

    @app.post("/api/logout")
    async def logout(request: Request) -> JSONResponse:
        """세션 무효화 + 쿠키 삭제(미인증이어도 안전)."""
        destroy_session(request.cookies.get(cookie_name()))
        resp = JSONResponse({"authed": False})
        resp.delete_cookie(key=cookie_name(), path="/")
        return resp

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict:
        """현재 쿠키의 인증 여부(프론트 게이트 폴링)."""
        return {"authed": verify_session(request.cookies.get(cookie_name()))}

    @app.get("/api/events/head")
    async def events_head(request: Request) -> dict:
        """현재 이벤트 스토어의 head(최대 seq)를 반환한다. 읽기전용.

        디스코드 어댑터가 콜드스타트(커서 유실) 시 0(전체 재방송) 대신 '지금부터'를
        고르도록 현재 head 를 알려준다 — 영구 부작용 싱크 재방송 스팸 방지. 비침투
        경계상 어댑터는 office 를 직접 import 하지 않고 이 HTTP 로 head 를 받는다.
        """
        return {"head_seq": request.app.state.store.head_seq()}

    @app.get("/events/stream")
    async def events_stream(request: Request) -> StreamingResponse:
        """이벤트 SSE 스트림. Last-Event-ID 있으면 그 seq 이후부터 재생.

        idle_close_ms query를 주면 그만큼 신규 이벤트가 없을 때 정상 종료한다
        (폴링·테스트). 안 주면 무기한(프로덕션 — 브라우저 disconnect로 닫힘).
        """
        last_seq = _parse_last_event_id(request)
        idle_close_ms = _parse_idle_close_ms(request)
        return StreamingResponse(
            _event_stream(
                fanout, last_seq, request=request, idle_close_ms=idle_close_ms
            ),
            media_type="text/event-stream",
            headers={
                # 프록시 버퍼링·캐시 방지(SSE 권장 헤더).
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # office-v2 US-0b: WebSocket 실시간 fan-out(SSE 병존·같은 fanout 큐).
    # 채팅 사이드 패널 양방향 입력을 위해 WS를 추가하되, 오피스 이벤트 스트림은
    # 컷오버까지 SSE(/events/stream)와 병존한다. 기존 라우트 0수정.
    ws_manager = WsConnectionManager()
    app.state.ws_manager = ws_manager

    @app.websocket("/ws")
    async def office_ws(websocket: WebSocket) -> None:
        # ★인증 게이트: accept() 이전에 쿠키 검증, 실패 시 close(1008)로 거부(ws.py 불변).
        if auth_required and not verify_session(websocket.cookies.get(cookie_name())):
            await websocket.close(code=1008)  # Policy Violation — 미인증.
            return
        await serve_office_ws(websocket, fanout, ws_manager)

    # ── Knowledge 검색 프록시 (US-G3-5) ─────────────────────────────────────
    # POST /api/knowledge/search → knowledge HTTP 서비스(:8810/search) 위임.
    # auth 게이트(_auth_gate 미들웨어)가 /api/* 를 보호하므로 별도 인증 처리 불필요.
    # knowledge 서비스 다운 시 502 + {"results": []} graceful 반환.

    _KNOWLEDGE_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=5.0, pool=5.0)

    @app.post("/api/knowledge/search")
    async def knowledge_search(
        body: _KnowledgeSearchBody, request: Request
    ) -> JSONResponse:
        """knowledge 검색 프록시 — :8810/search에 위임하고 결과를 반환한다.

        요청:  {"query": str, "k": int = 5}
        응답:  {"results": [{"text": str, "source": str, "score": float}, ...]}
        오류:  knowledge 서비스 다운/연결거부 시 502 + {"results": [], "error": "..."}
        """
        base_url = os.environ.get("KNOWLEDGE_SERVICE_URL", "http://127.0.0.1:8810")
        url = f"{base_url}/search"
        payload = {"query": body.query, "k": body.k}
        try:
            async with httpx.AsyncClient(timeout=_KNOWLEDGE_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            # SearchResponse 계약: {"results": [...], "count": int} → 프론트 계약으로 매핑.
            return JSONResponse({"results": data.get("results", [])})
        except httpx.HTTPError as exc:
            return JSONResponse({"results": [], "error": str(exc)}, status_code=502)

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """정적 서빙(web/dist·work/)을 마운트한다 — 경로는 endpoint.yaml에서.

    web/dist는 프론트 빌드 산출(아직 없으면 빈 디렉토리를 만들어 마운트만
    한다 — US-005가 채운다). work/는 직원 이미지(이미지 토글 플레이스홀더).
    """
    web_cfg = get_web_config()

    work_dir = _resolve_static(web_cfg["static_work"])
    if work_dir.is_dir():
        # /work/1.png 처럼 접근. 이미지라 html=False.
        app.mount("/work", StaticFiles(directory=str(work_dir)), name="work")

    # 게임 HTTP 서빙 — 모바일 폰 등에서 URL/QR 접속(기존엔 file:// 외 경로 없음). 더 구체적
    # 경로(/games/survivor)를 먼저, 그다음 /games, 마지막에 / 캐치올(Starlette 등록순 prefix 매칭).
    survivor_dir = _resolve_static("game/dist")
    if survivor_dir.is_dir():
        # Phaser 빌드(Vite·ES모듈)는 http 서빙 필요 — file:// 는 CORS 차단. vite base:'./' 라 서브경로 OK.
        app.mount(
            "/games/survivor",
            StaticFiles(directory=str(survivor_dir), html=True),
            name="game-survivor",
        )
    games_dir = _resolve_static("games")
    if games_dir.is_dir():
        # /games/ = 런처(games/index.html) · /games/clicker-mvp/ · /games/slide-puzzle/.
        app.mount(
            "/games", StaticFiles(directory=str(games_dir), html=True), name="games"
        )

    dist_dir = _resolve_static(web_cfg["static_dist"])
    # 빌드 전이라 없을 수 있다 — 빈 디렉토리를 만들어 마운트만 한다(빈 자리 OK).
    dist_dir.mkdir(parents=True, exist_ok=True)
    # SPA 진입점 서빙 — html=True 면 / 가 index.html로. 빌드 전엔 index가 없어
    # 404가 정상(정적 마운트는 살아 있음).
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="dist")
