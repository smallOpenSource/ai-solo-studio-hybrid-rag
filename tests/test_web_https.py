"""US-05 HTTPS 서빙 설정 — get_web_config ssl passthrough + __main__.main() kwargs 분기.

★실서버 미기동(uvicorn.run mock) — hang0 보장.
오프라인 결정론(LLM 0 · 파일시스템 접근 mock). slow 마커 없음.

테스트 범위(US-05 AC1·AC2):
- AC1: get_web_config()가 ssl_certfile·ssl_keyfile 키를 반환한다.
  - endpoint.yaml에 값이 있으면 경로 문자열 반환.
  - web 블록에 키가 없으면 None 반환.
- AC2: main() kwargs 분기 — uvicorn.run mock으로 실서버 미기동.
  - ssl 키 없으면(None) http 폴백(ssl 인자 없이 uvicorn.run 호출).
  - ssl 키 있고 os.access 모두 True → kwargs에 ssl_certfile·ssl_keyfile 포함.
  - ssl 키 있으나 os.access 중 하나라도 False → http 폴백(ssl 인자 없이 호출).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.config import get_web_config


# ── AC1: get_web_config ssl passthrough ──────────────────────────────────────


def test_get_web_config_returns_ssl_certfile_and_keyfile() -> None:
    """get_web_config()가 ssl_certfile·ssl_keyfile 키를 반환한다(값 있을 때).

    endpoint.yaml에 ssl_certfile·ssl_keyfile이 설정돼 있으면 경로 문자열을 돌려준다.
    """
    cfg = get_web_config()
    assert "ssl_certfile" in cfg, "ssl_certfile 키 누락"
    assert "ssl_keyfile" in cfg, "ssl_keyfile 키 누락"
    # 현재 endpoint.yaml에 경로가 설정돼 있으므로 None이 아닌 문자열.
    assert cfg["ssl_certfile"] is not None
    assert cfg["ssl_keyfile"] is not None


def test_get_web_config_ssl_none_when_yaml_has_no_ssl_keys() -> None:
    """web 블록에 ssl_* 키가 없으면 ssl_certfile·ssl_keyfile이 None."""
    fake_yaml: dict[str, Any] = {
        "web": {
            "host": "127.0.0.1",
            "port": 8800,
            "static_dist": "web/dist",
            "static_work": "work",
            # ssl_certfile / ssl_keyfile 없음
        }
    }
    with patch("core.config.load_endpoint_config", return_value=fake_yaml):
        cfg = get_web_config()
    assert cfg["ssl_certfile"] is None
    assert cfg["ssl_keyfile"] is None
    # 기존 4키 불변
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 8800
    assert cfg["static_dist"] == "web/dist"
    assert cfg["static_work"] == "work"


# ── AC2: main() kwargs 분기 (uvicorn.run mock — 실서버 미기동) ──────────────────


def _make_fake_cfg(
    *,
    cert: str | None,
    key: str | None,
) -> dict[str, Any]:
    return {
        "host": "0.0.0.0",
        "port": 33006,
        "static_dist": "web/dist",
        "static_work": "work",
        "ssl_certfile": cert,
        "ssl_keyfile": key,
    }


def test_main_no_ssl_keys_calls_uvicorn_without_ssl() -> None:
    """ssl 키가 None이면 uvicorn.run에 ssl 인자를 넘기지 않는다(HTTP 폴백)."""
    fake_cfg = _make_fake_cfg(cert=None, key=None)
    mock_run = MagicMock()
    with (
        patch("web_server.__main__.get_web_config", return_value=fake_cfg),
        patch("web_server.__main__.create_app", return_value=MagicMock()),
        patch("web_server.__main__.uvicorn.run", mock_run),
    ):
        from web_server.__main__ import main

        main()

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert "ssl_certfile" not in kwargs
    assert "ssl_keyfile" not in kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 33006
    assert kwargs["workers"] == 1


def test_main_ssl_readable_includes_ssl_kwargs() -> None:
    """ssl 키 있고 os.access 모두 True → uvicorn.run에 ssl_certfile·ssl_keyfile 포함."""
    fake_cfg = _make_fake_cfg(
        cert="/etc/studio/ssl/studio.example.com/fullchain.pem",
        key="/etc/studio/ssl/studio.example.com/privkey.pem",
    )
    mock_run = MagicMock()
    with (
        patch("web_server.__main__.get_web_config", return_value=fake_cfg),
        patch("web_server.__main__.create_app", return_value=MagicMock()),
        patch("web_server.__main__.uvicorn.run", mock_run),
        # os.access → 항상 True(파일 실재 불필요)
        patch("web_server.__main__.os.access", return_value=True),
    ):
        from web_server.__main__ import main

        main()

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["ssl_certfile"] == "/etc/studio/ssl/studio.example.com/fullchain.pem"
    assert kwargs["ssl_keyfile"] == "/etc/studio/ssl/studio.example.com/privkey.pem"
    assert kwargs["workers"] == 1


def test_main_ssl_not_readable_falls_back_to_http(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ssl 키 있으나 os.access False → 경고 출력 + HTTP 폴백(ssl 인자 없음)."""
    fake_cfg = _make_fake_cfg(
        cert="/etc/studio/ssl/studio.example.com/fullchain.pem",
        key="/etc/studio/ssl/studio.example.com/privkey.pem",
    )
    mock_run = MagicMock()
    with (
        patch("web_server.__main__.get_web_config", return_value=fake_cfg),
        patch("web_server.__main__.create_app", return_value=MagicMock()),
        patch("web_server.__main__.uvicorn.run", mock_run),
        # os.access → False(읽기 불가 시뮬)
        patch("web_server.__main__.os.access", return_value=False),
    ):
        from web_server.__main__ import main

        main()

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert "ssl_certfile" not in kwargs
    assert "ssl_keyfile" not in kwargs
    assert kwargs["workers"] == 1

    captured = capsys.readouterr()
    assert "ssl 파일 읽기 불가" in captured.out
    assert "HTTP 폴백" in captured.out


def test_main_ssl_only_cert_no_key_falls_back_to_http() -> None:
    """cert만 있고 key가 None이면 ssl 조건 미충족 → HTTP 폴백."""
    fake_cfg = _make_fake_cfg(
        cert="/etc/studio/ssl/studio.example.com/fullchain.pem",
        key=None,
    )
    mock_run = MagicMock()
    with (
        patch("web_server.__main__.get_web_config", return_value=fake_cfg),
        patch("web_server.__main__.create_app", return_value=MagicMock()),
        patch("web_server.__main__.uvicorn.run", mock_run),
    ):
        from web_server.__main__ import main

        main()

    _, kwargs = mock_run.call_args
    assert "ssl_certfile" not in kwargs
    assert "ssl_keyfile" not in kwargs
