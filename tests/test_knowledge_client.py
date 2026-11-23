"""Phase 8 P (옵션 B) — engine측 knowledge HTTP 클라이언트 단위 테스트 (engine env).

오프라인 전용 — engine env(qdrant-client 미설치)에서 실행. httpx.MockTransport로 서비스
응답을 흉내내 실 네트워크·실 서비스 0. knowledge_client는 httpx만 의존하므로 qdrant 없이도
import·동작한다(cross-env 갭 해소의 클라이언트 측).

검증:
  (1) import office.knowledge_client는 qdrant 없이 성공(격리).
  (2) ★connect 타임아웃 = 5s 고정(AC-P3·preflight 정신) — 모듈 _TIMEOUT 값 단언.
  (3) MockTransport 200 응답 → search()가 공유 계약 dict(results/count) 파싱.
  (4) k 인자가 요청 본문에 실린다.
  (5) 연결거부(ConnectError) → 예외 전파(엔진이 NODE_FAILED 처리·hang 0).
  (6) HTTP 500 → raise_for_status로 예외 전파.
  (7) real_knowledge_search_http(args, context) tool 형태 callable → 계약 dict.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

# ── (1) 지연/격리 import — qdrant 없이 성공 ──────────────────────────────────


def test_import_client_does_not_require_qdrant() -> None:
    """office.knowledge_client import는 qdrant 의존성을 건드리지 않는다(httpx만)."""
    import office.knowledge_client  # noqa: F401 — 에러 없으면 격리 성공


# ── (2) connect 타임아웃 = 5s 고정 단언 ──────────────────────────────────────


def test_connect_timeout_is_five_seconds() -> None:
    """★httpx connect 타임아웃 = 5s(AC-P3) — 서비스 다운 시 빠른 실패(hang 0)."""
    from office.knowledge_client import _TIMEOUT

    assert _TIMEOUT.connect == 5.0
    # read는 검색·리랭크 시간(넉넉) — ★라이브 검증서 실 bge 리랭크 top_n=20 ~52s 관측 →
    # 기존 30s가 매 검색 타임아웃이라 90s로 상향(connect=5s가 서비스다운 fast-fail 담당).
    assert _TIMEOUT.read == 90.0
    assert _TIMEOUT.write == 5.0
    assert _TIMEOUT.pool == 5.0


# ── (3)(4) MockTransport 200 → 계약 파싱 ─────────────────────────────────────

_RESPONSE = {
    "results": [
        {"text": "결과 본문", "source": "wiki/doc.md", "score": 0.87},
        {"text": "두 번째", "source": "wiki/other.md", "score": 0.42},
    ],
    "count": 2,
}


def test_search_parses_contract_dict() -> None:
    """MockTransport 200 응답 → search()가 {"results":[...],"count":int} 파싱."""
    from office.knowledge_client import search

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_RESPONSE)

    out = search("워크플로 엔진", k=3, transport=httpx.MockTransport(handler))

    assert set(out.keys()) == {"results", "count"}
    assert out["count"] == 2
    assert out["results"][0]["text"] == "결과 본문"
    assert out["results"][0]["source"] == "wiki/doc.md"
    assert out["results"][0]["score"] == 0.87
    # 요청은 /search로 가고 query/k가 본문에 실린다.
    assert captured["url"].endswith("/search")
    assert captured["body"]["query"] == "워크플로 엔진"
    assert captured["body"]["k"] == 3


def test_search_default_k_in_body() -> None:
    """k 생략 시 기본 5가 요청 본문에 실린다(SearchRequest 기본값)."""
    from office.knowledge_client import search

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [], "count": 0})

    search("질의", transport=httpx.MockTransport(handler))
    assert captured["body"]["k"] == 5


# ── (5)(6) 실패 경로 — 예외 전파(NODE_FAILED 토대) ───────────────────────────


def test_connection_refused_propagates() -> None:
    """연결거부(ConnectError) → 예외 전파(엔진이 NODE_FAILED 처리·hang 0, AC-P5)."""
    from office.knowledge_client import search

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(httpx.ConnectError):
        search("질의", transport=httpx.MockTransport(handler))


def test_http_500_propagates() -> None:
    """서비스 500 → raise_for_status로 예외 전파(조용한 실패 0)."""
    from office.knowledge_client import search

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(httpx.HTTPStatusError):
        search("질의", transport=httpx.MockTransport(handler))


# ── (7) tool 형태 callable ───────────────────────────────────────────────────


def test_real_knowledge_search_http_tool_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """real_knowledge_search_http(args, context) → args에서 query/k 추출→계약 dict."""
    import office.knowledge_client as kc

    captured: dict[str, Any] = {}

    def fake_search(query: str, k: int = 5, **_: Any) -> dict[str, Any]:
        captured["query"] = query
        captured["k"] = k
        return {"results": [], "count": 0}

    monkeypatch.setattr(kc, "search", fake_search)

    out = kc.real_knowledge_search_http({"query": "도구 형태", "k": 2}, context=None)
    assert out == {"results": [], "count": 0}
    assert captured["query"] == "도구 형태"
    assert captured["k"] == 2
