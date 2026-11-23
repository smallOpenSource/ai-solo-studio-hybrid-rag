"""crm_notify 워크플로 정의 — US-008 (T7).

노드 구성:
  N1  crm_get_customer  (tool · thinking=OFF)
  N2  order_get_latest  (tool · thinking=OFF)
  N2.5 summary          (chat · thinking=ON  ← thinking 양방향 검증용)
  N3  email_send        (tool · thinking=OFF · needs-approval)

흐름: N1 → N2 → N2.5 → N3 → 종료
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import (
    CRM_GET_CUSTOMER_SCHEMA,
    EMAIL_SEND_SCHEMA,
    ORDER_GET_LATEST_SCHEMA,
)

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_crm_get",
    kind="tool",
    prompt_file="prompts/crm_notify/n1_crm_get.txt",
    thinking="off",
    tools=[CRM_GET_CUSTOMER_SCHEMA],
    next="n2_order_get",
)

N2 = Node(
    id="n2_order_get",
    kind="tool",
    prompt_file="prompts/crm_notify/n2_order_get.txt",
    thinking="off",
    tools=[ORDER_GET_LATEST_SCHEMA],
    next="n2_5_summary",
)

N2_5 = Node(
    id="n2_5_summary",
    kind="chat",
    prompt_file="prompts/crm_notify/n2_5_summary.txt",
    thinking="on",  # thinking=ON — reasoning_chars>0 검증 (수용 12)
    next="n3_email_send",
)

N3 = Node(
    id="n3_email_send",
    kind="tool",
    prompt_file="prompts/crm_notify/n3_email_send.txt",
    thinking="off",
    tools=[EMAIL_SEND_SCHEMA],
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2, N2_5, N3]
WORKFLOW_NAME = "crm_notify"
