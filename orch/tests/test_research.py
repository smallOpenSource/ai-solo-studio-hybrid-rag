"""US-9a-2 i1 — RPI Research 파이프라인 단위 테스트.

stub crawler(네트워크 0) + stub search_fn(고정 SearxResult 2개) +
FakeStore(qdrant dep-0) 조합으로 전체 파이프라인 결정론 검증.

qdrant_client가 없는 orch env에서도 전체 suite PASS — dep-0 원칙.
qdrant 실 통합 테스트(test_provenance_with_real_store)는 importorskip graceful skip.

검증 항목:
  (a) ingest_search_results 후 search_hybrid로 색인 내용 회수됨.
  (b) ★회수된 chunk.meta['provenance']['provenance'] == 'untrusted'
      + source_url == URL 비공허(§11.1 불변식).
  (c) 부정변이: untrusted+source_url None → ArtifactProvenance validator 차단.
  (d) urls / indexed 카운트 정합성.
  (e) 크롤 실패 graceful — 실패 URL 스킵·정상 URL 계속 처리.
  (f) 검색 실패 graceful — 빈 dict 반환.
  (g) max_results 종료성 바운드.
"""

from __future__ import annotations

import pytest

from orch.knowledge_lifecycle import ArtifactProvenance
from orch.research import ingest_search_results
from orch.searxng import SearxResult


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_URL_A = "https://example.test/article/a"
_URL_B = "https://example.test/article/b"


# ---------------------------------------------------------------------------
# Stub 크롤러
# ---------------------------------------------------------------------------


class _StubCrawler:
    """결정론 stub crawler — 네트워크 0."""

    def fetch(self, url: str) -> str:
        return f"# {url}\n내용 마크다운 — {url}"


class _FailCrawler:
    """fetch 시 항상 예외 — graceful 테스트용."""

    def fetch(self, url: str) -> str:
        raise RuntimeError(f"크롤 실패: {url}")


class _PartialCrawler:
    """_URL_A만 실패, _URL_B는 성공."""

    def fetch(self, url: str) -> str:
        if url == _URL_A:
            raise RuntimeError("의도적 실패")
        return f"# {url}\n정상 콘텐츠"


# ---------------------------------------------------------------------------
# Stub 검색 함수
# ---------------------------------------------------------------------------


def _stub_search(query: str, max_results: int = 10) -> list[SearxResult]:
    """고정 2-결과 검색 stub."""
    return [
        SearxResult(url=_URL_A, title="Article A", content="snippet A"),
        SearxResult(url=_URL_B, title="Article B", content="snippet B"),
    ][:max_results]


def _fail_search(query: str, max_results: int = 10) -> list[SearxResult]:
    raise RuntimeError("SearXNG 다운")


# ---------------------------------------------------------------------------
# FakeStore — qdrant dep-0, 결정론
# ---------------------------------------------------------------------------


class _FakeChunk:
    """search_hybrid 반환용 최소 Chunk-like 객체."""

    def __init__(self, text: str, source: str, meta: dict) -> None:
        self.text = text
        self.source = source
        self.meta = meta


class FakeStore:
    """qdrant 없이 동작하는 in-memory store.

    capture_and_index가 생성한 Chunk(from knowledge.chunk)를 받아 저장하고
    search_hybrid로 simple substring 매칭 반환.
    """

    def __init__(self) -> None:
        self._chunks: list[_FakeChunk] = []

    def upsert(self, chunks: list) -> int:
        """Chunk 객체(text·source·meta)를 저장."""
        for c in chunks:
            self._chunks.append(_FakeChunk(text=c.text, source=c.source, meta=c.meta))
        return len(chunks)

    def search_hybrid(
        self, query: str, top_n: int = 20
    ) -> list[tuple[_FakeChunk, float]]:
        """query가 text에 포함된 청크를 반환(결정론·관련성 무관)."""
        results = [(c, 1.0) for c in self._chunks if query.lower() in c.text.lower()]
        return results[:top_n]

    def all_chunks(self) -> list[_FakeChunk]:
        return list(self._chunks)


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


# ---------------------------------------------------------------------------
# 테스트 — dep-0 (FakeStore)
# ---------------------------------------------------------------------------


def test_ingest_returns_expected_counts(store: FakeStore) -> None:
    """(d) urls·indexed·results 카운트 정합."""
    result = ingest_search_results(
        "F2P game design",
        store=store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    assert result["results"] == 2
    assert len(result["urls"]) == 2
    assert _URL_A in result["urls"]
    assert _URL_B in result["urls"]
    assert result["indexed"] > 0


def test_indexed_content_retrievable(store: FakeStore) -> None:
    """(a) 색인 후 search_hybrid로 회수됨."""
    ingest_search_results(
        "article content",
        store=store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    hits = store.search_hybrid("내용 마크다운")
    assert len(hits) > 0, "색인된 청크가 search_hybrid로 회수되어야 함"


def test_provenance_untrusted_invariant(store: FakeStore) -> None:
    """(b) ★§11.1 불변식: 모든 청크 provenance==untrusted + source_url 비공허."""
    ingest_search_results(
        "provenance check",
        store=store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    chunks = store.all_chunks()
    assert chunks, "색인된 청크가 있어야 함"
    for chunk in chunks:
        prov = chunk.meta.get("provenance", {})
        assert prov.get("provenance") == "untrusted", (
            f"§11.1 위반: provenance={prov.get('provenance')!r} (expected 'untrusted')"
        )
        assert prov.get("source_url"), (
            f"§11.1 위반: source_url 비어 있음 — chunk.source={chunk.source!r}"
        )
        assert prov["source_url"] in (_URL_A, _URL_B), (
            f"source_url={prov['source_url']!r}이 알려진 URL이 아님"
        )


def test_negative_mutation_no_trusted_provenance(store: FakeStore) -> None:
    """(c-2) 부정변이: ingest_search_results는 trusted provenance를 절대 색인하지 않음(§11.1)."""
    ingest_search_results(
        "trusted check",
        store=store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    for chunk in store.all_chunks():
        prov = chunk.meta.get("provenance", {})
        assert prov.get("provenance") != "trusted", (
            "ingest_search_results가 trusted provenance를 색인하면 안 됨 (§11.1 MAJOR-A)"
        )


def test_negative_mutation_untrusted_without_source_url_rejected() -> None:
    """(c-1) 부정변이: untrusted + source_url=None → ArtifactProvenance validator 차단."""
    with pytest.raises((ValueError, Exception)):
        ArtifactProvenance(provenance="untrusted", source_url=None)


def test_crawl_failure_all_graceful(store: FakeStore) -> None:
    """(e) 크롤 전체 실패 — 예외 누수 없이 빈 결과."""
    result = ingest_search_results(
        "fail",
        store=store,
        crawler=_FailCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    assert result["results"] == 0
    assert result["indexed"] == 0
    assert result["urls"] == []


def test_crawl_failure_partial_graceful(store: FakeStore) -> None:
    """(e) 부분 크롤 실패 — 실패 URL 스킵·나머지 URL 정상 색인."""
    result = ingest_search_results(
        "partial",
        store=store,
        crawler=_PartialCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    assert result["results"] == 1
    assert _URL_B in result["urls"]
    assert _URL_A not in result["urls"]
    assert result["indexed"] > 0


def test_search_failure_graceful(store: FakeStore) -> None:
    """(f) 검색 실패 — 예외 누수 없이 빈 결과."""
    result = ingest_search_results(
        "fail",
        store=store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_fail_search,
    )
    assert result == {"urls": [], "indexed": 0, "results": 0}


def test_max_results_bound(store: FakeStore) -> None:
    """(g) 종료성: max_results=1이면 URL 1개만 처리."""
    result = ingest_search_results(
        "bounded",
        store=store,
        crawler=_StubCrawler(),
        max_results=1,
        search_fn=_stub_search,
    )
    assert result["results"] == 1
    assert len(result["urls"]) == 1


def test_source_url_matches_crawled_url(store: FakeStore) -> None:
    """source_url이 실제 크롤된 URL과 일치함(provenance 추적성)."""
    ingest_search_results(
        "url match",
        store=store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    for chunk in store.all_chunks():
        prov = chunk.meta.get("provenance", {})
        # source_url == chunk.source (동일 URL)
        assert prov.get("source_url") == chunk.source, (
            f"source_url({prov.get('source_url')!r}) != chunk.source({chunk.source!r})"
        )


# ---------------------------------------------------------------------------
# 실 KnowledgeStore 통합 테스트 — qdrant_client 있을 때만 실행
# ---------------------------------------------------------------------------


def test_provenance_with_real_store() -> None:
    """실 KnowledgeStore(:memory:) + FakeEmbedder로 search_hybrid 왕복 검증.

    qdrant_client 미설치 env(orch)에서는 graceful skip.
    """
    pytest.importorskip("qdrant_client")
    from knowledge.store import KnowledgeStore  # noqa: PLC0415
    from orch.tests.conftest import FakeEmbedder  # noqa: PLC0415

    real_store = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder(dim=1024))
    real_store.ensure_collection()

    ingest_search_results(
        "real store provenance",
        store=real_store,
        crawler=_StubCrawler(),
        max_results=2,
        search_fn=_stub_search,
    )
    hits = real_store.search_hybrid("내용 마크다운", top_n=10)
    assert hits, "실 store에서 회수 결과 있어야 함"
    for chunk, _ in hits:
        prov = chunk.meta.get("provenance", {})
        assert prov.get("provenance") == "untrusted"
        assert prov.get("source_url") in (_URL_A, _URL_B)
