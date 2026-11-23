"""라이브 SMTP 스모크 — smoke_email 워크플로 로더 단위 검증 (AC-1.1).

prompt-only 주입: crm_notify 4노드를 재사용하되 N1 프롬프트만 교체하고 distinct
WORKFLOW_NAME을 반환한다(M-1). replace는 새 인스턴스를 만들 뿐이라 공유
crm_notify.NODES는 무변이임을 확인한다.

★scripts/run_tests.sh engine 으로만 실행.
"""

from __future__ import annotations

from office.run import _load_workflow
from workflows.crm_notify import NODES as CRM_NODES


def test_smoke_email_loader_returns_distinct_name_and_smoke_n1() -> None:
    """_load_workflow('smoke_email') → (4노드, 'smoke_email'); N1만 스모크 프롬프트."""
    nodes, wf_name = _load_workflow("smoke_email")
    assert (
        wf_name == "smoke_email"
    )  # ★distinct name(M-1) — 이벤트 로그상 crm_notify와 구분
    assert len(nodes) == 4
    # N1만 프롬프트 교체(C-SMOKE-1 조회), id/next는 유지(carryforward 체인 보존)
    assert nodes[0].prompt_file == "prompts/smoke_email/n1_crm_get.txt"
    assert nodes[0].id == "n1_crm_get"
    assert nodes[0].next == "n2_order_get"
    # N2/N2.5/N3는 crm_notify 것 재사용(바이트 동일)
    assert nodes[1].id == "n2_order_get"
    assert nodes[2].id == "n2_5_summary"
    assert nodes[3].id == "n3_email_send"
    assert nodes[3].next is None  # 발송 노드가 워크플로 종료(게이팅 보존)


def test_smoke_email_loader_does_not_mutate_crm_notify() -> None:
    """replace는 새 인스턴스 — 공유 crm_notify.NODES(N1 프롬프트)는 오염되지 않는다."""
    _load_workflow("smoke_email")
    assert CRM_NODES[0].prompt_file == "prompts/crm_notify/n1_crm_get.txt"
    assert CRM_NODES[0].id == "n1_crm_get"
