"""Unit tests for orch.media_provider.generate_media — image primary/fallback 라우팅.

라이브 0 — DashScope/comfy transport 를 가짜로 monkeypatch 해 라우팅만 검증:
  - image → DashScope(primary) 경유.
  - video → comfy(OpenAICompatTransport) 직행(DashScope 미지원).
  - DashScope raise → comfy fallback.
  - ORCH_IMAGE_PRIMARY=off → comfy 직행(DashScope 미생성).
"""

from __future__ import annotations

import asyncio

import pytest

import orch.dashscope_image as ds
import orch.media_provider as mp
from orch.comfy import MediaResult


def _result(provider: str, kind: str) -> MediaResult:
    return MediaResult(kind=kind, data=b"x", content_type="image/png", model=provider)


class _FakeDashScope:
    instances: list["_FakeDashScope"] = []

    def __init__(self, *a, **kw):
        _FakeDashScope.instances.append(self)

    async def generate(self, kind, prompt, **kw):
        return _result("dashscope", kind)


class _FakeDashScopeRaises(_FakeDashScope):
    async def generate(self, kind, prompt, **kw):
        raise RuntimeError("dashscope down")


class _FakeComfy:
    instances: list["_FakeComfy"] = []

    def __init__(self, *a, **kw):
        _FakeComfy.instances.append(self)

    async def generate(self, kind, prompt, **kw):
        return _result("comfy", kind)


@pytest.fixture(autouse=True)
def _reset():
    _FakeDashScope.instances.clear()
    _FakeComfy.instances.clear()
    yield


def _patch_comfy(monkeypatch):
    monkeypatch.setattr(mp, "OpenAICompatTransport", _FakeComfy, raising=True)


def _patch_dashscope(monkeypatch, klass, *, configured=True):
    # generate_media 는 함수 내부에서 orch.dashscope_image 의 심볼을 lazy import 한다 →
    # 모듈 속성을 패치하면 그 import 가 가짜를 집는다.
    monkeypatch.setattr(ds, "DashScopeImageTransport", klass, raising=True)
    monkeypatch.setattr(ds, "dashscope_configured", lambda env=None: configured)


def test_image_routes_to_dashscope(monkeypatch):
    monkeypatch.setenv("ORCH_IMAGE_PRIMARY", "dashscope")
    _patch_dashscope(monkeypatch, _FakeDashScope, configured=True)
    _patch_comfy(monkeypatch)
    res = asyncio.run(mp.generate_media("image", "a circle"))
    assert res.model == "dashscope"
    assert _FakeDashScope.instances  # primary 생성됨
    assert not _FakeComfy.instances  # comfy 미사용


def test_video_routes_to_comfy(monkeypatch):
    monkeypatch.setenv("ORCH_IMAGE_PRIMARY", "dashscope")
    _patch_dashscope(monkeypatch, _FakeDashScope, configured=True)
    _patch_comfy(monkeypatch)
    res = asyncio.run(mp.generate_media("video", "a clip"))
    assert res.model == "comfy"
    assert not _FakeDashScope.instances  # video 는 DashScope 미경유


def test_dashscope_failure_falls_back_to_comfy(monkeypatch):
    monkeypatch.setenv("ORCH_IMAGE_PRIMARY", "dashscope")
    _patch_dashscope(monkeypatch, _FakeDashScopeRaises, configured=True)
    _patch_comfy(monkeypatch)
    res = asyncio.run(mp.generate_media("image", "x"))
    assert res.model == "comfy"  # primary raise → comfy fallback
    assert _FakeDashScope.instances and _FakeComfy.instances


def test_flag_off_uses_comfy(monkeypatch):
    monkeypatch.setenv("ORCH_IMAGE_PRIMARY", "off")
    _patch_dashscope(monkeypatch, _FakeDashScope, configured=True)
    _patch_comfy(monkeypatch)
    res = asyncio.run(mp.generate_media("image", "x"))
    assert res.model == "comfy"
    assert not _FakeDashScope.instances  # off → DashScope 미생성


def test_unconfigured_dashscope_uses_comfy(monkeypatch):
    monkeypatch.setenv("ORCH_IMAGE_PRIMARY", "dashscope")
    _patch_dashscope(monkeypatch, _FakeDashScope, configured=False)
    _patch_comfy(monkeypatch)
    res = asyncio.run(mp.generate_media("image", "x"))
    assert res.model == "comfy"
    assert not _FakeDashScope.instances  # 미구성 → DashScope 미생성
