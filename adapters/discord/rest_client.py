"""Discord 어댑터 — 승인 REST 클라이언트 (US-004).

POST /api/inbox/{seq}/approve  또는  /api/inbox/{seq}/reject 를 httpx로 호출한다.
discord 패키지 무의존 — httpx만 사용.

200·404 멱등 처리 방침
-----------------------
- 200 : 정상 처리 또는 이미 처리된 seq 재approve(INTERRUPT_REQUESTED row 영구존재
        → office/approve.py:205 RESOLVED 중복 적재, 무가드). 어댑터는 "처리됨"으로 표시.
- 404 : INTERRUPT_REQUESTED row 자체가 없는 seq(존재한 적 없음).
        어댑터 관점에서도 "처리됨(handled=True)"으로 표시해 버튼 followup에 성공으로 응답.
        ★ RESOLVED 중복 적재는 office 책임이며 어댑터가 차단 불가.
- 5xx / 네트워크 오류 : handled=False, error=메시지 반환. 크래시 없음.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# localhost web_server로의 짧은 승인/거부 REST 호출 타임아웃
# (무명시 시 httpx 기본 5s 의존 회피 — H-08).
_LOCAL_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _auth_headers(cookie: str | None) -> dict[str, str]:
    """세션 쿠키(완성된 Cookie 헤더 값)를 헤더 dict 로 변환.

    cookie 는 "omc_session=<token>" 형태의 완성 Cookie 헤더 값이다(US-G6-0).
    None/빈 값이면 빈 dict — 미인증 진행(프로덕션이면 401, 후방호환 무변).
    """
    return {"Cookie": cookie} if cookie else {}


@dataclass
class ApproveResult:
    """승인/거부 REST 호출 결과."""

    handled: bool
    """True = 200 또는 404(둘 다 '처리됨/멱등'). False = 5xx/네트워크 오류."""
    status: int | None
    """HTTP 상태 코드. 네트워크 오류 시 None."""
    error: str | None
    """오류 메시지. 성공 시 None."""


async def _call(
    seq: int, action: str, base_url: str, *, cookie: str | None = None
) -> ApproveResult:
    """공통 호출 — action은 'approve' 또는 'reject'."""
    url = f"{base_url.rstrip('/')}/api/inbox/{seq}/{action}"
    try:
        async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
            resp = await client.post(url, headers=_auth_headers(cookie))
        status = resp.status_code
        if status in (200, 404):
            # 200: 정상 처리 또는 이미 처리된 seq 재호출(무해).
            # 404: seq 자체가 DB에 없음 — 사용자 입장에선 "처리됨"으로 알림.
            return ApproveResult(handled=True, status=status, error=None)
        # 5xx 등
        return ApproveResult(
            handled=False,
            status=status,
            error=f"서버 오류: HTTP {status}",
        )
    except httpx.HTTPError as exc:
        return ApproveResult(handled=False, status=None, error=str(exc))


@dataclass
class InboundResult:
    """인바운드(POST /api/inbound) REST 호출 결과(DC-2)."""

    handled: bool
    """True = 200/202(에코 append + chat enqueue 또는 멱등 duplicate). False = 5xx/네트워크."""
    status: int | None
    """HTTP 상태 코드. 네트워크 오류 시 None."""
    error: str | None
    """오류 메시지. 성공 시 None."""


async def post_inbound(
    text: str,
    message_id: str,
    base_url: str,
    author: str | None = None,
    *,
    cookie: str | None = None,
    target_role: str | None = None,
) -> InboundResult:
    """POST {base_url}/api/inbound — 디스코드 사람 발화를 웹으로 전달(DC-2 · Opt 2-A).

    봇(bot.py on_message)이 지정 채널의 사람 발화를 받아 호출한다. 웹이 화면 에코
    (TextMessage role=user) + chat 응답 트리거(enqueue)를 한다. 응답 회신은 SSE
    팬아웃이 자동 처리(post_text=true 전제).

    Args:
        text:       사람 발화 원문(content엔 순수 발화만 — 인코딩 금지).
        message_id: 디스코드 메시지 id 문자열(멱등키 run_id="discord-{message_id}"의 근거).
        base_url:   웹 서버 기본 URL.
        author:     발화자 표시명(선택·현재 미사용). 호환을 위해 받되 body로 전달만.

    Returns:
        InboundResult. 200/202(accepted/duplicate) → handled=True, 5xx/네트워크 →
        handled=False(크래시 0 — approve/reject와 동형).
    """
    url = f"{base_url.rstrip('/')}/api/inbound"
    body: dict[str, str] = {"text": text, "message_id": message_id}
    if author is not None:
        body["author"] = author
    # ★멀티봇 @멘션: 지목된 직원 역할 — 서버가 응답 run_id를 chat-{role}-{id}로 인코딩해
    # 그 직원 봇이 자기 정체성으로 게시하게 한다(디스패치 키). 미지목이면 미포함(폴백).
    if target_role:
        body["target_role"] = target_role
    try:
        async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=_auth_headers(cookie))
        status = resp.status_code
        if status in (200, 202):
            return InboundResult(handled=True, status=status, error=None)
        return InboundResult(
            handled=False,
            status=status,
            error=f"서버 오류: HTTP {status}",
        )
    except httpx.HTTPError as exc:
        return InboundResult(handled=False, status=None, error=str(exc))


@dataclass
class RunResult:
    """워크플로 enqueue(POST /api/run) REST 호출 결과(US-G6-1)."""

    handled: bool
    """True = 200(enqueue 성공). False = 5xx/4xx/네트워크 오류."""
    status: int | None
    """HTTP 상태 코드. 네트워크 오류 시 None."""
    task_id: int | None
    """enqueue 된 task_id(200 응답 본문에서 추출). 실패 시 None."""
    error: str | None
    """오류 메시지. 성공 시 None."""


async def post_run(
    workflow: str, base_url: str, *, cookie: str | None = None, mock: bool = False
) -> RunResult:
    """POST {base_url}/api/run — 워크플로를 task_queue에 enqueue(US-G6-1).

    봇(bot.py on_message)이 자연어를 게임 단계 지시로 분류했을 때 호출한다. 웹은
    실행하지 않고 enqueue만 하며(별도 워커 프로세스가 claim+run), 200 응답으로
    {"status":"enqueued","task_id":...}를 돌려준다.

    Args:
        workflow: 워크플로 이름(예: "game_qa"). classify_intent가 매핑한 game_* 이름.
        base_url: 웹 서버 기본 URL.
        cookie:   세션 쿠키(완성 Cookie 헤더 값·US-G6-0). None이면 미인증 진행(후방호환).
        mock:     True면 워커가 결정론 fake LLM으로 실행(검증용·payload로 전달).

    Returns:
        RunResult. 200 → handled=True + task_id, 그 외/네트워크 → handled=False
        (크래시 0 — approve/reject와 동형).
    """
    url = f"{base_url.rstrip('/')}/api/run"
    body: dict[str, object] = {"workflow": workflow, "mock": mock}
    try:
        async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=_auth_headers(cookie))
        status = resp.status_code
        if status == 200:
            task_id = None
            try:
                data = resp.json()
                raw_id = data.get("task_id")
                task_id = int(raw_id) if raw_id is not None else None
            except Exception:  # noqa: BLE001
                # 본문 파싱 실패는 enqueue 성공을 무효화하지 않는다(task_id만 None).
                task_id = None
            return RunResult(handled=True, status=200, task_id=task_id, error=None)
        return RunResult(
            handled=False,
            status=status,
            task_id=None,
            error=f"서버 오류: HTTP {status}",
        )
    except httpx.HTTPError as exc:
        return RunResult(handled=False, status=None, task_id=None, error=str(exc))


async def approve(
    seq: int, base_url: str, *, cookie: str | None = None
) -> ApproveResult:
    """POST /api/inbox/{seq}/approve.

    Args:
        seq: 인터럽트 seq (DB primary key).
        base_url: 웹 서버 기본 URL. 호출자가 WEB_BASE_URL 환경변수 등에서 주입.
        cookie: 세션 쿠키(완성 Cookie 헤더 값·US-G6-0). None이면 미인증 진행(후방호환).
    """
    return await _call(seq, "approve", base_url, cookie=cookie)


async def reject(
    seq: int, base_url: str, *, cookie: str | None = None
) -> ApproveResult:
    """POST /api/inbox/{seq}/reject.

    Args:
        seq: 인터럽트 seq (DB primary key).
        base_url: 웹 서버 기본 URL. 호출자가 WEB_BASE_URL 환경변수 등에서 주입.
        cookie: 세션 쿠키(완성 Cookie 헤더 값·US-G6-0). None이면 미인증 진행(후방호환).
    """
    return await _call(seq, "reject", base_url, cookie=cookie)
