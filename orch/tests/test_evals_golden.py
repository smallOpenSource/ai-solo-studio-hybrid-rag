"""orch/tests/test_evals_golden.py — vault/evals/golden.jsonl 스키마 검증.

P4a 골든셋 구조 단언:
- 각 줄 JSON 파싱 성공
- 필수키 존재(id·type·graph·input·expected·category·expects_tool)
- type ∈ {agent_task, routing, rag, injection}
- graph ∈ {cycle, org}
- id 유일
- 앵커 수 ≥ 50
- injection 앵커 ≥ 5
- 4 타입 전부 ≥ 1
- rag 앵커: expected에 answer_doc·answer_snippet 보유
- routing 앵커: expected.route ∈ {coo,dev_lead,escalate,end,build}
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_GOLDEN_PATH = Path(__file__).resolve().parents[2] / "vault" / "evals" / "golden.jsonl"

_VALID_TYPES = {"agent_task", "routing", "rag", "injection"}
_VALID_GRAPHS = {"cycle", "org"}
_VALID_ROUTES = {"coo", "dev_lead", "escalate", "end", "build"}
_REQUIRED_KEYS = {
    "id",
    "type",
    "graph",
    "input",
    "expected",
    "category",
    "expects_tool",
}

_MIN_ANCHORS = 50
_MIN_INJECTION = 5


# ── fixture: 파싱 한 번만 ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def anchors() -> list[dict]:
    assert _GOLDEN_PATH.exists(), f"golden.jsonl not found: {_GOLDEN_PATH}"
    rows: list[dict] = []
    for i, line in enumerate(_GOLDEN_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            pytest.fail(f"Line {i}: JSON parse error — {exc}")
    return rows


# ── 1. 필수키 존재 ────────────────────────────────────────────────────────────


def test_required_keys(anchors: list[dict]) -> None:
    for anchor in anchors:
        missing = _REQUIRED_KEYS - anchor.keys()
        assert not missing, f"Anchor id={anchor.get('id')!r} missing keys: {missing}"


# ── 2. type ∈ 4-enum ──────────────────────────────────────────────────────────


def test_type_enum(anchors: list[dict]) -> None:
    for anchor in anchors:
        assert anchor["type"] in _VALID_TYPES, (
            f"Anchor id={anchor['id']!r} has invalid type={anchor['type']!r}"
        )


# ── 3. graph ∈ {cycle, org} ───────────────────────────────────────────────────


def test_graph_enum(anchors: list[dict]) -> None:
    for anchor in anchors:
        assert anchor["graph"] in _VALID_GRAPHS, (
            f"Anchor id={anchor['id']!r} has invalid graph={anchor['graph']!r}"
        )


# ── 4. id 유일 ────────────────────────────────────────────────────────────────


def test_id_unique(anchors: list[dict]) -> None:
    ids = [a["id"] for a in anchors]
    seen: set[str] = set()
    dups = []
    for id_ in ids:
        if id_ in seen:
            dups.append(id_)
        seen.add(id_)
    assert not dups, f"Duplicate anchor ids: {dups}"


# ── 5. 앵커 수 ≥ 50 ───────────────────────────────────────────────────────────


def test_anchor_count(anchors: list[dict]) -> None:
    count = len(anchors)
    assert count >= _MIN_ANCHORS, (
        f"golden.jsonl has {count} anchors; need ≥ {_MIN_ANCHORS}"
    )


# ── 6. injection ≥ 5 ─────────────────────────────────────────────────────────


def test_injection_count(anchors: list[dict]) -> None:
    inj = [a for a in anchors if a["type"] == "injection"]
    assert len(inj) >= _MIN_INJECTION, (
        f"Only {len(inj)} injection anchors; need ≥ {_MIN_INJECTION}"
    )


# ── 7. 4 타입 전부 ≥ 1 ────────────────────────────────────────────────────────


def test_all_types_present(anchors: list[dict]) -> None:
    found = {a["type"] for a in anchors}
    missing = _VALID_TYPES - found
    assert not missing, f"Missing anchor types: {missing}"


# ── 8. rag 앵커: answer_doc / answer_snippet 보유 ────────────────────────────


def test_rag_anchors_have_answer_fields(anchors: list[dict]) -> None:
    for anchor in anchors:
        if anchor["type"] != "rag":
            continue
        exp = anchor.get("expected") or {}
        assert "answer_doc" in exp, (
            f"RAG anchor id={anchor['id']!r} missing expected.answer_doc"
        )
        assert "answer_snippet" in exp, (
            f"RAG anchor id={anchor['id']!r} missing expected.answer_snippet"
        )
        assert isinstance(exp["answer_doc"], str) and exp["answer_doc"].strip(), (
            f"RAG anchor id={anchor['id']!r} answer_doc is blank"
        )
        assert (
            isinstance(exp["answer_snippet"], str) and exp["answer_snippet"].strip()
        ), f"RAG anchor id={anchor['id']!r} answer_snippet is blank"


# ── 9. routing 앵커: expected.route ∈ 허용셋 ─────────────────────────────────


def test_routing_anchors_valid_route(anchors: list[dict]) -> None:
    for anchor in anchors:
        if anchor["type"] != "routing":
            continue
        exp = anchor.get("expected") or {}
        assert "route" in exp, (
            f"Routing anchor id={anchor['id']!r} missing expected.route"
        )
        assert exp["route"] in _VALID_ROUTES, (
            f"Routing anchor id={anchor['id']!r} has invalid route={exp['route']!r}; "
            f"allowed={_VALID_ROUTES}"
        )


# ── 10. injection 앵커: expected.must_not_contain 보유 ───────────────────────


def test_injection_anchors_have_must_not_contain(anchors: list[dict]) -> None:
    for anchor in anchors:
        if anchor["type"] != "injection":
            continue
        exp = anchor.get("expected") or {}
        assert "must_not_contain" in exp, (
            f"Injection anchor id={anchor['id']!r} missing expected.must_not_contain"
        )
        assert isinstance(exp["must_not_contain"], list) and exp["must_not_contain"], (
            f"Injection anchor id={anchor['id']!r} must_not_contain is empty"
        )


# ── 11. expects_tool: bool 타입 ───────────────────────────────────────────────


def test_expects_tool_is_bool(anchors: list[dict]) -> None:
    for anchor in anchors:
        assert isinstance(anchor["expects_tool"], bool), (
            f"Anchor id={anchor['id']!r} expects_tool must be bool, "
            f"got {type(anchor['expects_tool']).__name__}"
        )


# ── 12. agent_task 앵커: expected.completion 보유 ────────────────────────────


def test_agent_task_anchors_have_completion(anchors: list[dict]) -> None:
    for anchor in anchors:
        if anchor["type"] != "agent_task":
            continue
        exp = anchor.get("expected") or {}
        assert "completion" in exp, (
            f"agent_task anchor id={anchor['id']!r} missing expected.completion"
        )
        assert isinstance(exp["completion"], list) and exp["completion"], (
            f"agent_task anchor id={anchor['id']!r} completion is empty list"
        )


# ── 13. cycle routing: route ∈ {build, end} 전용 ────────────────────────────


def test_cycle_routing_valid_values(anchors: list[dict]) -> None:
    """cycle 그래프 routing 앵커의 route 값은 build|end 만 합법(org 값 혼용 방지)."""
    cycle_routes = {"build", "end"}
    for anchor in anchors:
        if anchor["type"] != "routing" or anchor["graph"] != "cycle":
            continue
        route = (anchor.get("expected") or {}).get("route", "")
        assert route in cycle_routes, (
            f"Cycle routing anchor id={anchor['id']!r} has route={route!r}; "
            f"cycle only allows {cycle_routes}"
        )


# ── 14. org routing: route ∈ {coo, dev_lead, escalate, end} 전용 ─────────────


def test_org_routing_valid_values(anchors: list[dict]) -> None:
    """org 그래프 routing 앵커의 route 값은 org 전용 값만 합법('build' 혼용 방지)."""
    org_routes = {"coo", "dev_lead", "escalate", "end"}
    for anchor in anchors:
        if anchor["type"] != "routing" or anchor["graph"] != "org":
            continue
        route = (anchor.get("expected") or {}).get("route", "")
        assert route in org_routes, (
            f"Org routing anchor id={anchor['id']!r} has route={route!r}; "
            f"org only allows {org_routes}"
        )


# ── 15. rag 앵커의 expects_tool = True ───────────────────────────────────────


def test_rag_anchors_expect_tool(anchors: list[dict]) -> None:
    """RAG 앵커는 knowledge 검색(도구 호출)이 필요 — expects_tool=True 단언."""
    for anchor in anchors:
        if anchor["type"] != "rag":
            continue
        assert anchor["expects_tool"] is True, (
            f"RAG anchor id={anchor['id']!r} should have expects_tool=True "
            f"(requires knowledge search tool call)"
        )


# ── 16. ★C-3 크로스파일 정합: golden rag 앵커 ↔ scorer(score_pass_at_k) ──────────
# 두 파일(golden.jsonl·scorers.py)이 정답 메타 위치(expected.answer_doc/snippet)에
# 합의함을 falsifiable 하게 잠근다. 회귀 이력: scorer 가 top-level 을 읽어 golden 의
# expected.* 를 못 봐 전 rag 앵커가 FAIL(green-theater 인접·단위 미검출)이었다.


def test_rag_anchor_scorable_by_pass_at_k(anchors: list[dict]) -> None:
    """모든 golden rag 앵커가 score_pass_at_k 로 채점 가능(정답 메타 expected 정합)."""
    from orch.evals.scorers import Verdict, score_pass_at_k

    rag = [a for a in anchors if a["type"] == "rag"]
    assert rag, "no rag anchors to cross-check"
    for anchor in rag:
        exp = anchor["expected"]
        doc, snip = exp.get("answer_doc", ""), exp.get("answer_snippet", "")
        assert doc or snip, f"rag {anchor['id']!r} expected has no answer meta"
        # 정답을 top-k 에 둔 synthetic 캡처 → scorer 가 expected 를 읽으면 PASS.
        hit_cap = {"search_results": [[snip or "x", doc or "wiki/x.md"]]}
        assert score_pass_at_k(anchor, hit_cap, k=3) is Verdict.PASS, (
            f"rag {anchor['id']!r} not scorable — scorer↔golden answer-meta "
            f"contract drift (expected.answer_doc/snippet)"
        )
        # 빈 결과 → 보장된 miss → FAIL(채점 신호 실재·vacuous 아님).
        assert score_pass_at_k(anchor, {"search_results": []}, k=3) is Verdict.FAIL


# ── 17. ★스키마 불변식: expects_tool=true → expected.tool 필수 (MAJOR 스키마 절반·MINOR-3) ──
# 회귀 이력: rag 앵커 17개가 expects_tool:true 인데 expected.tool 부재 → 게이트가
# score_tool_selection 을 적용(gate.py:90)하면 scorers.py:96 에서 무조건 FAIL →
# 완벽 캡처로도 실 golden 게이트 exit=1(MAJOR). 이 불변식을 P4a 스키마 게이트에서
# *시끄럽게* 강제해, 갭이 게이트가 아니라 골든 검증 시점에 드러나게 한다.


def test_expects_tool_requires_expected_tool(anchors: list[dict]) -> None:
    """expects_tool:true 앵커는 expected.tool 을 반드시 선언(tool_selection 채점 대상)."""
    for anchor in anchors:
        if not anchor.get("expects_tool"):
            continue
        tool = (anchor.get("expected") or {}).get("tool")
        assert tool, (
            f"anchor id={anchor['id']!r} 가 expects_tool:true 인데 expected.tool 부재 — "
            f"게이트가 score_tool_selection 에서 무조건 FAIL 시킨다(실 golden exit=1·MAJOR). "
            f"expected.tool(예 'knowledge_search')을 선언하라."
        )
