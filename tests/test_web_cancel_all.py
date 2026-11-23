"""P1 step3.5 — POST /api/run/cancel-all 계약(전체 활성 일괄 취소·동결 runner.py 미접촉).

cancel_all_router(신규 비동결)가 running·suspended 전 task에 cancel_requested를 set한다.
부분 facade: 활성 있으면 202+count+task_ids, 없으면 200. task별 liveness 판정은 복제 안
함(runner.py /cancel과 별 경로). 같은 store/DB 공유(app.state.store._db_path).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from office.event_store import EventStore
from office.task_queue import TaskQueue
from web_server.app import create_app


def _client_and_queue(tmp_path):
    """동일 DB를 보는 TestClient + TaskQueue(웹·테스트 같은 SQLite)."""
    db = tmp_path / "office.db"
    store = EventStore(db_path=db)
    app = create_app(store=store)
    return TestClient(app), TaskQueue(db_path=db)


def test_cancel_all_no_active_returns_200(tmp_path):
    """활성(running/suspended) 0건이면 200 no_active_run·count 0(무해 no-op)."""
    client, _queue = _client_and_queue(tmp_path)
    resp = client.post("/api/run/cancel-all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_active_run"
    assert body["count"] == 0


def test_cancel_all_signals_running_and_suspended_only(tmp_path):
    """running·suspended 전부에 cancel_requested set, queued는 미설정(활성 아님)."""
    client, queue = _client_and_queue(tmp_path)
    r1 = queue.enqueue("wf")
    queue.claim()  # → running
    r2 = queue.enqueue("wf")
    queue.claim()  # → running
    s1 = queue.enqueue("wf")
    queue.claim()
    queue.suspend(s1)  # → suspended
    q1 = queue.enqueue("wf")  # queued(활성 아님 — 취소 대상 아님)

    resp = client.post("/api/run/cancel-all")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "cancelling-all"
    assert body["count"] == 3
    assert set(body["task_ids"]) == {r1, r2, s1}

    assert queue.is_cancel_requested(r1)
    assert queue.is_cancel_requested(r2)
    assert queue.is_cancel_requested(s1)
    assert not queue.is_cancel_requested(q1)  # queued는 미설정


def test_cancel_all_does_not_touch_terminal_tasks(tmp_path):
    """done/failed(종결) task는 활성 아님 — 일괄 취소 대상에서 제외."""
    client, queue = _client_and_queue(tmp_path)
    d1 = queue.enqueue("wf")
    queue.claim()
    queue.complete(d1)  # done
    f1 = queue.enqueue("wf")
    queue.claim()
    queue.fail(f1)  # failed

    resp = client.post("/api/run/cancel-all")
    assert resp.status_code == 200  # 활성 0
    assert resp.json()["count"] == 0
    assert not queue.is_cancel_requested(d1)
    assert not queue.is_cancel_requested(f1)
