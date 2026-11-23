"""P6a — §6.8 잔여 API 표면 (add-only Tier-2) 통합 테스트.

/health(RO·alias) 200 · /knowledge/search(RO·stub) 200 + 빈 q 400 · CORS preflight
(WA origin 200·미허용 차단) · docs_url=None(/docs 404) · rate-limit 429(N+1) ·
/events/stream rate-limit 미발동(연결 유지 중·M-5) · RO 토큰 /directives 403(회귀).

model STUBBED·네트워크 0(stub searcher·실 bge 미접촉). rate_clock 주입(결정론).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_WA_ORIGIN = (
    "https://studio.example.com:2817"  # 테스트 주입(리터럴 금지=create_app 인자 경유)
)


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_p6api.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


def _app(tmp_db: str, **kw):
    return create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        **kw,
    )


# ── /health (RO·alias·open probe) ──────────────────────────────────────────────
def test_health_alias_200_no_auth(tmp_db):
    # /health 는 /healthz alias — auth 켜져도 open probe(키 불요·200).
    app = _app(tmp_db, auth=True)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # /healthz 병존(둘 다 200·동일 본문 형태).
        assert client.get("/healthz").status_code == 200


# ── /knowledge/search (RO) ─────────────────────────────────────────────────────
def test_knowledge_search_200_with_stub(tmp_db):
    app = _app(tmp_db, auth=False)
    with TestClient(app) as client:
        r = client.get("/knowledge/search", params={"q": "retention"})
        assert r.status_code == 200
        body = r.json()
        assert body["q"] == "retention"
        assert "results" in body and isinstance(body["results"], list)
        assert body["count"] == 1  # stub_searcher returns one citable source


def test_knowledge_search_empty_q_400(tmp_db):
    app = _app(tmp_db, auth=False)
    with TestClient(app) as client:
        assert client.get("/knowledge/search", params={"q": ""}).status_code == 400
        assert client.get("/knowledge/search").status_code == 400  # missing q


def test_knowledge_search_ro_token_allowed(tmp_db):
    # READ 경로 → RO 토큰 허용(write 게이트 무관).
    app = _app(tmp_db, auth=True)
    with TestClient(app) as client:
        r = client.get(
            "/knowledge/search",
            params={"q": "retention"},
            headers={"x-api-key": "orch-ro-dev"},
        )
        assert r.status_code == 200


# ── CORS (WA origin only) ──────────────────────────────────────────────────────
def test_cors_preflight_allows_wa_origin(tmp_db):
    app = _app(tmp_db, auth=False, cors_origins=[_WA_ORIGIN])
    with TestClient(app) as client:
        r = client.options(
            "/health",
            headers={
                "Origin": _WA_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == _WA_ORIGIN


def test_cors_disallowed_origin_blocked(tmp_db):
    app = _app(tmp_db, auth=False, cors_origins=[_WA_ORIGIN])
    with TestClient(app) as client:
        r = client.options(
            "/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        # 미허용 origin → allow-origin 헤더 부재(차단). starlette 는 400 반환.
        assert r.headers.get("access-control-allow-origin") != "https://evil.example"


# ── docs_url=None (/docs 404) ──────────────────────────────────────────────────
def test_docs_disabled_404(tmp_db):
    app = _app(tmp_db, auth=False)  # enable_docs 기본 False
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404  # §6.8 docs_url=None
        assert client.get("/redoc").status_code == 404  # redoc_url=None


def test_docs_enabled_when_flagged(tmp_db):
    app = _app(tmp_db, auth=False, enable_docs=True)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200


# ── rate-limit (429 over limit) ────────────────────────────────────────────────
def test_rate_limit_429_on_n_plus_one(tmp_db):
    # 고정 clock(시간 정지) + limit=3 → 4번째 요청 429. /outbound/frames 는 RO read.
    clock = [1000.0]
    app = _app(
        tmp_db,
        auth=False,
        rate_limit=3,
        rate_window_s=60.0,
        rate_clock=lambda: clock[0],
    )
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/outbound/frames").status_code == 200
        # N+1 → 429(window 안·시간 정지).
        assert client.get("/outbound/frames").status_code == 429


def test_rate_limit_window_slides(tmp_db):
    # window 경과 후 다시 허용(시간 전진).
    clock = [1000.0]
    app = _app(
        tmp_db,
        auth=False,
        rate_limit=2,
        rate_window_s=10.0,
        rate_clock=lambda: clock[0],
    )
    with TestClient(app) as client:
        assert client.get("/outbound/frames").status_code == 200
        assert client.get("/outbound/frames").status_code == 200
        assert client.get("/outbound/frames").status_code == 429
        clock[0] += 11.0  # window 경과
        assert client.get("/outbound/frames").status_code == 200


# ── M-5: /events/stream + /health 는 rate-limit 면제 ────────────────────────────
def test_sse_and_health_exempt_from_rate_limit(tmp_db):
    # limit=1 이라도 면제 경로는 반복 호출에 429 안 남(연결-유지 SSE/probe 비차단).
    clock = [1000.0]
    app = _app(
        tmp_db,
        auth=False,
        rate_limit=1,
        rate_window_s=60.0,
        rate_clock=lambda: clock[0],
    )
    with TestClient(app) as client:
        # /health 반복(면제) — 모두 200.
        for _ in range(5):
            assert client.get("/health").status_code == 200
        # /events/stream 반복(idle_close_ms 로 바운드·면제) — 429 0.
        for _ in range(3):
            r = client.get("/events/stream", params={"idle_close_ms": "50"})
            assert r.status_code == 200
        # 비면제 경로는 limit=1 이므로 두번째 429(면제가 카운터를 소진 안 했음을 입증).
        assert client.get("/outbound/frames").status_code == 200
        assert client.get("/outbound/frames").status_code == 429


# ── RO 토큰 write 라우트 403(회귀) ──────────────────────────────────────────────
def test_ro_token_directives_403_regression(tmp_db):
    app = _app(tmp_db, auth=True)
    with TestClient(app) as client:
        r = client.post(
            "/directives",
            json={"workflow": "x"},
            headers={"x-api-key": "orch-ro-dev"},
        )
        assert r.status_code == 403  # write route requires RW
