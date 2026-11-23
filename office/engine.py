"""워크플로 그래프 실행기 — NodeExecutor + WorkflowEngine.

계획 §4.4.5·§4.1 (수용 기준 4·5·16, US-004 T4a).

설계 원칙:
- NodeExecutor.execute()가 LLM을 호출하는 유일한 진입점.
  office/ 내 다른 코드에서 LLMClient.chat()을 직접 호출하면 안 된다.
- 영구 설계 규칙 4종이 이 단일 관문을 통과:
    ①정화: sanitize_for_history (core/history)
    ②length 가드: finish_reason=length → 즉시 노드 실패 (US-005 재시도 자리 예약)
    ③5xx body 보존: LLMClient 자체가 처리 (core/llm_client)
    ④노드별 max_tokens: nodes.MAX_TOKENS_BY_KIND (core/nodes)
- 되먹임 형태 (prompt_json 분기):
    · 정화된 content 문자열을 assistant 메시지 content로 유지
    · 도구 결과는 다음 user 노트(캐리포워드)로 주입 — role:tool 미사용
    (carryforward.py는 native_fc라 role:tool+정화 both였음 — 이 경로와 다름, §4.4.5)
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from core.call_modes import extract as modes_extract
from core.history import sanitize_for_history
from core.scoring import score
from office.events import (
    FailReason,
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunError,
    RunFinished,
    RunStarted,
    StateDelta,
    TextMessage,
    ToolCallResult,
    ToolCallStart,
)
from office.gating import GatingPolicy, ToolCallRequest, Verdict
from office.nodes import MAX_TOKENS_BY_KIND, Node

# 구성오류 진단용 모듈 로거(Phase 7 7a US-P7A-2 AC-2b). ★이벤트 아님(EventStore 미경유) —
# 새 이벤트 타입 0·11종 무변. None skip 분기의 사일런트 무실행을 운영자에게만 가시화한다.
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """현재 시각 ISO 8601 문자열 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _tool_accepts_context(tool_fn: Callable[..., Any]) -> bool:
    """도구 callable이 context 인자(2번째 위치 또는 'context' 키워드)를 받는지 판정한다.

    Phase 5 US-P5-1: 실도구(email_send)는 (args, context=None)을 받지만, 기존 테스트가
    주입하는 레거시 callable은 (args)만 받는다. 이 검사로 둘을 구분해 무회귀를 보장한다
    (1-인자 callable엔 context를 안 넘김). 시그니처를 못 읽는 builtin 등은 보수적으로
    False(=context 미주입)로 본다 — 안전 우선.
    """
    import inspect

    try:
        sig = inspect.signature(tool_fn)
    except (TypeError, ValueError):
        return False
    params = list(sig.parameters.values())
    for p in params:
        # 'context' 키워드를 명시적으로 받거나, **kwargs로 흡수하면 주입 가능.
        if p.name == "context" and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
        if p.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def _build_carry_note(accumulated: dict[str, Any]) -> str:
    """캐리포워드 누적값을 user 노트 문자열로 만든다.

    carryforward._build_carry_note 패턴 계승 (계획 §4.4.5).
    """
    if not accumulated:
        return ""
    parts = ", ".join(f"{k}={v!r}" for k, v in accumulated.items())
    return f"지금까지 확인된 값: {parts}"


def _infer_tool_name_from_node(node: Node) -> str:
    """Node.tools[0]에서 도구 이름을 추출한다. tools 없으면 node.id 반환."""
    tools: list[dict[str, Any]] = getattr(node, "tools", [])
    if tools:
        fn = tools[0].get("function", {})
        if fn.get("name"):
            return fn["name"]
        if tools[0].get("name"):
            return tools[0]["name"]
    return node.id


def _nodes_to_dicts(nodes: list[Node]) -> list[dict[str, Any]]:
    """Node 리스트를 JSON 직렬화 가능한 dict 리스트로 변환한다 (resume용)."""
    return [dataclasses.asdict(n) for n in nodes]


def _dicts_to_nodes(dicts: list[dict[str, Any]]) -> list[Node]:
    """_nodes_to_dicts()의 역변환 — dict 리스트를 Node 리스트로 복원한다."""
    return [Node(**d) for d in dicts]


# ── 노드 실행기 ────────────────────────────────────────────────────────────────


class NodeExecutor:
    """노드 단일 실행기 — LLM 호출의 유일한 진입점 (수용 기준 16).

    LLMClient를 생성자로 주입받아 테스트에서 fake LLM을 주입할 수 있다.
    office/ 내 다른 모듈은 이 클래스를 통해서만 LLM을 호출한다.
    """

    def __init__(
        self,
        llm_client: Any,
        event_store: Any | None = None,
    ) -> None:
        """
        Args:
            llm_client: core.llm_client.LLMClient 호환 객체.
                        .chat(model, messages, ...) 메서드를 가져야 한다.
            event_store: office.event_store.EventStore (선택).
                         None이면 이벤트를 발행하지 않는다.
        """
        self._llm = llm_client
        self._store = event_store

    def _emit(self, event: Any) -> None:
        """이벤트 스토어가 주입된 경우에만 이벤트를 발행한다."""
        if self._store is not None:
            self._store.append(event)

    @staticmethod
    def _record_node_metric(
        state: dict[str, Any],
        node: Node,
        llm_resp: dict[str, Any],
    ) -> dict[str, Any]:
        """노드 1개의 관측 메트릭을 state["_node_metrics"]에 누적한다 (최소 침습).

        수집 항목 (계획 §5 Observability·수용 12):
          - latency_ms      : LLM 호출 지연 (LLMClient가 측정해 응답에 실음)
          - finish_reason   : stop / length 등
          - reasoning_chars : reasoning_content 길이 (thinking ON이면 >0, OFF면 0)
          - thinking        : 노드의 thinking 토글 ("on"/"off")
          - kind            : 노드 종류

        반환값은 이번 노드의 메트릭 dict (STATE_DELTA payload에 실어 노출).
        """
        reasoning = llm_resp.get("reasoning_content")
        if not reasoning:
            # message 안에 들어오는 경우도 커버
            msg = llm_resp.get("message") or {}
            reasoning = msg.get("reasoning_content") or ""
        metric = {
            "latency_ms": float(llm_resp.get("latency_ms", 0.0) or 0.0),
            "finish_reason": llm_resp.get("finish_reason", ""),
            "reasoning_chars": len(reasoning or ""),
            "thinking": node.thinking,
            "kind": node.kind,
        }
        metrics: dict[str, Any] = dict(state.get("_node_metrics", {}))
        metrics[node.id] = metric
        state["_node_metrics"] = metrics
        return metric

    def execute(
        self,
        node: Node,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """노드를 실행하고 갱신된 상태를 반환한다.

        영구 설계 규칙 4종이 이 메서드를 반드시 통과한다 (우회 API 없음):
          ① 정화 (sanitize_for_history)
          ② length 가드 (finish_reason=length → 즉시 노드 실패)
          ③ 5xx body 보존 (LLMClient 내장)
          ④ 노드별 max_tokens (MAX_TOKENS_BY_KIND)

        Args:
            node : 실행할 Node 정의.
            state: 현재 워크플로 상태 dict.
                   "messages": 대화 히스토리 (list)
                   "accumulated": 캐리포워드 누적값 (dict)
                   "run_id": 이번 실행 ID (이벤트 발행용, 선택)
                   "model": 기본 모델 alias (선택)

        Returns:
            갱신된 상태 dict. "_node_failed"=True면 노드 실패.
        """
        if node.kind == "agent":
            # Phase 6 6b: agent 노드 1스텝(단일 추론)을 실행한다. 다단계 바운드 루프·
            # 게이팅·도구 실행은 WorkflowEngine._run_agent가 오케스트레이션한다(도구/
            # 게이팅이 엔진에 있으므로). execute는 LLM 호출의 유일 진입점이라 agent의
            # 매 추론 스텝도 이 단일 관문(정화·length 가드·노드별 max_tokens)을 통과한다.
            return self.agent_step(node, state)

        run_id: str = state.get("run_id", "")
        messages: list[dict[str, Any]] = list(state.get("messages", []))
        accumulated: dict[str, Any] = dict(state.get("accumulated", {}))

        # ── ① 프롬프트 구성 ──────────────────────────────────────────────────
        # 캐리포워드 누적 노트를 user 메시지로 주입 (prompt_json 분기)
        # 도구 결과는 user 노트로만 — role:tool 미사용 (§4.4.5)
        carry_note = _build_carry_note(accumulated)
        if carry_note:
            messages = list(messages) + [{"role": "user", "content": carry_note}]

        # 노드 프롬프트 로드 (인라인 우선, 파일 폴백)
        prompt_text = node.prompt or ""
        if not prompt_text and node.prompt_file:
            try:
                from pathlib import Path

                proj_root = Path(__file__).parent.parent
                prompt_text = (proj_root / node.prompt_file).read_text(encoding="utf-8")
            except FileNotFoundError:
                prompt_text = ""

        # 사용자 프롬프트 메시지 추가
        if prompt_text:
            messages = list(messages) + [{"role": "user", "content": prompt_text}]

        # ── ② LLM 호출 ───────────────────────────────────────────────────────
        # max_tokens: 노드 오버라이드 또는 MAX_TOKENS_BY_KIND (④)
        max_tokens = (
            node.max_tokens
            if node.max_tokens is not None
            else MAX_TOKENS_BY_KIND[node.kind]
        )

        # thinking 토글 (전제 #1 — 노드 단위)
        extra_body: dict[str, Any] | None = None
        if node.thinking == "off":
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

        # 사용할 모델 결정 (노드 지정 → 상태 기본 모델 순)
        model = node.model or state.get("model", "default")

        # prompt_json_retry 모드: 도구 목록을 system 프롬프트에 포함
        # 이미 messages를 직접 구성했으므로 도구가 있을 때만 system 메시지를 앞에 삽입
        if node.tools:
            from core.call_modes import _PROMPT_JSON_SYSTEM_SUFFIX, _tools_to_prompt

            tool_desc = _tools_to_prompt(node.tools)
            system_content = tool_desc + _PROMPT_JSON_SYSTEM_SUFFIX
            # system 메시지를 맨 앞에 삽입
            messages = [{"role": "system", "content": system_content}] + [
                m for m in messages if m.get("role") != "system"
            ]

        try:
            llm_resp = self._llm.chat(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.0,
                extra_body=extra_body,
            )
        except Exception as exc:
            # 5xx body 보존은 LLMClient 내장 (③). 여기서는 노드 실패만 처리.
            fail_detail = str(exc)
            self._emit(
                NodeFailed(
                    run_id=run_id,
                    ts=_now_iso(),
                    node_id=node.id,
                    reason=FailReason.escalation,
                    detail=f"LLM 호출 실패: {fail_detail[:200]}",
                )
            )
            new_state = dict(state)
            new_state["_node_failed"] = True
            new_state["_fail_detail"] = fail_detail
            return new_state

        finish_reason: str = llm_resp.get("finish_reason", "")

        # ── ③ finish_reason 검사 먼저 (②length 가드, US-005) ────────────────
        # length면 max_tokens ×2 상향 후 1회 재시도한다.
        # 재시도도 length → NODE_FAILED(escalation).
        # 절단 응답은 원본·재시도 모두 히스토리에 절대 들어가지 않는다.
        if finish_reason == "length":
            # 재시도 max_tokens: ×2 상향, 상한 16384 (32k 절대 금지)
            retry_max_tokens = min(max_tokens * 2, 16384)
            try:
                retry_resp = self._llm.chat(
                    model=model,
                    messages=messages,
                    max_tokens=retry_max_tokens,
                    temperature=0.0,
                    extra_body=extra_body,
                )
            except Exception as exc:
                # 재시도 LLM 호출 자체가 실패
                fail_detail = str(exc)
                self._emit(
                    NodeFailed(
                        run_id=run_id,
                        ts=_now_iso(),
                        node_id=node.id,
                        reason=FailReason.escalation,
                        detail=f"length 재시도 LLM 호출 실패: {fail_detail[:200]}",
                    )
                )
                new_state = dict(state)
                new_state["_node_failed"] = True
                new_state["_fail_detail"] = fail_detail
                return new_state

            retry_finish: str = retry_resp.get("finish_reason", "")
            if retry_finish == "length":
                # 재시도도 절단 → 노드 실패 확정
                self._emit(
                    NodeFailed(
                        run_id=run_id,
                        ts=_now_iso(),
                        node_id=node.id,
                        reason=FailReason.escalation,
                        detail=(
                            f"finish_reason=length 재시도도 절단: "
                            f"max_tokens={retry_max_tokens} (원본={max_tokens})"
                        ),
                    )
                )
                # 실패 노드도 메트릭 기록(finish_reason=length 노출)
                self._record_node_metric(state, node, retry_resp)
                new_state = dict(state)
                new_state["_node_failed"] = True
                new_state["_fail_detail"] = "finish_reason=length"
                return new_state

            # 재시도 성공(stop) → llm_resp·finish_reason을 재시도 값으로 교체
            llm_resp = retry_resp
            finish_reason = retry_finish

        # 노드 메트릭 누적 (최소 침습 — stop으로 확정된 응답 기준, 수용 12)
        self._record_node_metric(state, node, llm_resp)

        # ── ④ stop 응답만 정화 단일 관문 통과 (①정화) ──────────────────────
        raw_message: dict[str, Any] = llm_resp.get("message") or {}
        content_is_tool_json = node.kind == "tool"
        sanitized = sanitize_for_history(
            raw_message, content_is_tool_json=content_is_tool_json
        )

        if sanitized.get("_sanitize_failed"):
            # 정화 실패: content-JSON 복구 불가 → 노드 실패
            self._emit(
                NodeFailed(
                    run_id=run_id,
                    ts=_now_iso(),
                    node_id=node.id,
                    reason=FailReason.escalation,
                    detail="content-JSON 정화 실패: 복구 불가 → 히스토리 오염 차단",
                )
            )
            new_state = dict(state)
            new_state["_node_failed"] = True
            new_state["_fail_detail"] = "sanitize_failed"
            return new_state

        # chat 노드: TEXT_MESSAGE 이벤트 발행
        if node.kind == "chat":
            content_out = sanitized.get("content") or llm_resp.get("content") or ""
            self._emit(
                TextMessage(
                    run_id=run_id,
                    ts=_now_iso(),
                    role="assistant",
                    content=content_out,
                )
            )

        # ── ⑤ 규칙 기반 사후검증 ─────────────────────────────────────────────
        extracted: dict[str, Any] | None = None
        score_result: dict[str, str] | None = None

        if node.kind == "tool":
            # prompt_json_retry 모드로 응답 추출
            extracted = modes_extract(llm_resp, mode="prompt_json_retry")

            # tool 이벤트 발행
            tool_args = extracted.get("args", {}) if extracted else {}
            tool_name = extracted.get("name", "") if extracted else ""
            self._emit(
                ToolCallStart(
                    run_id=run_id,
                    ts=_now_iso(),
                    node_id=node.id,
                    name=tool_name,
                    args=tool_args,
                )
            )

            # expected가 있으면 채점
            if node.expected is not None:
                score_result = score(node.expected, extracted)
                if score_result["verdict"] != "correct":
                    self._emit(
                        NodeFailed(
                            run_id=run_id,
                            ts=_now_iso(),
                            node_id=node.id,
                            reason=FailReason.escalation,
                            detail=f"사후검증 실패: {score_result['verdict']} — {score_result['detail']}",
                        )
                    )
                    new_state = dict(state)
                    new_state["_node_failed"] = True
                    new_state["_fail_detail"] = score_result["detail"]
                    return new_state

        elif node.kind in ("chat", "longform", "coding"):
            # chat/longform/coding: content가 비어있지 않음을 확인
            content_val = sanitized.get("content") or llm_resp.get("content") or ""
            if not content_val.strip():
                self._emit(
                    NodeFailed(
                        run_id=run_id,
                        ts=_now_iso(),
                        node_id=node.id,
                        reason=FailReason.escalation,
                        detail="chat/longform/coding 노드: content가 비어 있음",
                    )
                )
                new_state = dict(state)
                new_state["_node_failed"] = True
                new_state["_fail_detail"] = "empty_content"
                return new_state

        # ── ⑥ 상태 캐리포워드 누적 ──────────────────────────────────────────
        new_accumulated = dict(accumulated)

        # tool 노드: 도구를 실제 실행하면 결과를 누적
        # (tools registry는 WorkflowEngine이 주입 — 여기서는 extracted만 누적)
        if extracted:
            new_accumulated[f"{node.id}_call"] = extracted

        # chat 노드: content를 누적
        if node.kind in ("chat", "longform", "coding"):
            content_val = sanitized.get("content") or llm_resp.get("content") or ""
            new_accumulated[f"{node.id}_content"] = content_val

        # 히스토리에 정화된 assistant 메시지 되먹임 (stop 응답만)
        new_messages = list(messages) + [sanitized]

        # STATE_DELTA 이벤트 발행 (노드 메트릭을 payload에 함께 노출, 수용 12)
        delta = {k: v for k, v in new_accumulated.items() if k not in accumulated}
        node_metric = state.get("_node_metrics", {}).get(node.id)
        if delta or node_metric:
            delta_payload = dict(delta)
            if node_metric is not None:
                delta_payload[f"{node.id}_metric"] = node_metric
            self._emit(
                StateDelta(
                    run_id=run_id,
                    ts=_now_iso(),
                    accumulated=delta_payload,
                )
            )

        new_state = dict(state)
        new_state["messages"] = new_messages
        new_state["accumulated"] = new_accumulated
        new_state["_node_failed"] = False
        new_state["_last_extracted"] = extracted
        new_state["_last_llm_resp"] = llm_resp
        return new_state

    # ── agent 노드 1스텝 (Phase 6 6b) ────────────────────────────────────────

    def agent_step(
        self,
        node: Node,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """agent 노드의 **단일 추론 스텝**을 실행한다 (LLM 1회 호출).

        WorkflowEngine._run_agent의 바운드 루프가 매 스텝 이 메서드를 부른다. execute와
        같은 단일 관문(정화·length 가드·노드별 max_tokens 8192·thinking)을 통과하되,
        도구 실행·게이팅은 하지 않는다(엔진 책임). 추론 텍스트는 TEXT_MESSAGE로 발행하고,
        content에 도구 호출 JSON이 있으면 prompt_json_retry로 추출해 반환한다.

        반환 state 키:
          "_agent_step_failed": True면 LLM 실패·length 소진(escalation은 호출자가 발행).
          "_agent_tool_call"  : {"name","args"} 또는 None(도구 호출 아님 = 추론/종료).
          "_agent_content"    : 정화된 assistant content(종료조건 파싱용).
          "messages"          : 정화 메시지를 되먹임한 누적 히스토리.
        """
        run_id: str = state.get("run_id", "")
        messages: list[dict[str, Any]] = list(state.get("messages", []))
        accumulated: dict[str, Any] = dict(state.get("accumulated", {}))

        # ① 프롬프트 구성 — 캐리포워드 누적 노트 + 노드 프롬프트(execute와 동일 형태).
        carry_note = _build_carry_note(accumulated)
        if carry_note:
            messages = list(messages) + [{"role": "user", "content": carry_note}]
        prompt_text = node.prompt or ""
        if not prompt_text and node.prompt_file:
            from pathlib import Path

            proj_root = Path(__file__).parent.parent
            try:
                prompt_text = (proj_root / node.prompt_file).read_text(encoding="utf-8")
            except FileNotFoundError:
                prompt_text = ""
        if prompt_text:
            messages = list(messages) + [{"role": "user", "content": prompt_text}]

        max_tokens = (
            node.max_tokens
            if node.max_tokens is not None
            else MAX_TOKENS_BY_KIND["agent"]
        )
        extra_body: dict[str, Any] | None = None
        if node.thinking == "off":
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        model = node.model or state.get("model", "default")

        # 도구 스키마를 system 프롬프트로 주입(execute tool 분기와 동일 — prompt_json).
        if node.tools:
            from core.call_modes import _PROMPT_JSON_SYSTEM_SUFFIX, _tools_to_prompt

            tool_desc = _tools_to_prompt(node.tools)
            system_content = tool_desc + _PROMPT_JSON_SYSTEM_SUFFIX
            messages = [{"role": "system", "content": system_content}] + [
                m for m in messages if m.get("role") != "system"
            ]

        # ② LLM 호출 + length 가드(×2 재시도, ≤16384). 절단은 히스토리에 안 들어간다.
        llm_resp, failed = self._invoke_with_length_guard(
            model, messages, max_tokens, extra_body
        )
        if failed:
            new_state = dict(state)
            new_state["_agent_step_failed"] = True
            return new_state

        self._record_node_metric(state, node, llm_resp)

        # ③ 정화 단일 관문 — ★agent content는 도구호출 JSON일 수도, 자유 추론 텍스트일 수도
        #   있다(tool 노드와 다름). content_is_tool_json=False로 자유 텍스트 취급해 정상
        #   추론 스텝이 정화 실패로 죽지 않게 한다 — 도구 호출 여부는 아래 modes_extract가
        #   lenient 파싱으로 판정한다(JSON이면 추출, 프로즈면 None). length 절단은 위
        #   _invoke_with_length_guard가 이미 막아 여기 절단 응답은 안 온다.
        raw_message: dict[str, Any] = llm_resp.get("message") or {}
        sanitized = sanitize_for_history(raw_message, content_is_tool_json=False)

        content_out = sanitized.get("content") or llm_resp.get("content") or ""

        # ④ 추론 텍스트를 TEXT_MESSAGE로 발행(11종 무변 — 새 타입 0).
        self._emit(
            TextMessage(
                run_id=run_id,
                ts=_now_iso(),
                role="assistant",
                content=content_out,
            )
        )

        # ⑤ content에 도구 호출 JSON이 있으면 추출(prompt_json_retry). 없으면 None.
        tool_call = modes_extract(llm_resp, mode="prompt_json_retry")
        if tool_call is not None and not tool_call.get("name"):
            tool_call = None

        new_messages = list(messages) + [sanitized]
        new_state = dict(state)
        new_state["messages"] = new_messages
        new_state["accumulated"] = accumulated
        new_state["_agent_step_failed"] = False
        new_state["_agent_tool_call"] = tool_call
        new_state["_agent_content"] = content_out
        new_state["_last_llm_resp"] = llm_resp
        return new_state

    def _invoke_with_length_guard(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        extra_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """LLM을 호출하고 finish_reason=length면 ×2(≤16384) 1회 재시도한다.

        agent_step 전용 보조 — execute의 length 가드(engine.py:287)와 같은 정책이되 도구
        실행·이벤트 발행 없이 (응답, 실패여부)만 돌려준다. 호출 자체 예외나 재시도도
        length면 실패(True). 절단 응답은 호출자가 히스토리에 안 넣는다.
        """
        try:
            resp = self._llm.chat(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.0,
                extra_body=extra_body,
            )
        except Exception:
            return {}, True
        if resp.get("finish_reason", "") != "length":
            return resp, False
        retry_max = min(max_tokens * 2, 16384)
        try:
            retry = self._llm.chat(
                model=model,
                messages=messages,
                max_tokens=retry_max,
                temperature=0.0,
                extra_body=extra_body,
            )
        except Exception:
            return {}, True
        if retry.get("finish_reason", "") == "length":
            return retry, True
        return retry, False


# ── 워크플로 그래프 실행기 ─────────────────────────────────────────────────────


class WorkflowEngine:
    """워크플로 그래프를 순회하며 노드를 순차 실행하는 엔진.

    - NodeExecutor를 통해서만 LLM을 호출한다.
    - 이벤트 스토어가 주입되면 AG-UI 정렬 이벤트를 발행한다.
    - tools registry가 주입되면 tool 노드에서 도구를 실제 실행한다.
    - gating_policy가 주입되면 도구 실행 직전(LLM 추출 후) 위험기반 게이팅을
      적용한다 (US-006). 게이팅 지점이 노드 실행 "전"이 아닌 "도구 실행 직전"인
      이유는 when 술어·승인 payload에 LLM이 뽑은 도구 인자가 필요하기 때문이다.
    """

    def __init__(
        self,
        executor: NodeExecutor,
        event_store: Any | None = None,
        tools: dict[str, Callable[..., Any]] | None = None,
        gating_policy: GatingPolicy | None = None,
        error_fails_node: bool = False,
    ) -> None:
        """
        Args:
            executor    : NodeExecutor 인스턴스 (LLM 호출 담당).
            event_store : EventStore (선택). None이면 이벤트 미발행.
            tools       : 도구 이름 → callable 레지스트리 (선택).
                          {'crm_get_customer': lambda args: {...}, ...}
            gating_policy: 위험기반 게이팅 정책 (선택, US-006).
                          None이면 게이팅 없이 모든 도구를 실행한다.
            error_fails_node: True면 도구 결과 dict에 "error" 키가 있을 때
                          노드 실패로 처리해 NODE_FAILED(escalation)를 발행한다
                          (US-008 수용 17 — error 시나리오 실증용, 기본 False라
                          기존 동작은 그대로: error 결과를 누적하고 계속 진행).
        """
        self._executor = executor
        self._store = event_store
        self._tools = tools or {}
        self._gating_policy = gating_policy
        self._error_fails_node = error_fails_node

    def _emit(self, event: Any) -> None:
        if self._store is not None:
            self._store.append(event)

    def run(
        self,
        nodes: list[Node],
        initial_state: dict[str, Any] | None = None,
        run_id: str | None = None,
        workflow: str = "",
        task_id: int | None = None,
        plan_mode: bool = False,
    ) -> dict[str, Any]:
        """노드 그래프를 처음부터 끝까지 순차 실행한다.

        Args:
            nodes        : 워크플로를 구성하는 노드 리스트.
            initial_state: 초기 상태 dict (선택). 없으면 빈 상태.
            run_id       : 이번 실행 식별자. None이면 자동 생성.
            workflow     : 워크플로 이름 (이벤트 발행용).
            task_id      : TaskQueue row id (선택). interrupt 시 payload에 실어
                           승인 CLI가 resume할 수 있게 한다. 없으면 None.
            plan_mode    : True면 시작 시 노드 계획을 TEXT_MESSAGE로 1회 발행하고,
                           이 run에서 interrupt 판정을 allow로 격상한다(block은 유지).

        Returns:
            최종 상태 dict. interrupt로 멈추면 _workflow_status="suspended"이며
            재개 정보(_resume_*)가 state에 보존된다 (JSON 직렬화 가능).
        """
        import uuid

        if run_id is None:
            run_id = str(uuid.uuid4())

        state: dict[str, Any] = {
            "run_id": run_id,
            "messages": [],
            "accumulated": {},
        }
        if initial_state:
            state.update(initial_state)
        state["run_id"] = run_id
        # 재개에 필요한 run 메타를 state에 보존 (JSON 직렬화 가능)
        state["_workflow"] = workflow
        state["_task_id"] = task_id
        state["_plan_mode"] = plan_mode
        state["_nodes"] = _nodes_to_dicts(nodes)

        # 노드 id → Node 인덱스
        node_map: dict[str, Node] = {n.id: n for n in nodes}

        # RUN_STARTED 이벤트
        self._emit(RunStarted(run_id=run_id, ts=_now_iso(), workflow=workflow))

        # Plan Mode: 노드 계획을 TEXT_MESSAGE로 1회 발행 (사전 일괄 승인 전제)
        if plan_mode:
            self._emit_plan(nodes, run_id)

        # 첫 노드부터 시작
        current_id: str | None = nodes[0].id if nodes else None

        return self._run_from(node_map, current_id, state, run_id, workflow, plan_mode)

    def _run_from(
        self,
        node_map: dict[str, Node],
        current_id: str | None,
        state: dict[str, Any],
        run_id: str,
        workflow: str,
        plan_mode: bool,
    ) -> dict[str, Any]:
        """current_id 노드부터 그래프를 순회한다 (run·resume 공용 루프).

        도구 실행 직전에 게이팅을 판정하므로 resume도 이 루프를 재사용한다.
        """
        # 노드 순회 절대 상한(H-04): 사이클 워크플로(A→B→A)가 무한히 LLM을
        # 호출하는 것을 방어한다. 현 워크플로는 선형 DAG(crm_notify 4노드, 사이클 0)라
        # 1000(정상 4스텝의 250배)에 절대 미도달 → 정상 무변·거짓 양성 0. 미래
        # 사이클 워크플로의 무한 순회만 차단한다(매직넘버 K 공식은 사이클 도입 시 설계).
        _STEP_LIMIT = 1000
        steps = 0
        try:
            while current_id is not None:
                if steps >= _STEP_LIMIT:
                    # 상한 초과 — 조용한 무한 순회 대신 명시적 실패(관측).
                    # 기존 FailReason 재사용(escalation: 사람 호출 필요), step_limit은
                    # detail에 명시(events.py 스키마 무변).
                    self._emit(
                        NodeFailed(
                            run_id=run_id,
                            ts=_now_iso(),
                            node_id=current_id,
                            reason=FailReason.escalation,
                            detail=(
                                f"step_limit: 노드 순회 절대 상한 {_STEP_LIMIT} 초과 "
                                f"(사이클 의심) — current_id={current_id!r}"
                            ),
                        )
                    )
                    self._emit(
                        RunFinished(
                            run_id=run_id,
                            ts=_now_iso(),
                            workflow=workflow,
                            status="failed",
                        )
                    )
                    state["_workflow_status"] = "failed"
                    return state
                steps += 1

                node = node_map.get(current_id)
                if node is None:
                    raise ValueError(f"노드 id를 찾을 수 없음: {current_id!r}")

                # ── agent 노드: 자체 바운드 루프(Phase 6 6b · C-A1) ──────────────
                # tool/chat 경로와 분리한 신규 분기. _run_agent가 3중 가드+게이팅 단일
                # 관문으로 루프를 돌고, 종료 상태를 state["_workflow_status"]에 싣는다.
                if node.kind == "agent":
                    state = self._run_agent(node, state, run_id, workflow, plan_mode)
                    status = state.get("_workflow_status")
                    if status in ("failed", "suspended", "rejected"):
                        return state
                    # 정상 진행(루프 완료) → 다음 노드로.
                    current_id = node.next
                    continue

                # 노드 실행 (LLM 추출 → state["_last_extracted"])
                state = self._executor.execute(node, state)

                if state.get("_node_failed"):
                    # 노드 실패 — 워크플로 종료
                    self._emit(
                        RunFinished(
                            run_id=run_id,
                            ts=_now_iso(),
                            workflow=workflow,
                            status="failed",
                        )
                    )
                    state["_workflow_status"] = "failed"
                    return state

                # tool 노드: 게이팅 판정(도구 실행 직전) → 도구 실행
                if node.kind == "tool":
                    verdict = self._gate(node, state, plan_mode)

                    if verdict == Verdict.block:
                        # block — 어떤 승인으로도 통과 불가
                        # NODE_FAILED(reason=gating) + RUN_FINISHED(rejected)
                        self._emit(
                            NodeFailed(
                                run_id=run_id,
                                ts=_now_iso(),
                                node_id=node.id,
                                reason=FailReason.gating,
                                detail=self._block_detail(state),
                            )
                        )
                        self._emit(
                            RunFinished(
                                run_id=run_id,
                                ts=_now_iso(),
                                workflow=workflow,
                                status="rejected",
                            )
                        )
                        state["_workflow_status"] = "rejected"
                        return state

                    if verdict == Verdict.interrupt:
                        # 사람 승인 필요 — INTERRUPT_REQUESTED 발행 후 suspend
                        self._suspend_for_approval(node, state, run_id)
                        state["_workflow_status"] = "suspended"
                        return state

                    # allow — 도구를 실제 실행하고 결과를 누적
                    state = self._run_tool(node, state, run_id)

                    # 도구 결과가 error여서 노드 실패한 경우(error_fails_node=True)
                    if state.get("_node_failed"):
                        self._emit(
                            RunFinished(
                                run_id=run_id,
                                ts=_now_iso(),
                                workflow=workflow,
                                status="failed",
                            )
                        )
                        state["_workflow_status"] = "failed"
                        return state

                # 다음 노드로
                current_id = node.next

        except Exception as exc:
            error_msg = str(exc)[:500]
            self._emit(RunError(run_id=run_id, ts=_now_iso(), error=error_msg))
            state["_workflow_status"] = "error"
            state["_error"] = error_msg
            return state

        # 정상 종료
        self._emit(
            RunFinished(
                run_id=run_id,
                ts=_now_iso(),
                workflow=workflow,
                status="success",
            )
        )
        state["_workflow_status"] = "success"
        return state

    # ── agent 바운드 루프 (Phase 6 6b · C-A1 · 결정 I/G/H/N3) ──────────────────

    # ① 스텝 절대 상한 — 종료불능 추론의 최후 방어(사후검증이 1차, 이건 안전망).
    #   _STEP_LIMIT=1000(워크플로 순회용)보다 훨씬 작게: agent 1노드 루프 전용.
    _AGENT_STEP_LIMIT = 12
    # ③ 누적 messages **입력** 상한(출력 상한 8192와 별개 차원). 누적 messages content
    #   총 길이가 이 임계를 넘으면 윈도잉으로 최근 스텝만 유지한다(accumulated는 보존 —
    #   _build_carry_note로 재주입돼 핵심 상태 손실 0). 길이 기반(토큰 근사)·결정론.
    _AGENT_MSG_CHARS_LIMIT = 24000
    # 윈도잉 시 유지할 최근 메시지 수(system 제외 — system은 항상 보존).
    _AGENT_WINDOW_KEEP = 4
    # 종료 신호 토큰(사후검증 AC-2g): agent가 목표 달성을 명시 선언하는 마커. 도구 호출
    #   없이 이 토큰이 content에 있으면 정상 완료. 없으면 추론 계속(상한이 최후 방어).
    _AGENT_DONE_MARKER = "AGENT_DONE"

    def _run_agent(
        self,
        agent_node: Node,
        state: dict[str, Any],
        run_id: str,
        workflow: str,
        plan_mode: bool,
    ) -> dict[str, Any]:
        """agent 노드의 자체 바운드 추론 루프(C-A1). 종료 상태를 state에 싣고 반환한다.

        3중 가드: ① 스텝 절대 상한(_AGENT_STEP_LIMIT) ② 단일호출 출력 토큰 상한(8192 —
        agent_step의 max_tokens, 기존 LLM 경로 재사용) ③ 누적 messages 입력 상한
        (_window_messages — accumulated 보존). 도구 호출은 **게이팅 단일 관문**
        `_agent_invoke_tool`만 경유한다(self._tools·self._llm·_run_tool 직접 접근 금지).

        결정 H: 비가역 도구를 만나면 그 스텝에서 즉시 suspend(루프당 최대 1개). 결정 I:
        가역 도구는 sub-id(`agent#step`)로, 비가역은 base-id로 발행(합성 Node). 사후검증
        (AC-2g): 도구 호출 없이 _AGENT_DONE_MARKER가 나오면 정상 완료, 아니면 추론 계속·
        상한 도달 시 escalation(무검증 자동종료 금지 — 상한은 최후 방어).

        Args:
            agent_node: kind="agent" 노드(base id가 식별·resume의 단일 소스).
            state     : 현재 워크플로 상태.
            run_id    : 실행 식별자.
            workflow  : 워크플로 이름(RUN_FINISHED 발행용).
            plan_mode : Plan Mode 여부(게이팅 관문으로 관통).

        Returns:
            state. _workflow_status ∈ {failed, suspended, rejected}면 호출자가 즉시
            반환한다. 미설정(정상 루프 완료)이면 호출자가 node.next로 진행한다.
        """
        # G-RESTART/N3: 이 agent 노드에서 이미 승인 소진된 비가역(base id 단위 1회). resume
        # 재진입이 _run_agent를 다시 돌 때 이미 승인된 비가역을 재차 suspend하지 않도록
        # state로 관통한다(재승인 루프 0). 키는 base agent id 단위(sub-id 무관 — 결정 I 정합).
        approved: set[str] = set(state.get("_agent_approved_irrev", []))

        # ★resume 재진입(G-RESTART) 시 직전 suspend가 남긴 마커를 비운다 — 새 루프가
        # _workflow_status를 매 스텝 새로 도출해야 하는데, 이전 "suspended"가 남아 있으면
        # 첫 도구 스텝 직후 종료 판정이 거짓 발동한다(B1 skip은 상태를 안 바꿈). 정상
        # 첫 진입(run)에선 이 키들이 없어 무영향.
        state = dict(state)
        for _stale in (
            "_workflow_status",
            "_resume_node_id",
            "_resume_tool",
            "_resume_args",
            "_agent_tool_call",
        ):
            state.pop(_stale, None)

        step = 0
        while step < self._AGENT_STEP_LIMIT:
            # ③ 입력 상한 — 누적 messages가 임계 초과면 윈도잉(accumulated 보존).
            state = self._window_messages(state)

            # ② 출력 상한은 agent_step 내부(max_tokens=8192). 단일 추론 스텝 실행.
            state = self._executor.execute(agent_node, state)

            if state.get("_agent_step_failed"):
                # LLM 실패·length 소진 → escalation(FailReason 3 무변).
                return self._agent_fail(
                    agent_node,
                    state,
                    run_id,
                    workflow,
                    "agent 추론 스텝 실패(LLM 오류 또는 length 소진)",
                )

            tool_call = state.get("_agent_tool_call")
            if not tool_call:
                # 도구 호출 없음 — 사후검증(AC-2g): 종료 마커 있으면 정상 완료.
                content = state.get("_agent_content", "") or ""
                if self._AGENT_DONE_MARKER in content:
                    state["_workflow_status"] = None  # 루프 완료 → 호출자가 next로.
                    state.pop("_workflow_status", None)
                    state["_agent_approved_irrev"] = list(approved)
                    return state
                # 마커 없는 순수 추론 스텝 → 다음 스텝(상한이 최후 방어).
                step += 1
                continue

            # 도구 호출 — 게이팅 단일 관문 경유(allow면 실행·interrupt면 suspend).
            state = self._agent_invoke_tool(
                agent_node, state, run_id, workflow, step, plan_mode, approved
            )
            status = state.get("_workflow_status")
            if status in ("suspended", "rejected"):
                return state
            if state.get("_node_failed"):
                # 도구 결과 error(error_fails_node) → escalation.
                return self._agent_fail(
                    agent_node,
                    state,
                    run_id,
                    workflow,
                    "agent 도구 실행 결과 error",
                )
            step += 1

        # ① 스텝 상한 도달 — 종료불능으로 판단, escalation(루프 탈출).
        return self._agent_fail(
            agent_node,
            state,
            run_id,
            workflow,
            f"agent 스텝 절대 상한 {self._AGENT_STEP_LIMIT} 도달(종료 신호 미검출)",
        )

    def _agent_fail(
        self,
        agent_node: Node,
        state: dict[str, Any],
        run_id: str,
        workflow: str,
        detail: str,
    ) -> dict[str, Any]:
        """agent 루프 실패 — NODE_FAILED(escalation) + RUN_FINISHED(failed) 발행."""
        self._emit(
            NodeFailed(
                run_id=run_id,
                ts=_now_iso(),
                node_id=agent_node.id,
                reason=FailReason.escalation,
                detail=detail,
            )
        )
        self._emit(
            RunFinished(
                run_id=run_id, ts=_now_iso(), workflow=workflow, status="failed"
            )
        )
        state["_workflow_status"] = "failed"
        return state

    def _window_messages(self, state: dict[str, Any]) -> dict[str, Any]:
        """누적 messages **입력** 상한 가드(③). content 총 길이가 임계 초과면 윈도잉한다.

        system 메시지는 항상 보존하고, 나머지는 **최근 _AGENT_WINDOW_KEEP개만** 유지한다.
        ★accumulated(engine.py:204 carryforward 단일 진실)는 **건드리지 않는다** — 다음
        스텝이 _build_carry_note(accumulated)로 user 노트를 재주입하므로 핵심 상태 손실 0
        (입력 상한이 carryforward를 안 깸 — N4). 임계 이하면 무동작(정상 무변).
        """
        messages: list[dict[str, Any]] = state.get("messages", [])
        total = sum(len(str(m.get("content", ""))) for m in messages)
        if total <= self._AGENT_MSG_CHARS_LIMIT:
            return state
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        kept = non_system[-self._AGENT_WINDOW_KEEP :]
        new_state = dict(state)
        new_state["messages"] = system_msgs + kept
        return new_state

    def _agent_invoke_tool(
        self,
        agent_node: Node,
        state: dict[str, Any],
        run_id: str,
        workflow: str,
        step: int,
        plan_mode: bool,
        approved: set[str],
    ) -> dict[str, Any]:
        """agent 루프가 도구를 호출하는 **유일 경로**(게이팅 단일 관문 · 지시4·MAJOR-4).

        시퀀스 = `_gate`(engine.py) → verdict 분기 → **allow시에만** `_run_tool`. agent
        분기는 self._tools·self._llm·_run_tool에 직접 접근하지 않고 이 관문만 경유한다
        (게이팅 우회 불가 — 구조적 강제).

        ★결정 I 발행 메커니즘(BLOCKER 해소·N1): `_run_tool` 호출 직전 `dataclasses.replace`
        로 **합성 Node**를 만든다 — 가역은 id=`f"{agent}#{step}"`(sub-id·도구별 B1/B2
        dedup), 비가역은 id=base agent id(루프당 1개라 충돌 0·resume 봉합 키 일치).
        `_run_tool`은 무수정(node.id가 5곳 단일 소스라 합성 Node 1개가 일관 관통).

        결정 H/N3: 비가역(verdict=interrupt)이면 즉시 suspend(그 스텝이 마지막). 단 이미
        base id 단위 승인 소진된 비가역(approved)은 _gate 우회(plan 격상 — 재승인 루프 0).
        """
        extracted = state.get("_agent_tool_call") or {}
        # 게이팅이 _last_extracted를 보므로(engine.py:735) agent 추출을 거기에 싣는다.
        state["_last_extracted"] = extracted

        base_id = agent_node.id
        # ★실제 verdict를 plan 격상 없이 먼저 본다 — 이 도구가 비가역(interrupt)인지/가역
        #   (allow)인지 판정하기 위함. 가역은 항상 sub-id(재승인 우회와 무관), 비가역만
        #   base-id + 재승인 우회 대상이다(가역까지 base-id로 만들면 sub-id dedup 붕괴).
        verdict = self._gate(agent_node, state, plan_mode)
        already_approved = base_id in approved

        if verdict == Verdict.block:
            self._emit(
                NodeFailed(
                    run_id=run_id,
                    ts=_now_iso(),
                    node_id=base_id,
                    reason=FailReason.gating,
                    detail=self._block_detail(state),
                )
            )
            self._emit(
                RunFinished(
                    run_id=run_id, ts=_now_iso(), workflow=workflow, status="rejected"
                )
            )
            state["_workflow_status"] = "rejected"
            return state

        if verdict == Verdict.interrupt:
            # 비가역. ★N3 재승인 우회: 이미 base id 단위 승인 소진됐으면 suspend 재발 없이
            #   base-id로 즉시 실행(gating.py interrupt→allow 머신을 노드 단위 승인으로 대체).
            #   resume의 _run_from 재진입이 같은 비가역을 만나도 무한 재승인 루프 0.
            if already_approved:
                synth_node = dataclasses.replace(agent_node, id=base_id)
                return self._run_tool(synth_node, state, run_id)
            # 미승인 비가역 — 결정 H: 즉시 suspend. node_id·_resume_node_id는 base agent id
            # (1b 봉합 — sub-id면 resume node_map.get None→RunError). 발행도 base-id.
            synth_irrev = dataclasses.replace(agent_node, id=base_id)
            self._suspend_for_approval(synth_irrev, state, run_id)
            state["_workflow_status"] = "suspended"
            approved.add(base_id)
            state["_agent_approved_irrev"] = list(approved)
            return state

        # allow(가역) — 합성 sub-id(`agent#step`)로 _run_tool(무수정·도구별 B1/B2 dedup).
        synth_node = dataclasses.replace(agent_node, id=f"{base_id}#{step}")
        return self._run_tool(synth_node, state, run_id)

    # ── 게이팅 보조 ──────────────────────────────────────────────────────────

    def _gate(
        self,
        node: Node,
        state: dict[str, Any],
        plan_mode: bool,
    ) -> Verdict:
        """도구 실행 직전 게이팅 판정. 정책이 없으면 항상 allow.

        LLM이 추출한 도구 이름·인자(state["_last_extracted"])로 판정한다.
        """
        if self._gating_policy is None:
            return Verdict.allow

        extracted = state.get("_last_extracted") or {}
        request = ToolCallRequest(
            name=extracted.get("name", ""),
            args=extracted.get("args", {}),
            node_id=node.id,
            run_id=state.get("run_id", ""),
        )
        return self._gating_policy.evaluate(request, plan_mode=plan_mode)

    def _block_detail(self, state: dict[str, Any]) -> str:
        """block 판정 시 NODE_FAILED.detail 문자열을 만든다."""
        extracted = state.get("_last_extracted") or {}
        tool_name = extracted.get("name", "")
        return f"도구 '{tool_name}'은 block 정책이라 어떤 승인으로도 실행 불가"

    def _suspend_for_approval(
        self,
        node: Node,
        state: dict[str, Any],
        run_id: str,
    ) -> None:
        """INTERRUPT_REQUESTED를 발행하고 재개 정보를 state에 보존한다.

        payload 계약(approve.py §4.4 — 어기면 영구 suspended):
            payload = {task_id, args(정화), tool, node_id, risk, accumulated}
        task_id는 run 호출자가 주입한 값(_task_id). 없으면 None.
        """
        extracted = state.get("_last_extracted") or {}
        tool_name = extracted.get("name", "")
        tool_args = extracted.get("args", {})
        risk = (
            self._gating_policy.risk_of(tool_name)
            if self._gating_policy is not None
            else "needs-approval"
        )
        task_id = state.get("_task_id")
        accumulated = state.get("accumulated", {})

        self._emit(
            InterruptRequested(
                run_id=run_id,
                ts=_now_iso(),
                node_id=node.id,
                tool=tool_name,
                risk=risk,
                payload={
                    "task_id": task_id,
                    "args": tool_args,
                    "tool": tool_name,
                    "node_id": node.id,
                    "risk": risk,
                    "accumulated": accumulated,
                },
            )
        )
        # 재개 정보를 state에 보존 (JSON 직렬화 가능 형태)
        state["_resume_node_id"] = node.id
        state["_resume_tool"] = tool_name
        state["_resume_args"] = tool_args

    def _run_tool(
        self,
        node: Node,
        state: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        """tool 노드의 도구를 실제 실행하고 결과를 캐리포워드 누적한다.

        state["_last_extracted"]의 인자로 도구를 호출한다(LLM 재호출 없음).
        결과는 accumulated에 저장하고 다음 노드용 user 노트로 주입한다
        (role:tool 미사용 — prompt_json 분기, 계획 §4.4.5).
        resume_after_approval도 이 헬퍼를 재사용해 "도구만 실행"을 보장한다.
        """
        extracted = state.get("_last_extracted")
        if not extracted or not self._tools:
            return state

        tool_name = extracted.get("name", "")
        tool_fn = self._tools.get(tool_name)
        if tool_fn is None:
            # ★구성오류 가드(Phase 7 7a AC-2b): 선언된 도구가 self._tools에 미등록이면
            # 도구를 사일런트로 건너뛴다(부수효과 0). 환각 도구명은 _gate가 default_risk
            # (gating.py:137 needs-approval)로 interrupt 선차단하므로 이 경로에 닿지 않는다 —
            # 여기 닿는 건 워크플로가 *선언*한 도구가 REAL_TOOLS(tools.py:399) 미등록인
            # 구성오류뿐이다. 정상(등록 도구) 경로에선 미발화. ★logger는 이벤트 아님
            # (EventStore 미경유)이라 11종·event_from_dict 무변. B1 dedup 분기
            # (has_tool_result)와는 구별 — 그건 도구 *발견* 후 중복 실행 skip이다.
            logger.warning(
                "agent/tool 노드 '%s'가 선언한 도구 '%s' 미등록 — 사일런트 무실행"
                "(구성오류: REAL_TOOLS 등록 누락 추정). run_id=%s",
                node.id,
                tool_name,
                run_id,
            )
            return state

        # ── B1 dedup 가드 (Phase 5 US-P5-2 AC6·7·9) ────────────────────────────
        # 도구 callable 호출 **직전**에 (run_id, node_id)의 TOOL_CALL_RESULT 존재를
        # 확인한다. 이미 있으면(=이 노드 도구가 이미 1회 실행됨) 도구를 재호출하지
        # 않고(부수효과 0) TOOL_CALL_RESULT도 재발행하지 않는다(정확히 1건 유지, AC9).
        # 이 가드가 _run_tool 단일 지점에 있어 일반 실행 경로(allow 분기)와
        # resume_after_approval 경로(재시작·중복 resume) 둘 다 자동으로 커버한다.
        #
        # 크래시 윈도 2종: (a) 마커 발행 후 크래시→재개 시 여기서 True→skip(도구
        # 미호출). (b) 마커 발행 전(tool_fn→_emit 사이) 크래시→재개 시 False라
        # 도구가 재호출되나, 비가역 도구(email_send)는 싱크 멱등키 UNIQUE(B2,
        # US-P5-1)가 이중 기록을 막아 email 정확히 1회.
        #
        # ★dedup skip 시 결과 복원: 현 crm_notify는 email이 마지막 노드(N3
        # next=None)라 후속 캐리포워드 복원이 불요 — skip 후 _run_from이 next=None을
        # 만나 정상 종료(success)에 도달한다(AC8). state를 망가뜨리지 않고 그대로
        # 반환하므로 _workflow_status(success)·RUN_FINISHED 1회가 보존된다.
        # 중간 노드 도구를 가진 일반 워크플로의 결과 복원(이벤트 스토어 재생)은
        # Phase 5 비범위 Follow-up(구현 X — 본 주석으로 명시만).
        #
        # ★이벤트 비대칭(NIT-4·INFO·비도달): 같은 run_id로 allow 경로를 2회 실행하면
        # TOOL_CALL_START(execute에서 dedup 전 발행)는 2회·TOOL_CALL_RESULT는 이 가드로
        # 1회 = 고아 START. 단 복구 경로는 resume_after_approval→_run_tool 직접이라 START
        # 미발행이고 초기 패스 동일 run_id 재실행은 복구에 없어 **도달 불가**. SSE 소비자는
        # 단독 START를 무시하므로 무해(기록만 — 동작 변경 없음).
        if self._store is not None and self._store.has_tool_result(run_id, node.id):
            return state

        # 도구 context 주입(Phase 5 US-P5-1 AC3): 비가역 도구(email_send)가 멱등키
        # f"{run_id}:{node_id}"를 만들 수 있도록 run_id·node_id를 **args와 별도 인자**로
        # 넘긴다. ★context는 절대 args에 병합하지 않는다 — TOOL_CALL_START.args(events)는
        # NodeExecutor.execute가 _last_extracted.args로 별도 발행하므로 여기서 args를
        # 오염시키지 않으면 이벤트 계약이 보존된다(계약 보존). context를 받지 않는
        # 레거시 callable(1-인자)은 시그니처 검사로 걸러 args만 넘긴다(무회귀).
        tool_args = extracted.get("args", {})
        context = {"run_id": run_id, "node_id": node.id}
        try:
            if _tool_accepts_context(tool_fn):
                tool_result = tool_fn(tool_args, context=context)
            else:
                tool_result = tool_fn(tool_args)
        except Exception as exc:
            tool_result = {"error": str(exc)}

        # ToolCallResult 이벤트 발행
        self._emit(
            ToolCallResult(
                run_id=run_id,
                ts=_now_iso(),
                node_id=node.id,
                name=tool_name,
                result=tool_result,
            )
        )

        # error_fails_node=True이고 결과에 error가 있으면 노드 실패 처리 (수용 17)
        if (
            self._error_fails_node
            and isinstance(tool_result, dict)
            and "error" in tool_result
        ):
            self._emit(
                NodeFailed(
                    run_id=run_id,
                    ts=_now_iso(),
                    node_id=node.id,
                    reason=FailReason.escalation,
                    detail=f"도구 '{tool_name}' 실행 결과 error: {tool_result.get('error')}",
                )
            )
            state = dict(state)
            state["_node_failed"] = True
            state["_fail_detail"] = f"tool_error: {tool_result.get('error')}"
            return state

        # 도구 결과를 accumulated에 저장
        new_acc = dict(state.get("accumulated", {}))
        new_acc[f"{node.id}_result"] = tool_result

        # 도구 결과를 user 노트로 다음 노드에 주입 (prompt_json 분기)
        # role:tool 미사용 — 계획 §4.4.5
        result_note = json.dumps(tool_result, ensure_ascii=False)
        new_msgs = list(state.get("messages", [])) + [
            {
                "role": "user",
                "content": f"도구 실행 결과: {result_note}",
            }
        ]

        # StateDelta 이벤트
        self._emit(
            StateDelta(
                run_id=run_id,
                ts=_now_iso(),
                accumulated={f"{node.id}_result": tool_result},
            )
        )

        state = dict(state)
        state["accumulated"] = new_acc
        state["messages"] = new_msgs
        return state

    def _emit_plan(self, nodes: list[Node], run_id: str) -> None:
        """Plan Mode: 실행 예정 노드 계획을 TEXT_MESSAGE로 1회 발행한다.

        각 노드의 id·kind·도구·위험등급을 사람이 읽을 형태로 보여준다.
        """
        plan_lines = ["[Plan Mode] 실행 예정 계획:"]
        for i, node in enumerate(nodes, 1):
            tool_name = _infer_tool_name_from_node(node) if node.tools else ""
            risk = ""
            if tool_name and self._gating_policy is not None:
                risk = self._gating_policy.risk_of(tool_name)
            tool_part = f", tool={tool_name}" if tool_name else ""
            risk_part = f", risk={risk}" if risk else ""
            plan_lines.append(
                f"  {i}. {node.id} (kind={node.kind}{tool_part}{risk_part})"
            )
        self._emit(
            TextMessage(
                run_id=run_id,
                ts=_now_iso(),
                role="assistant",
                content="\n".join(plan_lines),
            )
        )

    # ── resume 진입점 (M10b) ─────────────────────────────────────────────────

    def resume_after_approval(
        self,
        suspended_state: dict[str, Any],
        decision: str,
    ) -> dict[str, Any]:
        """승인/거부 결정 후 suspend된 워크플로를 재개한다 (M10b).

        approve: **LLM을 재호출하지 않는다.** suspend 시점에 영속화한 추출 args로
                 해당 노드의 도구만 실행하고, 다음 노드부터 정상 진행한다.
        reject : RUN_FINISHED(status=rejected)를 발행하고 state를 반환한다.

        Args:
            suspended_state: run()이 suspend 시 반환한 state (JSON 직렬화 가능).
                             _resume_node_id·_resume_args·_nodes·_task_id 등을 담음.
            decision       : "approve" 또는 "reject".

        Returns:
            재개 후 최종 상태 dict.
        """
        run_id: str = suspended_state.get("run_id", "")
        workflow: str = suspended_state.get("_workflow", "")
        plan_mode: bool = suspended_state.get("_plan_mode", False)
        node_dicts: list[dict[str, Any]] = suspended_state.get("_nodes", [])
        nodes = _dicts_to_nodes(node_dicts)
        node_map: dict[str, Node] = {n.id: n for n in nodes}
        resume_node_id: str = suspended_state.get("_resume_node_id", "")
        resume_node = node_map.get(resume_node_id)

        # 결정을 이벤트로 기록 (CLI가 이미 기록했더라도 엔진 경로에서 멱등 발행)
        self._emit(
            InterruptResolved(
                run_id=run_id,
                ts=_now_iso(),
                node_id=resume_node_id,
                tool=suspended_state.get("_resume_tool", ""),
                decision=decision,
            )
        )

        state = dict(suspended_state)

        # reject — 워크플로 거부 종료
        if decision != "approve":
            self._emit(
                RunFinished(
                    run_id=run_id,
                    ts=_now_iso(),
                    workflow=workflow,
                    status="rejected",
                )
            )
            state["_workflow_status"] = "rejected"
            return state

        # approve — LLM 재호출 없이 영속 args로 도구만 실행
        if resume_node is None:
            self._emit(
                RunError(
                    run_id=run_id,
                    ts=_now_iso(),
                    error=f"재개 노드를 찾을 수 없음: {resume_node_id!r}",
                )
            )
            state["_workflow_status"] = "error"
            return state

        # ── agent 노드 resume(G-RESTART · 지시3·MAJOR-3 · 결정 I-1b/N3) ──────────
        # ★tool 경로는 무변(아래 _run_tool — tool-path diff 0). agent는 _run_tool이 루프를
        # 모르므로(단일 도구만) **_run_from(노드 디스패치)로 진입**해 agent 노드를 처음부터
        # 재실행한다(G-RESTART). 승인된 비가역은 base agent id 단위로 승인 소진 표시 →
        # 재진입 루프가 그 비가역을 재차 suspend하지 않는다(_gate 우회 — 재승인 루프 0·N3).
        # 이미 발행된 가역 sub-id는 B1이 도구별 skip(부수효과 0), 비가역은 승인된 base-id
        # 1개만 B1+B2 보호 하에 실행(정확히-1회). _resume_node_id=base agent id라
        # node_map.get(agent_id) 성공(1b 봉합 — sub-id면 None→RunError).
        if resume_node.kind == "agent":
            approved = set(state.get("_agent_approved_irrev", []))
            approved.add(resume_node.id)
            state["_agent_approved_irrev"] = list(approved)
            return self._run_from(
                node_map, resume_node.id, state, run_id, workflow, plan_mode
            )

        # suspend 시점에 영속화한 추출 args를 _last_extracted로 복원해 _run_tool 재사용
        # (LLM.chat()을 부르지 않으므로 "도구만 실행" 보장)
        state["_last_extracted"] = {
            "name": suspended_state.get("_resume_tool", ""),
            "args": suspended_state.get("_resume_args", {}),
        }
        state = self._run_tool(resume_node, state, run_id)

        # 다음 노드부터 정상 진행 (게이팅 루프 재사용)
        return self._run_from(
            node_map, resume_node.next, state, run_id, workflow, plan_mode
        )
