"""web_server/inbound.py — 디스코드 양방향 인바운드 엔드포인트(DC-2 · Opt 2-A).

이 모듈이 하는 일(계획 ralplan-office-polish-discord.md §4 DC-2):
- POST /api/inbound : 디스코드 봇(adapters/discord/bot.py on_message)이 채널의 사람
  발화를 받아 호출한다. body {text, message_id, author?}:
    ① 화면 에코: EventStore.append(TextMessage(run_id="discord-{message_id}",
       role="user", content=text)) → FanoutPoller(app.py)가 SSE/WS/Discord로 자동
       팬아웃(웹 ChatPanel·OfficeScene 말풍선 갱신, Discord는 post_text=true면 게시).
    ② 응답 트리거: TaskQueue.enqueue(workflow="chat", payload={"query": text}) →
       워커가 query를 initial_state["messages"]로 시딩 → chat 노드 → assistant
       TEXT_MESSAGE append → 팬아웃 → SSE consumer → 디스코드 회신(별도 배선 0).

★멱등(C1·content 비오염·영속):
- 멱등키는 **run_id="discord-{message_id}"**(content 인코딩 금지). 같은 message_id가
  이미 events에 있으면(봇 재전송·중복) no-op로 200 duplicate(append·enqueue 0).
  content엔 순수 발화만 담아 ChatPanel/deriveScene/mapping 누출을 원천 차단한다.
- 영속: EventStore가 SQLite라 봇 재시작/크래시 후 같은 message_id 재전송도 흡수
  (재시작 무손실).

★run_id 정책(계획 §4 DC-2 #4): 에코 user run_id = "discord-{message_id}"(멱등키).
응답 assistant run_id = 워커가 enqueue 시 생성하는 별도 값(인바운드 시점엔 미지). 둘이
상이하나 무해(SSE/WS/Discord 팬아웃은 seq 기반·deriveScene 캐리포워드는 run_id 비종속).

설계(엔진·계약 비침투):
- 신규 이벤트 0(기존 TextMessage role=user 재사용). events 스키마 불변(json_extract
  불요 — run_id 단위 조회는 EventStore.read_rows 기존 메서드 재사용).
- 라우터는 create_app이 include_router로 마운트한다. DB 경로는 app.state.store에서
  읽어 같은 SQLite 파일을 공유한다(inbox/runner 동형).

LLM은 직접 호출하지 않는다(워커 경유 — 이 모듈에 LLM 호출 경로 0).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from office.event_store import EventStore
from office.events import TextMessage
from office.task_queue import TaskQueue

# 인바운드 트리거 워크플로 — W-2 chat(자유대화 단일 노드). 기존 query seam 재사용.
_INBOUND_WORKFLOW = "chat"


def _db_path_of(request: Request) -> Any:
    """app.state.store의 DB 경로를 돌려준다(웹·워커 동일 SQLite 공유 — inbox/runner 동형)."""
    return request.app.state.store._db_path


def _now_iso() -> str:
    """현재 시각 ISO-8601(UTC)."""
    return datetime.now(timezone.utc).isoformat()


def create_inbound_router() -> APIRouter:
    """디스코드 인바운드 라우터를 만든다(create_app이 include_router로 마운트).

    같은 store(app.state.store)의 DB를 공유해 에코 append·chat enqueue가 워커와 한
    SQLite를 본다(웹은 워크플로를 실행하지 않는다 — chat 응답은 워커가 처리).
    """
    router = APIRouter(prefix="/api/inbound", tags=["inbound"])

    @router.post("")
    def post_inbound(request: Request, body: dict[str, Any] | None = None) -> Any:
        """디스코드 사람 발화를 화면 에코 + chat 응답 트리거(멱등·content 비오염).

        body: {"text": str, "message_id": str, "author"?: str}. text/message_id가
        비거나 누락이면 400. 같은 message_id 재호출은 영속 멱등으로 200 duplicate.
        """
        body = body or {}
        text = body.get("text")
        message_id = body.get("message_id")
        target_role = body.get("target_role")  # ★멀티봇 @멘션: 지목 직원 역할(선택).

        # 빈/누락 검증 — text·message_id는 필수(author는 선택, 현재 미사용).
        if not isinstance(text, str) or not text:
            return JSONResponse(status_code=400, content={"detail": "text is required"})
        if not isinstance(message_id, str) or not message_id:
            return JSONResponse(
                status_code=400, content={"detail": "message_id is required"}
            )

        run_id = f"discord-{message_id}"
        db_path = _db_path_of(request)
        store = EventStore(db_path=db_path)

        # ① 영속 멱등 가드(C1/M3): 이 run_id 이벤트가 이미 있으면 no-op(중복 트리거 0).
        #    run_id 단위 조회는 EventStore.read_rows 재사용(content 미사용·스키마 불변).
        if store.read_rows(run_id):
            return JSONResponse(
                status_code=200,
                content={"status": "duplicate", "run_id": run_id},
            )

        # ② 화면 에코 — content엔 순수 발화만(인코딩 금지·deriveScene/mapping 누출 차단).
        store.append(
            TextMessage(run_id=run_id, ts=_now_iso(), role="user", content=text)
        )

        # ③ 응답 트리거 — query seam + ★멀티봇 라우팅. target_role(지목 직원)을 받으면
        #    응답 run_id를 chat-{role}-{message_id}로 인코딩해 payload에 싣는다. 워커가 그
        #    run_id로 실행 → 응답 이벤트가 chat-{role}-로 찍혀 그 직원 봇이 자기 정체성으로
        #    게시(@멘션 가능). 미지목이면 role="default"(primary 비서가 게시). ★에코 run_id
        #    (discord-{id})·멱등·대화기억(workflow==chat 필터)은 전부 무변.
        conv_role = (
            target_role if isinstance(target_role, str) and target_role else "default"
        )
        reply_run_id = f"chat-{conv_role}-{message_id}"
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(
            _INBOUND_WORKFLOW,
            payload={"query": text, "target_role": conv_role, "run_id": reply_run_id},
        )

        return JSONResponse(
            status_code=200,
            content={"status": "accepted", "task_id": task_id, "run_id": run_id},
        )

    return router
