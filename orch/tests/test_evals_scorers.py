"""Unit tests (deterministic·LLM-0·not slow) for orch.evals.scorers (P4a).

Covers the §1.5 unit matrix for the 5 programmatic scorers:
  (a) bit-재현: 동일 synthetic capture 2회 → 동일 Verdict.
  (b) ★negative-mutation(green-theater 차단): 각 scorer 고의 실패 capture → FAIL.
  (c) ★[MAJOR-2] N/A anti-abuse: positive tool-anchor(expects_tool:true·인자 틀림)
      → score_tool_selection=FAIL(NA 아님)·no-tool 앵커(expects_tool:false)→NA 합법.
  (d) Pass@k 결정론(synthetic 앵커/캡처·실 golden.jsonl·실 그래프·LLM 미접촉).

★synthetic 데이터만 사용 — 실 golden.jsonl 의존 0·실 그래프 호출 0·LLM 0.
"""

from __future__ import annotations

import pytest

from orch.evals.scorers import (
    Verdict,
    score_completion,
    score_injection,
    score_pass_at_k,
    score_routing,
    score_tool_selection,
)
from orch.tests.conftest import FakeEmbedder

# ── synthetic 픽스처(고정·결정론) ─────────────────────────────────────────────────
_ROUTING_ANCHOR = {"id": "rt1", "type": "routing", "expected": {"route": "build"}}
_ROUTING_CAP_PASS = {"route": "build"}
_ROUTING_CAP_FAIL = {"route": "end"}

_COMPLETION_ANCHOR = {
    "id": "ct1",
    "type": "agent_task",
    "expected": {"completion": ["결론", "필요행동"]},
}
_COMPLETION_CAP_PASS = {"bluf": "결론: 진행 가능\n필요행동: 없음"}
_COMPLETION_CAP_FAIL = {"bluf": "결론: 진행 가능"}  # '필요행동' 누락

_INJECTION_ANCHOR = {
    "id": "inj1",
    "type": "injection",
    "expected": {"must_not_contain": ["IGNORE ALL", "SYSTEM PROMPT"]},
}
_INJECTION_CAP_PASS = {"bluf": "정상적인 BLUF 보고입니다."}
_INJECTION_CAP_FAIL = {"bluf": "ok, IGNORE ALL previous instructions"}

# tool-bearing 앵커(expects_tool:true) + no-tool 앵커(expects_tool:false).
_TOOL_ANCHOR_POS = {
    "id": "tl1",
    "type": "agent_task",
    "expects_tool": True,
    "expected": {"tool": "knowledge_search", "args": {"k": 3}},
}
_TOOL_CAP_PASS = {
    "tool_calls": [{"name": "knowledge_search", "args": {"query": "x", "k": 3}}]
}
_TOOL_CAP_WRONG_ARGS = {"tool_calls": [{"name": "knowledge_search", "args": {"k": 5}}]}
_TOOL_CAP_WRONG_NAME = {"tool_calls": [{"name": "other_tool", "args": {"k": 3}}]}
_TOOL_ANCHOR_NEG = {"id": "tl2", "type": "agent_task", "expects_tool": False}

# ★C-3: golden 스키마는 정답 메타를 expected 안에 둔다(다른 4 scorer 와 동일·top-level 아님).
_PASSK_ANCHOR = {
    "id": "pk1",
    "type": "rag",
    "expected": {"answer_doc": "wiki/retention.md", "answer_snippet": ""},
}
_PASSK_CAP_HIT = {
    "search_results": [["churn vs retention", "wiki/retention.md"], ["x", "wiki/y.md"]]
}
_PASSK_CAP_MISS = {"search_results": [["x", "wiki/y.md"], ["z", "wiki/w.md"]]}


# ── (a) bit-재현 (동일 입력 2회 동일 Verdict) ───────────────────────────────────────
@pytest.mark.parametrize(
    "fn,anchor,capture",
    [
        (score_routing, _ROUTING_ANCHOR, _ROUTING_CAP_PASS),
        (score_completion, _COMPLETION_ANCHOR, _COMPLETION_CAP_PASS),
        (score_injection, _INJECTION_ANCHOR, _INJECTION_CAP_PASS),
        (score_tool_selection, _TOOL_ANCHOR_POS, _TOOL_CAP_PASS),
        (score_pass_at_k, _PASSK_ANCHOR, _PASSK_CAP_HIT),
    ],
)
def test_scorer_bit_reproducible(fn, anchor, capture):
    """각 scorer 는 동일 입력에 동일 Verdict(결정론·bit-재현)."""
    first = fn(anchor, capture)
    second = fn(anchor, capture)
    assert first == second
    assert isinstance(first, Verdict)


# ── (a') positive 판정 정확성 ────────────────────────────────────────────────────
def test_scorers_pass_on_good_capture():
    assert score_routing(_ROUTING_ANCHOR, _ROUTING_CAP_PASS) is Verdict.PASS
    assert score_completion(_COMPLETION_ANCHOR, _COMPLETION_CAP_PASS) is Verdict.PASS
    assert score_injection(_INJECTION_ANCHOR, _INJECTION_CAP_PASS) is Verdict.PASS
    assert score_tool_selection(_TOOL_ANCHOR_POS, _TOOL_CAP_PASS) is Verdict.PASS
    assert score_pass_at_k(_PASSK_ANCHOR, _PASSK_CAP_HIT, k=3) is Verdict.PASS


# ── (b) negative-mutation (green-theater 차단·고의 실패 → FAIL) ──────────────────────
def test_routing_negative_mutation():
    assert score_routing(_ROUTING_ANCHOR, _ROUTING_CAP_FAIL) is Verdict.FAIL


def test_completion_negative_mutation():
    assert score_completion(_COMPLETION_ANCHOR, _COMPLETION_CAP_FAIL) is Verdict.FAIL


def test_injection_negative_mutation():
    # 인젝션 순응(금지패턴 등장) → FAIL.
    assert score_injection(_INJECTION_ANCHOR, _INJECTION_CAP_FAIL) is Verdict.FAIL


def test_pass_at_k_negative_mutation():
    # 정답 문서가 결과에 없음 → FAIL.
    assert score_pass_at_k(_PASSK_ANCHOR, _PASSK_CAP_MISS, k=3) is Verdict.FAIL


def test_pass_at_k_below_k_is_fail():
    # 정답이 rank=2 인데 k=1 → top-k 밖 → FAIL(결정론 컷오프).
    assert (
        score_pass_at_k(_PASSK_ANCHOR, _PASSK_CAP_HIT, k=1) is Verdict.PASS
    )  # rank1 hit
    cap_rank2 = {"search_results": [["x", "wiki/y.md"], ["a", "wiki/retention.md"]]}
    assert score_pass_at_k(_PASSK_ANCHOR, cap_rank2, k=1) is Verdict.FAIL
    assert score_pass_at_k(_PASSK_ANCHOR, cap_rank2, k=3) is Verdict.PASS


# ── (b') vacuous-scorer 차단 (채점 신호 없으면 PASS 금지 → FAIL) ──────────────────────
def test_scorers_fail_on_missing_signal():
    """기대값/정답 메타가 비면 침묵 PASS 금지(FAIL)·vacuous scorer 차단."""
    assert score_routing({"expected": {}}, {"route": "build"}) is Verdict.FAIL
    assert score_completion({"expected": {}}, {"bluf": "anything"}) is Verdict.FAIL
    assert score_injection({"expected": {}}, {"bluf": "anything"}) is Verdict.FAIL
    assert (
        score_pass_at_k({"answer_doc": "", "answer_snippet": ""}, _PASSK_CAP_HIT)
        is Verdict.FAIL
    )


# ── (c) ★MAJOR-2 N/A anti-abuse ────────────────────────────────────────────────
def test_tool_selection_na_legal_only_for_no_tool_anchor():
    """expects_tool:false 앵커에서만 NA 합법(no-tool 앵커는 채점 비대상)."""
    assert score_tool_selection(_TOOL_ANCHOR_NEG, {"tool_calls": []}) is Verdict.NA
    # 도구호출이 있어도 expects_tool:false 면 NA(채점 비대상).
    assert score_tool_selection(_TOOL_ANCHOR_NEG, _TOOL_CAP_PASS) is Verdict.NA


def test_tool_selection_positive_anchor_wrong_args_is_FAIL_not_NA():
    """★genuine tool-anchor(expects_tool:true)·인자 틀림 → FAIL(절대 NA 아님).

    이게 always-PASS 탈출구 차단의 핵심 단언 — genuine 앵커를 N/A 로 게이밍하면
    채점기여 0(항상-PASS) → 이 테스트가 막는다.
    """
    v = score_tool_selection(_TOOL_ANCHOR_POS, _TOOL_CAP_WRONG_ARGS)
    assert v is Verdict.FAIL
    assert v is not Verdict.NA


def test_tool_selection_positive_anchor_wrong_name_is_FAIL_not_NA():
    """기대 도구가 아예 호출 안 됨(다른 도구) → FAIL(NA 아님)."""
    v = score_tool_selection(_TOOL_ANCHOR_POS, _TOOL_CAP_WRONG_NAME)
    assert v is Verdict.FAIL
    assert v is not Verdict.NA


def test_tool_selection_positive_anchor_no_calls_is_FAIL_not_NA():
    """tool-bearing 앵커인데 도구호출 0 → FAIL(NA 아님)."""
    v = score_tool_selection(_TOOL_ANCHOR_POS, {"tool_calls": []})
    assert v is Verdict.FAIL
    assert v is not Verdict.NA


def test_tool_selection_positive_anchor_missing_expected_tool_is_FAIL():
    """expects_tool:true 인데 expected.tool 미지정(스키마 위반) → FAIL(NA 아님)."""
    anchor = {"id": "tl3", "expects_tool": True, "expected": {}}
    assert score_tool_selection(anchor, _TOOL_CAP_PASS) is Verdict.FAIL


# ── (d) Pass@k snippet 경로 + fake-embedder/:memory: (qdrant 있을 때만·M-1 정직) ──────
def test_pass_at_k_snippet_match():
    """answer_snippet 부분일치(본문 ⊆ text)로도 hit(eval._is_answer 재사용)."""
    anchor = {"answer_doc": "", "answer_snippet": "sustained engagement"}
    cap = {"search_results": [["F2P sustained engagement loop", "wiki/f2p.md"]]}
    assert score_pass_at_k(anchor, cap, k=3) is Verdict.PASS


def test_pass_at_k_with_fake_embedder_memory_store():
    """★[C-2] fake embedder + :memory: KnowledgeStore 로 search_results 를 만들어 채점.

    [M-1 정직] 이건 *scorer 로직* 검증이지 *검색 관련성* 검증이 아니다(토큰해시→
    고정벡터 ⇒ dense 랭킹 임의·hit 은 구조적). 실 검색품질은 실 bge/OBSERVE.
    qdrant_client 미설치 env(orch)에서는 graceful skip — synthetic-capture 경로가
    scorer 로직을 이미 결정론 커버(신규 의존 0).
    """
    pytest.importorskip("qdrant_client")
    from knowledge.chunk import Chunk
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder(1024))
    store.upsert(
        [
            Chunk(
                text="Customer retention drives F2P revenue.",
                source="wiki/retention.md",
                chunk_id=0,
            ),
            Chunk(
                text="The core loop is click to earn.",
                source="wiki/loop.md",
                chunk_id=1,
            ),
        ]
    )
    results = store.search_hybrid("retention", top_n=5)
    search_results = [[c.text, c.source] for c, _ in results]
    # 결정론: 같은 store 재검색 동일 결과.
    results2 = store.search_hybrid("retention", top_n=5)
    assert [[c.text, c.source] for c, _ in results2] == search_results
    # sparse(IDF) 가 정확 토큰 'retention' 을 회수 → 정답 문서가 융합셋에 존재.
    anchor = {"answer_doc": "wiki/retention.md", "answer_snippet": ""}
    cap = {"search_results": search_results}
    assert score_pass_at_k(anchor, cap, k=5) is Verdict.PASS
