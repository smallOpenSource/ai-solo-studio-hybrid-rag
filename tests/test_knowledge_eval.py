"""US-005 K6 — 한국어 검색 평가 하니스(분리집계) 단위 테스트.

오프라인 전용 (고정 소형 코퍼스 + mock store/reranker). 실서버 접촉 0 → slow 아님.

검증(계획 산출물 3 — 분리집계가 측정의 핵심이라 정확히):
- Hit@k(k=1,3,5)·MRR 계산 정확(dense·리랭커 두 구성).
- 두 구성 비교표가 산출된다(dense vs 리랭커).
- ★실패 분리집계 정확 — dense 회수실패(top_n에도 없음) vs 리랭크실패(top_n엔
  있으나 리랭크 후 top-k 밖)를 각각 유발하는 케이스로 구분 단언.
- 카테고리별(semantic/keyword) 집계 — 키워드 회수실패 트리거.
- 리랭커 top-k 순위변경 관측 케이스(합격선).
- 재채점(rescore) — 보존 raw.jsonl로 동일 결과 재생성.
- 리포트에 "소표본·통계 유의 주장 금지" 명기.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from knowledge.chunk import Chunk
from knowledge.eval import (
    _RERANK_FAIL,
    _RETRIEVAL_FAIL,
    _SUCCESS,
    evaluate,
    generate_report,
    write_report,
)

# ---------------------------------------------------------------------------
# 고정 소형 코퍼스 — 결정론. source/text로 정답 매칭을 흉내낸다.
# ---------------------------------------------------------------------------

# 코퍼스 청크: (text, source). 정답 매칭은 answer_doc(source 부분일치) 또는
# answer_snippet(text 부분일치)로 한다(_is_answer).
_DOC_A = ("사과 바나나 포도 과일 가게 신선 채소", "wiki/fruit.md")
_DOC_B = ("자동차 엔진 바퀴 정비소 수리 부품", "wiki/car.md")
_DOC_C = ("고양이 강아지 토끼 동물 병원 사료", "wiki/animal.md")
_DOC_D = ("파이썬 함수 변수 클래스 코드 디버그", "wiki/code.md")


class MockStore:
    """질의→고정 dense 후보 목록을 돌려주는 mock store(search_dense 계약).

    query_to_candidates[query] = [(text, source), ...] 를 *준 순서가 곧 dense
    순위*가 되게(앞이 1등) 내림차순 점수로 돌려준다. 평가셋의 정답이 이 후보
    안에 있/없게 구성해 dense 회수실패 vs 리랭크실패를 결정론으로 만든다.
    """

    def __init__(self, query_to_candidates: dict[str, list[tuple[str, str]]]) -> None:
        self.query_to_candidates = query_to_candidates
        self.calls: list[tuple[str, int]] = []

    def search_dense(self, query: str, top_n: int = 20) -> list[tuple[Chunk, float]]:
        self.calls.append((query, top_n))
        rows = self.query_to_candidates.get(query, [])[:top_n]
        # 준 순서가 dense 순위 — 위치로 내림차순 점수를 매긴다(앞=높음).
        return [
            (Chunk(text=t, source=s, chunk_id=i), float(len(rows) - i))
            for i, (t, s) in enumerate(rows)
        ]


class MockReranker:
    """text→리랭커 점수를 돌려주는 mock(rerank 계약, dict 형식).

    score_by_text에 없는 문서는 0.0점. 일부러 dense 순서와 다르게 점수를 매겨
    리랭커가 순위를 바꾸는지(rank_changed)·top-k 밖으로 미는지(리랭크실패)를
    결정론으로 만든다.
    """

    def __init__(self, score_by_text: dict[str, float]) -> None:
        self.score_by_text = score_by_text
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> dict[str, Any]:
        self.calls.append((query, list(documents)))
        results = [
            {"index": i, "score": self.score_by_text.get(doc, 0.0)}
            for i, doc in enumerate(documents)
        ]
        return {"results": results}


def _write_eval_jsonl(tmp_path: Path, items: list[dict[str, Any]]) -> Path:
    """평가셋 JSONL을 임시 경로에 쓴다."""
    path = tmp_path / "eval.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return path


# ===========================================================================
# 실패 분리집계 — 핵심. dense 회수실패 vs 리랭크실패 vs 성공 각각.
# ===========================================================================


class TestFailureBreakdown:
    """dense 회수실패 / 리랭크실패 / 성공을 각각 유발하는 케이스로 구분 단언."""

    def test_dense_retrieval_fail_when_answer_not_in_top_n(
        self, tmp_path: Path
    ) -> None:
        """정답이 dense top_n 후보에 아예 없으면 dense 회수실패로 분류된다."""
        # 질의 후보에 정답 문서(wiki/fruit.md)가 없다 → 회수실패.
        store = MockStore({"과일 질의": [_DOC_B, _DOC_C]})  # fruit 없음
        reranker = MockReranker({_DOC_B[0]: 0.9, _DOC_C[0]: 0.5})
        items = [
            {
                "id": "q1",
                "query": "과일 질의",
                "answer_doc": "wiki/fruit.md",
                "answer_snippet": "",
                "category": "keyword",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        assert result["per_query"][0]["classification"] == _RETRIEVAL_FAIL
        assert result["failure_breakdown"][_RETRIEVAL_FAIL] == 1
        # 회수실패면 dense_rank·rerank_rank 모두 None.
        assert result["per_query"][0]["dense_rank"] is None
        assert result["per_query"][0]["rerank_rank"] is None

    def test_rerank_fail_when_in_top_n_but_pushed_out_of_top_k(
        self, tmp_path: Path
    ) -> None:
        """정답이 dense top_n엔 있으나 리랭커가 top-k 밖으로 밀면 리랭크실패."""
        # 정답(fruit)이 dense 후보엔 있지만(회수 OK), 리랭커가 최저점을 줘서
        # 6개 후보 중 꼴찌로 민다 → top-k(max k=5) 밖 → 리랭크실패.
        extra = [(f"채움문서{i} 본문 내용", f"wiki/pad{i}.md") for i in range(5)]
        # 준 순서가 dense 순위 — 정답(fruit)을 dense 1등 자리에 둔다.
        candidates = [(_DOC_A[0], _DOC_A[1])] + extra
        store = MockStore({"과일 질의": candidates})
        # 리랭커: 정답(fruit)에 최저점, 채움문서들에 높은 점수 → 정답 꼴찌.
        scores = {_DOC_A[0]: -10.0}
        for i, (t, _s) in enumerate(extra):
            scores[t] = 5.0 - i
        reranker = MockReranker(scores)
        items = [
            {
                "id": "q2",
                "query": "과일 질의",
                "answer_doc": "wiki/fruit.md",
                "answer_snippet": "",
                "category": "semantic",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        row = result["per_query"][0]
        assert row["classification"] == _RERANK_FAIL
        assert row["dense_rank"] == 1  # dense는 1등으로 회수
        assert row["rerank_rank"] == 6  # 리랭커가 꼴찌(6등)로 밈 → top-5 밖
        assert result["failure_breakdown"][_RERANK_FAIL] == 1

    def test_success_when_answer_in_rerank_top_k(self, tmp_path: Path) -> None:
        """정답이 리랭커 top-k 안에 들면 성공으로 분류된다."""
        store = MockStore({"과일 질의": [_DOC_B, _DOC_A, _DOC_C]})
        # 리랭커가 정답(fruit)을 1등으로 끌어올린다 → 성공.
        reranker = MockReranker({_DOC_A[0]: 0.99, _DOC_B[0]: 0.2, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "q3",
                "query": "과일 질의",
                "answer_doc": "wiki/fruit.md",
                "answer_snippet": "",
                "category": "keyword",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        row = result["per_query"][0]
        assert row["classification"] == _SUCCESS
        assert row["rerank_rank"] == 1
        assert result["failure_breakdown"][_SUCCESS] == 1

    def test_breakdown_counts_sum_to_n(self, tmp_path: Path) -> None:
        """세 분류 건수 합 = 전체 질의 수(빠짐·중복 없음)."""
        store = MockStore(
            {
                "회수실패": [_DOC_B, _DOC_C],  # 정답 fruit 없음 → 회수실패
                "성공질의": [_DOC_A, _DOC_B],  # 정답 fruit 있음, 리랭커 1등 → 성공
            }
        )
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.5, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "f",
                "query": "회수실패",
                "answer_doc": "wiki/fruit.md",
                "category": "keyword",
            },
            {
                "id": "s",
                "query": "성공질의",
                "answer_doc": "wiki/fruit.md",
                "category": "semantic",
            },
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        fb = result["failure_breakdown"]
        assert fb[_RETRIEVAL_FAIL] + fb[_RERANK_FAIL] + fb[_SUCCESS] == result["n"]
        assert fb[_RETRIEVAL_FAIL] == 1
        assert fb[_SUCCESS] == 1

    def test_answer_matched_by_snippet_substring(self, tmp_path: Path) -> None:
        """answer_snippet 부분일치로도 정답을 인정한다(answer_doc 없이)."""
        store = MockStore({"스니펫 질의": [_DOC_A, _DOC_B]})
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.1})
        items = [
            {
                "id": "snip",
                "query": "스니펫 질의",
                "answer_doc": "",
                "answer_snippet": "포도 과일",  # _DOC_A text의 부분
                "category": "semantic",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        assert result["per_query"][0]["classification"] == _SUCCESS


# ===========================================================================
# Hit@k · MRR 계산 정확
# ===========================================================================


class TestMetrics:
    """두 구성(dense·rerank)의 Hit@k·MRR 정확."""

    def test_hit_at_k_and_mrr_for_rank_two(self, tmp_path: Path) -> None:
        """정답이 리랭커 2등이면 Hit@1=0%·Hit@3=100%·MRR=0.5."""
        store = MockStore({"질의": [_DOC_A, _DOC_B, _DOC_C]})
        # 리랭커: 정답(fruit)을 2등으로(car가 1등).
        reranker = MockReranker({_DOC_B[0]: 0.9, _DOC_A[0]: 0.8, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "q",
                "query": "질의",
                "answer_doc": "wiki/fruit.md",
                "category": "semantic",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, k_values=[1, 3, 5], top_n=20, reranker=reranker)
        m = result["metrics"]["rerank"]
        assert m["hit@k"][1]["count"] == 0  # 1등 아님
        assert m["hit@k"][3]["count"] == 1  # 3 안에 있음
        assert m["hit@k"][5]["count"] == 1
        assert m["mrr"] == pytest.approx(0.5)  # 2등 → 1/2

    def test_dense_vs_rerank_metrics_differ(self, tmp_path: Path) -> None:
        """dense와 리랭커 지표가 별도로 집계된다(비교표 데이터)."""
        store = MockStore({"질의": [_DOC_B, _DOC_A]})  # dense: car 1등, fruit 2등
        # 리랭커가 fruit를 1등으로 끌어올림 → rerank Hit@1=100%, dense Hit@1=0%.
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.1})
        items = [
            {
                "id": "q",
                "query": "질의",
                "answer_doc": "wiki/fruit.md",
                "category": "semantic",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        assert result["metrics"]["dense"]["hit@k"][1]["count"] == 0
        assert result["metrics"]["rerank"]["hit@k"][1]["count"] == 1
        assert result["metrics"]["dense"]["mrr"] == pytest.approx(0.5)  # dense 2등
        assert result["metrics"]["rerank"]["mrr"] == pytest.approx(1.0)  # 리랭커 1등

    def test_mrr_zero_when_no_hit(self, tmp_path: Path) -> None:
        """정답이 후보에 없으면 그 질의의 역순위 기여는 0(MRR 낮춤)."""
        store = MockStore({"질의": [_DOC_B, _DOC_C]})  # 정답 없음
        reranker = MockReranker({_DOC_B[0]: 0.9, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "q",
                "query": "질의",
                "answer_doc": "wiki/fruit.md",
                "category": "keyword",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        assert result["metrics"]["dense"]["mrr"] == 0.0
        assert result["metrics"]["rerank"]["mrr"] == 0.0


# ===========================================================================
# 카테고리별 분리집계 (키워드 회수실패 = sparse 트리거)
# ===========================================================================


class TestByCategory:
    """semantic/keyword 카테고리별 회수실패·성공 분리집계."""

    def test_keyword_retrieval_fail_isolated_by_category(self, tmp_path: Path) -> None:
        """키워드 질의의 dense 회수실패가 카테고리 집계로 분리된다."""
        store = MockStore(
            {
                "키워드질의": [_DOC_B, _DOC_C],  # 정답 fruit 없음 → 키워드 회수실패
                "의미질의": [_DOC_A, _DOC_B],  # 정답 fruit 있음 → 의미 성공
            }
        )
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.5, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "kw",
                "query": "키워드질의",
                "answer_doc": "wiki/fruit.md",
                "category": "keyword",
            },
            {
                "id": "sem",
                "query": "의미질의",
                "answer_doc": "wiki/fruit.md",
                "category": "semantic",
            },
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        by_cat = result["by_category"]
        assert by_cat["keyword"][_RETRIEVAL_FAIL] == 1
        assert by_cat["keyword"][_SUCCESS] == 0
        assert by_cat["semantic"][_SUCCESS] == 1
        assert by_cat["semantic"][_RETRIEVAL_FAIL] == 0


# ===========================================================================
# 리랭커 top-k 순위변경 관측 (합격선)
# ===========================================================================


class TestRankChangeObservation:
    """리랭커가 정답 top-k 순위를 바꾸는 사례를 관측·기록한다."""

    def test_rank_changed_example_recorded(self, tmp_path: Path) -> None:
        """리랭커가 dense 순위를 바꾸면 rank_changed 사례로 기록된다."""
        store = MockStore({"질의": [_DOC_B, _DOC_A, _DOC_C]})  # dense: fruit 2등
        # 리랭커가 fruit를 1등으로 → 순위 2→1 변경.
        reranker = MockReranker({_DOC_A[0]: 0.99, _DOC_B[0]: 0.5, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "rc",
                "query": "질의",
                "answer_doc": "wiki/fruit.md",
                "category": "semantic",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        examples = result["rank_changed_examples"]
        assert len(examples) == 1
        assert examples[0]["id"] == "rc"
        assert examples[0]["dense_rank"] == 2
        assert examples[0]["rerank_rank"] == 1

    def test_no_rank_change_when_orders_match(self, tmp_path: Path) -> None:
        """dense와 리랭커 순위가 같으면 순위변경 사례가 없다."""
        store = MockStore({"질의": [_DOC_A, _DOC_B]})  # dense: fruit 1등
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.1})  # 리랭커도 1등
        items = [
            {
                "id": "same",
                "query": "질의",
                "answer_doc": "wiki/fruit.md",
                "category": "keyword",
            }
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        assert result["rank_changed_examples"] == []


# ===========================================================================
# 리포트 생성 + 재채점(rescore)
# ===========================================================================


class TestReportAndRescore:
    """비교표·분리집계 리포트 + 원본 보존·재채점."""

    def _two_query_result(self, tmp_path: Path) -> tuple[Any, Path]:
        store = MockStore(
            {
                "회수실패": [_DOC_B, _DOC_C],
                "성공질의": [_DOC_A, _DOC_B],
            }
        )
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.5, _DOC_C[0]: 0.1})
        items = [
            {
                "id": "f",
                "query": "회수실패",
                "answer_doc": "wiki/fruit.md",
                "category": "keyword",
            },
            {
                "id": "s",
                "query": "성공질의",
                "answer_doc": "wiki/fruit.md",
                "category": "semantic",
            },
        ]
        path = _write_eval_jsonl(tmp_path, items)
        result = evaluate(path, store, top_n=20, reranker=reranker)
        return result, path

    def test_report_has_comparison_table_and_breakdown(self, tmp_path: Path) -> None:
        """리포트에 비교표·분리집계·소표본 경고가 들어간다."""
        result, _ = self._two_query_result(tmp_path)
        report = generate_report(result)
        # 비교표(dense vs 리랭커)
        assert "dense-only" in report
        assert "dense+리랭커" in report
        assert "Hit@1" in report and "MRR" in report
        # 실패 분리집계
        assert "dense 회수실패" in report
        assert "리랭크실패" in report
        # sparse 트리거 언급
        assert "sparse" in report
        # 소표본·통계 유의 주장 금지 명기(계획 수용6)
        assert "소표본" in report
        assert "통계 유의 주장 금지" in report

    def test_write_report_preserves_raw_for_rescore(self, tmp_path: Path) -> None:
        """write_report가 원본 raw.jsonl을 보존하고 report.md를 쓴다."""
        result, _ = self._two_query_result(tmp_path)
        out_dir = tmp_path / "out"
        report_path = write_report(result, out_dir)
        assert report_path.exists()
        assert report_path.name == "report.md"
        raw_path = out_dir / "raw.jsonl"
        assert raw_path.exists()
        # 원본 항목이 그대로 보존된다(재채점 가능).
        from core.report_common import load_jsonl

        preserved = load_jsonl(raw_path)
        assert len(preserved) == 2
        assert {r["id"] for r in preserved} == {"f", "s"}

    def test_rescore_from_preserved_raw_reproduces_result(self, tmp_path: Path) -> None:
        """보존된 raw.jsonl을 다시 채점하면 동일한 분리집계가 나온다(재채점)."""
        result, _ = self._two_query_result(tmp_path)
        out_dir = tmp_path / "out"
        write_report(result, out_dir)
        raw_path = out_dir / "raw.jsonl"

        # 같은 store/reranker로 보존 raw를 재채점 → 동일 결과.
        store = MockStore(
            {
                "회수실패": [_DOC_B, _DOC_C],
                "성공질의": [_DOC_A, _DOC_B],
            }
        )
        reranker = MockReranker({_DOC_A[0]: 0.9, _DOC_B[0]: 0.5, _DOC_C[0]: 0.1})
        rescored = evaluate(raw_path, store, top_n=20, reranker=reranker)
        assert rescored["failure_breakdown"] == result["failure_breakdown"]
        assert rescored["metrics"] == result["metrics"]


# ===========================================================================
# 빈 평가셋·경계 안전
# ===========================================================================


def test_empty_eval_set_is_safe(tmp_path: Path) -> None:
    """평가셋이 비면 n=0·분류 0·MRR 0으로 안전 반환(리포트도 생성)."""
    store = MockStore({})
    reranker = MockReranker({})
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    result = evaluate(path, store, top_n=20, reranker=reranker)
    assert result["n"] == 0
    assert result["failure_breakdown"] == {
        _RETRIEVAL_FAIL: 0,
        _RERANK_FAIL: 0,
        _SUCCESS: 0,
    }
    assert result["metrics"]["dense"]["mrr"] == 0.0
    # 빈 셋이어도 리포트는 생성된다(소표본 경고 포함).
    report = generate_report(result)
    assert "소표본" in report
