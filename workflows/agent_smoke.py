"""agent_smoke 워크플로 정의 — Phase 6 6b 격리 테스트 워크플로(OBJ-4).

agent 노드 1개로 구성한다(kind="agent"). 도구 스키마:
  - crm_get_customer  (가역 · always-allow) ┐ 다중 가역 → 합성 sub-id 도구별 dedup 실증
  - order_get_latest  (가역 · always-allow) ┘
  - agent_action      (비가역 · needs-approval) → HITL suspend→resume(G-RESTART) 실증

★crm_notify는 무변경(비범위). 이 워크플로는 agent 루프의 게이팅 단일 관문·합성 sub-id
발행·resume 봉합을 격리 검증하는 용도이며, 종료 신호는 mock LLM이 _AGENT_DONE_MARKER로
낸다(사후검증 AC-2g).

흐름: AGENT(자율 루프) → 종료
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import (
    AGENT_ACTION_SCHEMA,
    CRM_GET_CUSTOMER_SCHEMA,
    ORDER_GET_LATEST_SCHEMA,
)

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

AGENT = Node(
    id="agent_main",
    kind="agent",
    prompt="고객 정보를 조회하고 필요한 동작을 수행하라.",
    thinking="off",
    tools=[CRM_GET_CUSTOMER_SCHEMA, ORDER_GET_LATEST_SCHEMA, AGENT_ACTION_SCHEMA],
    next=None,  # 워크플로 종료(단일 agent 노드)
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [AGENT]
WORKFLOW_NAME = "agent_smoke"
