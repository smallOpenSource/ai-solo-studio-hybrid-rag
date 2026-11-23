"""engine측 knowledge 검색 HTTP 클라이언트 — Phase 8 P (옵션 B).

engine env 워커가 knowledge_search를 실행할 때, 디스크(임베디드 qdrant)를 직접 열지 않고
knowledge HTTP 검색 서비스(knowledge/service.py)에 위임한다. 워커는 httpx만 의존하므로
qdrant_client 미설치 engine env에서도 동작한다(cross-env 갭 해소).

★connect 타임아웃 5s 필수(AC-P3): 서비스 다운 시 connect가 빠르게 실패해야 워크플로가
hang하지 않고 NODE_FAILED로 떨어진다(동시성 1 엔진에서 무한대기 한 건이 전체를 정지시킴 —
preflight.sh의 lint_http_timeout.sh가 무타임아웃 httpx 호출을 차단). read=30s(검색·리랭크
시간), write/pool=5s. bge_client.py:66-68의 httpx.Timeout(connect/read/write/pool) 패턴 계승.

응답은 core.knowledge_contract.SearchResponse(서비스와 공유 단일 계약)로 파싱해
knowledge_search 도구 출력 형태 dict {"results":[...], "count":int}로 돌려준다.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from core.knowledge_contract import SearchRequest, SearchResponse

# knowledge 서비스 URL — 환경변수 오버라이드(기본 localhost:8810, endpoint.yaml 정합).
_DEFAULT_URL = "http://127.0.0.1:8810"

# ★connect=5s 필수(AC-P3) — 서비스 다운 시 빠른 실패(hang 0 → NODE_FAILED).
#   read=90s(검색·리랭크) — ★라이브 검증 2026-06-10: 실 bge 리랭크(top_n=20)가 ~52s라
#   기존 read=30s가 research_summary knowledge_search를 매번 타임아웃시킴 → 90s로 상향.
#   서비스 다운 fast-fail은 connect=5s가 담당(read는 '느린 정상 검색' 허용폭). write/pool=5s.
#   bge_client.py:66-68 패턴 계승.
_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=5.0, pool=5.0)


def _service_url() -> str:
    """knowledge 서비스 base URL을 환경변수에서 읽는다(없으면 기본 localhost:8810)."""
    return os.environ.get("KNOWLEDGE_SERVICE_URL", _DEFAULT_URL)


def search(
    query: str,
    k: int = 5,
    filter: dict[str, Any] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """knowledge 검색 서비스에 HTTP로 위임하고 도구 출력 계약 dict를 돌려준다.

    Args:
        query: 검색 질의.
        k: 최종 반환 상한(기본 5).
        filter: 후보를 좁히는 선택 조건(None이면 전체).
        transport: httpx 전송계층 주입(테스트용 MockTransport·인프로세스 ASGITransport).
            None이면 실 네트워크로 서비스에 접속한다.

    Returns:
        {"results": [{"text", "source", "score"}, ...], "count": int}
        — knowledge_search 도구 출력 형태와 동일(SearchResponse 파싱).

    Raises:
        httpx.HTTPError: 서비스 다운/연결거부/타임아웃·HTTP 오류 → 예외 전파
            (엔진이 NODE_FAILED로 처리). connect=5s로 빠른 실패(hang 0).
    """
    req = SearchRequest(query=query, k=k, filter=filter)
    url = f"{_service_url()}/search"
    with httpx.Client(timeout=_TIMEOUT, transport=transport) as client:
        resp = client.post(url, json=req.model_dump())
    resp.raise_for_status()
    parsed = SearchResponse.model_validate(resp.json())
    return parsed.model_dump()


def real_knowledge_search_http(
    args: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """knowledge_search 실도구(HTTP) — REAL_TOOLS 바인딩용 tool 형태 callable.

    도구 callable 시그니처(args, context=None)를 따른다. args에서 query/k를 뽑아
    search()로 HTTP 위임 → 계약 dict 반환(기존 knowledge_search 출력 형태 유지·가역 도구라
    context 무시). 서비스 다운/연결거부 시 예외 전파 → 엔진이 NODE_FAILED 처리(connect=5s).
    """
    query: str = args.get("query", "")
    k: int = int(args.get("k", 5))
    return search(query, k=k)
