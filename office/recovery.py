"""running 고아 2a 복구 — 이벤트 재생(A-R2) + 그래프 재로드 + B-HYBRID 판정.

Phase 6 6b Chunk A(계획 ralplan-phase6b-2a-agent.md §6 US-P6B-1). 직전 워커 크래시로
running 상태에 남은 task(고아)를 run_id 보존하며 **재개**한다(6a의 2b=fail+Notify →
2a=재개). 엔진 핫패스(engine.py execute/_run_from/resume)를 건드리지 않으려고 이 모듈로
격리한다 — 복구 경로(워커 부팅 시에만)에서만 도는 코드라 정상 실행 회귀 0.

설계(§3 결정 A·B · A-R2 + B-HYBRID):
- **A-R2 재구성(2원 결합)**:
  ① `EventStore.read(run_id)`(event_store.py:104) seq 순 재생 → STATE_DELTA의
     accumulated 키(`{node.id}_result`/`{node.id}_content` 등)를 누적해 **accumulated 복원**.
     (완료 마커 이벤트가 따로 없어 — engine.py:698 current_id 전이는 미발행 — 노드 경계는
     아래 ②그래프 next 체인 + accumulated 키 존재로 추론한다.)
  ② `_load_workflow(wf_name)`(run.py:108) → **워크플로 그래프**(running 고아엔 `_nodes`가
     없음 — engine.py:563 _nodes는 suspended_state 한정 — 그래프 재로드가 유일 경로).
- **messages 복원 불요**: carryforward 설계상 재개 노드가 `_build_carry_note(accumulated)`
  (engine.py:209)로 user 노트를 재생성하므로 accumulated만 있으면 다음 노드 진행 가능.
- **재개**: 같은 run_id로 `engine.run(..., run_id=<동일>, initial_state={accumulated:복원})`.
  이미 실행된 tool 노드는 B1(`has_tool_result` engine.py:841)이 skip(부수효과 0), 비가역은
  B2 멱등키(business.py)가 이중발송 0. → email 정확히-1회 보존.
- **B-HYBRID(가역만 재개)**: 재구성으로 구한 **재개 노드(첫 미완료 노드)**가 비가역
  (needs-approval/block)이면 2a 재개 안 하고 **fail+NodeFailed(escalation) Notify**(2b 폴백).
  가역이면 재개. 도구 없는 chat 노드는 `_infer_tool_name_from_node`가 node.id를 반환해
  default_risk(needs-approval) **오판**하므로 가역 취급 후 **다음 도구 노드까지 전진 판정**.
  ★agent 노드는 sub-id 재구성을 안 떠안고 비가역 보수 취급(fail+Notify) — 2a 범위는
  결정론 노드(tool/chat) 그래프 한정(AC-P6B-2f-vi · _load_workflow crm_notify agent 0개).

신규 외부 의존성 0(stdlib + 기존 office import).
"""

from __future__ import annotations

import json
from typing import Any

from office.event_store import EventStore
from office.gating import (
    RISK_ALWAYS_ALLOW,
    GatingPolicy,
)
from office.nodes import Node


def replay_accumulated(store: EventStore, run_id: str) -> dict[str, Any]:
    """이벤트를 seq 순 재생해 accumulated를 복원한다(A-R2 ①).

    STATE_DELTA 이벤트의 accumulated dict를 발행 순서대로 병합한다. 엔진은 노드별
    명시 prefix 키(`{node.id}_result`·`{node.id}_call`·`{node.id}_content`·
    `{node.id}_metric`)로 STATE_DELTA를 발행하므로(engine.py:452-465·906-912) 재생
    병합이 결정적이다. read_rows로 원시 payload를 읽어 event_from_dict 역직렬화 비용·
    실패 위험을 피한다(잘못된 값이 있어도 재구성이 죽지 않게).

    Args:
        store : 이벤트 스토어.
        run_id: 복원 대상 실행 식별자.

    Returns:
        복원된 accumulated dict(키 없으면 빈 dict).
    """
    accumulated: dict[str, Any] = {}
    for row in store.read_rows(run_id):
        if row.get("type") != "STATE_DELTA":
            continue
        try:
            data = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        delta = data.get("accumulated", {})
        if isinstance(delta, dict):
            accumulated.update(delta)
    return accumulated


def _node_completed(node: Node, accumulated: dict[str, Any]) -> bool:
    """이 노드가 재생 accumulated 기준 이미 완료됐는지 판정한다(A-R2 ②).

    - tool 노드: `{node.id}_result`가 있으면 도구 실행 완료(TOOL_CALL_RESULT 재생).
    - chat/longform/coding 노드: `{node.id}_content`가 있으면 생성 완료.
    - 기타(도구/콘텐츠 마커 미정 노드, 예: agent): 보수적으로 미완료(False) — 재개 노드로
      판정돼 B-HYBRID가 비가역 취급(fail+Notify)으로 처리한다.
    """
    if node.kind == "tool":
        return f"{node.id}_result" in accumulated
    if node.kind in ("chat", "longform", "coding"):
        return f"{node.id}_content" in accumulated
    return False


def infer_resume_node_id(nodes: list[Node], accumulated: dict[str, Any]) -> str | None:
    """그래프 next 체인 + accumulated 마커로 **재개 노드 id**를 추론한다(A-R2 ②).

    nodes[0]부터 next 체인을 따라가며, 완료 마커가 있는 노드는 건너뛰고 **첫 미완료
    노드 id**를 돌려준다. 모든 노드가 완료면 None(재개할 노드 없음 — 사실상 완료된 run).

    ★완료 마커 이벤트가 따로 없어(engine.py:698 current_id 전이 미발행) 그래프 재로드가
    필수다 — events 단독으로는 노드 경계를 알 수 없다.

    Args:
        nodes      : `_load_workflow`로 재로드한 워크플로 노드 리스트.
        accumulated: replay_accumulated가 복원한 누적값.

    Returns:
        재개할 첫 미완료 노드 id. 전부 완료면 None. nodes가 비면 None.
    """
    node_map: dict[str, Node] = {n.id: n for n in nodes}
    current_id: str | None = nodes[0].id if nodes else None
    seen: set[str] = set()
    while current_id is not None:
        if current_id in seen:
            # 사이클 방어(선형 DAG엔 무관하나 안전) — 더 못 가면 미완료로 본다.
            return current_id
        seen.add(current_id)
        node = node_map.get(current_id)
        if node is None:
            return current_id
        if _node_completed(node, accumulated):
            current_id = node.next
            continue
        return current_id
    return None


def _next_tool_node_is_reversible(
    nodes: list[Node],
    start_id: str,
    gating_policy: GatingPolicy,
) -> bool:
    """start_id부터 next 체인을 전진하며 **첫 도구 노드의 가역성**을 판정한다(B-HYBRID).

    도구 없는 chat 노드는 `_infer_tool_name_from_node`(engine.py:89)가 node.id를 반환해
    default_risk(needs-approval)로 **오판**되므로, chat 노드는 "가역"으로 보고 **다음 도구
    노드까지 전진**한다. agent 노드를 만나면 보수적으로 **비가역(False)** — 2a 범위는
    결정론 노드 한정(AC-P6B-2f-vi). 첫 도구 노드의 risk가 always-allow면 가역(True),
    아니면 비가역(False). 도구 노드 없이 그래프가 끝나면(전부 chat) 가역(True).

    crm_notify의 N2.5(chat)→N3(email_send·needs-approval) 케이스를 실제로 밟는다:
    재개 노드가 N2.5면 chat이라 전진 → N3가 needs-approval → False(fail+Notify).
    """
    from office.engine import _infer_tool_name_from_node

    node_map: dict[str, Node] = {n.id: n for n in nodes}
    current_id: str | None = start_id
    seen: set[str] = set()
    while current_id is not None:
        if current_id in seen:
            return False  # 사이클 — 보수적 비가역
        seen.add(current_id)
        node = node_map.get(current_id)
        if node is None:
            return False
        if node.kind == "agent":
            # agent 노드는 2a 범위 밖(비가역 보수 취급).
            return False
        if node.kind == "tool":
            tool_name = _infer_tool_name_from_node(node)
            return gating_policy.risk_of(tool_name) == RISK_ALWAYS_ALLOW
        # chat/longform/coding: 도구 없음 → 가역 취급, 다음 노드로 전진.
        current_id = node.next
    # 도구 노드 없이 그래프 종료(전부 chat) — 비가역 도구 없음 = 가역.
    return True


def is_resumable(
    nodes: list[Node],
    resume_node_id: str | None,
    gating_policy: GatingPolicy,
) -> bool:
    """재개 노드(첫 미완료)가 **가역 경로**라 2a 재개해도 되는지 판정한다(B-HYBRID).

    resume_node_id가 None(전부 완료)이면 재개 가능(잔여 진행 없음 → 즉시 정상 종료).
    아니면 재개 노드부터 첫 도구 노드의 가역성으로 판정한다. 비가역(needs-approval/
    block)이면 False → 호출자가 fail+NodeFailed(escalation) Notify(2b 폴백).
    """
    if resume_node_id is None:
        return True
    return _next_tool_node_is_reversible(nodes, resume_node_id, gating_policy)
