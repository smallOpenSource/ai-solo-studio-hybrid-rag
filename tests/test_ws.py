"""office-v2 US-0b — WebSocket fan-out(ws.py) 단위·통합 테스트.

오프라인 결정론(실서버·LLM 0). tmp_path db 주입으로 state/office.db 무오염.
slow 마커 없음. ★bare pytest 금지 — scripts/run_tests.sh로 구동.

범위(수용 기준):
- WsConnectionManager: connect(accept+등록)·disconnect·broadcast(전파·dead 자기치유)·count.
- _frame: seq + event 중첩(payload_json 원문 재파싱·새 이벤트 타입 0).
- _read_hello_cursor: hello.last_seq 파싱·비-hello/disconnect → 0.
- 통합(TestClient): seeded 이벤트가 hello 커서 재생으로 WS에 흘러나온다(SSE 동등·결손0).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from office.event_store import EventStore
from office.events import TextMessage
from web_server.app import create_app
from web_server.sse import EventFanout
from web_server.ws import (
    WsConnectionManager,
    _frame,
    _read_hello_cursor,
)


def _ts() -> str:
    return "2026-06-13T00:00:00"


class FakeWS:
    """테스트용 가짜 WebSocket — accept/send_text/receive_json만 흉내."""

    def __init__(self, to_receive: list | None = None, fail_send: bool = False) -> None:
        self.sent: list[str] = []
        self.accepted = False
        self._rx = list(to_receive or [])
        self._fail = fail_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self._fail:
            raise RuntimeError("dead socket")
        self.sent.append(message)

    async def receive_json(self):
        if self._rx:
            return self._rx.pop(0)
        raise WebSocketDisconnect()


# ── WsConnectionManager ──────────────────────────────────────────────────────


def test_ws_manager_connect_accepts_and_registers() -> None:
    mgr = WsConnectionManager()
    ws = FakeWS()
    asyncio.run(mgr.connect(ws))
    assert ws.accepted is True
    assert mgr.connection_count == 1


def test_ws_manager_disconnect_removes() -> None:
    mgr = WsConnectionManager()
    ws = FakeWS()
    asyncio.run(mgr.connect(ws))
    mgr.disconnect(ws)
    assert mgr.connection_count == 0
    # 없는 연결 disconnect는 조용히 무시.
    mgr.disconnect(ws)
    assert mgr.connection_count == 0


def test_ws_manager_broadcast_sends_to_all() -> None:
    mgr = WsConnectionManager()
    a, b = FakeWS(), FakeWS()
    asyncio.run(mgr.connect(a))
    asyncio.run(mgr.connect(b))
    asyncio.run(mgr.broadcast("hi"))
    assert a.sent == ["hi"]
    assert b.sent == ["hi"]


def test_ws_manager_broadcast_drops_dead_connection() -> None:
    """전송 실패한(끊긴) 연결은 broadcast가 활성 집합에서 제거(자기치유)."""
    mgr = WsConnectionManager()
    ok = FakeWS()
    dead = FakeWS(fail_send=True)
    asyncio.run(mgr.connect(ok))
    asyncio.run(mgr.connect(dead))
    asyncio.run(mgr.broadcast("x"))
    assert ok.sent == ["x"]
    assert mgr.connection_count == 1  # dead 제거됨


# ── _frame: seq + event 중첩 ─────────────────────────────────────────────────


def test_frame_nests_event_with_seq() -> None:
    payload = json.dumps({"type": "TEXT_MESSAGE", "content": "hi"})
    f = json.loads(_frame(7, payload))
    assert f["seq"] == 7
    assert f["event"]["type"] == "TEXT_MESSAGE"
    assert f["event"]["content"] == "hi"


# ── _read_hello_cursor ───────────────────────────────────────────────────────


def test_read_hello_cursor_parses_last_seq() -> None:
    ws = FakeWS(to_receive=[{"type": "hello", "last_seq": 5}])
    assert asyncio.run(_read_hello_cursor(ws)) == 5


def test_read_hello_cursor_non_hello_returns_zero() -> None:
    ws = FakeWS(to_receive=[{"type": "chat", "text": "hi"}])
    assert asyncio.run(_read_hello_cursor(ws)) == 0


def test_read_hello_cursor_disconnect_returns_zero() -> None:
    ws = FakeWS(to_receive=[])  # receive_json → WebSocketDisconnect
    assert asyncio.run(_read_hello_cursor(ws)) == 0


def test_read_hello_cursor_bad_last_seq_returns_zero() -> None:
    ws = FakeWS(to_receive=[{"type": "hello", "last_seq": "not-int"}])
    assert asyncio.run(_read_hello_cursor(ws)) == 0


# ── 통합: TestClient WebSocket 재생 ──────────────────────────────────────────


def _store(tmp_path: Path) -> EventStore:
    return EventStore(db_path=tmp_path / "office.db")


def test_ws_endpoint_replays_seeded_event(tmp_path: Path) -> None:
    """seeded 이벤트가 hello(last_seq=0) 커서 재생으로 WS에 그대로 흘러나온다."""
    store = _store(tmp_path)
    seq = store.append(TextMessage(run_id="r1", ts=_ts(), content="ws-hello"))
    fanout = EventFanout(store)
    fanout.poll_once()  # 큐에 적재(연결별 replay_from가 읽는다)

    app = create_app(store, fanout)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "last_seq": 0})
        msg = ws.receive_json()

    assert msg["seq"] == seq
    assert msg["event"]["type"] == "TEXT_MESSAGE"
    assert msg["event"]["content"] == "ws-hello"


def test_ws_endpoint_cursor_skips_already_seen(tmp_path: Path) -> None:
    """hello.last_seq를 주면 그 이후만 재생한다(재연결 결손·중복 0)."""
    store = _store(tmp_path)
    s1 = store.append(TextMessage(run_id="r2", ts=_ts(), content="첫째"))
    s2 = store.append(TextMessage(run_id="r2", ts=_ts(), content="둘째"))
    fanout = EventFanout(store)
    fanout.poll_once()

    app = create_app(store, fanout)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "last_seq": s1})  # s1 이후만 → s2
        msg = ws.receive_json()

    assert msg["seq"] == s2
    assert msg["event"]["content"] == "둘째"
