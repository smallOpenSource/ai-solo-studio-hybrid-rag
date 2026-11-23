"""순수 이벤트→Discord 메시지 매핑 (discord 모듈 import 금지).

event_to_message(seq, payload, toggles) -> DiscordMessageSpec | None

★자연어 게시(2026-06-16·사용자 요구 "사람이 메시지 하는 수준으로 자연스럽게"):
브래킷 라벨([실행]·[제작:X]·[메시지:assistant])·식별자 노출(노드=·워크플로=·도구=)을
전부 폐기하고, 직원이 채널에 보내는 자연스러운 한국어 발화로 매핑한다. 시스템성 노이즈
(일반 워크플로 run 시작/완료·사용자 발화 되울림)는 억제한다.

게시 정책:
  INTERRUPT_REQUESTED          → 항상 게시(자연어 승인 요청) + 버튼 2개 + is_persistent
  RUN_STARTED/FINISHED         → game_* 워크플로만 자연어 상태("기획 시작할게요"/"마쳤어요")
                                  · 일반(chat 등)은 None(노이즈 억제 — 실 답변은 TEXT_MESSAGE로)
  RUN_ERROR                    → 자연어 오류 알림
  NODE_FAILED escalation|question → 자연어로 게시
  NODE_FAILED gating           → 기본 None, toggles["post_gating"]=True면 자연어 게시
  STATE_SNAPSHOT/DELTA         → 항상 None
  INTERRUPT_RESOLVED           → 항상 None
  TEXT_MESSAGE                 → 기본 None, toggles["post_text"]=True면 ★assistant 발화만
                                  내용 그대로 게시(라벨 0·되울림 0·user/system은 None)
  TOOL_CALL_START/RESULT       → 기본 None, toggles["post_tool"]=True면 자연어 짧은 줄
  미지/잘못된 type              → None (크래시 0)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# US-G6-2: 게임 제작 워크플로(game_*) 단계 라벨 SSOT = commands.STAGE_LABELS(중복 정의
# 회피·drift 0). commands 는 pure(discord/httpx/office 무의존)라 순수성·비침투 보존.
from adapters.discord.commands import STAGE_LABELS as _GAME_STAGE_LABELS

# Discord 단일 메시지 상한(2000)보다 보수적인 안전 캡. 초과 시 말줄임(완전 청킹은
# 게시 레이어 후속 — 라벨 폐기로 기존 100자 잘림 회귀는 이미 해소).
_MAX_LEN = 1900


@dataclass
class ButtonSpec:
    """Discord 버튼 명세 (discord 모듈 무의존)."""

    label: str
    custom_id: str
    style: str = "secondary"


@dataclass
class DiscordMessageSpec:
    """Discord 게시 메시지 명세 (직렬화 가능 dataclass)."""

    content: str
    buttons: list[ButtonSpec] = field(default_factory=list)
    # persistent View 요건: timeout=None + 모든 컴포넌트 custom_id 명시
    is_persistent: bool = False


def _clip(text: str) -> str:
    """Discord 상한 안전 캡(자연 발화는 대개 미달 — 초과 시에만 말줄임)."""
    if len(text) > _MAX_LEN:
        return text[: _MAX_LEN - 1] + "…"
    return text


def event_to_message(
    seq: int,
    payload: dict,
    toggles: dict | None = None,
) -> DiscordMessageSpec | None:
    """이벤트 payload를 Discord 메시지 spec으로 변환한다(자연어).

    Args:
        seq:      SSE 프레임 id: 라인에서 추출한 이벤트 시퀀스 번호(승인 버튼 custom_id용).
        payload:  SSE data: JSON 파싱 결과 (event.to_dict() 형식). payload["type"]로 분기.
        toggles:  게시 절제 토글. None이면 전부 기본(절제) 정책.
                  {"post_text": bool, "post_tool": bool, "post_gating": bool}

    Returns:
        DiscordMessageSpec | None. None이면 미게시.

    결정론: 같은 (seq, payload, toggles) 입력 → 항상 동일한 spec 반환.
    """
    if toggles is None:
        toggles = {}

    event_type: str = payload.get("type", "")

    # ── INTERRUPT_REQUESTED → 자연어 승인 요청 + 버튼 ───────────────────────────
    if event_type == "INTERRUPT_REQUESTED":
        tool = payload.get("tool", "")
        risk = payload.get("risk", "")
        if tool:
            risk_note = f" (위험도: {risk})" if risk else ""
            content = f"잠깐 확인 부탁드려요 — '{tool}' 작업을 진행할까요?{risk_note}"
        else:
            content = "잠깐 확인 부탁드려요 — 이 작업을 진행할까요?"
        buttons = [
            ButtonSpec(label="승인", custom_id=f"approve:{seq}", style="success"),
            ButtonSpec(label="거부", custom_id=f"reject:{seq}", style="danger"),
        ]
        return DiscordMessageSpec(content=content, buttons=buttons, is_persistent=True)

    # ── RUN_STARTED → game_* 단계만 자연어 상태(일반은 억제) ────────────────────
    if event_type == "RUN_STARTED":
        label = _GAME_STAGE_LABELS.get(payload.get("workflow", ""))
        if label:
            return DiscordMessageSpec(content=f"{label} 작업 시작할게요.")
        return None  # 일반 워크플로(chat 등) 시작 알림은 노이즈 → 미게시.

    # ── RUN_FINISHED → game_* 단계만 자연어 상태(일반은 억제) ───────────────────
    if event_type == "RUN_FINISHED":
        label = _GAME_STAGE_LABELS.get(payload.get("workflow", ""))
        if label:
            status = payload.get("status", "")
            if status == "success":
                return DiscordMessageSpec(content=f"{label} 작업 마쳤어요. ✅")
            return DiscordMessageSpec(
                content=f"{label} 작업을 끝내지 못했어요. (상태: {status})"
            )
        return None  # 일반 워크플로 완료 알림은 노이즈 → 미게시.

    # ── RUN_ERROR → 자연어 오류 ─────────────────────────────────────────────────
    if event_type == "RUN_ERROR":
        error = payload.get("error", "")
        if error:
            return DiscordMessageSpec(
                content=_clip(f"작업 중 문제가 생겼어요: {error}")
            )
        return DiscordMessageSpec(content="작업 중 문제가 생겼어요.")

    # ── NODE_FAILED → 자연어(escalation/question 게시·gating은 토글) ────────────
    if event_type == "NODE_FAILED":
        reason = payload.get("reason", "")
        detail = payload.get("detail", "")
        if reason == "escalation":
            base = "확인이 필요한 상황이 생겼어요."
            return DiscordMessageSpec(content=_clip(f"{base} {detail}".strip()))
        if reason == "question":
            # question의 본문(detail)이 곧 직원의 질문 — 그대로 자연 발화.
            return DiscordMessageSpec(
                content=_clip(detail) if detail else "여쭤볼 게 있어요."
            )
        if reason == "gating":
            if toggles.get("post_gating", False):
                base = "이 작업은 승인이 필요해서 잠시 멈췄어요."
                return DiscordMessageSpec(content=_clip(f"{base} {detail}".strip()))
            return None
        return None  # 알 수 없는 reason → None

    # ── STATE_SNAPSHOT / STATE_DELTA → 항상 None ───────────────────────────────
    if event_type in ("STATE_SNAPSHOT", "STATE_DELTA"):
        return None

    # ── INTERRUPT_RESOLVED → 항상 None ─────────────────────────────────────────
    if event_type == "INTERRUPT_RESOLVED":
        return None

    # ── TEXT_MESSAGE → assistant 발화만 내용 그대로(라벨 0·되울림 0) ─────────────
    if event_type == "TEXT_MESSAGE":
        if not toggles.get("post_text", False):
            return None
        # ★사용자 발화(role=user) 되울림 금지 — 사용자는 자기 메시지를 이미 본다.
        #   system도 미게시. assistant(직원 답변)만 자연 발화로 게시.
        if payload.get("role", "") != "assistant":
            return None
        text = payload.get("content", "")
        if not text:
            return None
        return DiscordMessageSpec(content=_clip(text))

    # ── TOOL_CALL_START / RESULT → 자연어 짧은 줄(기본 억제) ─────────────────────
    if event_type in ("TOOL_CALL_START", "TOOL_CALL_RESULT"):
        if not toggles.get("post_tool", False):
            return None
        name = payload.get("name", "")
        verb = "사용 중이에요" if event_type == "TOOL_CALL_START" else "확인했어요"
        if name:
            return DiscordMessageSpec(content=f"({name} 도구 {verb})")
        return DiscordMessageSpec(content=f"(도구 {verb})")

    # ── 미지/잘못된 type → None (크래시 0) ─────────────────────────────────────
    return None
