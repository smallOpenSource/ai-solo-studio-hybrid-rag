"""web_server/inbox.py — Phase 4a Agent Inbox API + 승인 배선(US-003 / T3).

이 모듈이 하는 일:
- GET /api/inbox : 이벤트를 Review / Notify / Question 3분류로 묶어 반환한다
  (계획 §4.5 매핑).
    * Review   ← INTERRUPT_REQUESTED 미해소(= office.approve._all_pending_interrupts).
                 [승인]/[거부] 버튼이 달리는 칸(게이트의 핵심).
    * Notify   ← NODE_FAILED(escalation) · RUN_FINISHED · RUN_ERROR ·
                 NODE_FAILED(gating). 읽음만(버튼 액션 불필요).
    * Question ← NODE_FAILED(question). 4a는 분류 칸·렌더만(되묻기→재개 트리거는
                 후속). reason=question은 events.FailReason에 Phase 4a에서 추가됨.
- POST /api/inbox/{interrupt_id}/approve|reject :
    office.approve._record_decision(interrupt_id, decision, resume=False)를 호출한다
    (계획 §4.6 M3). resume=False라 INTERRUPT_RESOLVED 발행 + state/approvals/<id>.json
    파일 기록만 하고 TaskQueue.resume는 건너뛴다(task 상태 불변). 실제 재개는 워크플로
    전용 스레드의 폴링 루프(resume_after_approval)가 US-004에서 단일 담당한다 —
    resume 이중 트리거(상태 천이 경합)를 막기 위함.

설계(엔진 비침투):
- Notify / Question 분류용 이벤트 조회는 EventStore.read_since(0)로 원문 payload_json을
  읽어 type/reason만 본다(event_from_dict 미경유 — 잘못된 reason이 들어 있어도 분류가
  깨지지 않고, NODE_FAILED가 아닌 행은 그대로 건너뜀). Review 목록은 office.approve의
  _all_pending_interrupts를 그대로 재사용한다(중복 구현 0 — 얇은 게이트웨이 원칙).
- 라우터는 create_app이 include_router로 마운트한다. DB 경로는 app.state.store에서
  읽어 같은 SQLite 파일을 공유한다(웹·워크플로 동일 진실).

LLM은 직접 호출하지 않는다(이 모듈에 LLM 호출 경로 0).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from office.approve import _all_pending_interrupts, _record_decision

# Notify 칸에 들어가는 (type, reason) 매핑 — reason이 없는 type은 reason=None로 매칭.
# NODE_FAILED는 reason으로 Notify(escalation·gating)/Question(question)을 가른다.
_NOTIFY_NODE_FAILED_REASONS = {"escalation", "gating"}
_QUESTION_NODE_FAILED_REASONS = {"question"}
# reason 무관하게 Notify로 가는 라이프사이클 종료 이벤트.
_NOTIFY_LIFECYCLE_TYPES = {"RUN_FINISHED", "RUN_ERROR"}


def _db_path_of(request: Request) -> Any:
    """app.state.store의 DB 경로를 돌려준다(웹·워크플로 동일 SQLite 공유)."""
    store = request.app.state.store
    return store._db_path


def _classify_notify_question(
    db_path: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """전체 이벤트를 훑어 Notify·Question 칸 항목을 분류해 돌려준다.

    read_since(0)로 원문 payload_json을 읽어 type/reason만 본다(event_from_dict
    미경유 — 잘못된 reason이 있어도 분류가 5xx로 깨지지 않는다). Review(인터럽트)는
    여기서 다루지 않는다(별도 _all_pending_interrupts).

    Returns:
        (notify, question) — 각 항목은 {seq, type, reason, run_id, node_id, detail,
        status, error, ts} 중 해당 필드를 담은 dict. seq 오름차순.
    """
    from office.event_store import EventStore

    store = EventStore(db_path=db_path)
    notify: list[dict[str, Any]] = []
    question: list[dict[str, Any]] = []

    for seq, payload_json in store.read_since(0):
        try:
            data = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            continue  # 깨진 행은 조용히 건너뜀(분류는 멈추면 안 됨).

        etype = data.get("type", "")
        if etype == "NODE_FAILED":
            reason = data.get("reason", "")
            item = {
                "seq": seq,
                "type": etype,
                "reason": reason,
                "run_id": data.get("run_id", ""),
                "node_id": data.get("node_id", ""),
                "detail": data.get("detail", ""),
                "ts": data.get("ts", ""),
            }
            if reason in _QUESTION_NODE_FAILED_REASONS:
                question.append(item)
            elif reason in _NOTIFY_NODE_FAILED_REASONS:
                notify.append(item)
            # 그 외 reason(비표준)은 어느 칸에도 넣지 않는다(조용히 무시).
        elif etype in _NOTIFY_LIFECYCLE_TYPES:
            notify.append(
                {
                    "seq": seq,
                    "type": etype,
                    "run_id": data.get("run_id", ""),
                    "status": data.get("status", ""),
                    "error": data.get("error", ""),
                    "ts": data.get("ts", ""),
                }
            )

    return notify, question


def build_inbox(db_path: Any) -> dict[str, list[dict[str, Any]]]:
    """Inbox 3분류 dict를 만든다(계획 §4.5). 라우트·테스트 공용 순수 함수.

    Review   = _all_pending_interrupts(미해소 INTERRUPT_REQUESTED).
    Notify   = NODE_FAILED(escalation|gating) + RUN_FINISHED + RUN_ERROR.
    Question = NODE_FAILED(question).
    """
    review = _all_pending_interrupts(db_path=db_path)
    notify, question = _classify_notify_question(db_path)
    return {"review": review, "notify": notify, "question": question}


def create_inbox_router() -> APIRouter:
    """Inbox API 라우터를 만든다(create_app이 include_router로 마운트).

    같은 store(app.state.store)의 DB를 공유해 웹·워크플로가 한 SQLite를 본다.
    """
    router = APIRouter(prefix="/api/inbox", tags=["inbox"])

    @router.get("")
    def get_inbox(request: Request) -> dict[str, list[dict[str, Any]]]:
        """이벤트를 Review/Notify/Question 3분류로 반환한다."""
        return build_inbox(_db_path_of(request))

    @router.post("/{interrupt_id}/approve")
    def approve(interrupt_id: int, request: Request) -> dict[str, Any]:
        """승인 — _record_decision(resume=False)로 기록만(재개는 폴링 루프 단일 담당)."""
        return _decide(interrupt_id, "approve", request)

    @router.post("/{interrupt_id}/reject")
    def reject(interrupt_id: int, request: Request) -> dict[str, Any]:
        """거부 — _record_decision(resume=False)로 기록만(동일 경로)."""
        return _decide(interrupt_id, "reject", request)

    return router


def _decide(interrupt_id: int, decision: str, request: Request) -> dict[str, Any]:
    """approve/reject 공통 — _record_decision(resume=False) 호출(M3).

    resume=False라 INTERRUPT_RESOLVED·파일만 기록하고 TaskQueue.resume는 건너뛴다
    (task 상태 불변). 없는 interrupt_id면 404로 응답한다.
    """
    db_path = _db_path_of(request)
    try:
        return _record_decision(interrupt_id, decision, db_path=db_path, resume=False)
    except ValueError as exc:
        # 해당 pending INTERRUPT_REQUESTED가 없음 → 404.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
