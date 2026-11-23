"""office/nodes.py 단위 테스트 — US-004 T4a.

테스트 범위 (계획 §5 Unit, 수용 기준 4):
1. Node dataclass 기본 필드 검증
2. MAX_TOKENS_BY_KIND — 전 값 ≤ 16384 (32k 미사용 단언)
3. resolve_max_tokens — 종류별 기본값 + 오버라이드
4. kind="agent" → NotImplementedError
5. MAX_TOKENS_BY_KIND 거처가 office/nodes.py 단일 위치임을 단언
"""

from __future__ import annotations

from pathlib import Path

from office.nodes import MAX_TOKENS_BY_KIND, Node

# ── 1. Node dataclass 기본 필드 ─────────────────────────────────────────────


def test_node_defaults() -> None:
    """Node dataclass의 기본값이 계획 §4.4.5와 일치한다."""
    node = Node(id="n1", kind="tool")
    assert node.id == "n1"
    assert node.kind == "tool"
    assert node.prompt is None
    assert node.prompt_file is None
    assert node.model is None
    assert node.thinking == "off"
    assert node.tools == []
    assert node.next is None
    assert node.expected is None
    assert node.max_tokens is None


def test_node_fields_assignable() -> None:
    """Node 필드가 지정 값으로 올바르게 설정된다."""
    node = Node(
        id="crm_node",
        kind="chat",
        prompt="요약하라",
        model="gemma-4-e4b-it",
        thinking="on",
        tools=[{"function": {"name": "fn"}}],
        next="next_node",
        expected={"name": "fn", "args": {}},
        max_tokens=2048,
    )
    assert node.kind == "chat"
    assert node.thinking == "on"
    assert node.model == "gemma-4-e4b-it"
    assert node.next == "next_node"
    assert node.max_tokens == 2048


# ── 2. MAX_TOKENS_BY_KIND — 32k 미사용 단언 ────────────────────────────────


def test_max_tokens_by_kind_all_keys_present() -> None:
    """MAX_TOKENS_BY_KIND에 tool/chat/longform/coding 4종이 있다."""
    assert "tool" in MAX_TOKENS_BY_KIND
    assert "chat" in MAX_TOKENS_BY_KIND
    assert "longform" in MAX_TOKENS_BY_KIND
    assert "coding" in MAX_TOKENS_BY_KIND


def test_max_tokens_by_kind_no_32k() -> None:
    """MAX_TOKENS_BY_KIND의 모든 값이 16384 이하다 (32k 금지 — 수용 기준 4)."""
    for kind, tokens in MAX_TOKENS_BY_KIND.items():
        assert tokens <= 16384, (
            f"kind={kind!r}의 max_tokens={tokens}가 16384를 초과한다. 32k 금지."
        )


def test_max_tokens_by_kind_values() -> None:
    """MAX_TOKENS_BY_KIND의 구체 값이 계획 §4.6과 일치한다."""
    assert MAX_TOKENS_BY_KIND["tool"] == 512
    assert MAX_TOKENS_BY_KIND["chat"] == 4096
    assert MAX_TOKENS_BY_KIND["longform"] == 8192
    assert MAX_TOKENS_BY_KIND["coding"] == 16384


# ── 3. resolve_max_tokens ────────────────────────────────────────────────────


def test_resolve_max_tokens_uses_kind_default() -> None:
    """max_tokens 오버라이드 없으면 MAX_TOKENS_BY_KIND[kind]를 반환한다."""
    for kind in ("tool", "chat", "longform", "coding"):
        node = Node(id="n", kind=kind)  # type: ignore[arg-type]
        assert node.resolve_max_tokens() == MAX_TOKENS_BY_KIND[kind]


def test_resolve_max_tokens_override() -> None:
    """max_tokens 오버라이드가 있으면 그 값을 사용한다."""
    node = Node(id="n", kind="tool", max_tokens=256)
    assert node.resolve_max_tokens() == 256


def test_resolve_max_tokens_override_beats_default() -> None:
    """오버라이드가 기본값보다 작아도 그대로 반환한다."""
    node = Node(id="n", kind="chat", max_tokens=128)
    assert node.resolve_max_tokens() == 128


# ── 4. kind="agent" → 8192 (Phase 6 6b 지원) ───────────────────────────────


def test_agent_kind_resolve_returns_8192() -> None:
    """kind='agent' 노드 resolve_max_tokens는 8192를 반환한다(Phase 6 6b·flip).

    ★의도적 flip(REG-flip·N-5): 기존 NotImplementedError 단언을 동작 단언으로 교체.
    MAX_TOKENS_BY_KIND['agent']=8192(≤16384·32k 금지)가 단일 거처다.
    """
    node = Node(id="n", kind="agent")
    assert node.resolve_max_tokens() == 8192


def test_agent_kind_resolve_override_beats_default() -> None:
    """agent 노드도 max_tokens 오버라이드가 있으면 그 값을 우선한다."""
    node = Node(id="n", kind="agent", max_tokens=2048)
    assert node.resolve_max_tokens() == 2048


# ── 5. MAX_TOKENS_BY_KIND 거처 단일 위치 단언 ─────────────────────────────


def test_max_tokens_by_kind_defined_only_in_nodes_py() -> None:
    """MAX_TOKENS_BY_KIND는 office/nodes.py에만 정의되고 models.yaml에는 없다.

    수용 기준 4: max_tokens 거처가 nodes.py/노드 정의이고 models.yaml 아님.
    """
    proj_root = Path(__file__).parent.parent

    # nodes.py에 MAX_TOKENS_BY_KIND 정의가 있다
    nodes_src = (proj_root / "office" / "nodes.py").read_text(encoding="utf-8")
    assert "MAX_TOKENS_BY_KIND" in nodes_src, "nodes.py에 MAX_TOKENS_BY_KIND가 없다"

    # models.yaml에 노드 종류별 max_tokens 매핑이 없다
    models_yaml = (proj_root / "config" / "models.yaml").read_text(encoding="utf-8")
    # models.yaml은 모델 alias별이므로 tool/chat/longform 같은 노드 종류 키가 없어야 함
    for kind in ("tool:", "chat:", "longform:", "coding:"):
        assert kind not in models_yaml, (
            f"models.yaml에 노드 종류 키 {kind!r}가 있다 — "
            "노드별 max_tokens 거처는 nodes.py여야 한다"
        )
