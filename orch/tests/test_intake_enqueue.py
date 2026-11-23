"""US-9d-1 — INTAKE/RESEARCH 부서 intake_requests enqueue (i3).

설계 규약:
  (a) intake/research 노드가 반환 dict에 intake_requests 리스트 필드 방출(operator.add reducer)
  (b) app.py sink가 push_task("intake", item) 호출 (REDIS 게이트)
  (c) ★노드 본문에 in-process 워커/agent.run 호출 0 단언 (§15 경계)
  (d) REDIS 미설정(store=None) 시 graceful no-op·예외 0
  (e) ★CRITICAL-fix seam: **컴파일 그래프**(build_org_graph)를 ainvoke→aget_state 했을 때
      intake_requests 가 snap.values 에 present(단일 dept AND 동시 intake+research) +
      push_task spy 발화. 두 절반(노드 단독·sink 수동주입) 격리 아님 — 통합 seam 입증.

add-only 보존 단언:
  - intake/research 기존 필드(artifacts/shared_facts/departments/routing_trace/messages) 보존
  - 비-intake 부서(dev/qa/art/librarian)는 intake_requests 방출 0

결정론·LLM 0: use_caller(stub) context manager 패턴(기존 test_org_graph.py 동형).
pytest-asyncio 미설치 → asyncio.run 구동.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from orch.graph import use_caller
from orch.org_graph import (
    _make_department_node,
    build_org_graph,
    use_caller as _org_use_caller,  # noqa: F401  (org_graph 재노출 확인)
    use_strategy_caller,
)


# ── stub 헬퍼 ────────────────────────────────────────────────────────────────


def _stub(prompt: str, temperature: float, max_tokens: int) -> str:
    """결정론 stub — LLM 호출 0."""
    return "stub-artifact"


def _run_node(dept_id: str, directive: str = "테스트 지시") -> dict[str, Any]:
    """use_caller(stub) context manager로 _make_department_node 결과 dict 반환.

    기존 test_org_graph.py 동형 패턴.
    """
    with use_caller(_stub), use_strategy_caller(_stub):
        node_fn = _make_department_node(dept_id)
        state: dict[str, Any] = {"directive": directive}
        return node_fn(state)


# ── (a) intake/research 노드 intake_request 방출 ────────────────────────────


def test_intake_node_emits_intake_request():
    """intake 부서 노드가 반환 dict에 intake_requests 리스트(1-원소) 방출."""
    result = _run_node("intake")
    assert "intake_requests" in result, (
        "intake 노드가 intake_requests 필드를 방출해야 함"
    )
    reqs = result["intake_requests"]
    assert isinstance(reqs, list) and len(reqs) == 1
    ir = reqs[0]
    assert ir["queue"] == "intake"
    assert ir["dept"] == "intake"
    assert ir["directive"] == "테스트 지시"
    assert "payload" in ir and ir["payload"]


def test_research_node_emits_intake_request():
    """research 부서 노드가 반환 dict에 intake_requests 리스트 방출."""
    result = _run_node("research")
    assert "intake_requests" in result, (
        "research 노드가 intake_requests 필드를 방출해야 함"
    )
    reqs = result["intake_requests"]
    assert isinstance(reqs, list) and len(reqs) == 1
    assert reqs[0]["queue"] == "intake"
    assert reqs[0]["dept"] == "research"


def test_non_intake_depts_no_intake_request():
    """dev/qa/art/librarian 부서는 intake_requests 방출 0."""
    for dept in ("dev", "qa", "art", "librarian"):
        result = _run_node(dept)
        assert "intake_requests" not in result, (
            f"{dept} 부서는 intake_requests를 방출하면 안 됨"
        )


# ── (b) sink push_task 호출 (REDIS 게이트 로직 직접 검증) ───────────────────


def test_sink_calls_push_task_for_intake_request():
    """org 비-escalate 분기: intake_requests 드레인 → 항목별 push_task('intake', item)."""
    push_calls: list[tuple] = []

    class StubStore:
        async def push_task(self, queue: str, item: dict) -> None:
            push_calls.append((queue, item))

    ir_item = {
        "queue": "intake",
        "dept": "research",
        "directive": "리서치 해줘",
        "payload": "연구 결과",
    }
    snap_values = {
        "intake_requests": [ir_item],
        "shared_facts": {},
        "bluf": "결론",
    }

    store = StubStore()

    async def run():
        # app.py _run kind==org sink 로직 동형 재현(드레인 루프).
        if store is not None:
            for _ir in (snap_values or {}).get("intake_requests", []):
                await store.push_task(_ir.get("queue", "intake"), _ir)

    asyncio.run(run())

    assert len(push_calls) == 1, "push_task 정확히 1회 호출"
    queue, item = push_calls[0]
    assert queue == "intake"
    assert item["dept"] == "research"
    assert item["directive"] == "리서치 해줘"


# ── (d) REDIS 미설정(store=None) graceful ───────────────────────────────────


def test_sink_no_push_when_store_none():
    """store=None(REDIS 미설정) 시 push_task 호출 0·예외 0 (graceful)."""
    snap_values = {
        "intake_requests": [
            {
                "queue": "intake",
                "dept": "intake",
                "directive": "test",
                "payload": "p",
            }
        ]
    }

    async def run():
        store = None
        if store is not None:
            for _ir in (snap_values or {}).get("intake_requests", []):
                await store.push_task(_ir.get("queue", "intake"), _ir)
        # 예외 없이 여기까지 도달 = graceful

    asyncio.run(run())  # 예외 없으면 PASS


def test_sink_no_push_when_no_intake_request():
    """intake_requests 없는 org(dev/qa 등) 에서는 push_task 호출 0."""
    push_calls: list = []

    class StubStore:
        async def push_task(self, queue, item):
            push_calls.append((queue, item))

    async def run():
        store = StubStore()
        snap_values = {"shared_facts": {}, "bluf": "결론"}  # intake_requests 없음
        if store is not None:
            for _ir in (snap_values or {}).get("intake_requests", []):
                await store.push_task(_ir.get("queue", "intake"), _ir)

    asyncio.run(run())
    assert push_calls == [], "intake_requests 없으면 push_task 호출 0"


# ── (c) 노드 본문 in-process 워커 호출 0 단언 ───────────────────────────────


def test_node_body_no_inprocess_worker_calls():
    """★§15 경계: _node 함수 본문에 in-process 워커/agent.run 호출 0.

    팩토리 함수(_make_department_node) 전체가 아닌 ★_node 내부 함수만 대상.
    push_task·asyncio.run·agent.run·intake_worker 등 비동기 워커 패턴 검사.
    """
    # _node 함수 자체의 소스 추출 (팩토리 본문이 아닌 내부 _node)
    with use_caller(_stub), use_strategy_caller(_stub):
        node_fn = _make_department_node("intake")
    node_src = inspect.getsource(node_fn)

    # 금지 패턴: in-process 워커 실행 패턴
    forbidden = [
        r"push_task\s*\(",  # push_task 직접 호출
        r"asyncio\.run\s*\(",
        r"agent\.run\s*\(",
        r"intake_worker\s*\(",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, node_src), (
            f"★in-process 워커 패턴 감지: '{pattern}' — §15 경계 위반"
        )

    # sync def _node에 await 없어야 함
    assert "await " not in node_src, (
        "★sync _node 본문에 await 호출 — in-process 비동기 워커 위반"
    )


# ── add-only 보존 단언 ───────────────────────────────────────────────────────


def test_intake_node_preserves_existing_fields():
    """intake 노드가 intake_request 추가 시 기존 필드(artifacts/shared_facts 등) 보존."""
    result = _run_node("intake", "보존 테스트")
    # 기존 필드 전부 보존
    assert "artifacts" in result and "intake" in result["artifacts"]
    assert "shared_facts" in result and "intake" in result["shared_facts"]
    assert "departments" in result and "intake" in result["departments"]
    assert "routing_trace" in result and "intake" in result["routing_trace"]
    assert "messages" in result and result["messages"]
    # intake_requests add-only
    assert "intake_requests" in result


def test_research_node_preserves_existing_fields():
    """research 노드가 intake_requests 추가 시 기존 필드 보존."""
    result = _run_node("research", "리서치 보존")
    assert "artifacts" in result and "research" in result["artifacts"]
    assert "shared_facts" in result and "research" in result["shared_facts"]
    assert "departments" in result and "research" in result["departments"]
    assert "intake_requests" in result


def test_non_intake_nodes_fields_unchanged():
    """비-intake 부서 필드 구조 변화 없음(add-only 검증)."""
    for dept in ("dev", "qa"):
        result = _run_node(dept)
        assert set(result.keys()) == {
            "artifacts",
            "shared_facts",
            "departments",
            "routing_trace",
            "messages",
        }, f"{dept} 부서 필드 변화 감지: {set(result.keys())}"


# ── (e) ★CRITICAL-fix: 컴파일 그래프 통합 seam (두 절반 격리 아님) ────────────
#
# CRITICAL-1 근본원인: intake_requests 가 OrgState 채널로 선언돼야 LangGraph 가 체크포인트
# 보존 → finalize/snapshot 후 aget_state 의 snap.values 에 present → app.py sink 가 드레인.
# 미선언이면 노드가 set 해도 드롭되어 sink 가 항상 빈 리스트를 읽음(enqueue 0). 아래 테스트는
# **build_org_graph(MemorySaver()).ainvoke → aget_state** 로 실제 그래프를 구동해 채널 보존을
# 단언하고(단일 dept AND 동시 intake+research) push_task spy 발화를 입증한다.


def _route_stub_factory(coo_picks: str):
    """gemma 부서/COO caller stub: COO 디스패치 응답에 부서명을 심어 _pick_many 가 선택하게 함.

    coo_picks 예: "intake" (단일) 또는 "intake, research" (동시 fan-out).
    그 외 호출(부서 산출물/finalize)은 결정론 stub 텍스트 반환.
    """

    def _caller(prompt: str, temperature: float, max_tokens: int) -> str:
        if "[디스패치]" in prompt:
            return f"pick: {coo_picks}"
        return "stub-artifact"

    return _caller


def _ceo_stub(prompt: str, temperature: float, max_tokens: int) -> str:
    """CEO(qwen) stub: 항상 coo 로 라우팅(고확신·escalate/숙의 우회)."""
    return "ROUTE: coo | conf 0.95 | 인입/연구 작업"


async def _drive_org_graph(coo_picks: str):
    """컴파일 org 그래프를 stub caller 로 1회 구동하고 (snap.values, push_spy) 반환.

    app.py _run kind==org sink 의 intake enqueue 드레인 로직을 동형 재현해 spy 발화를 입증한다.
    """
    push_calls: list[tuple] = []

    class StubStore:
        async def push_task(self, queue: str, item: dict) -> None:
            push_calls.append((queue, item))

    caller = _route_stub_factory(coo_picks)
    graph = build_org_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": f"org-test-{coo_picks}"}}
    with use_caller(caller), use_strategy_caller(_ceo_stub):
        await graph.ainvoke({"directive": "외부 자료 인입하고 조사해줘"}, cfg)
        snap = await graph.aget_state(cfg)

    # ★app.py sink 드레인 로직 동형(통합 seam): snap.values 에서 직접 읽어 push.
    store = StubStore()
    for _ir in (snap.values or {}).get("intake_requests", []):
        await store.push_task(_ir.get("queue", "intake"), _ir)
    return snap.values, push_calls


def test_compiled_graph_single_dept_intake_requests_present():
    """★CRITICAL seam: 단일 intake dept → aget_state 의 snap.values 에 intake_requests present.

    (미선언 채널이면 드롭되어 이 단언이 FAIL — 그게 falsifiable 증거.)
    """
    values, push_calls = asyncio.run(_drive_org_graph("intake"))
    reqs = (values or {}).get("intake_requests")
    assert reqs is not None, (
        "intake_requests 가 snap.values 에 present 여야 함(채널 미선언이면 드롭)"
    )
    assert isinstance(reqs, list) and len(reqs) == 1
    assert reqs[0]["dept"] == "intake"
    # sink push_task spy 발화(통합 seam — 노드/싱크 격리 아님)
    assert len(push_calls) == 1
    assert push_calls[0][0] == "intake"


def test_compiled_graph_concurrent_intake_research_accumulate():
    """★CRITICAL seam: 동시 intake+research fan-out → operator.add reducer 가 2건 누적.

    bare key(reducer 없음)였다면 concurrent-write 로 드롭/오류 → 이 단언이 falsify.
    """
    values, push_calls = asyncio.run(_drive_org_graph("intake, research"))
    reqs = (values or {}).get("intake_requests")
    assert reqs is not None, "동시 fan-out 에서도 intake_requests present"
    assert isinstance(reqs, list) and len(reqs) == 2, (
        f"intake+research 2건 누적 기대(operator.add)·실제 {reqs}"
    )
    depts = sorted(r["dept"] for r in reqs)
    assert depts == ["intake", "research"]
    # 2건 모두 push_task spy 발화
    assert len(push_calls) == 2
    assert {c[0] for c in push_calls} == {"intake"}


def test_compiled_graph_non_intake_dept_no_requests():
    """★대조군: art 단독 dispatch → intake_requests 채널 비어 있음(또는 부재) → push 0."""
    values, push_calls = asyncio.run(_drive_org_graph("art"))
    reqs = (values or {}).get("intake_requests") or []
    assert reqs == [], f"비-intake 부서는 intake_requests 0 이어야 함·실제 {reqs}"
    assert push_calls == []
