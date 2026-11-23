"""P2-rest-A US-A4 — eval c2_summary(§24.5 C2 분리집계) 단위. 순수함수·결정론.

C2 헤드라인(delivered post-rerank) + 진단(융합셋) + semantic 회귀 분류를 합성 per_query로 검증.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from knowledge.eval import c2_summary  # noqa: E402


def _rec(id_, cat, dense_rank, hybrid_fused_rank, hybrid_rerank_rank, rerank_rank=None):
    return {
        "id": id_,
        "category": cat,
        "dense_rank": dense_rank,
        "hybrid_fused_rank": hybrid_fused_rank,
        "hybrid_rerank_rank": hybrid_rerank_rank,
        "rerank_rank": rerank_rank,
    }


def test_headline_counts_dense_miss_recovered_post_rerank():
    # keyword 앵커: dense 단독 실패(None)였으나 hybrid rerank top-K(≤5)가 회복 → 헤드라인.
    pq = [
        _rec(
            "k1", "keyword", dense_rank=None, hybrid_fused_rank=2, hybrid_rerank_rank=1
        ),
        _rec(
            "k2", "keyword", dense_rank=None, hybrid_fused_rank=3, hybrid_rerank_rank=4
        ),
        _rec(
            "k3", "keyword", dense_rank=2, hybrid_fused_rank=1, hybrid_rerank_rank=1
        ),  # dense 성공→헤드라인 아님
    ]
    s = c2_summary(pq, max_k=5)
    assert set(s["headline_recovered"]) == {"k1", "k2"}
    assert set(s["diagnostic_recovered"]) == {"k1", "k2"}


def test_headline_subset_of_diagnostic_when_rerank_drops():
    # 융합셋엔 회복(진단)되나 rerank가 top-K 밖으로 떨굼 → 헤드라인 제외(green-theater 구멍 차단).
    pq = [
        _rec(
            "k1",
            "keyword",
            dense_rank=None,
            hybrid_fused_rank=4,
            hybrid_rerank_rank=None,
        ),
        _rec(
            "k2", "keyword", dense_rank=None, hybrid_fused_rank=2, hybrid_rerank_rank=9
        ),  # >max_k
    ]
    s = c2_summary(pq, max_k=5)
    assert s["headline_recovered"] == []  # rerank가 못 살림
    assert set(s["diagnostic_recovered"]) == {"k1", "k2"}  # 융합셋엔 있음


def test_semantic_regression_detected():
    # semantic 앵커: dense+rerank 성공인데 hybrid rerank에서 깨짐 → 회귀 카운트.
    pq = [
        _rec(
            "s1",
            "semantic",
            dense_rank=1,
            hybrid_fused_rank=1,
            hybrid_rerank_rank=1,
            rerank_rank=1,
        ),
        _rec(
            "s2",
            "semantic",
            dense_rank=2,
            hybrid_fused_rank=None,
            hybrid_rerank_rank=None,
            rerank_rank=2,
        ),
    ]
    s = c2_summary(pq, max_k=5)
    assert s["semantic_regression"] == 1  # s2만 회귀


def test_no_signal_returns_empty_headline():
    # 두 sparse 경로 무신호(dense 실패인데 hybrid도 회복 0) → 헤드라인 [] (정직 보고 트리거).
    pq = [
        _rec(
            "k1",
            "keyword",
            dense_rank=None,
            hybrid_fused_rank=None,
            hybrid_rerank_rank=None,
        ),
    ]
    s = c2_summary(pq, max_k=5)
    assert s["headline_recovered"] == []
    assert s["diagnostic_recovered"] == []
    assert "note" in s  # _SMALL_SAMPLE_NOTE 동반(통계주장 금지)
