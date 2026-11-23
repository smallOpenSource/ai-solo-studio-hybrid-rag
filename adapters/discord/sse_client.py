"""US-002 — SSE 소비 클라이언트 (httpx 스트리밍 + 재연결).

web_server /events/stream 을 async 소비해 (seq, payload_dict) 흐름을 yield.
discord 모듈 무의존. httpx 만 사용(신규 의존성 0).

★ seq 출처: SSE 프레임 `id:` 라인 단독 — payload dict 에는 없음(app.py:104).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# 재연결 backoff 파라미터
_BACKOFF_BASE: float = 1.0
_BACKOFF_MAX: float = 30.0

# SSE 소비 타임아웃 — SSE는 long-lived 스트림이고 이벤트가 드물게 오므로
# read=None(읽기 무제한)이 정답이다. read 상한을 두면 ping(15s) 사이 침묵을
# ReadTimeout으로 오인해 재연결 폭주를 일으킨다(H-01). connect만 상한을 둔다.
_SSE_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)


# ── 순수 파서 헬퍼 ────────────────────────────────────────────────────────────


def _parse_sse_lines(lines: list[str]) -> list[tuple[int, dict]]:
    """SSE 라인 목록(빈 줄 포함)을 프레임 단위로 파싱해 (seq, payload) 리스트 반환.

    규칙:
    - 빈 줄이 프레임 경계(1 프레임 완성 트리거).
    - `id: {n}` 라인 → seq(int) 설정.
    - `data: {json}` 라인 → payload = json.loads(값).
    - `: ...` 로 시작하는 코멘트(`:ping` 포함) → 건너뜀.
    - seq 와 payload 둘 다 있어야 yield; 하나라도 없으면 프레임 버림.
    - json.JSONDecodeError / 빈 data / 깨진 seq → 건너뜀(흐름 불중단).

    이 함수는 순수(I/O 없음) — 단위 테스트가 직접 호출한다.
    """
    results: list[tuple[int, dict]] = []
    current_seq: int | None = None
    current_payload: dict | None = None

    for raw_line in lines:
        # 빈 줄 → 프레임 경계
        if raw_line == "":
            if current_seq is not None and current_payload is not None:
                results.append((current_seq, current_payload))
            # 프레임 초기화
            current_seq = None
            current_payload = None
            continue

        # 코멘트 라인(`: ping` 등) → 건너뜀
        if raw_line.startswith(":"):
            continue

        # `id:` 라인
        if raw_line.startswith("id:"):
            raw_val = raw_line[3:].strip()
            try:
                current_seq = int(raw_val)
            except (ValueError, TypeError):
                # 파싱 불가 seq → 이 프레임 버림
                current_seq = None
            continue

        # `data:` 라인
        if raw_line.startswith("data:"):
            raw_val = raw_line[5:].strip()
            if not raw_val:
                # 빈 data → 건너뜀
                continue
            try:
                current_payload = json.loads(raw_val)
            except json.JSONDecodeError:
                # 깨진 JSON → 건너뜀
                current_payload = None
            continue

        # 그 외 필드(event:, retry: 등) → 무시

    # 파일 끝에 빈 줄 없이 끝난 경우도 처리
    if current_seq is not None and current_payload is not None:
        results.append((current_seq, current_payload))

    return results


# ── async generator ───────────────────────────────────────────────────────────


async def consume_events(
    base_url: str,
    last_event_id: int | None = None,
    max_retries: int | None = None,
    *,
    cookie: str | None = None,
    on_unauthorized: "Callable[[], Awaitable[str | None]] | None" = None,
) -> AsyncIterator[tuple[int, dict]]:
    """web_server /events/stream 을 무기한 소비하는 async generator.

    각 SSE 프레임을 (seq: int, payload: dict) 로 yield.

    Args:
        base_url: web_server 루트 URL (예: "http://localhost:8000").
        last_event_id: 재연결 커서. None 이면 처음부터(0), int 이면 해당 seq
                       이후부터 수신(Last-Event-ID 헤더로 전달).
        max_retries: 연속 재연결 실패 허용 횟수. None(기본)이면 무기한 재시도
                     (프로덕션 — "무기한 소비" 설계 유지). 유한값이면 그만큼
                     연속 실패 후 소비를 중단한다(테스트·운영에서 무한 스핀 방지).
                     성공 연결 시 카운터는 0으로 리셋된다.
        cookie: 세션 쿠키(완성 Cookie 헤더 값·US-G6-0). 있으면 매 연결 요청 헤더에
                Cookie 로 첨부(Last-Event-ID 와 공존). None(기본)이면 미첨부 —
                기존 동작 무변(후방호환).
        on_unauthorized: 401 수신 시 호출할 비동기 재인증 콜백(기본 None → 기존 동작
                         무변). 반환값이 truthy 면 그 값을 새 cookie 로 채택해 다음
                         재연결에 사용한다. ★호출자가 바운드(재로그인 1회)로 구현해야
                         하며, 이 분기도 기존 backoff+max_retries 머신에 그대로 얹어
                         무한 스핀 0 을 유지한다(sleep(backoff) 경유).

    동작:
    - `?idle_close_ms=` 쿼리는 절대 붙이지 않는다(무기한 소비).
    - `: ping` 코멘트·빈 data·깨진 JSON → 건너뜀(흐름 불중단).
    - 네트워크 끊김(httpx 예외) → backoff(1→2→4→…최대 30초) 후
      마지막 수신 seq 를 Last-Event-ID 로 재요청(결손 0).
    - web_server 미기동(ConnectError) → 크래시 아닌 재시도 로그.
    """
    url = f"{base_url.rstrip('/')}/events/stream"
    # last_event_id 추적: 재연결 시 갱신
    cursor: int | None = last_event_id
    backoff: float = _BACKOFF_BASE
    fail_count: int = 0  # 연속 재연결 실패 카운터(max_retries 가드용)

    while True:
        headers: dict[str, str] = {}
        if cursor is not None:
            headers["Last-Event-ID"] = str(cursor)
        if cookie:
            headers["Cookie"] = cookie

        try:
            async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    # 재연결 성공 → backoff·실패 카운터 초기화
                    backoff = _BACKOFF_BASE
                    fail_count = 0

                    # 라인 버퍼 누적 후 빈 줄 경계마다 프레임 파싱
                    current_seq: int | None = None
                    current_payload: dict | None = None

                    async for raw_line in response.aiter_lines():
                        line = raw_line  # 이미 str, 개행 제거됨

                        # 빈 줄 → 프레임 경계
                        if line == "":
                            if current_seq is not None and current_payload is not None:
                                cursor = current_seq  # 재연결 커서 전진
                                yield (current_seq, current_payload)
                            current_seq = None
                            current_payload = None
                            continue

                        # 코멘트(`: ping` 등) → 건너뜀
                        if line.startswith(":"):
                            continue

                        # `id:` 라인
                        if line.startswith("id:"):
                            raw_val = line[3:].strip()
                            try:
                                current_seq = int(raw_val)
                            except (ValueError, TypeError):
                                current_seq = None
                            continue

                        # `data:` 라인
                        if line.startswith("data:"):
                            raw_val = line[5:].strip()
                            if not raw_val:
                                continue
                            try:
                                current_payload = json.loads(raw_val)
                            except json.JSONDecodeError:
                                logger.debug(
                                    "SSE data JSON 파싱 실패, 건너뜀: %.120s", raw_val
                                )
                                current_payload = None
                            continue

        except httpx.ConnectError as exc:
            fail_count += 1
            logger.warning(
                "SSE 연결 실패(서버 미기동?), %.1f초 후 재시도: %s",
                backoff,
                exc,
            )
        except httpx.HTTPStatusError as exc:
            fail_count += 1
            logger.warning(
                "SSE HTTP 오류 %s, %.1f초 후 재시도",
                exc.response.status_code,
                backoff,
            )
            # 401(세션 만료/미인증) + 재인증 콜백 주어지면 1회 바운드 재인증 시도.
            # ★이 분기도 아래 max_retries 가드 + sleep(backoff) 머신을 그대로 경유한다
            #   → 재인증이 갱신 쿠키를 돌려주든 None 이든 무한 스핀 0(콜백은 호출자가
            #   재로그인 1회 상한으로 구현). 새 쿠키는 다음 재연결 헤더에 반영된다.
            if exc.response.status_code == 401 and on_unauthorized is not None:
                new = await on_unauthorized()
                if new:
                    cookie = new
        except httpx.RequestError as exc:
            fail_count += 1
            logger.warning(
                "SSE 네트워크 오류, %.1f초 후 재시도(cursor=%s): %s",
                backoff,
                cursor,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            fail_count += 1
            logger.error(
                "SSE 예상치 못한 오류, %.1f초 후 재시도: %s",
                backoff,
                exc,
            )

        # ★max_retries 가드: 연속 실패가 한도 도달 시 소비 중단(무한 스핀 방지).
        # None(기본)이면 무기한 재시도 — 프로덕션 "무기한 소비" 설계 불변.
        if max_retries is not None and fail_count >= max_retries:
            logger.error(
                "SSE 재연결 %d회 연속 실패 — 소비 중단(max_retries=%d)",
                fail_count,
                max_retries,
            )
            return
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _BACKOFF_MAX)
