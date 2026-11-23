"""Phase 8 P (옵션 B) — 공유 스키마 왕복 계약테스트 (knowledge env·AC-P7).

★상호맹목 mock 차단: 서비스 핸들러와 클라이언트 parser가 각자 mock으로 통과하면
계약이 어긋나도 둘 다 green일 수 있다(mock vanity). 이 테스트는 인프로세스 ASGI 전송계층으로
**실 클라이언트(office.knowledge_client.search)를 인프로세스 실 서비스(knowledge.service)에
직접 연결**한다 → 클라이언트 직렬화(SearchRequest) → 서비스 핸들러 → mock store →
SearchResponse 직렬화 → 클라이언트 파싱의 **왕복 정합**을 단언한다.

(httpx 0.28의 httpx.ASGITransport는 async 전용(handle_async_request만)이라 동기 클라이언트
경로와 안 맞는다 → starlette TestClient의 동기 ASGI 전송계층(_TestClientTransport,
handle_request 보유)을 꺼내 실 클라이언트에 주입한다. 같은 인프로세스 앱에 동기로 붙는다.)

양측은 core.knowledge_contract(SearchRequest/SearchResult/SearchResponse)라는 단일 공유
모델로 묶인다 — 한쪽 계약이 바뀌면 이 왕복이 깨진다(맹목 mock으로 충족 불가).

knowledge env는 qdrant_client + httpx 양쪽을 보유하므로 클라이언트·서비스를 한 프로세스에서
함께 구동할 수 있다(이 테스트의 전제). 실 qdrant·실 bge는 mock store/reranker로 우회.
"""

from __future__ import annotations

from typing import Any

import httpx
from starlette.testclient import TestClient

from core.knowledge_contract import SearchRequest, SearchResponse
from knowledge.chunk import Chunk
from knowledge.service import create_app

_SRC = "wiki/roundtrip.md"
_DOC_A = "지식 검색 서비스 왕복 계약"
_DOC_B = "두 번째 후보 청크 본문"


class FakeStore:
    """search_dense가 고정 (Chunk, dense_score)를 돌려주는 결정론 mock."""

    def __init__(self, texts: list[str]) -> None:
        self._chunks = [
            Chunk(text=t, source=_SRC, chunk_id=i) for i, t in enumerate(texts)
        ]

    def search_dense(self, query: str, top_n: int = 20) -> list[tuple[Chunk, float]]:
        return [(c, 1.0 - 0.1 * i) for i, c in enumerate(self._chunks)]


class FakeReranker:
    """rerank가 주입 점수로 결정론 응답하는 mock."""

    def __init__(self, score_by_text: dict[str, float]) -> None:
        self.score_by_text = score_by_text

    def rerank(self, query: str, documents: list[str]) -> dict[str, Any]:
        return {
            "results": [
                {"index": i, "score": self.score_by_text[doc]}
                for i, doc in enumerate(documents)
            ]
        }


def _inprocess_transport() -> httpx.BaseTransport:
    """mock store/reranker를 주입한 실 서비스 앱을 인프로세스 동기 전송계층으로 감싼다.

    starlette TestClient의 동기 ASGI 전송계층(_TestClientTransport·handle_request)을
    꺼내 실 클라이언트(httpx.Client) 경로에 그대로 주입한다 → 실 서비스 핸들러에 동기로 붙음.
    """
    app = create_app(
        store=FakeStore([_DOC_A, _DOC_B]),
        reranker=FakeReranker({_DOC_A: 0.9, _DOC_B: 0.3}),
    )
    return TestClient(app)._transport


def test_roundtrip_client_to_service_contract() -> None:
    """실 클라이언트 → 실 서비스 핸들러 → mock store → 실 클라이언트 파싱 왕복 정합."""
    from office.knowledge_client import search

    out = search(
        "왕복 계약 질의",
        k=5,
        transport=_inprocess_transport(),
    )

    # 클라이언트가 돌려준 dict가 도구 출력 계약(results/count)을 만족.
    assert set(out.keys()) == {"results", "count"}
    assert out["count"] == 2
    # 서비스(리랭커 0.9>0.3) → 클라이언트 파싱 순서 정합: _DOC_A가 1등.
    assert out["results"][0]["text"] == _DOC_A
    assert out["results"][0]["source"] == _SRC
    assert out["results"][0]["score"] == 0.9
    for r in out["results"]:
        # P8 E-1: 기존 3필드 보존 + provenance add-only(왕복 보존).
        assert set(r.keys()) == {"text", "source", "score", "provenance"}


def test_roundtrip_shares_single_contract_model() -> None:
    """★양측이 core.knowledge_contract 단일 모델로 묶임 — 서비스/클라이언트 동일 참조."""
    import knowledge.service as svc
    import office.knowledge_client as kc

    # 서비스 핸들러와 클라이언트가 같은 SearchRequest/SearchResponse 클래스를 import.
    assert svc.SearchRequest is SearchRequest
    assert svc.SearchResponse is SearchResponse
    assert kc.SearchRequest is SearchRequest
    assert kc.SearchResponse is SearchResponse


def test_roundtrip_empty_query_contract() -> None:
    """빈 질의도 왕복 정합 — {"results":[],"count":0} 안전 계약(환각 가드 토대)."""
    from office.knowledge_client import search

    out = search("   ", transport=_inprocess_transport())
    assert out == {"results": [], "count": 0}
