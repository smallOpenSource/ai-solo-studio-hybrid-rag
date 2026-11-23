"""SMTP_FROM 분리 단언 (라이브스모크 트랙 B — Brevo 모던 계정 SMTP Login≠From 대응).

office/tools.py `_try_smtp_send` 의 msg['From'] 가
  - SMTP_FROM 설정 시 → 그 값(verified sender),
  - 미설정/공백 시 → SMTP_USER(★기존 동작 회귀 보존, Gmail 등 login=From 자연일치),
임을 **시뮬레이션 자격증명**으로 단언한다(실 SMTP 연결 0).
또한 발송차단 게이트(HOST/USER/PASS 가드)가 SMTP_FROM 단독으로 깨지지 않음을 단언한다.

★범위: 이 테스트는 msg['From'] 헤더 결정만 커버한다(send-count·멱등은 test_smoke_send_reorder.py,
발송차단은 test_smoke_smtp_gate.py 가 담당). From 개행주입은 EmailMessage/smtplib 자체 가드에 의존.
"""

from __future__ import annotations

import smtplib

import office.tools as tools


def _fake_smtp(capture: dict, connect_log: list):
    """smtplib.SMTP 대체 — send_message 의 msg 헤더를 캡처(실 연결 0)."""

    class _Fake:
        def __init__(self, host, port, timeout=None):
            connect_log.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            capture["From"] = msg["From"]
            capture["To"] = msg["To"]

    return _Fake


def test_smtp_from_overrides_login_when_set(monkeypatch):
    env = {
        "SMTP_HOST": "smtp-relay.example.net",
        "SMTP_PORT": "587",
        "SMTP_USER": "random-login@smtp-relay.example.net",  # relay random login
        "SMTP_PASS": "sim-smtp-key",
        "SMTP_FROM": "sender@example.com",  # verified sender
    }
    monkeypatch.setattr("core.config.load_env", lambda: env)
    cap, log = {}, []
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(cap, log))

    ok = tools._try_smtp_send("rcpt@example.com", "subj", "body")

    assert ok is True
    assert cap["From"] == "sender@example.com"  # SMTP_FROM 우선
    assert cap["To"] == "rcpt@example.com"
    assert log == [("smtp-relay.example.net", 587)]


def test_from_falls_back_to_user_when_smtp_from_absent(monkeypatch):
    # SMTP_FROM 미설정 → SMTP_USER (기존 동작 회귀 보존, 예: Gmail)
    env = {
        "SMTP_HOST": "smtp.example.org",
        "SMTP_PORT": "587",
        "SMTP_USER": "me@example.org",
        "SMTP_PASS": "sim-app-pw",
    }
    monkeypatch.setattr("core.config.load_env", lambda: env)
    cap, log = {}, []
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(cap, log))

    ok = tools._try_smtp_send("rcpt@example.com", "subj", "body")

    assert ok is True
    assert cap["From"] == "me@example.org"  # 폴백 = SMTP_USER


def test_blank_smtp_from_falls_back_to_user(monkeypatch):
    # 공백뿐인 SMTP_FROM → strip() 후 falsy → SMTP_USER 폴백
    env = {
        "SMTP_HOST": "h",
        "SMTP_PORT": "587",
        "SMTP_USER": "login@example.com",
        "SMTP_PASS": "p",
        "SMTP_FROM": "   ",
    }
    monkeypatch.setattr("core.config.load_env", lambda: env)
    cap, log = {}, []
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(cap, log))

    ok = tools._try_smtp_send("r@example.com", "s", "b")

    assert ok is True
    assert cap["From"] == "login@example.com"


def test_gate_blocks_send_when_host_absent_even_with_smtp_from(monkeypatch):
    # SMTP_FROM 만 있고 HOST/USER/PASS 부재 → 발송 0(발송차단 게이트 보존·332 가드 우선)
    env = {"SMTP_FROM": "sender@example.com"}
    monkeypatch.setattr("core.config.load_env", lambda: env)
    cap, log = {}, []
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(cap, log))

    ok = tools._try_smtp_send("r@example.com", "s", "b")

    assert ok is False  # host/user/password 부재 → return False (341 도달 안 함)
    assert log == []  # smtplib.SMTP 생성 시도 0 (실 연결 0)
