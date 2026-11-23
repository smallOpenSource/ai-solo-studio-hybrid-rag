"""SR/renewal P8 US-8a-0 (E-1·CRITICAL-1) — provenance 회수 전파 테스트 (knowledge env).

P9 가 ``chunk.meta['provenance']`` 에 저장한 출처 신뢰표시가 **회수 경계까지** 살아남는지
검증한다 — 스팟라이팅(§11.1②)이 untrusted 를 선택 래핑하려면 이 신호가 회수 결과에
실려야 한다(소실=침묵붕괴·M0).

★green-theater 차단: pydantic 모델 단독이 아니라 **실 service.py FastAPI 왕복**으로
검증한다 — search.py → SearchResponse response_model 직렬화(JSON) → client parse 에서
provenance 가 생존해야 한다(오프라인 TestClient·실 qdrant/bge 0). 기본 None 무회귀 +
None fail-safe(legacy → 외부 컬렉션서 untrusted 간주) 앵커 포함.
"""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from core.knowledge_contract import SearchResponse, SearchResult
from knowledge.chunk import Chunk
from knowledge.service import create_app

_SRC = "crawl/external.md"


class _Store:
    """search_dense 가 고정 (Chunk, score) 목록을 돌려주는 결정론 mock."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks

    def search_dense(self, query: str, top_n: int = 20) -> list[tuple[Chunk, float]]:
        return [(c, 1.0 - 0.1 * i) for i, c in enumerate(self._chunks)]


class _Reranker:
    """rerank 가 입력 순서대로 내림차순 점수를 매기는 결정론 mock(실서버 형식)."""

    def rerank(self, query: str, documents: list[str]) -> dict[str, Any]:
        return {
            "results": [
                {"index": i, "score": float(len(documents) - i), "document": doc}
                for i, doc in enumerate(documents)
            ]
        }


def _client(chunks: list[Chunk]) -> TestClient:
    return TestClient(create_app(store=_Store(chunks), reranker=_Reranker()))


# ── (u2) ★실 service.py FastAPI 왕복: provenance 가 search→response_model→client 생존 ──


def test_provenance_field_exists_and_defaults_none() -> None:
    """SearchResult.provenance add-only·기본 None(기존 3필드 보존·소비자 무회귀)."""
    r = SearchResult(text="t", source="s", score=1.0)
    assert r.provenance is None
    # 기존 3필드 그대로
    assert r.text == "t" and r.source == "s" and r.score == 1.0


def test_provenance_survives_real_service_roundtrip() -> None:
    """★u2 핵심: P9 nested-dict provenance(untrusted)→search.py→FastAPI response_model
    JSON 직렬화→SearchResponse.model_validate(client parse) 까지 'untrusted' 가 생존."""
    chunk = Chunk(
        text="external scraped doc",
        source=_SRC,
        chunk_id=0,
        # P9 capture_and_index 가 저장하는 정확한 형태(ArtifactProvenance.model_dump dict).
        meta={"provenance": {"provenance": "untrusted", "source_url": "http://x.test"}},
    )
    with _client([chunk]) as client:
        resp = client.post("/search", json={"query": "doc", "k": 5})
    assert resp.status_code == 200
    body = resp.json()  # ← 실제 FastAPI response_model 직렬화 산출(JSON)
    assert body["results"][0]["provenance"] == "untrusted", body
    # client 측 parse 경로(office.knowledge_client 와 동일 model_validate→model_dump).
    parsed = SearchResponse.model_validate(body).model_dump()
    assert parsed["results"][0]["provenance"] == "untrusted", parsed


def test_trusted_provenance_survives_roundtrip() -> None:
    """trusted 도 동일 경로로 'trusted' 가 그대로 전파(스팟라이팅이 비래핑 결정 가능)."""
    chunk = Chunk(
        text="internal artifact",
        source="vault/note.md",
        chunk_id=0,
        meta={"provenance": {"provenance": "trusted", "source_url": None}},
    )
    with _client([chunk]) as client:
        body = client.post("/search", json={"query": "note", "k": 5}).json()
    assert body["results"][0]["provenance"] == "trusted", body


def test_legacy_chunk_no_meta_yields_none_no_regression() -> None:
    """★u6 일부: meta 없는 legacy 청크(pre-P9 ~178pt)→provenance=None(무회귀)."""
    chunk = Chunk(text="legacy corpus doc", source="wiki/old.md", chunk_id=0)
    with _client([chunk]) as client:
        body = client.post("/search", json={"query": "old", "k": 5}).json()
    # 회수는 정상(text/source/score 보존) + provenance 만 None(소비자 무회귀).
    r = body["results"][0]
    assert r["text"] == "legacy corpus doc"
    assert r["provenance"] is None, r


# ── (u6) None fail-safe: None/unknown → 외부 컬렉션서 untrusted 간주(침묵 비래핑 금지) ──


def test_none_fail_safe_is_untrusted() -> None:
    """provenance=None(legacy) → spotlight 가 untrusted 로 래핑(우회 방지·falsifiable)."""
    from orch.spotlight import is_untrusted, wrap_untrusted

    # fail-safe 판정
    assert is_untrusted(None) is True
    # 래핑 실증(침묵 비래핑이면 이 단언이 실패해 falsifiable)
    wrapped = wrap_untrusted(
        "legacy chunk body", source_url="wiki/old.md", provenance=None
    )
    assert "<untrusted_data" in wrapped
    assert "legacy chunk body" in wrapped


def test_unknown_provenance_value_is_untrusted() -> None:
    """알 수 없는 값(예: 빈 문자열·오타)도 fail-safe untrusted(trusted 만 신뢰)."""
    from orch.spotlight import is_untrusted

    assert is_untrusted("garbage") is True
    assert is_untrusted("") is True
    assert is_untrusted("trusted") is False  # 유일하게 비래핑
