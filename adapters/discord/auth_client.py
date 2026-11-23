"""US-G6-0 — 봇 세션 인증 클라이언트(로그인→opaque 세션 쿠키).

프로덕션 web_server(auth_required=True)는 /api/*(login/logout/status 제외)+
/events/stream 을 미인증 시 401로 막는다. 봇이 회사를 운영하려면(SSE 소비·승인·
인바운드·워크플로 enqueue) 먼저 세션 쿠키를 얻어야 한다.

POST /api/login {password} → Set-Cookie(omc_session=<opaque>) 에서 토큰을 추출해
돌려준다(httpx만·discord/office/web_server import 0).

★비번 비노출: password 는 호출자가 env(LOGIN_PASSWORD)에서 읽어 주입한다. 이 모듈은
로그·예외 메시지에 password 를 절대 기록하지 않는다(실패는 상태코드/일반 사유만).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_LOCAL_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

_DEFAULT_COOKIE_NAME = "omc_session"


async def login(
    base_url: str,
    password: str,
    *,
    cookie_name: str = _DEFAULT_COOKIE_NAME,
) -> str | None:
    """POST {base_url}/api/login 으로 로그인해 세션 토큰(쿠키 값)을 돌려준다.

    Returns 세션 토큰(쿠키 값). 실패({ok:false}·비200·쿠키 부재·네트워크) 시 None
    (크래시 0·비번 비노출).
    """
    if not password:
        logger.warning("로그인 암호 미설정 — 세션 인증 건너뜀(미인증 호출은 401).")
        return None

    url = f"{base_url.rstrip('/')}/api/login"
    try:
        async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
            resp = await client.post(url, json={"password": password})
    except httpx.HTTPError as exc:
        logger.warning("로그인 요청 실패(네트워크) — 미인증 진행: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning("로그인 비정상 응답(status=%s) — 미인증 진행.", resp.status_code)
        return None
    try:
        ok = bool(resp.json().get("ok", False))
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        logger.warning("로그인 거부(암호 불일치) — 미인증 진행.")
        return None

    token = resp.cookies.get(cookie_name)
    if not token:
        logger.warning("로그인 성공이나 세션 쿠키(%s) 부재 — 미인증 진행.", cookie_name)
        return None
    return token
