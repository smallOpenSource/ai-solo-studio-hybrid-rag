"""Unit tests for orch.dashscope_image — DashScope Qwen-image transport(라이브 0).

httpx.MockTransport 주입으로 실 네트워크 없이 검증:
  - image: POST→url→download→MediaResult bytes.
  - 응답에 ``code`` 존재→RuntimeError(에러 신호).
  - image_edit: 요청 본문 content 에 ``{"image": ...}`` 포함(capture request).
  - kind=video→ValueError(미지원).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from orch.comfy import MediaResult
from orch.dashscope_image import DashScopeImageTransport

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(16))
_SECRET = "DS-SECRET-DO-NOT-LEAK"
_ENV = {
    "DASHSCOPE_BASE": "https://dashscope.example.test",
    "DASHSCOPE_API_KEY": _SECRET,
    "DASHSCOPE_IMAGE_MODEL": "qwen-image-2.0-pro",
    "DASHSCOPE_IMAGE_EDIT_MODEL": "qwen-image-edit-max",
}


def _ok_handler(captured: dict | None = None):
    """POST(generation)→image url, GET(download)→PNG bytes 를 돌려주는 핸들러."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            if captured is not None:
                captured.update(json.loads(req.content))
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {
                                            "image": "https://dashscope.example.test/img/1"
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                },
            )
        # GET = 24h url 다운로드
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    return h


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_image_generate_downloads_bytes():
    t = DashScopeImageTransport(_ENV, transport=_transport(_ok_handler()))
    res = asyncio.run(t.generate("image", "a red circle", size="1024x1024"))
    assert isinstance(res, MediaResult)
    assert res.data == _PNG
    assert res.content_type == "image/png"
    assert res.kind == "image"
    assert res.model == "qwen-image-2.0-pro"


def test_image_edit_includes_input_image_in_body():
    captured: dict = {}
    t = DashScopeImageTransport(_ENV, transport=_transport(_ok_handler(captured)))
    data_uri = "data:image/png;base64,AAAA"
    res = asyncio.run(t.generate("image_edit", "make it blue", image=data_uri))
    assert res.data == _PNG
    assert res.model == "qwen-image-edit-max"  # per-kind edit model
    # 요청 본문 content 에 입력이미지가 포함됐는지 캡처로 단언(M-C/AC11 메커니즘).
    content = captured["input"]["messages"][0]["content"]
    assert {"image": data_uri} in content
    assert any("text" in c and c["text"] == "make it blue" for c in content)


def test_image_edit_without_image_raises():
    t = DashScopeImageTransport(_ENV, transport=_transport(_ok_handler()))
    with pytest.raises(ValueError):
        asyncio.run(t.generate("image_edit", "edit", image=None))


def test_response_code_raises():
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "InvalidApiKey", "message": "no"})

    t = DashScopeImageTransport(_ENV, transport=_transport(h))
    with pytest.raises(RuntimeError):
        asyncio.run(t.generate("image", "x"))


def test_video_kind_raises_value_error():
    t = DashScopeImageTransport(_ENV, transport=_transport(_ok_handler()))
    with pytest.raises(ValueError):
        asyncio.run(t.generate("video", "a clip"))


def test_error_redacts_secret():
    def h(req: httpx.Request) -> httpx.Response:
        # 4xx → raise_for_status → 내부에서 타입명만으로 재포장.
        return httpx.Response(401, json={"code": "x"}, text=f"unauth key={_SECRET}")

    t = DashScopeImageTransport(_ENV, transport=_transport(h))
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(t.generate("image", "x"))
    assert _SECRET not in str(ei.value)  # 시크릿/본문 0노출
