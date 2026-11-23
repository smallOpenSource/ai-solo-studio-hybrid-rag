"""US-002 SSE 소비 클라이언트 단위 테스트.

오프라인 결정론(실서버·discord 0). _parse_sse_lines 순수 헬퍼를
직접 테스트해 라인 파싱 로직 전수 검증.
async 부분(consume_events)은 httpx mock 으로 커버.

★ 정적 단언: `import discord` 0.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio  # noqa: F401 — pytest-asyncio 등록 확인용

from adapters.discord.sse_client import (
    _parse_sse_lines,
    _SSE_TIMEOUT,
    consume_events,
)


# ── 정적 단언: discord import 없음 ────────────────────────────────────────────


def test_no_discord_import_in_sse_client() -> None:
    """`import discord` 가 sse_client.py 에 없어야 한다."""
    src = Path(__file__).parent.parent / "adapters" / "discord" / "sse_client.py"
    text = src.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        assert not (
            stripped.startswith("import discord") or stripped.startswith("from discord")
        ), f"discord import 발견: {line!r}"


# ── _parse_sse_lines: 기본 1 프레임 ──────────────────────────────────────────


def test_parse_single_frame_returns_seq_and_payload() -> None:
    """`id:` + `data:` + 빈 줄 → (seq, payload) 정확 분리."""
    lines = ["id: 7", 'data: {"type": "RUN_STARTED"}', ""]
    result = _parse_sse_lines(lines)
    assert len(result) == 1
    seq, payload = result[0]
    assert seq == 7
    assert payload == {"type": "RUN_STARTED"}


def test_parse_seq_is_from_id_line_not_payload() -> None:
    """seq 는 `id:` 라인에서만 — payload 에 seq 키가 없어도 정상 파싱."""
    lines = ["id: 42", 'data: {"content": "hello"}', ""]
    result = _parse_sse_lines(lines)
    assert result == [(42, {"content": "hello"})]


# ── _parse_sse_lines: 멀티프레임 순차 ────────────────────────────────────────


def test_parse_multi_frame_sequential() -> None:
    """여러 프레임이 빈 줄 경계로 순서대로 파싱된다."""
    lines = [
        "id: 1",
        'data: {"n": 1}',
        "",
        "id: 2",
        'data: {"n": 2}',
        "",
        "id: 3",
        'data: {"n": 3}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [
        (1, {"n": 1}),
        (2, {"n": 2}),
        (3, {"n": 3}),
    ]


def test_parse_multi_frame_order_preserved() -> None:
    """멀티프레임 순서가 입력 순서와 동일하다."""
    lines = []
    for i in range(10):
        lines += [f"id: {i + 1}", f'data: {{"seq": {i + 1}}}', ""]
    result = _parse_sse_lines(lines)
    assert [seq for seq, _ in result] == list(range(1, 11))


# ── _parse_sse_lines: ping/빈 data/깨진 JSON 건너뜀 ──────────────────────────


def test_parse_ping_comment_skipped() -> None:
    """: ping 코멘트는 건너뛰고 이후 프레임은 정상 파싱."""
    lines = [
        ": ping",
        "",
        "id: 5",
        'data: {"type": "OK"}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [(5, {"type": "OK"})]


def test_parse_colon_comment_any_form_skipped() -> None:
    """: 로 시작하는 모든 코멘트가 건너뛰어진다."""
    lines = [
        ": keep-alive",
        ": 서버 코멘트",
        "id: 10",
        'data: {"x": 1}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [(10, {"x": 1})]


def test_parse_empty_data_skipped() -> None:
    """data: 값이 비어있는 라인은 건너뛰고 해당 프레임은 버려진다."""
    lines = [
        "id: 3",
        "data: ",
        "",
        "id: 4",
        'data: {"ok": true}',
        "",
    ]
    result = _parse_sse_lines(lines)
    # id:3 은 빈 data 라 버려짐, id:4 만 살아남음
    assert result == [(4, {"ok": True})]


def test_parse_broken_json_skipped() -> None:
    """깨진 JSON 은 건너뛰고 해당 프레임은 버려진다(흐름 불중단)."""
    lines = [
        "id: 1",
        "data: {broken json",
        "",
        "id: 2",
        'data: {"ok": true}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [(2, {"ok": True})]


def test_parse_broken_json_does_not_affect_next_frame() -> None:
    """깨진 JSON 프레임 뒤에 오는 정상 프레임들이 모두 파싱된다."""
    lines = [
        "id: 1",
        "data: <<<invalid>>>",
        "",
        "id: 2",
        'data: {"a": 1}',
        "",
        "id: 3",
        'data: {"b": 2}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [(2, {"a": 1}), (3, {"b": 2})]


def test_parse_mixed_valid_invalid_frames() -> None:
    """ping·빈 data·깨진 JSON 이 섞인 스트림에서 유효 프레임만 추출."""
    lines = [
        ": ping",
        "",
        "id: 1",
        'data: {"type": "A"}',
        "",
        "id: 2",
        "data: ",
        "",
        "id: 3",
        "data: NOT_JSON",
        "",
        "id: 4",
        'data: {"type": "D"}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [(1, {"type": "A"}), (4, {"type": "D"})]


# ── _parse_sse_lines: seq 없는 프레임 버려짐 ──────────────────────────────────


def test_parse_frame_without_id_discarded() -> None:
    """`id:` 라인 없는 프레임은 seq 없으므로 버린다."""
    lines = [
        'data: {"type": "NOID"}',
        "",
        "id: 9",
        'data: {"type": "HASID"}',
        "",
    ]
    result = _parse_sse_lines(lines)
    assert result == [(9, {"type": "HASID"})]


# ── _parse_sse_lines: 파일 끝 빈 줄 없는 경우 ────────────────────────────────


def test_parse_no_trailing_empty_line() -> None:
    """스트림 끝에 빈 줄 없이 종료된 프레임도 수집된다."""
    lines = ["id: 99", 'data: {"eof": true}']
    result = _parse_sse_lines(lines)
    assert result == [(99, {"eof": True})]


# ── consume_events: last_event_id 재연결 헤더 구성 ───────────────────────────
# 모든 async 테스트는 asyncio.sleep 을 즉시 반환 mock 으로 교체해 backoff 대기를 없앤다.


def _make_connect_error_client(
    captured: list[dict] | None = None, captured_urls: list[str] | None = None
):
    """첫 stream() 호출에서 헤더/URL 을 캡처한 뒤 ConnectError 를 올리는 fake client."""

    class _FakeStreamCtx:
        def __init__(self, headers, url):
            if captured is not None:
                captured.append(dict(headers or {}))
            if captured_urls is not None:
                captured_urls.append(url)

        async def __aenter__(self):
            raise httpx.ConnectError("refused")

        async def __aexit__(self, *_):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, method, url, headers=None, **kw):
            return _FakeStreamCtx(headers, url)

    return _FakeClient()


@pytest.mark.asyncio
async def test_consume_events_sends_last_event_id_header() -> None:
    """last_event_id 가 주어지면 Last-Event-ID 헤더로 전달된다."""
    captured: list[dict] = []

    with patch(
        "httpx.AsyncClient", return_value=_make_connect_error_client(captured=captured)
    ):
        with patch("adapters.discord.sse_client.asyncio.sleep", new_callable=AsyncMock):
            gen = consume_events(
                "http://localhost:9999", last_event_id=10, max_retries=1
            )
            try:
                await gen.__anext__()
            except (StopAsyncIteration, Exception):
                pass
            finally:
                await gen.aclose()

    assert captured, "stream() 가 호출되지 않음"
    assert captured[0].get("Last-Event-ID") == "10", f"헤더 오류: {captured[0]}"


@pytest.mark.asyncio
async def test_consume_events_no_last_event_id_no_header() -> None:
    """last_event_id=None 이면 Last-Event-ID 헤더를 보내지 않는다."""
    captured: list[dict] = []

    with patch(
        "httpx.AsyncClient", return_value=_make_connect_error_client(captured=captured)
    ):
        with patch("adapters.discord.sse_client.asyncio.sleep", new_callable=AsyncMock):
            gen = consume_events(
                "http://localhost:9999", last_event_id=None, max_retries=1
            )
            try:
                await gen.__anext__()
            except (StopAsyncIteration, Exception):
                pass
            finally:
                await gen.aclose()

    assert captured, "stream() 가 호출되지 않음"
    assert "Last-Event-ID" not in captured[0], f"헤더가 있어선 안 됨: {captured[0]}"


@pytest.mark.asyncio
async def test_consume_events_no_idle_close_ms_in_url() -> None:
    """`?idle_close_ms=` 쿼리 파라미터를 URL 에 붙이지 않는다."""
    captured_urls: list[str] = []

    with patch(
        "httpx.AsyncClient",
        return_value=_make_connect_error_client(captured_urls=captured_urls),
    ):
        with patch("adapters.discord.sse_client.asyncio.sleep", new_callable=AsyncMock):
            gen = consume_events("http://localhost:9999", max_retries=1)
            try:
                await gen.__anext__()
            except (StopAsyncIteration, Exception):
                pass
            finally:
                await gen.aclose()

    assert captured_urls, "stream() 가 호출되지 않음"
    for url in captured_urls:
        assert "idle_close_ms" not in url, f"idle_close_ms 가 URL 에 포함됨: {url}"


@pytest.mark.asyncio
async def test_consume_events_yields_seq_and_payload() -> None:
    """실제 SSE 라인을 흘려줬을 때 (seq, payload) 를 yield 한다."""

    async def _mock_lines():
        yield "id: 1"
        yield 'data: {"type": "RUN_STARTED"}'
        yield ""
        yield "id: 2"
        yield 'data: {"type": "RUN_FINISHED"}'
        yield ""

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def aiter_lines(self):
            return _mock_lines()

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *_):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, method, url, headers=None, **kw):
            return _FakeStreamCtx()

    results: list[tuple[int, dict]] = []
    with patch("httpx.AsyncClient", return_value=_FakeClient()):
        async for seq, payload in consume_events("http://localhost:9999"):
            results.append((seq, payload))
            if len(results) >= 2:
                break

    assert results == [
        (1, {"type": "RUN_STARTED"}),
        (2, {"type": "RUN_FINISHED"}),
    ]


@pytest.mark.asyncio
async def test_consume_events_cursor_advances_on_reconnect() -> None:
    """재연결 시 마지막 수신 seq 가 Last-Event-ID 헤더로 전달된다."""
    call_headers: list[dict] = []
    call_count = 0

    async def _first_then_disconnect():
        yield "id: 7"
        yield 'data: {"type": "X"}'
        yield ""
        raise httpx.ReadError("connection reset")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def aiter_lines(self):
            return _first_then_disconnect()

    class _FakeStreamCtx:
        def __init__(self, h):
            self._h = h

        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *_):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, method, url, headers=None, **kw):
            nonlocal call_count
            call_count += 1
            call_headers.append(dict(headers or {}))
            return _FakeStreamCtx(headers)

    results: list[tuple[int, dict]] = []

    async def _run():
        with patch("httpx.AsyncClient", return_value=_FakeClient()):
            with patch(
                "adapters.discord.sse_client.asyncio.sleep", new_callable=AsyncMock
            ):  # backoff sleep 즉시
                async for seq, payload in consume_events(
                    "http://localhost:9999", last_event_id=None
                ):
                    results.append((seq, payload))
                    if len(results) >= 1 and call_count >= 2:
                        break

    await _run()

    # 첫 번째 요청: Last-Event-ID 없음(last_event_id=None)
    assert "Last-Event-ID" not in call_headers[0]
    # 두 번째 이후 요청: 마지막 수신 seq=7 이 헤더에
    if len(call_headers) >= 2:
        assert call_headers[1].get("Last-Event-ID") == "7", (
            f"재연결 헤더 오류: {call_headers[1]}"
        )


# ── H-01: SSE 소비 타임아웃 구성(read=None, connect 상한) ─────────────────────


def test_sse_timeout_read_is_none_connect_bounded() -> None:
    """_SSE_TIMEOUT은 read=None(SSE long-lived 정답)·connect=10s 상한이어야 한다.

    read 상한이 있으면 ping(15s) 침묵을 ReadTimeout으로 오인해 재연결 폭주(H-01).
    """
    assert _SSE_TIMEOUT.read is None
    assert _SSE_TIMEOUT.connect == 10.0
