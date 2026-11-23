"""office/engine.py 단위 테스트 — US-004 T4a.

테스트 범위 (계획 §5 Unit, 수용 기준 4·5·16):
1. 노드 성공/실패 판정
2. 캐리포워드 누적 — 앞 노드 결과가 다음 노드 user 노트에 등장
3. 종류별 max_tokens 호출 인자 검증
4. thinking 토글이 extra_body에 반영
5. kind="agent" → NotImplementedError
6. 이벤트 발행 순서 (tmp db EventStore)
7. 정적 단언 (수용 16): office/ 소스에서 LLMClient.chat() 호출이
   engine.py NodeExecutor 내부에만 존재함을 grep식으로 단언
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.nodes import MAX_TOKENS_BY_KIND, Node

# ── Fake LLM 헬퍼 ────────────────────────────────────────────────────────────


class FakeLLM:
    """테스트용 fake LLM — 호출 인자를 캡처하고 미리 정해진 응답을 반환한다."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        """
        Args:
            responses: 순서대로 반환할 응답 리스트.
                       소진되면 마지막 응답을 반복한다.
        """
        self.calls: list[dict[str, Any]] = []  # 호출 인자 캡처
        self._responses = responses or []
        self._idx = 0

    def _next_resp(self) -> dict[str, Any]:
        if not self._responses:
            return _stop_resp('{"name":"fn","arguments":{}}')
        resp = self._responses[self._idx]
        if self._idx < len(self._responses) - 1:
            self._idx += 1
        return resp

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "extra_body": extra_body,
            }
        )
        return self._next_resp()


def _stop_resp(content: str, reasoning: str = "") -> dict[str, Any]:
    """finish_reason=stop인 fake 응답을 만든다."""
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "reasoning_content": reasoning,
            "tool_calls": [],
        },
        "content": content,
        "reasoning_content": reasoning,
        "tool_calls": [],
        "finish_reason": "stop",
        "usage": {},
        "latency_ms": 1.0,
    }


def _length_resp() -> dict[str, Any]:
    """finish_reason=length인 fake 응답을 만든다 (절단 시뮬레이션)."""
    return {
        "message": {
            "role": "assistant",
            "content": '{"name":"fn","arguments":{"body":"잘린 내용...',
            "reasoning_content": "",
            "tool_calls": [],
        },
        "content": '{"name":"fn","arguments":{"body":"잘린 내용...',
        "reasoning_content": "",
        "tool_calls": [],
        "finish_reason": "length",
        "usage": {},
        "latency_ms": 1.0,
    }


# ── 1. 노드 성공/실패 판정 ────────────────────────────────────────────────────


def test_tool_node_success() -> None:
    """tool 노드가 valid JSON content를 반환하면 성공이다."""
    fake = FakeLLM(
        [_stop_resp('{"name":"crm_get_customer","arguments":{"email":"a@b.com"}}')]
    )
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n1", kind="tool", prompt="고객 조회", thinking="off")
    state: dict[str, Any] = {
        "run_id": "r1",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }
    result = executor.execute(node, state)
    assert not result.get("_node_failed"), "tool 노드가 예상치 않게 실패했다"


def test_tool_node_length_fail() -> None:
    """finish_reason=length → max_tokens ×2 재시도 → 재시도도 length → 노드 실패.

    US-005 length 가드 행위 단언:
    - 원본 max_tokens(512)로 1차 호출 후 length
    - retry max_tokens(1024 = min(512*2, 16384))로 2차 호출
    - 재시도도 length → _node_failed=True, _fail_detail="finish_reason=length"
    - 총 호출 횟수 = 2 (원본 + 재시도 정확히 1회)
    """
    fake = FakeLLM([_length_resp()])  # 마지막 응답 반복 → 재시도도 length
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n1", kind="tool", prompt="도구 호출", thinking="off")
    state: dict[str, Any] = {
        "run_id": "r1",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }
    result = executor.execute(node, state)
    assert result.get("_node_failed") is True
    assert result.get("_fail_detail") == "finish_reason=length"
    # US-005 강화 단언: 재시도 1회 포함 총 2회 호출
    assert len(fake.calls) == 2, (
        f"length 가드는 원본+재시도 2회 호출이어야 한다. 실제: {len(fake.calls)}"
    )
    # retry max_tokens = min(512 * 2, 16384) = 1024
    original_max = MAX_TOKENS_BY_KIND["tool"]  # 512
    expected_retry = min(original_max * 2, 16384)  # 1024
    assert fake.calls[0]["max_tokens"] == original_max, "1차 호출 max_tokens 불일치"
    assert fake.calls[1]["max_tokens"] == expected_retry, (
        f"retry max_tokens 불일치: 기대={expected_retry}, 실제={fake.calls[1]['max_tokens']}"
    )


def test_tool_node_sanitize_fail() -> None:
    """복구 불가 content-JSON은 노드 실패로 처리된다 (정화 실패)."""
    bad = '{"name":"fn","arguments":{"x":"잘린...'
    fake = FakeLLM([_stop_resp(bad)])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n1", kind="tool", prompt="도구 호출", thinking="off")
    state: dict[str, Any] = {
        "run_id": "r1",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }
    result = executor.execute(node, state)
    assert result.get("_node_failed") is True
    assert result.get("_fail_detail") == "sanitize_failed"


def test_chat_node_success() -> None:
    """chat 노드가 비어있지 않은 content를 반환하면 성공이다."""
    fake = FakeLLM([_stop_resp("주문이 완료되었습니다.")])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n_chat", kind="chat", prompt="요약하라", thinking="on")
    state: dict[str, Any] = {
        "run_id": "r1",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }
    result = executor.execute(node, state)
    assert not result.get("_node_failed"), "chat 노드가 예상치 않게 실패했다"


def test_chat_node_empty_content_fails() -> None:
    """chat 노드의 content가 비어있으면 노드 실패다."""
    fake = FakeLLM([_stop_resp("")])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n_chat", kind="chat", prompt="요약하라", thinking="off")
    state: dict[str, Any] = {
        "run_id": "r1",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }
    result = executor.execute(node, state)
    assert result.get("_node_failed") is True
    assert result.get("_fail_detail") == "empty_content"


def test_agent_kind_single_step_no_raise() -> None:
    """kind='agent' 노드는 Phase 6 6b부터 NotImplementedError를 안 내고 1스텝을 실행한다.

    ★의도적 flip(REG-flip·N-5): 기존 test_agent_kind_raises를 동작 테스트로 교체.
    execute(agent)는 agent_step에 위임 — 단일 추론(정화·max_tokens 8192)을 통과하고
    실패하지 않으며, 도구 호출 JSON이면 _agent_tool_call로 추출한다(도구 실행은 엔진 책임).
    """
    fake = FakeLLM(
        [_stop_resp('{"name":"crm_get_customer","arguments":{"customer_id":"C-1"}}')]
    )
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n_agent", kind="agent", prompt="자율 수행")
    state: dict[str, Any] = {"run_id": "r1", "messages": [], "accumulated": {}}
    result = executor.execute(node, state)
    assert not result.get("_agent_step_failed"), "agent 스텝이 예상치 않게 실패했다"
    assert result["_agent_tool_call"] == {
        "name": "crm_get_customer",
        "args": {"customer_id": "C-1"},
    }
    # 출력 토큰 상한 8192(MAX_TOKENS_BY_KIND['agent'])로 호출됐는지.
    assert fake.calls[0]["max_tokens"] == 8192


# ── 2. 캐리포워드 누적 ──────────────────────────────────────────────────────


def test_carryforward_accumulated_in_next_node_user_note() -> None:
    """앞 노드의 도구 결과가 다음 노드의 user 노트(캐리포워드)에 등장한다.

    계획 §4.4.5: prompt_json 분기에서 도구 결과는 user 노트로 주입,
    role:tool 미사용.
    """
    # N1: tool 노드 성공 → accumulated에 n1_call 저장
    resp1 = _stop_resp('{"name":"crm_get_customer","arguments":{"email":"a@b.com"}}')
    # N2: tool 노드 — 두 번째 호출의 messages를 검사
    resp2 = _stop_resp(
        '{"name":"order_get_latest","arguments":{"customer_id":"C-001"}}'
    )

    fake = FakeLLM([resp1, resp2])

    node1 = Node(id="n1", kind="tool", prompt="고객 조회", thinking="off", next="n2")
    node2 = Node(id="n2", kind="tool", prompt="주문 조회", thinking="off")

    executor = NodeExecutor(llm_client=fake)

    state: dict[str, Any] = {
        "run_id": "r-carry",
        "messages": [],
        "accumulated": {},
        "model": "test-model",
    }

    # N1 실행
    state = executor.execute(node1, state)
    assert not state.get("_node_failed")

    # N1 결과가 accumulated에 저장됐는지 확인
    assert "n1_call" in state.get("accumulated", {})

    # N2 실행
    state = executor.execute(node2, state)

    # N2 호출 시 messages에 캐리포워드 user 노트가 있어야 한다
    assert len(fake.calls) == 2
    n2_messages = fake.calls[1]["messages"]
    # user role 메시지 중 "지금까지 확인된 값" 노트가 있어야 함
    carry_msgs = [
        m
        for m in n2_messages
        if m.get("role") == "user" and "지금까지 확인된 값" in m.get("content", "")
    ]
    assert len(carry_msgs) >= 1, (
        "N2 messages에 캐리포워드 user 노트가 없다. "
        f"메시지 목록: {[m.get('content', '')[:80] for m in n2_messages]}"
    )


def test_tool_result_not_role_tool() -> None:
    """도구 결과 주입 시 role:tool 메시지가 없다 (prompt_json 분기 규칙)."""
    resp1 = _stop_resp('{"name":"fn","arguments":{"x":"1"}}')
    resp2 = _stop_resp('{"name":"fn2","arguments":{}}')
    fake = FakeLLM([resp1, resp2])

    node1 = Node(id="n1", kind="tool", prompt="p1", thinking="off", next="n2")
    node2 = Node(id="n2", kind="tool", prompt="p2", thinking="off")

    # tools registry에 fn을 등록해서 결과가 생기도록
    tools = {"fn": lambda args: {"customer_id": "C-001"}}
    engine = WorkflowEngine(
        executor=NodeExecutor(llm_client=fake),
        tools=tools,
    )
    engine.run([node1, node2], run_id="r-no-role-tool")

    # 두 번째 호출 messages에 role:tool이 없어야 한다
    n2_messages = fake.calls[1]["messages"]
    role_tool_msgs = [m for m in n2_messages if m.get("role") == "tool"]
    assert role_tool_msgs == [], (
        "prompt_json 분기에서 role:tool 메시지가 생성되면 안 된다. "
        f"발견된 메시지: {role_tool_msgs}"
    )


# ── 3. 종류별 max_tokens 호출 인자 검증 ──────────────────────────────────────


@pytest.mark.parametrize(
    "kind,expected_tokens",
    [
        ("tool", 512),
        ("chat", 4096),
        ("longform", 8192),
        ("coding", 16384),
    ],
)
def test_max_tokens_by_kind_passed_to_llm(kind: str, expected_tokens: int) -> None:
    """노드 종류별 max_tokens가 LLM chat 호출 인자에 정확히 전달된다 (수용 기준 4)."""
    if kind == "tool":
        content = '{"name":"fn","arguments":{}}'
    else:
        content = "결과 텍스트"
    fake = FakeLLM([_stop_resp(content)])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n", kind=kind, prompt="p", thinking="off")  # type: ignore[arg-type]
    state: dict[str, Any] = {
        "run_id": "r",
        "messages": [],
        "accumulated": {},
        "model": "m",
    }
    executor.execute(node, state)

    assert len(fake.calls) == 1
    assert fake.calls[0]["max_tokens"] == expected_tokens, (
        f"kind={kind!r}에서 max_tokens={fake.calls[0]['max_tokens']}가 전달됐는데 "
        f"{expected_tokens}여야 한다"
    )


def test_max_tokens_override_passed_to_llm() -> None:
    """노드 max_tokens 오버라이드가 호출 인자에 반영된다."""
    fake = FakeLLM([_stop_resp('{"name":"fn","arguments":{}}')])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n", kind="tool", prompt="p", thinking="off", max_tokens=256)
    state: dict[str, Any] = {
        "run_id": "r",
        "messages": [],
        "accumulated": {},
        "model": "m",
    }
    executor.execute(node, state)
    assert fake.calls[0]["max_tokens"] == 256


def test_all_max_tokens_by_kind_le_16384() -> None:
    """MAX_TOKENS_BY_KIND의 모든 값이 16384 이하다 (32k 금지 — 수용 기준 4)."""
    for kind, tokens in MAX_TOKENS_BY_KIND.items():
        assert tokens <= 16384, (
            f"{kind}: max_tokens={tokens}가 16384를 초과한다. 32k 일괄 금지."
        )


# ── 4. thinking 토글이 extra_body에 반영 ──────────────────────────────────


def test_thinking_off_sets_enable_thinking_false() -> None:
    """thinking='off' 노드는 extra_body에 enable_thinking=False가 들어간다."""
    fake = FakeLLM([_stop_resp('{"name":"fn","arguments":{}}')])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n", kind="tool", prompt="p", thinking="off")
    state: dict[str, Any] = {
        "run_id": "r",
        "messages": [],
        "accumulated": {},
        "model": "m",
    }
    executor.execute(node, state)

    extra = fake.calls[0]["extra_body"]
    assert extra is not None
    assert extra.get("chat_template_kwargs", {}).get("enable_thinking") is False


def test_thinking_on_does_not_set_enable_thinking_false() -> None:
    """thinking='on' 노드는 extra_body에 enable_thinking=False가 없다."""
    fake = FakeLLM([_stop_resp("요약 결과입니다.")])
    executor = NodeExecutor(llm_client=fake)
    node = Node(id="n", kind="chat", prompt="p", thinking="on")
    state: dict[str, Any] = {
        "run_id": "r",
        "messages": [],
        "accumulated": {},
        "model": "m",
    }
    executor.execute(node, state)

    extra = fake.calls[0]["extra_body"]
    # thinking=on이면 extra_body가 None이거나 enable_thinking=False가 없어야 함
    if extra is not None:
        assert extra.get("chat_template_kwargs", {}).get("enable_thinking") is not False


# ── 5. 이벤트 발행 순서 (tmp db EventStore) ───────────────────────────────


def test_workflow_engine_emits_events_in_order(tmp_path: Path) -> None:
    """WorkflowEngine이 이벤트를 올바른 순서로 발행한다.

    기대 순서: RUN_STARTED → (TOOL_CALL_START) → STATE_DELTA → RUN_FINISHED
    """
    store = EventStore(db_path=tmp_path / "test.db")
    fake = FakeLLM(
        [
            _stop_resp('{"name":"crm_get_customer","arguments":{"email":"a@b.com"}}'),
        ]
    )
    executor = NodeExecutor(llm_client=fake, event_store=store)
    node = Node(id="n1", kind="tool", prompt="고객 조회", thinking="off")
    engine = WorkflowEngine(executor=executor, event_store=store)

    engine.run([node], run_id="r-evt", workflow="test_wf")

    events = store.read("r-evt")
    event_types = [e.to_dict()["type"] for e in events]

    assert event_types[0] == "RUN_STARTED", (
        f"첫 이벤트가 RUN_STARTED여야 함: {event_types}"
    )
    assert event_types[-1] in ("RUN_FINISHED", "RUN_ERROR"), (
        f"마지막 이벤트가 RUN_FINISHED/RUN_ERROR여야 함: {event_types}"
    )
    # TOOL_CALL_START가 RUN_STARTED 이후에 등장
    if "TOOL_CALL_START" in event_types:
        assert event_types.index("TOOL_CALL_START") > event_types.index("RUN_STARTED")


def test_workflow_engine_emits_run_started_and_finished(tmp_path: Path) -> None:
    """단순 워크플로에서 RUN_STARTED·RUN_FINISHED가 발행된다."""
    store = EventStore(db_path=tmp_path / "test.db")
    fake = FakeLLM([_stop_resp('{"name":"fn","arguments":{}}')])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    node = Node(id="n1", kind="tool", prompt="p")
    engine = WorkflowEngine(executor=executor, event_store=store)
    engine.run([node], run_id="r-lifecycle", workflow="wf")

    events = store.read("r-lifecycle")
    types = [e.to_dict()["type"] for e in events]
    assert "RUN_STARTED" in types
    assert "RUN_FINISHED" in types


def test_workflow_engine_node_fail_emits_node_failed(tmp_path: Path) -> None:
    """노드 실패 시 NODE_FAILED 이벤트가 발행된다."""
    store = EventStore(db_path=tmp_path / "test.db")
    fake = FakeLLM([_length_resp()])  # length → 노드 실패
    executor = NodeExecutor(llm_client=fake, event_store=store)
    node = Node(id="n_fail", kind="tool", prompt="p")
    engine = WorkflowEngine(executor=executor, event_store=store)
    engine.run([node], run_id="r-fail", workflow="wf")

    events = store.read("r-fail")
    types = [e.to_dict()["type"] for e in events]
    assert "NODE_FAILED" in types, f"NODE_FAILED가 없음: {types}"


# ── 6. 정적 단언 — LLM 호출이 NodeExecutor 내부에만 (수용 기준 16) ─────────


def test_no_llm_call_outside_executor() -> None:
    """office/ 소스 전체에서 LLM .chat() 실제 호출이 engine.py NodeExecutor 외에 없다.

    수용 기준 16: 영구규칙 우회 불가 정적 단언.

    검사 방법:
    - office/ 내 모든 .py 파일을 읽어 실제 LLM chat 호출 패턴을 검색.
    - 실제 호출 패턴: 식별자(변수명/속성)._llm.chat( 또는 _llm.chat(
    - 주석(#)·docstring(따옴표로 시작하는 줄)은 무시.
    - engine.py에서 self._llm.chat( 는 NodeExecutor 내부 허용 호출.
    - 그 외 파일/위치의 _llm.chat( 호출은 금지.
    """
    proj_root = Path(__file__).parent.parent
    office_dir = proj_root / "office"

    # 실제 LLM chat 호출을 나타내는 패턴 — 식별자 뒤에 오는 .chat( (메서드 호출)
    # 문자열 리터럴이나 docstring 내 텍스트와 구분하기 위해
    # 실제 Python 표현식 패턴(식별자 직후 . 없이 .chat()만)을 사용
    # _llm.chat( 또는 llm.chat( 또는 client.chat( 형태
    _LLM_CALL_RE = re.compile(r"\b\w+\.chat\s*\(")

    violations: list[str] = []

    for py_file in sorted(office_dir.glob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        lines = source.splitlines()
        in_multiline_str = False

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()

            # 단순 주석 줄 무시
            if stripped.startswith("#"):
                continue
            # docstring/문자열 리터럴 시작·종료 추적 (3중 따옴표)
            triple_count = stripped.count('"""') + stripped.count("'''")
            if triple_count % 2 != 0:
                in_multiline_str = not in_multiline_str
                continue
            if in_multiline_str:
                continue
            # 줄이 문자열 상수처럼 시작하면 무시 (docstring 첫 줄)
            if stripped.startswith(('"""', "'''", '"', "'")):
                continue

            # 실제 .chat( 호출이 있는지 검사
            if not _LLM_CALL_RE.search(line):
                continue

            # engine.py의 self._llm.chat( 는 허용 (NodeExecutor 내부)
            if py_file.name == "engine.py" and "self._llm.chat(" in line:
                continue

            violations.append(f"{py_file.name}:{lineno}: {line.strip()}")

    assert not violations, (
        "office/ 소스에서 NodeExecutor 외부의 LLM .chat() 호출이 발견됐다:\n"
        + "\n".join(violations)
        + "\n\nLLM 호출은 office/engine.py의 NodeExecutor.execute() 내에서만 해야 한다."
    )


def test_engine_py_has_llm_call_inside_executor() -> None:
    """engine.py에 self._llm.chat( 호출이 실제로 존재한다 (정적 단언 역방향 확인)."""
    proj_root = Path(__file__).parent.parent
    engine_src = (proj_root / "office" / "engine.py").read_text(encoding="utf-8")
    assert "self._llm.chat(" in engine_src, (
        "engine.py에 self._llm.chat( 호출이 없다 — NodeExecutor 구현을 확인하라"
    )


# ── 8. 노드 순회 절대 상한 (H-04 — 사이클 워크플로 무한순회 방어) ──────────────


def test_cyclic_workflow_hits_step_limit(tmp_path: Path) -> None:
    """사이클 노드맵(A→B→A)이 절대 상한 1000에 도달해 step_limit으로 종료한다.

    ★mock LLM이라 1000스텝을 빠르게 돈다(timeout 가드 안). chat 노드라 게이팅·
    도구 미관여 → 순수 순회 루프만 검증. NODE_FAILED(escalation, detail에 step_limit)
    + RUN_FINISHED(failed)가 발행되고 _workflow_status="failed"여야 한다.
    """
    store = EventStore(db_path=tmp_path / "cycle.db")
    fake = FakeLLM([_stop_resp("응답")])  # chat 노드용 stop 응답 무한 반복
    executor = NodeExecutor(llm_client=fake, event_store=store)
    # A.next=B, B.next=A → 무한 순회(사이클).
    node_a = Node(id="a", kind="chat", prompt="pa", thinking="off", next="b")
    node_b = Node(id="b", kind="chat", prompt="pb", thinking="off", next="a")
    engine = WorkflowEngine(executor=executor, event_store=store)

    final = engine.run([node_a, node_b], run_id="r-cycle", workflow="cyc")

    assert final["_workflow_status"] == "failed"
    events = store.read("r-cycle")
    node_failed = [e.to_dict() for e in events if e.to_dict()["type"] == "NODE_FAILED"]
    assert node_failed, "step_limit 도달 시 NODE_FAILED가 발행돼야 함"
    assert "step_limit" in node_failed[-1]["detail"], (
        f"detail에 step_limit 명시 필요: {node_failed[-1]['detail']}"
    )
    assert node_failed[-1]["reason"] == "escalation"  # 기존 enum 재사용(스키마 무변)
    types = [e.to_dict()["type"] for e in events]
    assert types[-1] == "RUN_FINISHED"
    # mock LLM은 chat 노드당 1회 호출 → 상한 1000 부근에서 멈춰야 함(무한 아님).
    assert len(fake.calls) <= 1001, f"순회가 상한 근처에서 멈춰야 함: {len(fake.calls)}"


def test_linear_workflow_never_hits_step_limit(tmp_path: Path) -> None:
    """선형 DAG(현 워크플로 형태)는 절대 상한에 미도달한다(거짓 양성 0).

    2노드 선형(a→b→None)은 2스텝에 정상 종료 → step_limit NODE_FAILED 없음.
    """
    store = EventStore(db_path=tmp_path / "linear.db")
    fake = FakeLLM([_stop_resp("응답")])
    executor = NodeExecutor(llm_client=fake, event_store=store)
    node_a = Node(id="a", kind="chat", prompt="pa", thinking="off", next="b")
    node_b = Node(id="b", kind="chat", prompt="pb", thinking="off")  # next=None
    engine = WorkflowEngine(executor=executor, event_store=store)

    final = engine.run([node_a, node_b], run_id="r-linear", workflow="lin")

    assert final["_workflow_status"] == "success"
    events = store.read("r-linear")
    step_limit_fails = [
        e.to_dict()
        for e in events
        if e.to_dict()["type"] == "NODE_FAILED"
        and "step_limit" in e.to_dict().get("detail", "")
    ]
    assert not step_limit_fails, (
        "선형 워크플로는 step_limit에 도달하면 안 됨(거짓 양성)"
    )
