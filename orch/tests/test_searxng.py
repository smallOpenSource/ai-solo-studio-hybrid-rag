"""US-9a-1 u1 — SearXNG 검색 클라이언트 파싱·타임아웃 바운드 (httpx MockTransport·결정론)."""

from __future__ import annotations

import httpx
import pytest

from orch.searxng import (
    SearxConfig,
    SearxResult,
    _parse_results,
    load_searx_config,
    search,
)

_FIXTURE = {
    "query": "test",
    "results": [
        {
            "url": "https://a.example/1",
            "title": "A",
            "content": "snippet a",
            "engine": "duckduckgo",
        },
        {"url": "https://b.example/2", "title": "B", "content": "snippet b"},
        {"url": "", "title": "empty-url", "content": "drop"},  # 빈 url → 제외
    ],
}


def _mock(payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        # 쿼리/포맷이 실제로 전달되는지 경계 확인.
        assert request.url.params.get("format") == "json"
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_search_parses_and_normalizes():
    res = search(
        "test",
        config=SearxConfig(base_url="http://x:2815"),
        transport=_mock(_FIXTURE),
    )
    assert len(res) == 2  # 빈 url 제외
    assert res[0] == SearxResult(
        url="https://a.example/1", title="A", content="snippet a", engine="duckduckgo"
    )
    assert res[1].url == "https://b.example/2"
    assert res[1].engine == ""  # 누락 필드 기본값


def test_search_respects_max_results():
    res = search(
        "test",
        config=SearxConfig(base_url="http://x:2815"),
        max_results=1,
        transport=_mock(_FIXTURE),
    )
    assert len(res) == 1


def test_search_raises_on_http_error():
    with pytest.raises(httpx.HTTPStatusError):
        search(
            "test",
            config=SearxConfig(base_url="http://x:2815"),
            transport=_mock({}, status=500),
        )


def test_config_timeout_is_bounded():
    # 종료성 P0: 타임아웃은 항상 유한 양수(무한/0/음수 차단).
    assert load_searx_config(
        {"SEARXNG_URL": "http://h:9/", "SEARXNG_TIMEOUT_S": "3.5"}
    ) == (SearxConfig(base_url="http://h:9", timeout_s=3.5))
    assert load_searx_config({"SEARXNG_TIMEOUT_S": "0"}).timeout_s == 10.0  # 0→기본
    assert (
        load_searx_config({"SEARXNG_TIMEOUT_S": "bad"}).timeout_s == 10.0
    )  # 파싱실패→기본
    assert load_searx_config({}).timeout_s > 0


def test_parse_results_empty():
    assert _parse_results({}, 10) == []
    assert _parse_results({"results": []}, 10) == []
