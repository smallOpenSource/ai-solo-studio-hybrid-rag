"""web_server/auth.py — 인메모리 opaque 세션 인증 (단일 워커·workers=1 전제).

★설계(architect 반영): 서명 토큰(HMAC)이 아니라 **추측 불가 랜덤 토큰**(secrets.token_urlsafe)을
발급하고 서버 메모리 dict에 만료시각과 함께 저장한다. 토큰 자체에 의미가 없으므로(opaque)
위조가 불가능하고, HMAC secret의 출처/유출 문제가 원천 소거된다.

- 암호는 서버측 .env(LOGIN_PASSWORD)에서만 읽어 상수시간 비교(hmac.compare_digest) —
  클라이언트 번들/응답/로그에 평문 노출 0.
- 단일 워커(scripts/serve.sh·__main__.py가 uvicorn workers=1 고정)라 프로세스 메모리 dict
  공유가 안전하다. 재시작 시 전 세션 무효(인메모리·PoC 수용).
- 시간 의존 함수는 `now` 인자를 받아 결정론 테스트 가능(내부 time.time 폴백).

엔진/이벤트 모듈과 무관(워크플로 이벤트 아님). app.py 핸들러와 WS 게이트가 이 헬퍼만 호출한다.
"""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Optional

from core.config import get_web_config, load_env

# 토큰(opaque) → 만료 epoch초. 단일 워커 프로세스 메모리(workers=1 고정).
_SESSIONS: dict[str, float] = {}

# 기본값(endpoint.yaml web.auth 미설정 시).
_DEFAULT_PASSWORD_ENV = "LOGIN_PASSWORD"
_DEFAULT_COOKIE_NAME = "omc_session"
_DEFAULT_TTL_SECONDS = 86400


def _auth_cfg() -> dict:
    """endpoint.yaml web.auth 블록(없으면 빈 dict)."""
    return get_web_config().get("auth", {}) or {}


def cookie_name() -> str:
    """세션 쿠키 이름."""
    return _auth_cfg().get("cookie_name", _DEFAULT_COOKIE_NAME)


def ttl_seconds() -> int:
    """세션 수명(초)."""
    return int(_auth_cfg().get("ttl_seconds", _DEFAULT_TTL_SECONDS))


def _expected_password() -> str:
    """서버측 .env에서 로그인 암호를 읽는다(미설정 시 빈 문자열 → 잠금)."""
    key = _auth_cfg().get("password_env", _DEFAULT_PASSWORD_ENV)
    return load_env().get(key, "")


def check_password(candidate: Optional[str]) -> bool:
    """후보 암호를 서버 암호와 상수시간 비교한다.

    암호가 서버에 설정돼 있지 않으면 항상 False(개방 방지·기본 잠금).
    """
    expected = _expected_password()
    if not expected:
        return False
    return hmac.compare_digest(candidate or "", expected)


def create_session(now: Optional[float] = None) -> str:
    """새 세션을 만들고 opaque 토큰을 돌려준다(쿠키 값으로 사용).

    @param now 만료 기준 시각(테스트 주입용·기본 time.time()).
    """
    base = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = base + ttl_seconds()
    return token


def verify_session(token: Optional[str], now: Optional[float] = None) -> bool:
    """토큰이 존재하고 미만료면 True. 만료 토큰은 lazy 삭제한다.

    @param now 검증 기준 시각(테스트 주입용·기본 time.time()).
    """
    if not token:
        return False
    base = time.time() if now is None else now
    expiry = _SESSIONS.get(token)
    if expiry is None:
        return False  # 미지/위조 토큰.
    if base >= expiry:
        _SESSIONS.pop(token, None)  # 만료 — 청소.
        return False
    return True


def destroy_session(token: Optional[str]) -> None:
    """세션을 무효화한다(로그아웃). 없으면 무시."""
    if token:
        _SESSIONS.pop(token, None)


def _reset_for_tests() -> None:
    """테스트 격리용 — 모든 세션을 비운다."""
    _SESSIONS.clear()
