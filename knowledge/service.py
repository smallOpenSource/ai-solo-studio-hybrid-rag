"""knowledge HTTP 검색 서비스 — Phase 8 P (cross-env qdrant 해소, 옵션 B).

knowledge env가 `state/qdrant`를 소유하는 작은 FastAPI 서비스다. engine env 워커는
이 서비스에 HTTP로 검색을 위임하므로 디스크 락(임베디드 qdrant)을 *물리적으로* 안 건드린다
→ 워커×ingest 경합을 코드 수술 0으로 제거(H-13 워커-경합 구조적 0).

★이 서비스는 knowledge/search.py·store.py를 **무수정**으로 감싸는 얇은 래퍼다:
- POST /search → knowledge.search.search(query, k, filter, store=, reranker=) → SearchResponse.
- GET /health → {"status": "ok"} (런북 prerequisite·감독 헬스체크, AC-P6).

store/reranker는 create_app 주입으로 테스트가 결정론 mock을 끼운다(실서버 0). None이면
search()가 기본(endpoint.yaml knowledge 컬렉션 + BGEClient)을 쓴다.

★단일 인스턴스(uvicorn --workers 1)로 띄운다 — qdrant 임베디드는 프로세스 단위 파일락이라
state/qdrant 단일 소유자가 이 서비스 하나여야 한다(동시성 1, H-13). serve_knowledge.sh 참조.
응답 계약은 core.knowledge_contract(서비스·클라이언트 공유 단일 스키마)에 정의된다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from core.knowledge_contract import SearchRequest, SearchResponse


def create_app(store: Any = None, reranker: Any = None) -> FastAPI:
    """knowledge 검색 FastAPI 앱을 만든다(store/reranker 주입 가능).

    Args:
        store: dense 검색 제공자. None이면 search()가 KnowledgeStore() 기본을 쓴다.
        reranker: 리랭커 제공자. None이면 search()가 BGEClient() 기본을 쓴다.
            (테스트는 결정론 mock을 주입해 실서버·실 qdrant 없이 핸들러를 검증한다.)

    Returns:
        POST /search · GET /health 두 라우트를 가진 FastAPI 앱.
    """
    app = FastAPI(title="knowledge-search", version="1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        """헬스체크 — 200 OK + {"status": "ok"}(감독·런북 prerequisite)."""
        return {"status": "ok"}

    @app.post("/search", response_model=SearchResponse)
    def search_endpoint(req: SearchRequest) -> SearchResponse:
        """질의를 knowledge.search.search에 위임하고 공유 계약으로 응답한다.

        search.py·store.py 무수정 — 주입된 store/reranker(없으면 기본)로 위임만 한다.
        빈 질의·k<1은 search()가 {"results":[],"count":0} 안전 반환(환각 가드 토대).
        """
        from knowledge.search import search  # 지연 import — qdrant는 호출 시점에만

        result = search(
            req.query,
            k=req.k,
            filter=req.filter,
            store=store,
            reranker=reranker,
        )
        return SearchResponse(**result)

    return app


# 모듈레벨 앱 — uvicorn knowledge.service:app 진입점(serve_knowledge.sh).
app = create_app()
