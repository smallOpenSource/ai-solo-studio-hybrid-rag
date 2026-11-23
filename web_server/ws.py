"""web_server/ws.py — office-v2 US-0b: WebSocket 실시간 fan-out (SSE 병존).

기존 SSE(/events/stream)와 **같은 EventFanout 큐**를 소비해 동일한 OfficeEvent를
WebSocket으로도 흘린다. 채팅 사이드 패널의 양방향 입력을 위해 WS를 도입하되,
오피스 이벤트 스트림은 컷오버까지 SSE와 **병존**한다. 단일 워커(M2) 전제.

★불변: 새 이벤트 타입 0 — payload_json(DB 저장 원문)을 그대로 실어 보낸다.

리플레이 커서(WS엔 표준 Last-Event-ID 없음):
- 클라가 접속 직후 ``{"type":"hello","last_seq":N}``을 보내면 ``replay_from(N)``으로
  N+ 이후를 결손 0으로 재생(SSE ``_event_stream``과 동등). hello 없으면 last_seq=0.

이벤트 전달의 정확성(결손 0·중복 0·재연결 재생)은 **연결별 커서 폴링**
(``serve_office_ws`` → ``_send_loop``)이 책임진다 — SSE 제너레이터와 동일한 방식
(큐 상태 불변·커서만 오름차순 전진). ``WsConnectionManager``는 활성 연결 추적과
부수적 broadcast(관측·향후 글로벌 푸시)를 제공한다.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from web_server.sse import EventFanout

# WS 큐 폴링 간격(초) — SSE _STREAM_TICK과 동급의 저빈도 폴링.
_WS_TICK = 0.1
# hello(커서) 대기 타임아웃(초). 미수신 시 last_seq=0(큐 전부 재생).
_HELLO_TIMEOUT = 2.0


class WsConnectionManager:
    """활성 WebSocket 연결 집합·broadcast (단일 워커 인메모리)."""

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        """연결을 수락하고 활성 집합에 등록한다(accept는 여기서 한 번만)."""
        await ws.accept()
        self._active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        """활성 집합에서 제거한다(없으면 조용히 무시)."""
        self._active.discard(ws)

    async def broadcast(self, message: str) -> None:
        """활성 연결 전부에 text 전송. 실패한(끊긴) 연결은 집합에서 제거(자기치유)."""
        dead: list[WebSocket] = []
        for ws in list(self._active):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._active.discard(ws)

    @property
    def connection_count(self) -> int:
        """현재 활성 연결 수(테스트·관측용)."""
        return len(self._active)


def _frame(seq: int, payload_json: str) -> str:
    """WS 프레임 1개를 만든다 — ``{"seq", "event"}``.

    payload_json은 DB 저장 원문(OfficeEvent JSON)이다. seq를 붙여 클라가
    재연결 커서(hello.last_seq)로 쓸 수 있게 하고, payload는 event로 중첩한다
    (클라 isOfficeEvent가 ``.event``를 검사). ★새 이벤트 타입 도입 아님.
    """
    return json.dumps({"seq": seq, "event": json.loads(payload_json)})


async def _read_hello_cursor(ws: WebSocket) -> int:
    """접속 직후 첫 메시지가 hello면 last_seq를, 아니면/타임아웃이면 0을 돌려준다.

    hello가 아닌 첫 메시지(예: 즉시 채팅 입력)는 무시하고 0을 돌려준다(PoC:
    채팅 입력 처리는 후속 US). 결손 0 재생을 위한 커서 확정만 수행한다.
    """
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=_HELLO_TIMEOUT)
    except Exception:
        # 타임아웃·disconnect·비JSON 등은 전부 커서 0(전부 재생)으로 처리.
        return 0
    if isinstance(first, dict) and first.get("type") == "hello":
        try:
            return max(0, int(first.get("last_seq", 0) or 0))
        except (TypeError, ValueError):
            return 0
    return 0


async def _send_loop(ws: WebSocket, fanout: EventFanout, cursor: int) -> None:
    """연결별 커서로 fanout 큐를 폴링하며 신규 이벤트를 WS로 흘린다(결손0·중복0).

    연결 종료(disconnect/취소)는 정상 종료로 흡수 — 미회수 예외가 teardown 레이스의
    'Task exception was never retrieved'를 유발하므로 이 루프 안에서 삼키고 반환한다.
    """
    try:
        while True:
            new_events = fanout.replay_from(cursor)
            for seq, payload_json in new_events:
                await ws.send_text(_frame(seq, payload_json))
                cursor = seq
            await asyncio.sleep(_WS_TICK)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        # 끊긴 소켓 send(RuntimeError)·disconnect·취소 → 정상 종료(연결 종료 신호).
        return


async def _recv_loop(ws: WebSocket) -> None:
    """클라 메시지를 드레인하며 disconnect를 감지한다(채팅 입력 처리는 후속 US).

    disconnect는 정상 종료로 흡수(예외를 남기지 않음) — 미회수 WebSocketDisconnect는
    TestClient teardown 레이스에서 'Task exception was never retrieved'를 유발하므로 이
    루프 안에서 삼키고 깨끗이 반환한다.
    """
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


async def serve_office_ws(
    ws: WebSocket, fanout: EventFanout, manager: WsConnectionManager
) -> None:
    """WS 연결 1개를 처리한다 — hello 커서 → 연결별 송신/수신 동시 루프 → 정리.

    SSE ``_event_stream``과 동등한 연결별 커서 폴링으로 결손/중복 0을 보장한다.
    송신·수신 루프 중 하나가 끝나면(주로 disconnect) 나머지를 취소하고 정리한다.

    ★teardown 견고성(flake 봉합): 연결 종료/서버 셧다운으로 이 핸들러가 cancel되거나
    disconnect가 와도 (a) CancelledError/WebSocketDisconnect를 정상 종료로 흡수해 앱
    태스크가 깨끗이 반환하고(TestClient ``__exit__``의 portal future가 cancel로 깨지지
    않음 — `with websocket_connect` teardown 시 간헐 CancelledError 봉합), (b) finally에서
    두 백그라운드 태스크를 반드시 cancel+gather(return_exceptions=True)로 회수해 'Task
    exception was never retrieved'를 원천 차단한다.
    """
    await manager.connect(ws)
    send_task: asyncio.Task | None = None
    recv_task: asyncio.Task | None = None
    try:
        cursor = await _read_hello_cursor(ws)
        send_task = asyncio.create_task(_send_loop(ws, fanout, cursor))
        recv_task = asyncio.create_task(_recv_loop(ws))
        await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, asyncio.CancelledError):
        # 연결 종료/teardown 취소 → 정상 종료로 흡수(핸들러는 깨끗이 반환).
        pass
    finally:
        # 두 백그라운드 태스크를 반드시 취소·회수(미회수 예외 0·teardown 레이스 봉합).
        for task in (send_task, recv_task):
            if task is not None and not task.done():
                task.cancel()
        pending = [t for t in (send_task, recv_task) if t is not None]
        if pending:
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.CancelledError:
                # anyio cancel scope는 매 await마다 취소를 재전달한다(TestClient teardown의
                # cs.cancel). 자식은 이미 cancel됐고 CancelledError를 흡수·반환하므로 미회수
                # 예외 0 — 여기선 그 재전달만 삼켜 핸들러가 깨끗이 반환하게 한다.
                pass
        manager.disconnect(ws)
