"""Phase 8 P (옵션 B) — knowledge HTTP 검색 서비스 단위 테스트 (knowledge env).

오프라인 전용 — FastAPI TestClient(httpx 기반·서버 미기동) + 결정론 mock store/reranker
주입(실 qdrant·실 bge 0). 서비스는 knowledge.search.search를 무수정으로 감싸는 래퍼이므로
mock store/reranker로 search 파이프라인을 결정론 구동해 HTTP 경계만 검증한다.

검증:
  (1) POST /search → SearchResponse 공유 계약(results/count) 반환.
  (2) results 각 원소 = {text, source, score} 형식 보존.
  (3) 빈 질의·k<1 → {"results":[],"count":0} 안전 반환(환각 가드 토대).
  (4) GET /health → 200 + {"status":"ok"}(런북 prerequisite·AC-P6).
  (5) store/reranker 주입이 search 기본(실서버) 경로를 우회.
"""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from knowledge.chunk import Chunk
from knowledge.service import create_app

# ---------------------------------------------------------------------------
# 결정론 mock — 실 qdrant/실 bge 없이 dense 회수와 리랭크를 흉내낸다
# (test_knowledge_search.py 패턴 참고: store.search_dense / reranker.rerank 계약).
# ---------------------------------------------------------------------------

_SRC = "wiki/doc.md"
_DOC_A = "사과 바나나 포도 과일 가게"
_DOC_B = "자동차 엔진 바퀴 정비소"


class FakeStore:
    """search_dense가 고정 (Chunk, dense_score) 목록을 돌려주는 결정론 mock."""

    def __init__(self, texts: list[str]) -> None:
        self._chunks = [
            Chunk(text=t, source=_SRC, chunk_id=i) for i, t in enumerate(texts)
        ]

    def search_dense(self, query: str, top_n: int = 20) -> list[tuple[Chunk, float]]:
        # dense 점수는 역순(첫 청크가 높음) — 리랭커가 재정렬함을 보이기 위함.
        return [(c, 1.0 - 0.1 * i) for i, c in enumerate(self._chunks)]


class FakeReranker:
    """rerank가 주입 점수로 결정론 응답하는 mock(실서버 capture 형식)."""

    def __init__(self, score_by_text: dict[str, float]) -> None:
        self.score_by_text = score_by_text

    def rerank(self, query: str, documents: list[str]) -> dict[str, Any]:
        return {
            "results": [
                {"index": i, "score": self.score_by_text[doc], "document": doc}
                for i, doc in enumerate(documents)
            ]
        }


def _client(texts: list[str], scores: dict[str, float]) -> TestClient:
    """mock store/reranker를 주입한 서비스 TestClient를 만든다."""
    app = create_app(store=FakeStore(texts), reranker=FakeReranker(scores))
    return TestClient(app)


# ── (4) /health ──────────────────────────────────────────────────────────────


def test_health_returns_ok() -> None:
    """GET /health → 200 + {"status":"ok"} (감독·런북 prerequisite, AC-P6)."""
    with _client([_DOC_A], {_DOC_A: 0.9}) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── (1)(2) POST /search 계약 ─────────────────────────────────────────────────


def test_search_returns_contract_shape() -> None:
    """POST /search → SearchResponse 계약(results/count·각 원소 text/source/score).

    ★P8 E-1: provenance 필드가 add-only 로 추가됨 — 기존 3필드는 보존(subset)하고
    provenance 키가 함께 실린다(meta 없는 청크는 None). 소비자 무회귀(add-only).
    """
    with _client([_DOC_A, _DOC_B], {_DOC_A: 0.9, _DOC_B: 0.2}) as client:
        resp = client.post("/search", json={"query": "과일 사과", "k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"results", "count"}
    assert body["count"] == len(body["results"])
    assert body["count"] == 2
    for r in body["results"]:
        # 기존 3필드 보존(무회귀) + P8 E-1 provenance add-only.
        assert {"text", "source", "score"}.issubset(r.keys())
        assert set(r.keys()) == {"text", "source", "score", "provenance"}
        assert isinstance(r["text"], str)
        assert isinstance(r["source"], str)
        assert isinstance(r["score"], float)
        # meta 없는 청크(FakeStore) → provenance None.
        assert r["provenance"] is None
    # 리랭커 점수 내림차순 — _DOC_A(0.9)가 1등.
    assert body["results"][0]["text"] == _DOC_A
    assert body["results"][0]["source"] == _SRC


def test_search_respects_k_limit() -> None:
    """k=1이면 상위 1건만 반환(top-k 상한이 위임을 통해 적용)."""
    with _client([_DOC_A, _DOC_B], {_DOC_A: 0.9, _DOC_B: 0.2}) as client:
        resp = client.post("/search", json={"query": "과일", "k": 1})
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["text"] == _DOC_A


# ── (3) 빈 질의·k<1 안전 반환(환각 가드 토대) ────────────────────────────────


def test_search_empty_query_is_safe() -> None:
    """빈 질의 → {"results":[],"count":0} (search.py:74 안전 반환)."""
    with _client([_DOC_A], {_DOC_A: 0.9}) as client:
        resp = client.post("/search", json={"query": "   "})
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "count": 0}


def test_search_k_below_one_is_safe() -> None:
    """k<1 → {"results":[],"count":0} (search.py:74 안전 반환)."""
    with _client([_DOC_A], {_DOC_A: 0.9}) as client:
        resp = client.post("/search", json={"query": "과일", "k": 0})
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "count": 0}


def test_search_default_k_when_omitted() -> None:
    """k 생략 시 기본 5로 위임(SearchRequest.k 기본값)."""
    with _client([_DOC_A, _DOC_B], {_DOC_A: 0.9, _DOC_B: 0.2}) as client:
        resp = client.post("/search", json={"query": "과일 사과"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2
