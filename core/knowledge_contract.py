"""knowledge 검색 서비스 공유 계약 — Phase 8 P (cross-env qdrant 해소, 옵션 B).

knowledge HTTP 검색 서비스(knowledge env)와 engine측 클라이언트(engine env)가
**공유하는 단일 응답 스키마**다. 서비스는 이 모델을 response_model로 쓰고, 클라이언트는
이 모델로 응답을 파싱한다 → 두 쪽이 같은 계약에 묶인다(상호맹목 mock 차단, AC-P7 왕복 계약).

★순수 pydantic만 의존(qdrant import 0) → 양 env에서 import 가능. 계약 정의는
knowledge.search.search 출력(search.py:16-18)을 모델로 상수화한 것이며, 행위는 0 추가한다.

계약(knowledge_search 도구 출력 — US-006이 그대로 노출):
    요청:  {"query": str, "k": int=5, "filter": dict|None=None}
    응답:  {"results": [{"text": str, "source": str, "score": float}, ...], "count": int}
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """POST /search 요청 본문 — knowledge.search.search 인자와 1:1 대응."""

    query: str
    k: int = 5
    filter: dict[str, Any] | None = None


class SearchResult(BaseModel):
    """검색 결과 한 건 — 청크 텍스트·출처·리랭커 점수·출처 신뢰표시.

    provenance(P8 E-1·add-only): ``chunk.meta['provenance']`` 에 P9 가 저장한 출처
    신뢰표시("trusted"|"untrusted")를 회수 경계까지 전파한다(§11.1①→②). 기본 None
    (legacy ~178pt pre-P9 코퍼스·필드 미설정) — 소비자는 None 을 외부 컬렉션서
    untrusted 로 간주(fail-safe·spotlight.py). 기존 3필드는 보존(소비자 무회귀).
    """

    text: str
    source: str
    score: float
    provenance: str | None = None


class SearchResponse(BaseModel):
    """POST /search 응답 — knowledge_search 도구 출력 계약과 동일.

    results는 리랭커 점수 내림차순 상위 k개, count는 그 개수. 후보가 없으면
    results=[]·count=0(search.py:103 빈 코퍼스 안전 반환과 동형).
    """

    results: list[SearchResult] = Field(default_factory=list)
    count: int = 0
