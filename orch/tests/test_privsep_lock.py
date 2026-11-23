"""SR/renewal P8b US-8b-2 — 권한분리(privsep) lock 테스트.

검증 항목:
  u4: RESEARCH/INTAKE 노드가
      (a) 위험도구(execute/publish/delete/shell류) 미바인드
      (b) 노드 반환 dict에 위험 enqueue(실행류 큐·코드실행/머지/게시 큐 push) 부재
          화이트리스트 단언.

향후 위험 도구/enqueue 추가 시 FAIL — 부재증명 lock.
org_graph 노드 반환 키 인벤토리(artifacts/shared_facts/departments/routing_trace/
messages/intake_requests)가 위험 실행 명령 0임을 단언.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from orch.org_graph import (
    OrgState,
    _make_department_node,
    use_caller,
)


# ---------------------------------------------------------------------------
# 허용 키 화이트리스트 (org_graph.py OrgState 기반)
# ---------------------------------------------------------------------------
# RESEARCH/INTAKE 부서 노드가 반환할 수 있는 안전 키 목록.
# 이 목록에 없는 키가 반환되면 FAIL → 새로운 위험 enqueue 추가 탐지.
_SAFE_RETURN_KEYS: frozenset[str] = frozenset(
    {
        "artifacts",  # 산출물(텍스트·비실행)
        "shared_facts",  # §27.5 Blackboard(비실행 사실 공유)
        "departments",  # 라우팅 추적(비실행)
        "routing_trace",  # 라우팅 추적(비실행)
        "messages",  # LangGraph 메시지(비실행)
        "intake_requests",  # 인입 요청 enqueue(큐 push 판단·실행은 app.py sink)
    }
)

# 위험 키워드: 반환 dict 키 또는 값에 이 패턴이 있으면 위험 enqueue로 판정.
_DANGEROUS_KEY_PATTERNS: tuple[str, ...] = (
    "execute",
    "publish",
    "delete",
    "shell",
    "run_code",
    "merge",
    "deploy",
    "push_task",  # ★안전: intake_requests 내 'queue' 값이 'intake'인 경우만 허용
    "eval",
    "exec",
)

# intake_requests 내 허용 queue 값 (비실행·판단 전달만)
_ALLOWED_INTAKE_QUEUE_VALUES: frozenset[str] = frozenset({"intake"})


# ---------------------------------------------------------------------------
# 헬퍼: 위험 enqueue 여부 판정
# ---------------------------------------------------------------------------


def _is_dangerous_enqueue(result: dict[str, Any]) -> list[str]:
    """반환 dict에서 위험 enqueue 패턴을 탐지한다.

    Returns:
        발견된 위험 패턴 목록(비어 있으면 안전).
    """
    violations: list[str] = []

    # 1. 허용 화이트리스트 외 키 탐지
    extra_keys = set(result.keys()) - _SAFE_RETURN_KEYS
    for k in extra_keys:
        violations.append(f"화이트리스트 외 키: {k!r}")

    # 2. intake_requests 내용 검증
    intake_reqs = result.get("intake_requests", [])
    for req in intake_reqs:
        if not isinstance(req, dict):
            continue
        queue_val = req.get("queue", "")
        if queue_val not in _ALLOWED_INTAKE_QUEUE_VALUES:
            violations.append(
                f"intake_requests queue 위험값: {queue_val!r} "
                f"(허용={_ALLOWED_INTAKE_QUEUE_VALUES})"
            )
        # 위험 필드 탐지 (push_task / execute / shell 등)
        for dangerous in _DANGEROUS_KEY_PATTERNS:
            if dangerous in str(req).lower() and dangerous != "push_task":
                violations.append(
                    f"intake_requests 내 위험 패턴: {dangerous!r} in {req!r}"
                )

    return violations


def _stub_caller(prompt: str, temp: float, max_tok: int) -> str:
    """결정론 stub caller — LLM 없이 노드 실행."""
    return "정상 산출물: 시장 분석 결과입니다."


# ---------------------------------------------------------------------------
# u4: 위험도구 미바인드 단언
# ---------------------------------------------------------------------------


class TestU4PrivsepToolBinding:
    """u4a: RESEARCH/INTAKE 노드 소스코드에 위험도구 바인딩 패턴 0."""

    @pytest.mark.parametrize("dept_id", ["research", "intake"])
    def test_u4a_no_bind_tools_in_node_source(self, dept_id: str) -> None:
        """u4a: _make_department_node 소스에 bind_tools 호출 0."""
        node_fn = _make_department_node(dept_id)
        # 클로저 내부 소스 검사
        source = inspect.getsource(node_fn)
        assert "bind_tools" not in source, (
            f"[u4a] {dept_id} 노드 소스에 bind_tools 발견 — 위험도구 바인딩 금지"
        )

    @pytest.mark.parametrize("dept_id", ["research", "intake"])
    def test_u4a_no_execute_in_node_source(self, dept_id: str) -> None:
        """u4a: 노드 소스에 execute/publish/delete/shell 패턴 0."""
        node_fn = _make_department_node(dept_id)
        source = inspect.getsource(node_fn)
        for dangerous in ("bind_tools", "execute(", "publish(", "delete(", "shell("):
            assert dangerous not in source, (
                f"[u4a] {dept_id} 노드 소스에 위험 패턴 발견: {dangerous!r}"
            )

    def test_u4a_make_department_node_factory_source(self) -> None:
        """u4a: _make_department_node 팩토리 자체 소스 검사."""
        factory_source = inspect.getsource(_make_department_node)
        for dangerous in (
            "bind_tools",
            ".execute(",
            ".publish(",
            ".delete(",
            "shell(",
            "subprocess",
        ):
            assert dangerous not in factory_source, (
                f"[u4a] _make_department_node 팩토리에 위험 패턴: {dangerous!r}"
            )


# ---------------------------------------------------------------------------
# u4: 노드 반환 화이트리스트 단언
# ---------------------------------------------------------------------------


class TestU4PrivsepReturnWhitelist:
    """u4b: RESEARCH/INTAKE 노드 반환 dict에 위험 enqueue 부재 화이트리스트."""

    @pytest.mark.parametrize("dept_id", ["research", "intake"])
    def test_u4b_return_keys_whitelist(self, dept_id: str) -> None:
        """u4b: 노드 반환 키가 화이트리스트 내(향후 위험 추가 → FAIL)."""
        node_fn = _make_department_node(dept_id)
        state: OrgState = {"directive": "시장 조사를 수행하라"}  # type: ignore[typeddict-item]

        with use_caller(_stub_caller):
            result = node_fn(state)

        assert isinstance(result, dict), "노드는 dict를 반환해야 함"

        extra_keys = set(result.keys()) - _SAFE_RETURN_KEYS
        assert not extra_keys, (
            f"[u4b] {dept_id} 노드가 화이트리스트 외 키 반환: {extra_keys}\n"
            f"허용 키={_SAFE_RETURN_KEYS}"
        )

    @pytest.mark.parametrize("dept_id", ["research", "intake"])
    def test_u4b_no_dangerous_enqueue(self, dept_id: str) -> None:
        """u4b: 반환 dict에 위험 실행 enqueue 0(부재증명 falsifiable)."""
        node_fn = _make_department_node(dept_id)
        state: OrgState = {"directive": "외부 데이터를 인입하라"}  # type: ignore[typeddict-item]

        with use_caller(_stub_caller):
            result = node_fn(state)

        violations = _is_dangerous_enqueue(result)
        assert not violations, f"[u4b] {dept_id} 노드 위험 enqueue 발견:\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    @pytest.mark.parametrize("dept_id", ["research", "intake"])
    def test_u4b_intake_requests_queue_value_safe(self, dept_id: str) -> None:
        """u4b: intake_requests[*].queue 값이 허용값('intake')만."""
        node_fn = _make_department_node(dept_id)
        state: OrgState = {"directive": "외부 문서를 인입하라"}  # type: ignore[typeddict-item]

        with use_caller(_stub_caller):
            result = node_fn(state)

        intake_reqs = result.get("intake_requests", [])
        for req in intake_reqs:
            queue_val = req.get("queue", "")
            assert queue_val in _ALLOWED_INTAKE_QUEUE_VALUES, (
                f"[u4b] {dept_id} intake_requests queue 위험값: {queue_val!r}"
            )

    def test_u4b_non_intake_research_depts_no_intake_requests(self) -> None:
        """u4b: art/librarian/dev/qa 부서는 intake_requests 방출 안 함."""
        safe_depts = ("art", "librarian", "dev", "qa")
        for dept_id in safe_depts:
            node_fn = _make_department_node(dept_id)
            state: OrgState = {"directive": "에셋을 만들어라"}  # type: ignore[typeddict-item]

            with use_caller(_stub_caller):
                result = node_fn(state)

            intake_reqs = result.get("intake_requests", [])
            assert not intake_reqs, (
                f"[u4b] {dept_id} 부서가 intake_requests 방출: {intake_reqs}"
            )


# ---------------------------------------------------------------------------
# u4: OrgState 채널 인벤토리 불변식
# ---------------------------------------------------------------------------


class TestU4OrgStateInventory:
    """u4c: OrgState 채널이 위험 실행 명령 키 미포함 불변식."""

    def test_u4c_orgstate_keys_no_dangerous_channels(self) -> None:
        """u4c: OrgState 필드에 위험 실행 채널(execute/publish/delete) 없음."""
        org_state_fields = set(OrgState.__annotations__.keys())
        dangerous_fields = {
            f
            for f in org_state_fields
            if any(
                d in f.lower()
                for d in ("execute", "publish", "delete", "shell", "run_code")
            )
        }
        assert not dangerous_fields, (
            f"[u4c] OrgState에 위험 실행 채널 필드 추가됨: {dangerous_fields}\n"
            "권한분리 위반 — 이 필드가 정말 필요하면 ADR 필요"
        )

    def test_u4c_all_depts_return_keys_whitelist(self) -> None:
        """u4c: 모든 6개 부서 노드 반환 키가 화이트리스트 내."""
        all_depts = ("research", "art", "librarian", "intake", "dev", "qa")
        state: OrgState = {"directive": "작업을 수행하라"}  # type: ignore[typeddict-item]

        with use_caller(_stub_caller):
            for dept_id in all_depts:
                node_fn = _make_department_node(dept_id)
                result = node_fn(state)
                extra_keys = set(result.keys()) - _SAFE_RETURN_KEYS
                assert not extra_keys, (
                    f"[u4c] {dept_id} 부서 화이트리스트 외 키: {extra_keys}"
                )


# ---------------------------------------------------------------------------
# u4: 위험도구 추가 감지 (미래 회귀 방지)
# ---------------------------------------------------------------------------


class TestU4FutureRegression:
    """u4d: 위험 패턴 추가 시 즉시 FAIL하는 sentinel 테스트."""

    def test_u4d_sentinel_safe_result_passes(self) -> None:
        """u4d: 안전한 반환 dict는 위반 0 (sentinel 정상 동작 확인)."""
        safe_result = {
            "artifacts": {"research": "조사 결과"},
            "shared_facts": {"research": {"by": "research", "finding": "결과"}},
            "departments": ["research"],
            "routing_trace": ["research"],
            "messages": [{"role": "assistant", "content": "[research] ..."}],
            "intake_requests": [
                {
                    "queue": "intake",
                    "dept": "research",
                    "directive": "...",
                    "payload": "...",
                }
            ],
        }
        violations = _is_dangerous_enqueue(safe_result)
        assert not violations, f"안전 결과에 위반 발생(sentinel 오작동): {violations}"

    def test_u4d_sentinel_dangerous_queue_fails(self) -> None:
        """u4d: 위험 queue 값 추가 시 FAIL (미래 회귀 lock)."""
        dangerous_result = {
            "artifacts": {"research": "조사 결과"},
            "shared_facts": {},
            "departments": ["research"],
            "routing_trace": ["research"],
            "messages": [],
            "intake_requests": [
                {"queue": "code_execute", "dept": "research", "directive": "rm -rf /"}
            ],
        }
        violations = _is_dangerous_enqueue(dangerous_result)
        assert violations, "위험 queue 값이 탐지되지 않음 — sentinel lock 오작동"

    def test_u4d_sentinel_extra_key_fails(self) -> None:
        """u4d: 화이트리스트 외 키 추가 시 FAIL (미래 회귀 lock)."""
        extra_key_result = {
            "artifacts": {},
            "shared_facts": {},
            "departments": [],
            "routing_trace": [],
            "messages": [],
            "intake_requests": [],
            "execute_command": "rm -rf /important",  # 위험 추가
        }
        violations = _is_dangerous_enqueue(extra_key_result)
        assert violations, "화이트리스트 외 키가 탐지되지 않음 — sentinel lock 오작동"
