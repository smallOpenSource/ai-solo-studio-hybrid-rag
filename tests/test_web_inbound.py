"""DC-2 — 디스코드 인바운드 엔드포인트(POST /api/inbound) 통합 테스트.

검증(계획 ralplan-office-polish-discord.md §4 DC-2 AC):
- 화면 에코: events에 TEXT_MESSAGE role=user content=text(★content==발화·접두 없음·
  C1 비오염) 1건 append + run_id == "discord-{message_id}".
- 응답 트리거: task_queue에 workflow=chat·query=text task 1건 enqueue.
- ★영속 멱등: 동일 message_id 재POST → events·queue 증가 0(duplicate). 별도
  EventStore 인스턴스로 재조회해도 흡수(재시작 무손실 시뮬).
- 빈 text/누락 → 400.
- 기존 /api/run·/api/inbox·SSE·WS 회귀 0(라우트 공존·정적 마운트 전 마운트).
- chat 워크플로 라운드트립(★비자가당착): mock 워커가 chat enqueue를 claim·run하면
  assistant TEXT_MESSAGE가 정확히 1건·content 비어있지 않음·CRM canned 문자열 비단언.

오프라인 결정론(실서버·LLM 0). tmp_path db 주입(실 파일 무오염). slow 마커 없음.
모든 대기에 타임아웃 가드(conftest 기본 30s + 명시 _HANG_GUARD_S).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from office.event_store import EventStore
from office.task_queue import TaskQueue
from web_server.app import create_app

_HANG_GUARD_S = 10.0


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트용 SQLite DB 경로(실 파일 오염 없음)."""
    return tmp_path / "office.db"


@pytest.fixture
def store(db_path: Path) -> EventStore:
    return EventStore(db_path=db_path)


def _user_text_events(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    """run_id의 TEXT_MESSAGE role=user 이벤트 payload dict 목록(seq 오름차순)."""
    rows = EventStore(db_path=db_path).read_rows(run_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        data = json.loads(row["payload_json"])
        if data.get("type") == "TEXT_MESSAGE" and data.get("role") == "user":
            out.append(data)
    return out


# ── 1. 화면 에코 + run_id 멱등키(C1 비오염) ──────────────────────────────────


class TestInboundEcho:
    """POST /api/inbound → user TEXT_MESSAGE append(content==발화·run_id=discord-{id})."""

    def test_inbound_appends_user_text_message(
        self, store: EventStore, db_path: Path
    ) -> None:
        """인바운드 → events에 user TEXT_MESSAGE 1건·content가 발화와 정확히 동일."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post(
                "/api/inbound",
                json={"text": "안녕하세요 봇", "message_id": "111"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["run_id"] == "discord-111"
        assert isinstance(body["task_id"], int)

        events = _user_text_events(db_path, "discord-111")
        assert len(events) == 1
        # ★C1 비오염: content엔 순수 발화만(접두 인코딩 [discord:...] 없음).
        assert events[0]["content"] == "안녕하세요 봇"
        assert "[discord:" not in events[0]["content"]

    def test_inbound_run_id_is_discord_message_id(
        self, store: EventStore, db_path: Path
    ) -> None:
        """에코 user 이벤트의 run_id == discord-{message_id}(멱등키·content 미사용)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            client.post("/api/inbound", json={"text": "hi", "message_id": "abc-9"})
        rows = EventStore(db_path=db_path).read_rows("discord-abc-9")
        assert len(rows) == 1
        assert rows[0]["run_id"] == "discord-abc-9"


# ── 2. 응답 트리거(chat enqueue·query seam) ──────────────────────────────────


class TestInboundEnqueue:
    """POST /api/inbound → task_queue에 workflow=chat·query=text enqueue."""

    def test_inbound_enqueues_chat_task_with_query(
        self, store: EventStore, db_path: Path
    ) -> None:
        """인바운드 → queued task 1건(workflow=chat·payload.query==발화)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post(
                "/api/inbound", json={"text": "오늘 날씨 어때", "message_id": "222"}
            )
        task_id = resp.json()["task_id"]

        queue = TaskQueue(db_path=db_path)
        queued = queue.list_by_status("queued")
        assert len(queued) == 1
        assert queued[0]["id"] == task_id
        assert queued[0]["workflow"] == "chat"
        payload = json.loads(queued[0]["payload_json"])
        assert payload.get("query") == "오늘 날씨 어때"


# ── 3. ★영속 멱등(재시작 무손실) ─────────────────────────────────────────────


class TestInboundIdempotency:
    """동일 message_id 재POST → events·queue 증가 0(run_id 영속 멱등)."""

    def test_duplicate_message_id_no_increase(
        self, store: EventStore, db_path: Path
    ) -> None:
        """같은 message_id 2회 → 2번째는 duplicate(append·enqueue 0)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            r1 = client.post("/api/inbound", json={"text": "한번", "message_id": "333"})
            r2 = client.post("/api/inbound", json={"text": "한번", "message_id": "333"})
        assert r1.json()["status"] == "accepted"
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"

        # events: user TEXT_MESSAGE 정확히 1건(2번째는 append 0).
        assert len(_user_text_events(db_path, "discord-333")) == 1
        # queue: chat task 정확히 1건(2번째는 enqueue 0).
        assert len(TaskQueue(db_path=db_path).list_by_status("queued")) == 1

    def test_duplicate_persists_across_store_instances(
        self, store: EventStore, db_path: Path
    ) -> None:
        """★재시작 무손실: 별도 EventStore 인스턴스 재조회로도 중복 흡수(영속 멱등).

        봇 재시작/크래시 후 같은 message_id 재전송을 시뮬한다 — 새 create_app/새
        store로 같은 db를 열어도 run_id가 SQLite에 영속이라 duplicate로 흡수된다.
        """
        app1 = create_app(store=store)
        with TestClient(app1) as client1:
            client1.post("/api/inbound", json={"text": "영속", "message_id": "444"})
        # 별도 store/app(재시작 시뮬) — 같은 db_path.
        app2 = create_app(store=EventStore(db_path=db_path))
        with TestClient(app2) as client2:
            r2 = client2.post(
                "/api/inbound", json={"text": "영속", "message_id": "444"}
            )
        assert r2.json()["status"] == "duplicate"
        assert len(_user_text_events(db_path, "discord-444")) == 1
        assert len(TaskQueue(db_path=db_path).list_by_status("queued")) == 1


# ── 4. 검증(빈/누락 → 400) ───────────────────────────────────────────────────


class TestInboundValidation:
    """빈 text·message_id 누락 → 400(append·enqueue 0)."""

    def test_empty_text_returns_400(self, store: EventStore, db_path: Path) -> None:
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/inbound", json={"text": "", "message_id": "555"})
        assert resp.status_code == 400
        # 부작용 0: 이벤트·큐 미생성.
        assert EventStore(db_path=db_path).read_since(0) == []
        assert TaskQueue(db_path=db_path).list_by_status("queued") == []

    def test_missing_message_id_returns_400(
        self, store: EventStore, db_path: Path
    ) -> None:
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/inbound", json={"text": "내용만"})
        assert resp.status_code == 400
        assert EventStore(db_path=db_path).read_since(0) == []

    def test_missing_text_returns_400(self, store: EventStore, db_path: Path) -> None:
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/inbound", json={"message_id": "666"})
        assert resp.status_code == 400


# ── 5. 기존 라우트 회귀 0(공존) ──────────────────────────────────────────────


class TestInboundCoexistence:
    """인바운드 라우터가 기존 /api/run·/api/inbox·SSE·WS와 공존(회귀 0)."""

    def test_run_route_still_works(self, store: EventStore, db_path: Path) -> None:
        """/api/run enqueue가 인바운드 마운트 후에도 정상(라우트 공존)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.post("/api/run", json={"workflow": "crm_notify"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "enqueued"

    def test_inbox_route_still_works(self, store: EventStore, db_path: Path) -> None:
        """/api/inbox 3분류 조회가 정상(회귀 0)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            resp = client.get("/api/inbox")
        assert resp.status_code == 200
        assert set(resp.json()) == {"review", "notify", "question"}

    def test_inbound_then_run_distinct_queues(
        self, store: EventStore, db_path: Path
    ) -> None:
        """인바운드(chat) + /api/run(crm_notify) → 둘 다 queued(서로 독립)."""
        app = create_app(store=store)
        with TestClient(app) as client:
            client.post("/api/inbound", json={"text": "대화", "message_id": "777"})
            client.post("/api/run", json={"workflow": "crm_notify"})
        queued = TaskQueue(db_path=db_path).list_by_status("queued")
        workflows = sorted(t["workflow"] for t in queued)
        assert workflows == ["chat", "crm_notify"]


# ── 6. chat 라운드트립(★비자가당착 — mock 워커가 assistant 응답 생성) ─────────


class TestInboundChatRoundtrip:
    """인바운드 → 워커(mock)가 chat task claim·run → assistant TEXT_MESSAGE 발행.

    ★비자가당착: assistant 응답이 정확히 1건·content 비어있지 않음만 단언한다
    (CRM canned "김민준" 문자열은 절대 단언하지 않는다 — mock 결정론 텍스트 비종속).
    """

    def test_worker_runs_chat_task_emits_assistant_message(
        self, store: EventStore, db_path: Path
    ) -> None:
        """인바운드 enqueue를 in-process 워커(mock)가 처리 → assistant TEXT_MESSAGE 1건."""
        from office.worker import Worker

        app = create_app(store=store)
        with TestClient(app) as client:
            # mock 플래그를 직접 enqueue payload에 넣을 수 없으므로(인바운드는 query만),
            # 인바운드로 user 에코 + chat enqueue를 만든 뒤 task payload에 mock을 주입한다.
            resp = client.post(
                "/api/inbound", json={"text": "안녕", "message_id": "888"}
            )
        task_id = resp.json()["task_id"]

        # chat task payload에 mock=True 병합(실서버·LLM 0 — 결정론 fake LLM 경로).
        from office.db import get_connection

        queue = TaskQueue(db_path=db_path)
        task = queue.get(task_id)
        payload = json.loads(task["payload_json"])
        payload["mock"] = True
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE tasks SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), task_id),
            )
            conn.commit()

        # in-process 워커 1 tick(claim→run chat 노드→complete). max_polls 바운드(hang 0).
        w = Worker(db_path=db_path, poll_interval=0.0)
        processed = w.run(max_ticks=1, max_polls=3)
        assert processed >= 1

        # assistant TEXT_MESSAGE가 정확히 1건·content 비어있지 않음(구조검증·CRM 비단언).
        assistant_msgs: list[dict[str, Any]] = []
        for _seq, pj in EventStore(db_path=db_path).read_since(0):
            data = json.loads(pj)
            if data.get("type") == "TEXT_MESSAGE" and data.get("role") == "assistant":
                assistant_msgs.append(data)
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"].strip() != ""

        # 워커 chat task는 완료(done) — 게이팅 없는 단일 chat 노드.
        assert queue.get(task_id)["status"] == "done"
