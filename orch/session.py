"""orch.session — opaque in-memory cookie session, byte-compatible with web_server/auth.py.

The live Discord adapter (adapters/discord/auth_client.login + bot.py) authenticates by
POSTing ``/api/login {password}`` and reading the ``Set-Cookie: omc_session=<opaque>``
token, then passes that cookie on every subsequent call. To be a FULL DROP-IN for that
adapter (so a later ``WEB_BASE_URL`` flip needs ZERO adapter edits), orch must reproduce
the SAME opaque-session contract that ``web_server/auth.py`` implements:

  * a guess-proof random token (``secrets.token_urlsafe(32)``) stored server-side in an
    in-memory dict with an expiry — the token is opaque (no meaning, no HMAC secret to
    leak), exactly like the live server.
  * the password is read server-side from the SAME source the live server uses — the repo
    ``.env`` key ``LOGIN_PASSWORD`` (endpoint.yaml ``web.auth.password_env`` default) — and
    compared in constant time (``hmac.compare_digest``). It is NEVER logged or echoed.
  * the cookie name/ttl mirror endpoint.yaml ``web.auth`` (``omc_session`` / 86400s).

Why a separate module (not ``import web_server.auth``): the orch conda env does NOT ship
``python-dotenv`` (web_server/auth.py -> core.config imports ``dotenv``), so importing it
would crash. orch/graph.py already loads ``.env`` with a stdlib parser for the same reason;
this module reuses that discipline and reads ONLY the password key. office/web_server stay
UNTOUCHED.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]  # /opt/studio

# Mirror endpoint.yaml web.auth defaults (cookie name + ttl + password env key). These
# match web_server/auth.py so the adapter's hardcoded ``omc_session`` cookie name lines up.
_COOKIE_NAME = "omc_session"
_TTL_SECONDS = 86400
_PASSWORD_ENV = "LOGIN_PASSWORD"

# opaque token -> expiry epoch seconds. Single-process in-memory dict (orch runs one
# uvicorn worker), same model as web_server/auth.py's _SESSIONS. Restart invalidates all.
_SESSIONS: dict[str, float] = {}


def cookie_name() -> str:
    """Session cookie name (matches the adapter's hardcoded ``omc_session``)."""
    return _COOKIE_NAME


def ttl_seconds() -> int:
    """Session lifetime in seconds."""
    return _TTL_SECONDS


def _read_password_from_env_file() -> str:
    """Read LOGIN_PASSWORD from the repo .env with a stdlib parser (no python-dotenv).

    Mirrors core.config.load_env precedence: os.environ wins over the .env file. Returns
    "" when unset (which locks login — matches web_server/auth.py's default-locked policy).
    The value is never logged.
    """
    env_val = os.environ.get(_PASSWORD_ENV)
    if env_val:
        return env_val
    path = _REPO_ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == _PASSWORD_ENV:
            return val.strip().strip("'").strip('"')
    return ""


def check_password(candidate: Optional[str]) -> bool:
    """Constant-time compare the candidate against the server password.

    If no password is configured, always False (default-locked — never opens). Mirrors
    web_server/auth.py::check_password byte-for-byte.
    """
    expected = _read_password_from_env_file()
    if not expected:
        return False
    return hmac.compare_digest(candidate or "", expected)


def create_session(now: Optional[float] = None) -> str:
    """Mint a new session and return its opaque token (used as the cookie value)."""
    base = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = base + ttl_seconds()
    return token


def verify_session(token: Optional[str], now: Optional[float] = None) -> bool:
    """True iff the token exists and is unexpired; expired tokens are lazily evicted."""
    if not token:
        return False
    base = time.time() if now is None else now
    expiry = _SESSIONS.get(token)
    if expiry is None:
        return False
    if base >= expiry:
        _SESSIONS.pop(token, None)
        return False
    return True


def destroy_session(token: Optional[str]) -> None:
    """Invalidate a session (logout). No-op if absent."""
    if token:
        _SESSIONS.pop(token, None)


def _reset_for_tests() -> None:
    """Test isolation — clear all sessions."""
    _SESSIONS.clear()
