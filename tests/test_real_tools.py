"""실도구 + context 주입 단위/통합 테스트 — Phase 5 US-P5-1 (AC1·2·3·4).

테스트 범위 (계획 §5 Unit + §6 AC):
- AC1: real_crm_get_customer/real_order_get_latest가 business.db 실조회(시드 반환)
- AC1/17: 미존재 id → error dict(code=NOT_FOUND) → NODE_FAILED 경로 보존
- AC2: real_email_send가 email_outbox에 기록(부수효과 관측 가능)
- AC3: email_send 멱등키 f"{run_id}:{node_id}"로 이중 발송 차단(같은 context 2회 → 1행)
- AC3: context 주입이 TOOL_CALL_START.args를 오염시키지 않음(엔진 경로, 계약 보존)
- AC4: REAL_TOOLS가 외부 SaaS 호출 0 — 자격증명 부재 시 싱크만(smtplib 미접속)

business 스토어는 reset_business_store로 tmp 경로를 주입해 격리한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from office.business import BusinessStore
from office.engine import NodeExecutor, WorkflowEngine, _tool_accepts_context
from office.event_store import EventStore
from office.events import ToolCallResult, ToolCallStart
from office.tools import (
    REAL_TOOLS,
    real_crm_get_customer,
    real_email_send,
    real_order_get_latest,
    reset_business_store,
)
from workflows.crm_notify import NODES, WORKFLOW_NAME


@pytest.fixture
def biz_store(tmp_path: Path) -> Iterator[BusinessStore]:
    """tmp business.db를 전역 스토어로 주입하고, 끝나면 리셋한다(격리)."""
    store = BusinessStore(db_path=tmp_path / "business.db")
    reset_business_store(store)
    try:
        yield store
    finally:
        reset_business_store(None)  # 기본 경로로 복원(다음 호출 시 재생성)


# ── AC1: crm/order 실조회 (시드 반환) ─────────────────────────────────────────


def test_real_crm_get_customer_returns_seed(biz_store: BusinessStore) -> None:
    """real_crm_get_customer가 business.db 시드를 실조회해 돌려준다(mock 값 정합)."""
    result = real_crm_get_customer({"customer_id": "C-2041"})
    assert result == {
        "customer_id": "C-2041",
        "name": "김민준",
        "email": "minjun.kim@example.com",
        "tier": "gold",
        "phone": "010-1234-5678",
    }


def test_real_order_get_latest_returns_seed(biz_store: BusinessStore) -> None:
    """real_order_get_latest가 business.db 시드를 실조회해 돌려준다."""
    result = real_order_get_latest({"customer_id": "C-2041"})
    assert result["order_id"] == "ORD-9981"
    assert result["product"] == "노트북 Pro 15"
    assert result["status"] == "배송완료"


# ── AC1/17: 미존재 id → error dict (NODE_FAILED 경로 보존) ────────────────────


def test_real_crm_error_id_returns_error(biz_store: BusinessStore) -> None:
    """기존 'error-id' 계약 보존 → error dict(code=NOT_FOUND)."""
    result = real_crm_get_customer({"customer_id": "error-id"})
    assert result.get("code") == "NOT_FOUND"
    assert "error" in result


def test_real_crm_missing_id_returns_error(biz_store: BusinessStore) -> None:
    """미존재 customer_id → error dict(NODE_FAILED 경로 보존, 수용 17)."""
    result = real_crm_get_customer({"customer_id": "C-9999"})
    assert result.get("code") == "NOT_FOUND"
    assert "error" in result


def test_real_order_missing_id_returns_error(biz_store: BusinessStore) -> None:
    """미존재 customer_id 주문 → error dict."""
    result = real_order_get_latest({"customer_id": "C-9999"})
    assert result.get("code") == "NOT_FOUND"


# ── AC2: email_send 싱크 기록 (부수효과 관측 가능) ───────────────────────────


def test_real_email_send_records_to_outbox(biz_store: BusinessStore) -> None:
    """real_email_send가 email_outbox에 행을 기록한다(부수효과 관측 가능)."""
    result = real_email_send(
        {"to": "minjun.kim@example.com", "subject": "주문 확인", "body": "안녕하세요"},
        context={"run_id": "run-abc", "node_id": "n3_email_send"},
    )
    assert result["sent"] is True
    assert result["to"] == "minjun.kim@example.com"
    assert biz_store.count_emails() == 1
    row = biz_store.get_email_by_key("run-abc:n3_email_send")
    assert row is not None
    assert row["subject"] == "주문 확인"


# ── AC3: 멱등키로 이중 발송 차단 (같은 context 2회 → 1행) ────────────────────


def test_real_email_send_idempotent_same_context(biz_store: BusinessStore) -> None:
    """같은 (run_id, node_id) context로 2회 호출 → 싱크 행 1개(이중 발송 차단, B2/AC3)."""
    ctx = {"run_id": "run-xyz", "node_id": "n3_email_send"}
    first = real_email_send({"to": "a@b.com", "subject": "s", "body": "b"}, context=ctx)
    second = real_email_send(
        {"to": "a@b.com", "subject": "s", "body": "b"}, context=ctx
    )
    assert first["deduplicated"] is False  # 첫 발송
    assert second["deduplicated"] is True  # 둘째는 멱등키 충돌로 무시
    assert biz_store.count_emails() == 1
    # message_id가 멱등키 기반 결정론 — 같은 키면 같은 id
    assert first["message_id"] == second["message_id"] == "OUTBOX-run-xyz:n3_email_send"


def test_real_email_send_distinct_context_two_rows(biz_store: BusinessStore) -> None:
    """서로 다른 run_id → 각각 발송(정상 다건)."""
    real_email_send(
        {"to": "a@b.com", "subject": "s", "body": "b"},
        context={"run_id": "run-1", "node_id": "n3_email_send"},
    )
    real_email_send(
        {"to": "a@b.com", "subject": "s", "body": "b"},
        context={"run_id": "run-2", "node_id": "n3_email_send"},
    )
    assert biz_store.count_emails() == 2


# ── AC4: REAL_TOOLS 시그니처 균일 (context 수용) ──────────────────────────────


def test_all_real_tools_accept_context() -> None:
    """REAL_TOOLS의 모든 callable이 context 인자를 받는다(_tool_accepts_context True)."""
    for name, fn in REAL_TOOLS.items():
        assert _tool_accepts_context(fn) is True, f"{name}이 context를 안 받음"


def test_real_tools_registry_keys() -> None:
    """REAL_TOOLS가 프로덕션 도구를 모두 등록한다 (crm/order/email/knowledge + game_data_write/read/validate)."""
    assert set(REAL_TOOLS.keys()) == {
        "crm_get_customer",
        "order_get_latest",
        "email_send",
        "knowledge_search",
        "game_data_write",  # G005 US-G5-1 — game/ 로컬 파일쓰기(가역·always-allow)
        "game_data_read",  # G005 US-G5-4 — game/ 로컬 파일읽기(읽기전용·always-allow)
        "game_validate",  # G005 US-G5-6 — game/data 스키마 검증(읽기전용·always-allow)
    }


# ── AC3: context 주입이 TOOL_CALL_START.args를 오염시키지 않음 (엔진 경로) ────


class _SeqLLM:
    """노드별 응답을 순서대로 반환하는 Fake LLM."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._idx = 0

    def chat(
        self, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


def _stop(content: str, reasoning: str = "") -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "reasoning_content": reasoning,
            "tool_calls": [],
        },
        "content": content,
        "reasoning_content": reasoning,
        "finish_reason": "stop",
        "usage": {},
        "latency_ms": 5.0,
    }


def test_context_not_leaked_into_tool_call_start_args(
    biz_store: BusinessStore, tmp_path: Path
) -> None:
    """엔진이 context를 args와 별도 인자로 넘기므로 TOOL_CALL_START.args에 run_id/node_id가
    새지 않는다(계약 보존, AC3). N1 crm 노드를 실도구로 실행해 단언한다."""
    db_path = str(tmp_path / "office.db")
    store = EventStore(db_path)
    # N1(crm) 1노드만 실행하면 충분 — gating 없이 도구 즉시 실행.
    from dataclasses import replace

    n1_only = [replace(NODES[0], next=None)]
    llm = _SeqLLM(
        [_stop('{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}')]
    )
    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(executor=executor, event_store=store, tools=REAL_TOOLS)
    engine.run(nodes=n1_only, run_id="ctx", workflow=WORKFLOW_NAME)

    events = store.read("ctx")
    starts = [e for e in events if isinstance(e, ToolCallStart)]
    assert len(starts) == 1
    args = starts[0].args
    # args는 LLM이 추출한 값만 — context 키가 절대 섞이지 않는다.
    assert "run_id" not in args
    assert "node_id" not in args
    assert args == {"customer_id": "C-2041"}

    # 도구는 정상 실행돼 결과가 났다(실조회 성공).
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 1
    assert results[0].result["customer_id"] == "C-2041"


# ── 레거시 1-인자 callable 무회귀 (context 미주입) ────────────────────────────


def test_legacy_single_arg_tool_not_given_context() -> None:
    """1-인자 lambda는 _tool_accepts_context False → 엔진이 context를 안 넘긴다(무회귀)."""
    assert _tool_accepts_context(lambda args: {"ok": True}) is False
    # **kwargs를 받으면 주입 가능으로 판정
    assert _tool_accepts_context(lambda args, **kw: {"ok": True}) is True
