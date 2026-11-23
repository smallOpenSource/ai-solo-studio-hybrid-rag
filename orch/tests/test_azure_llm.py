"""Unit tests for orch.azure_llm — Azure Responses caller + primary/fallback 래퍼.

라이브 네트워크 0 — ``openai.OpenAI`` 를 monkeypatch 하거나 env 를 비워 검증한다.
검증:
  - azure_caller: 성공→output_text / 빈 output→raise / 미구성→raise.
  - make_primary_fallback_caller: primary ok→azure 결과 · primary raise→fallback 호출·반환
    · ORCH_LLM_PRIMARY=off→fallback 만(azure 미호출) · azure 미구성→fallback.
  - 시크릿/응답본문 0노출(에러 문자열에 키·url 부재).
"""

from __future__ import annotations

import logging

import pytest

import orch.azure_llm as az

_KEY = "AZ-SECRET-DO-NOT-LEAK"


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.output_text = text


class _FakeResponses:
    def __init__(self, *, text: str = "", raises: Exception | None = None) -> None:
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _FakeResp(self._text)


class _FakeClient:
    last: "_FakeClient | None" = None

    def __init__(self, *, _text: str = "", _raises: Exception | None = None, **kwargs):
        self.init_kwargs = kwargs
        self.responses = _FakeResponses(text=_text, raises=_raises)
        _FakeClient.last = self


def _patch_openai(monkeypatch, *, text="", raises=None):
    """``from openai import OpenAI`` 를 가짜 클라이언트로 치환(라이브 0)."""
    import openai

    def _factory(**kwargs):
        return _FakeClient(_text=text, _raises=raises, **kwargs)

    monkeypatch.setattr(openai, "OpenAI", _factory, raising=True)


def _configure_azure(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://az.example.test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", _KEY)
    monkeypatch.setenv("AZURE_OPENAI_MODEL_NAME", "gpt-5.4-mini")


# ── azure_caller ──────────────────────────────────────────────────────────────
def test_azure_caller_returns_output_text(monkeypatch):
    _configure_azure(monkeypatch)
    _patch_openai(monkeypatch, text="hello-from-azure")
    out = az.azure_caller("prompt", 0.0, 64)
    assert out == "hello-from-azure"
    # temperature 미전달·max_tokens→max_output_tokens(하한 16) 계약 확인.
    call = _FakeClient.last.responses.calls[0]
    assert "temperature" not in call
    assert call["max_output_tokens"] == 64


def test_azure_caller_empty_output_raises(monkeypatch):
    _configure_azure(monkeypatch)
    _patch_openai(monkeypatch, text="")  # 빈 output_text → raise
    with pytest.raises(RuntimeError):
        az.azure_caller("p", 0.0, 32)


def test_azure_caller_unconfigured_raises(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_MODEL_NAME", raising=False)
    # config/app.env 가 키를 채울 수 있으므로 model 명시 제거로 미구성 강제 불가 →
    # 대신 azure_configured(env=...) 의 빈 env 경로를 직접 단언(미구성=False).
    assert az.azure_configured(env={}) is False


def test_azure_caller_error_redacts_secret(monkeypatch):
    _configure_azure(monkeypatch)
    boom = RuntimeError(f"connection to https://az.example.test failed key={_KEY}")
    _patch_openai(monkeypatch, raises=boom)
    with pytest.raises(RuntimeError) as ei:
        az.azure_caller("p", 0.0, 16)
    msg = str(ei.value)
    assert _KEY not in msg  # 시크릿 0노출
    assert "https://az.example.test" not in msg  # url/본문 0노출
    assert "RuntimeError" in msg  # 타입명만


# ── make_primary_fallback_caller ────────────────────────────────────────────────
def _fallback_factory():
    calls: list[tuple] = []

    def fb(prompt: str, temperature: float, max_tokens: int) -> str:
        calls.append((prompt, temperature, max_tokens))
        return "fallback-result"

    return fb, calls


def test_wrapper_primary_ok_returns_azure(monkeypatch):
    _configure_azure(monkeypatch)
    monkeypatch.setenv("ORCH_LLM_PRIMARY", "azure")
    _patch_openai(monkeypatch, text="azure-win")
    fb, fb_calls = _fallback_factory()
    caller = az.make_primary_fallback_caller(fb, label="t")
    assert caller("p", 0.0, 32) == "azure-win"
    assert fb_calls == []  # primary 성공 → fallback 미호출


def test_wrapper_primary_raises_calls_fallback(monkeypatch):
    _configure_azure(monkeypatch)
    monkeypatch.setenv("ORCH_LLM_PRIMARY", "azure")
    _patch_openai(monkeypatch, raises=RuntimeError(f"boom {_KEY}"))
    fb, fb_calls = _fallback_factory()
    caller = az.make_primary_fallback_caller(fb, label="t")
    assert caller("p", 0.3, 48) == "fallback-result"
    assert fb_calls == [("p", 0.3, 48)]  # 동일 인자 inline 전달


def test_wrapper_flag_off_uses_fallback_only(monkeypatch):
    _configure_azure(monkeypatch)
    monkeypatch.setenv("ORCH_LLM_PRIMARY", "off")
    # off 면 azure 가 *호출조차* 되면 안 됨 → OpenAI 가 불리면 폭발하도록 설정.
    import openai

    def _explode(**kwargs):
        raise AssertionError("azure must NOT be constructed when flag=off")

    monkeypatch.setattr(openai, "OpenAI", _explode, raising=True)
    fb, fb_calls = _fallback_factory()
    caller = az.make_primary_fallback_caller(fb, label="t")
    assert caller("p", 0.0, 16) == "fallback-result"
    assert fb_calls and fb_calls[0][0] == "p"


def test_wrapper_azure_unconfigured_uses_fallback(monkeypatch):
    monkeypatch.setenv("ORCH_LLM_PRIMARY", "azure")
    # 미구성 강제 — azure_configured 를 False 로 패치(app.env 키 간섭 배제).
    monkeypatch.setattr(az, "azure_configured", lambda env=None: False)
    import openai

    def _explode(**kwargs):
        raise AssertionError("azure must NOT be constructed when unconfigured")

    monkeypatch.setattr(openai, "OpenAI", _explode, raising=True)
    fb, fb_calls = _fallback_factory()
    caller = az.make_primary_fallback_caller(fb, label="t")
    assert caller("p", 0.0, 16) == "fallback-result"
    assert fb_calls


def test_wrapper_no_secret_in_logs(monkeypatch, caplog):
    _configure_azure(monkeypatch)
    monkeypatch.setenv("ORCH_LLM_PRIMARY", "azure")
    _patch_openai(monkeypatch, raises=RuntimeError(f"network {_KEY}"))
    fb, _ = _fallback_factory()
    caller = az.make_primary_fallback_caller(fb, label="t")
    with caplog.at_level(logging.DEBUG):
        caller("p", 0.0, 16)  # primary 실패 → fallback (served_by 로그 발생)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert _KEY not in blob  # 어떤 캡처 로그에도 키 부재
    assert "served_by=fallback" in blob  # greppable 관찰 아티팩트
