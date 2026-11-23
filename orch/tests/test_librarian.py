"""P2-rest US-B(선행) — LIBRARIAN dedup-merge + MOC 단위. 결정론·LLM 0·orch env(qdrant 불요)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orch.librarian import Artifact, dedup_merge, render_moc  # noqa: E402


def test_exact_dedup_merges_identical():
    arts = [
        Artifact("a", "코어 루프 설계"),
        Artifact("b", "코어  루프   설계"),  # 공백만 다름 → 정규화 동일 → 병합
        Artifact("c", "리텐션 곡선"),
    ]
    r = dedup_merge(arts)
    assert [k.id for k in r.kept] == ["a", "c"]
    assert r.moc["a"] == ["b"] and r.moc["c"] == []


def test_no_similarity_keeps_near_dups_separate():
    # similarity 미주입 → exact만. 유사하지만 다른 텍스트는 분리 유지(false-merge 방지).
    arts = [Artifact("a", "게임 리텐션 전략"), Artifact("b", "게임 리텐션 지표")]
    r = dedup_merge(arts)
    assert [k.id for k in r.kept] == ["a", "b"]


def test_near_dup_merges_above_threshold():
    # 주입 similarity가 ≥threshold면 병합.
    def sim(x, y):
        return 0.97 if ("리텐션" in x and "리텐션" in y) else 0.1

    arts = [Artifact("a", "게임 리텐션 전략"), Artifact("b", "게임 리텐션 지표")]
    r = dedup_merge(arts, similarity=sim, threshold=0.95)
    assert [k.id for k in r.kept] == ["a"]
    assert r.moc["a"] == ["b"]


def test_false_merge_negative_below_threshold():
    # 구별되는(유사도 낮은) 2개는 threshold 미만이면 그대로 2개(분리).
    def sim(x, y):
        return 0.3

    arts = [Artifact("a", "퍼즐 난이도 곡선"), Artifact("b", "F2P 수익 모델")]
    r = dedup_merge(arts, similarity=sim, threshold=0.95)
    assert [k.id for k in r.kept] == ["a", "b"]


def test_render_moc_links_absorbed():
    arts = [Artifact("vault/x.md", "동일"), Artifact("vault/y.md", "동일")]
    r = dedup_merge(arts)
    moc = render_moc(r, title="Knowledge MOC")
    assert "# Knowledge MOC" in moc
    assert "[[vault/x.md]]" in moc
    assert "중복 병합" in moc and "[[vault/y.md]]" in moc
