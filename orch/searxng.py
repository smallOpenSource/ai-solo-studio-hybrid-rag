"""orch.searxng — §8 SearXNG 메타검색 클라이언트 (P9·NET-NEW).

§8(spec line 372): SearXNG(rootless Podman·publish ``0.0.0.0:<port>:8080``·``settings.yml``
``formats:[json,html]``)를 ``GET /search?q=...&format=json`` 으로 질의해 결과(url/title/snippet)를
정규화 반환한다. 컨테이너는 **P1에서 배포**됨(``aigs-searxng``·``127.0.0.1:2815``·라이브 검증 200 JSON).
본 모듈은 그 엔드포인트의 **검색 클라이언트** — 연구 파이프라인 RPI Research 진입점이며, 결과 URL을
``knowledge.crawl`` 로 넘겨 비신뢰 인입(``capture_and_index(provenance="untrusted")``·§11.1)한다.

종료성(P0): HTTP 타임아웃 **바운드**(connect+read·무한 0). 시크릿: SearXNG 는 로컬 무인증이나
``base_url`` 외 값(쿼리 등)을 로깅하지 않는다(예외는 타입명만).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx

_DEFAULT_BASE = "http://127.0.0.1:2815"
_DEFAULT_TIMEOUT_S = 10.0
_CONNECT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class SearxResult:
    """SearXNG 단일 결과(정규화)."""

    url: str
    title: str
    content: str  # snippet
    engine: str = ""


@dataclass(frozen=True)
class SearxConfig:
    base_url: str = _DEFAULT_BASE
    timeout_s: float = _DEFAULT_TIMEOUT_S


def load_searx_config(env: Optional[dict[str, str]] = None) -> SearxConfig:
    """``SEARXNG_URL`` / ``SEARXNG_TIMEOUT_S`` 환경에서 설정(기본=로컬 컨테이너·10s 바운드)."""
    e = env if env is not None else os.environ
    base = (e.get("SEARXNG_URL") or _DEFAULT_BASE).rstrip("/")
    try:
        t = float(e.get("SEARXNG_TIMEOUT_S") or _DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        t = _DEFAULT_TIMEOUT_S
    # 종료성: 타임아웃은 항상 유한 양수(무한/0 차단).
    if not (t > 0):
        t = _DEFAULT_TIMEOUT_S
    return SearxConfig(base_url=base, timeout_s=t)


def _parse_results(payload: dict, max_results: int) -> list[SearxResult]:
    """SearXNG json payload의 ``results[]`` → 정규화(빈 url 제외·top max_results)."""
    out: list[SearxResult] = []
    for item in (payload.get("results") or [])[:max_results]:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            SearxResult(
                url=url,
                title=(item.get("title") or "").strip(),
                content=(item.get("content") or "").strip(),
                engine=(item.get("engine") or "").strip(),
            )
        )
    return out


def search(
    query: str,
    *,
    config: Optional[SearxConfig] = None,
    max_results: int = 10,
    transport: Optional[httpx.BaseTransport] = None,
) -> list[SearxResult]:
    """SearXNG 메타검색: ``GET /search?q=...&format=json`` → 정규화 결과(바운드 타임아웃).

    transport: 테스트 주입 seam(``httpx.MockTransport``). 기본 None=실 엔드포인트.
    """
    cfg = config if config is not None else load_searx_config()
    timeout = httpx.Timeout(cfg.timeout_s, connect=_CONNECT_TIMEOUT_S)
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.get(
                cfg.base_url + "/search",
                params={"q": query, "format": "json"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError:
        # 호출자가 처리하도록 전파(값/쿼리 로깅 0·타입은 호출자 책임).
        raise
    return _parse_results(payload, max_results)
