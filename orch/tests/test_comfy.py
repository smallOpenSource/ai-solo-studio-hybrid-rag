"""P7a — orch/comfy.py OpenAICompatTransport unit (mock·실 HTTP 0·httpx.MockTransport).

submit→view(sync) · submit→poll→view(async) · 종료성 이중가드(max_poll_count·deadline) ·
Semaphore(1) 직렬화 · 시크릿 값 미로깅 · kind/config 가드. 라이브 e2e 는 P7b/P7c 별도.
"""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from orch import comfy
from orch.comfy import (
    ComfyConfig,
    MediaResult,
    OpenAICompatTransport,
    _auth_headers,
    _resolve_kind,
)

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(16))
_B64 = base64.b64encode(_PNG).decode()
_SECRET = "SECRET_KEY_DO_NOT_LEAK"


def _cfg() -> ComfyConfig:
    return ComfyConfig(
        base="http://test.local/v1",
        api_key=_SECRET,
        ep_image="/images/generations",
        ep_image_edit="/images/edits",
        ep_video="/videos/generations",
        image_model="img-m",
        image_edit_model="edit-m",
        video_model="vid-m",
        video_audio_model="vidaud-m",
    )


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --------------------------------------------------------------------- sync paths


def test_sync_b64_image():
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/images/generations")
        return httpx.Response(200, json={"created": 1, "data": [{"b64_json": _B64}]})

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    res = asyncio.run(t.generate("image", "a red circle", size="64x64"))
    assert isinstance(res, MediaResult)
    assert res.data == _PNG
    assert res.kind == "image"
    assert res.poll_count == 0  # sync · no polling


def test_sync_url_image_downloads():
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(
                200, json={"data": [{"url": "http://test.local/v1/blob/1"}]}
            )
        # second GET = download
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    res = asyncio.run(t.generate("image", "x"))
    assert res.data == _PNG
    assert res.content_type == "image/png"


def test_image_edit_uses_edit_model():
    captured = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _j

        captured.update(_j.loads(req.content))
        return httpx.Response(200, json={"data": [{"b64_json": _B64}]})

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    asyncio.run(t.generate("image_edit", "edit it"))
    assert captured["model"] == "edit-m"  # per-kind model selector


# -------------------------------------------------------------------- async poll


def test_async_poll_completes():
    state = {"polls": 0}

    def h(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"id": "job1", "status": "processing"})
        state["polls"] += 1
        if state["polls"] < 2:
            return httpx.Response(200, json={"status": "processing"})
        return httpx.Response(
            200, json={"status": "completed", "data": [{"b64_json": _B64}]}
        )

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    res = asyncio.run(
        t.generate("video", "clip", max_poll_count=10, poll_interval_s=0.0)
    )
    assert res.data == _PNG
    assert res.poll_count >= 2


# ----------------------------------------------------------- termination guards


def test_max_poll_count_terminates():
    def h(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"id": "job1", "status": "processing"})
        return httpx.Response(200, json={"status": "processing"})  # never completes

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    with pytest.raises(TimeoutError):
        asyncio.run(t.generate("video", "x", max_poll_count=3, poll_interval_s=0.0))


def test_deadline_terminates(monkeypatch):
    # 결정론적 deadline: monotonic 을 점프시켜 첫 poll 에서 마감 초과.
    seq = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0])

    def fake_monotonic():
        try:
            return next(seq)
        except StopIteration:
            return 1000.0

    monkeypatch.setattr(comfy.time, "monotonic", fake_monotonic)

    def h(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"id": "job1", "status": "processing"})
        return httpx.Response(200, json={"status": "processing"})

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    with pytest.raises(TimeoutError):
        asyncio.run(
            t.generate(
                "video", "x", deadline_s=10.0, max_poll_count=999, poll_interval_s=0.0
            )
        )


# ------------------------------------------------------------------ Semaphore(1)


def test_semaphore_serializes():
    inflight = {"cur": 0, "max": 0}

    async def h(req: httpx.Request) -> httpx.Response:
        inflight["cur"] += 1
        inflight["max"] = max(inflight["max"], inflight["cur"])
        await asyncio.sleep(0.03)
        inflight["cur"] -= 1
        return httpx.Response(200, json={"data": [{"b64_json": _B64}]})

    async def run_two():
        t1 = OpenAICompatTransport(_cfg(), transport=_transport(h))
        t2 = OpenAICompatTransport(_cfg(), transport=_transport(h))
        await asyncio.gather(t1.generate("image", "a"), t2.generate("image", "b"))

    asyncio.run(run_two())
    assert inflight["max"] == 1  # 모듈-레벨 Semaphore(1) → 동시 in-flight 1


# --------------------------------------------------------------- secret hygiene


def test_auth_header_present_but_not_leaked_on_error():
    assert _auth_headers(_cfg())["Authorization"] == f"Bearer {_SECRET}"

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    try:
        asyncio.run(t.generate("image", "x"))
        raised = None
    except Exception as e:  # noqa: BLE001
        raised = e
    assert raised is not None
    assert _SECRET not in str(raised)  # 에러 메시지에 키 값 미노출


def test_result_meta_does_not_carry_secret():
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": _B64}]})

    t = OpenAICompatTransport(_cfg(), transport=_transport(h))
    res = asyncio.run(t.generate("image", "x"))
    assert _SECRET not in repr(res)


# --------------------------------------------------------------------- guards


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        _resolve_kind(_cfg(), "hologram")


def test_not_configured_raises():
    cfg = ComfyConfig(
        base="",
        api_key="",
        ep_image="/images/generations",
        ep_image_edit="/images/edits",
        ep_video="/videos/generations",
        image_model="m",
        image_edit_model="m",
        video_model="m",
        video_audio_model="m",
    )
    t = OpenAICompatTransport(cfg)
    with pytest.raises(RuntimeError):
        asyncio.run(t.generate("image", "x"))


def test_resolve_kind_models():
    cfg = _cfg()
    assert _resolve_kind(cfg, "image")[1] == "img-m"
    assert _resolve_kind(cfg, "image_edit")[1] == "edit-m"
    assert _resolve_kind(cfg, "video")[1] == "vid-m"
    assert _resolve_kind(cfg, "video_audio")[1] == "vidaud-m"
