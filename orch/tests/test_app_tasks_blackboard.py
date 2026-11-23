"""P2-rest-B — /tasks graceful(REDIS 미설정) + RO 허용 + Blackboard 프로덕션 sink 배선.

정적(grep app.py) + 동적(라이브 org run 에서 sink 호출됨·spy) — "테스트에서만 sink" green-theater 차단.
model STUBBED·라이브 미접촉(create_app stub)·vault 미오염(spy 가 실제 스냅샷 대체).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher
from orch.tests.test_org_graph import make_stub


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_btest.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


# ── /tasks graceful (minor 1) ──────────────────────────────────────────────────
def test_tasks_graceful_empty_when_redis_disabled(tmp_db):
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=False,
    )
    with TestClient(app) as client:
        r = client.get("/tasks")
        assert r.status_code == 200  # 503 아님(graceful)
        assert r.json()["tasks"] == []


def test_tasks_accepts_ro_token_rejects_none(tmp_db):
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=True,
    )
    with TestClient(app) as client:
        # /tasks 는 READ 경로(_write_path=False) → RO 토큰 허용.
        assert (
            client.get("/tasks", headers={"x-api-key": "orch-ro-dev"}).status_code
            == 200
        )
        # 토큰 없음 → 401.
        assert client.get("/tasks").status_code == 401


# ── Blackboard 프로덕션 sink 배선 ──────────────────────────────────────────────
def test_sink_statically_wired_in_run_org_branch():
    # ★정적(Critic MAJOR): app.py _run org 분기가 blackboard_sink 를 호출(source 단언).
    src = Path(__file__).resolve().parents[1] / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "await blackboard_sink(" in text
    org_idx = text.index('elif kind == "org"')
    call_idx = text.index("await blackboard_sink(")
    assert call_idx > org_idx  # org 분기 이후(post-invoke) 호출


def test_sink_invoked_on_live_org_run(tmp_db, monkeypatch):
    # ★동적: 실제 org run 이 sink 를 호출함(라이브 _run 경로 실행 증거·vault 미오염 spy).
    import orch.app as appmod

    recorded: list[str] = []

    async def spy(values, store, *, proj, **kw):
        recorded.append(proj)
        return None

    monkeypatch.setattr(appmod, "blackboard_sink", spy)
    monkeypatch.setenv("ORCH_ORG_ROUTING", "replace")  # 신규 directive → org 그래프
    stub = make_stub(ceo="coo", coo_pick="research")
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub,
        strategy_caller=stub,
        searcher=stub_searcher,
        tracing=False,
        auth=False,
    )
    with TestClient(app) as client:
        r = client.post("/api/run", json={"workflow": "조직 지시 처리", "mock": False})
        assert r.status_code == 200
        for _ in range(120):  # bounded poll(fire-and-forget _run 완료 대기)
            if recorded:
                break
            time.sleep(0.05)
    assert recorded, "blackboard_sink 가 라이브 org run 에서 호출되지 않음(배선 끊김)"
    assert recorded[0]  # proj(thread_id) 전달됨
