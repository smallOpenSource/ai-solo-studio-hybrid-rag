"""SR/renewal P9 US-9a-2 — RPI Research 진입점(§8·§11.1·§18.2).

SearXNG 검색 결과 URL → crawl4ai 크롤 → capture_and_index(provenance=untrusted) 색인 →
search_hybrid 회수 가능한 상태로 만드는 인입 파이프라인.

★모든 외부 인입 색인은 capture_and_index(untrusted)만 허용(§11.1 MAJOR-A):
  raw store.upsert / ingest 직접 호출 절대 금지.

종료성(P0): max_results 바운드·크롤 실패 graceful 스킵(예외 누수 0).
crawler·search_fn은 주입 seam — 테스트는 stub, 프로덕션은 실 구현 기본값.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from orch.knowledge_lifecycle import ArtifactProvenance, capture_and_index
from orch.searxng import SearxResult, search as _default_search

logger = logging.getLogger(__name__)


class CrawlerProtocol(Protocol):
    """크롤러 주입 seam — crawl4ai 또는 테스트 stub."""

    def fetch(self, url: str) -> str: ...


def _make_default_crawler() -> "CrawlerProtocol":
    """실 Crawl4aiCrawler 지연 생성(crawl env 필요·import 지연으로 orch env 보호)."""
    try:
        from knowledge.crawl import Crawl4aiCrawler  # type: ignore[import]

        return Crawl4aiCrawler()  # type: ignore[return-value]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Crawl4aiCrawler를 import할 수 없음 — crawl env에서 실행하거나 "
            "테스트 시 stub crawler를 주입하세요."
        ) from exc


def ingest_search_results(
    query: str,
    *,
    store: object,
    crawler: CrawlerProtocol | None = None,
    max_results: int = 3,
    search_fn: Callable[..., list[SearxResult]] = _default_search,
) -> dict:
    """검색 결과 URL을 크롤해 capture_and_index(untrusted)로 색인한다.

    Args:
        query: SearXNG 검색 쿼리.
        store: knowledge.store._Store Protocol 구현체(KnowledgeStore 또는 mock).
        crawler: 크롤러 인스턴스(None이면 Crawl4aiCrawler 지연 생성).
        max_results: 검색 결과 상한(종료성 보장).
        search_fn: 검색 함수 주입 seam(기본=orch.searxng.search).

    Returns:
        {"urls": [...], "indexed": 총_chunk수, "results": 처리된_URL_수}

    ★§11.1 MAJOR-A: 내부에서 store.upsert / ingest 직접 호출 금지.
    모든 색인은 capture_and_index(provenance=ArtifactProvenance(provenance='untrusted', ...)) 경유.
    """
    _crawler = crawler if crawler is not None else _make_default_crawler()

    # 1. 검색
    try:
        results: list[SearxResult] = search_fn(query, max_results=max_results)
    except Exception:
        logger.warning("SearXNG 검색 실패 — 빈 결과 반환", exc_info=True)
        return {"urls": [], "indexed": 0, "results": 0}

    urls: list[str] = []
    total_indexed: int = 0

    for result in results:
        url = result.url
        if not url:
            continue

        # 2. 크롤
        try:
            markdown = _crawler.fetch(url)
        except Exception:
            logger.warning("크롤 실패, 스킵: %s", url, exc_info=True)
            continue

        if not markdown:
            logger.debug("빈 마크다운, 스킵: %s", url)
            continue

        # 3. capture_and_index(untrusted) — §11.1 유일 인입 경로
        provenance = ArtifactProvenance(
            provenance="untrusted",
            source_url=url,
        )
        try:
            n = capture_and_index(store, markdown, source=url, provenance=provenance)
            total_indexed += n
            urls.append(url)
        except Exception:
            logger.warning("capture_and_index 실패, 스킵: %s", url, exc_info=True)

    return {"urls": urls, "indexed": total_indexed, "results": len(urls)}
