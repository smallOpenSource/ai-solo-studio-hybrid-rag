"""US-004 K4 — 검색 한 벌(dense→리랭커) 단위 테스트.

오프라인 전용 (bge mock + 인메모리 Qdrant `:memory:`). 실서버 접촉 0 → slow 아님.

검증:
- dense 후보 → 리랭커 재정렬 → top-k(≤5) 단언(리랭커가 dense 순서를 뒤집는다).
- 리랭커 응답 형식 어댑터: dict({"results":[...]}) · bare list 양형식 점수 파싱.
  점수 키(score / relevance_score) 변형·미정렬 응답·범위 밖 index 견고성.
- 결과 형식 {"results":[{text,source,score}], "count"} (knowledge_search 도구 계약).
- 빈 질의 / 빈 컬렉션 안전(리랭커 호출 0). k 경계(k≤5 상한·k<1 빈 결과).
- store·reranker 주입(테스트 mock) — 실서버 기본 경로는 타지 않는다.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from knowledge.chunk import Chunk
from knowledge.search import _normalize_rerank, search
from knowledge.store import KnowledgeStore

# ---------------------------------------------------------------------------
# 결정론 mock — 실서버(bge) 없이 dense 회수와 리랭크를 흉내낸다.
# ---------------------------------------------------------------------------

_DIM = 1024


class FakeEmbedder:
    """텍스트를 결정론 1024차원 벡터로 바꾸는 mock(bge embed 대체).

    토큰을 1024 버킷에 해싱해 빈도를 채운다 → 단어가 겹치는 텍스트끼리 코사인
    유사도가 높다. "질의와 겹치는 청크가 dense 상위"라는 회수를 흉내낼 수 있다.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in (t for t in text.lower().split() if t):
            bucket = (hash(tok) % _DIM + _DIM) % _DIM
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            norm = 1.0
        return [v / norm for v in vec]


class DictReranker:
    """{"results":[{"index","score","document"}]} 형식 mock(실서버 capture 형태).

    주입된 score_by_text로 점수를 매긴다(결정론). 일부러 점수 내림차순이 아니라
    원본(dense) 순서대로 응답을 만들어, search가 점수로 재정렬하는지 검증한다.
    """

    def __init__(self, score_by_text: dict[str, float]) -> None:
        self.score_by_text = score_by_text
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> dict[str, Any]:
        self.calls.append((query, list(documents)))
        results = [
            {"index": i, "score": self.score_by_text[doc], "document": doc}
            for i, doc in enumerate(documents)
        ]
        return {"results": results}


class ListReranker:
    """[{"index","relevance_score"}] bare-list 형식 mock(목 서버 형태).

    점수 키가 relevance_score다(score 아님) — 어댑터의 키 변형 흡수 검증.
    """

    def __init__(self, score_by_text: dict[str, float]) -> None:
        self.score_by_text = score_by_text
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        self.calls.append((query, list(documents)))
        return [
            {"index": i, "relevance_score": self.score_by_text[doc]}
            for i, doc in enumerate(documents)
        ]


class BoomReranker:
    """호출되면 실패하는 mock — 빈 결과 경로에서 리랭커 미호출을 강제 검증."""

    def rerank(self, query: str, documents: list[str]) -> Any:
        raise AssertionError("리랭커가 호출되면 안 된다(빈 후보/빈 질의)")


def _store_with(*texts: str, source: str = "wiki/doc.md") -> KnowledgeStore:
    """주어진 텍스트들을 업서트한 인메모리 store(결정론 mock embedder)."""
    s = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
    chunks = [Chunk(text=t, source=source, chunk_id=i) for i, t in enumerate(texts)]
    s.upsert(chunks)
    return s


# 검색 코퍼스: dense가 겹침으로 회수하는 후보들. 질의와 단어가 겹칠수록 dense 상위.
_DOC_FRUIT = "사과 바나나 포도 과일 가게 신선"
_DOC_CAR = "자동차 엔진 바퀴 정비소 수리"
_DOC_ANIMAL = "고양이 강아지 토끼 동물 병원"


# ===========================================================================
# dense → 리랭커 → top-k 파이프라인
# ===========================================================================


class TestSearchPipeline:
    """dense 후보를 리랭커가 재정렬하고 top-k로 자른다."""

    def test_reranker_reorders_dense_candidates(self) -> None:
        """리랭커 점수가 dense 순서를 뒤집으면 결과도 그 순서를 따른다."""
        store = _store_with(_DOC_FRUIT, _DOC_CAR, _DOC_ANIMAL)
        # dense는 '과일'로 _DOC_FRUIT를 1등으로 회수하지만, 리랭커는 _DOC_ANIMAL을
        # 최고점으로 매긴다 → 결과 1등은 _DOC_ANIMAL이어야 한다(리랭크가 이김).
        reranker = DictReranker({_DOC_FRUIT: 0.1, _DOC_CAR: 0.2, _DOC_ANIMAL: 0.9})
        out = search("과일 가게 사과", k=3, store=store, reranker=reranker)
        texts = [r["text"] for r in out["results"]]
        assert texts[0] == _DOC_ANIMAL
        assert texts == [_DOC_ANIMAL, _DOC_CAR, _DOC_FRUIT]

    def test_reranker_receives_dense_candidate_texts(self) -> None:
        """리랭커는 dense가 회수한 후보 본문을 documents로 받는다(쿼리도 그대로)."""
        store = _store_with(_DOC_FRUIT, _DOC_CAR, _DOC_ANIMAL)
        reranker = DictReranker({_DOC_FRUIT: 0.3, _DOC_CAR: 0.2, _DOC_ANIMAL: 0.1})
        search("과일 사과", k=5, store=store, reranker=reranker)
        assert len(reranker.calls) == 1
        called_query, called_docs = reranker.calls[0]
        assert called_query == "과일 사과"
        assert set(called_docs) == {_DOC_FRUIT, _DOC_CAR, _DOC_ANIMAL}

    def test_result_shape_is_tool_contract(self) -> None:
        """결과 형식 = {"results":[{text,source,score}], "count"} (도구 출력 계약)."""
        store = _store_with(_DOC_FRUIT, _DOC_CAR)
        reranker = ListReranker({_DOC_FRUIT: 0.8, _DOC_CAR: 0.4})
        out = search("과일 사과", k=5, store=store, reranker=reranker)
        assert set(out.keys()) == {"results", "count"}
        assert out["count"] == len(out["results"])
        for r in out["results"]:
            # P8 E-1: 기존 3필드 보존 + provenance add-only(meta 없는 청크는 None).
            assert set(r.keys()) == {"text", "source", "score", "provenance"}
            assert isinstance(r["text"], str)
            assert isinstance(r["source"], str)
            assert isinstance(r["score"], float)
            assert r["provenance"] is None
        # source가 청크에서 보존된다.
        assert all(r["source"] == "wiki/doc.md" for r in out["results"])

    def test_score_in_result_is_reranker_score(self) -> None:
        """결과 score는 dense 유사도가 아니라 리랭커 점수다."""
        store = _store_with(_DOC_FRUIT, _DOC_CAR)
        reranker = DictReranker({_DOC_FRUIT: 7.5, _DOC_CAR: -3.0})
        out = search("과일", k=5, store=store, reranker=reranker)
        top = out["results"][0]
        assert top["text"] == _DOC_FRUIT
        assert top["score"] == 7.5  # 리랭커 점수 그대로(음수·로짓 허용)


# ===========================================================================
# top-k 경계
# ===========================================================================


class TestTopK:
    """top-k 상한·경계."""

    def test_top_k_limits_results(self) -> None:
        """k가 후보보다 작으면 상위 k개만 돌려준다."""
        store = _store_with(_DOC_FRUIT, _DOC_CAR, _DOC_ANIMAL)
        reranker = DictReranker({_DOC_FRUIT: 0.9, _DOC_CAR: 0.5, _DOC_ANIMAL: 0.1})
        out = search("과일", k=2, store=store, reranker=reranker)
        assert out["count"] == 2
        assert [r["text"] for r in out["results"]] == [_DOC_FRUIT, _DOC_CAR]

    def test_k_capped_at_five(self) -> None:
        """k>5를 넘겨도 최종 상한은 5다(Context Rot 대응 top-k≤5)."""
        texts = [f"고유토큰{i} 문서 본문 내용 채우기" for i in range(8)]
        store = _store_with(*texts)
        reranker = DictReranker({t: float(len(texts) - i) for i, t in enumerate(texts)})
        out = search("문서 본문 내용", k=20, store=store, reranker=reranker)
        assert out["count"] == 5

    def test_k_less_than_one_returns_empty(self) -> None:
        """k<1이면 빈 결과(리랭커 호출 0)."""
        store = _store_with(_DOC_FRUIT)
        out = search("과일", k=0, store=store, reranker=BoomReranker())
        assert out == {"results": [], "count": 0}

    def test_fewer_candidates_than_k(self) -> None:
        """후보가 k보다 적으면 있는 만큼만 돌려준다."""
        store = _store_with(_DOC_FRUIT)
        reranker = DictReranker({_DOC_FRUIT: 0.9})
        out = search("과일 사과", k=5, store=store, reranker=reranker)
        assert out["count"] == 1
        assert out["results"][0]["text"] == _DOC_FRUIT


# ===========================================================================
# 빈 결과 안전(리랭커 미호출)
# ===========================================================================


class TestEmptySafety:
    """빈 질의·빈 컬렉션에서 리랭커를 부르지 않고 안전 반환한다."""

    def test_empty_query_returns_empty(self) -> None:
        """빈 문자열 질의는 빈 결과(리랭커 호출 0)."""
        store = _store_with(_DOC_FRUIT)
        out = search("", k=5, store=store, reranker=BoomReranker())
        assert out == {"results": [], "count": 0}

    def test_whitespace_query_returns_empty(self) -> None:
        """공백뿐인 질의도 빈 결과(리랭커 호출 0)."""
        store = _store_with(_DOC_FRUIT)
        out = search("   \n\t  ", k=5, store=store, reranker=BoomReranker())
        assert out == {"results": [], "count": 0}

    def test_empty_collection_returns_empty(self) -> None:
        """컬렉션이 비어 dense 후보가 0이면 빈 결과(리랭커 호출 0)."""
        store = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
        out = search("아무 질의", k=5, store=store, reranker=BoomReranker())
        assert out == {"results": [], "count": 0}

    def test_filter_excluding_all_returns_empty(self) -> None:
        """filter가 모든 후보를 거르면 빈 결과(리랭커 호출 0)."""
        store = _store_with(_DOC_FRUIT, _DOC_CAR)
        out = search(
            "과일",
            k=5,
            filter={"source": "wiki/없는파일.md"},
            store=store,
            reranker=BoomReranker(),
        )
        assert out == {"results": [], "count": 0}


# ===========================================================================
# filter — 리랭크 전 후보 좁히기
# ===========================================================================


class TestFilter:
    """filter는 dense 후보를 리랭크 전에 좁힌다(source·meta)."""

    def test_filter_by_source_keeps_only_matching(self) -> None:
        """source filter는 그 출처 청크만 남긴다."""
        s = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
        s.upsert(
            [
                Chunk(text=_DOC_FRUIT, source="wiki/a.md", chunk_id=0),
                Chunk(text=_DOC_CAR, source="wiki/b.md", chunk_id=0),
            ]
        )
        reranker = DictReranker({_DOC_FRUIT: 0.9, _DOC_CAR: 0.9})
        out = search(
            "과일 자동차",
            k=5,
            filter={"source": "wiki/a.md"},
            store=s,
            reranker=reranker,
        )
        assert out["count"] == 1
        assert out["results"][0]["source"] == "wiki/a.md"


# ===========================================================================
# 리랭커 응답 형식 어댑터 (_normalize_rerank) — dict·list 양형식
# ===========================================================================


class TestRerankAdapter:
    """리랭커 응답 정규화: dict/list·점수 키 변형·미정렬·견고성."""

    def test_dict_with_results_key(self) -> None:
        """{"results":[{index,score}]} → (index,score) 내림차순(실서버 capture 형태)."""
        raw = {
            "results": [
                {"index": 0, "score": 7.53, "document": "A"},
                {"index": 1, "score": -11.02, "document": "B"},
            ]
        }
        assert _normalize_rerank(raw, n_docs=2) == [(0, 7.53), (1, -11.02)]

    def test_bare_list_with_score(self) -> None:
        """[{index,score}] bare list → 내림차순(test_bge_client 형태)."""
        raw = [{"index": 0, "score": 0.2}, {"index": 1, "score": 0.9}]
        assert _normalize_rerank(raw, n_docs=2) == [(1, 0.9), (0, 0.2)]

    def test_bare_list_with_relevance_score_key(self) -> None:
        """relevance_score 키도 점수로 흡수한다(목 서버 형태)."""
        raw = [
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.1},
        ]
        assert _normalize_rerank(raw, n_docs=2) == [(0, 0.9), (1, 0.1)]

    def test_unsorted_response_gets_sorted_desc(self) -> None:
        """응답이 점수 순이 아니어도 내림차순으로 재정렬한다."""
        raw = {
            "results": [
                {"index": 0, "score": 0.3},
                {"index": 1, "score": 0.9},
                {"index": 2, "score": 0.5},
            ]
        }
        assert _normalize_rerank(raw, n_docs=3) == [(1, 0.9), (2, 0.5), (0, 0.3)]

    def test_missing_index_uses_response_position(self) -> None:
        """index 키가 없으면 응답 순서를 원본 순서로 본다."""
        raw = [{"score": 0.2}, {"score": 0.8}]
        # 위치 0→0.2, 위치 1→0.8 → 내림차순 [(1,0.8),(0,0.2)]
        assert _normalize_rerank(raw, n_docs=2) == [(1, 0.8), (0, 0.2)]

    def test_scalar_score_list(self) -> None:
        """점수 스칼라 평행 리스트도 위치를 index로 흡수한다."""
        raw = [0.2, 0.9, 0.5]
        assert _normalize_rerank(raw, n_docs=3) == [(1, 0.9), (2, 0.5), (0, 0.2)]

    def test_scores_key_in_dict(self) -> None:
        """{"scores":[...]} 변형도 흡수한다."""
        raw = {"scores": [0.1, 0.7]}
        assert _normalize_rerank(raw, n_docs=2) == [(1, 0.7), (0, 0.1)]

    def test_out_of_range_index_skipped(self) -> None:
        """원본 범위를 벗어난 index 원소는 건너뛴다(견고성)."""
        raw = {
            "results": [
                {"index": 0, "score": 0.9},
                {"index": 5, "score": 0.99},  # n_docs=2 범위 밖 → 무시
            ]
        }
        assert _normalize_rerank(raw, n_docs=2) == [(0, 0.9)]

    def test_unreadable_score_skipped(self) -> None:
        """점수를 못 읽는 원소는 건너뛴다(나머지는 살린다)."""
        raw = {
            "results": [
                {"index": 0, "score": "nan-text"},  # float 변환 불가 → 무시
                {"index": 1, "score": 0.5},
            ]
        }
        assert _normalize_rerank(raw, n_docs=2) == [(1, 0.5)]

    def test_empty_or_unknown_shape_returns_empty(self) -> None:
        """빈·미지 형식이면 빈 목록(검색이 빈 결과로 안전 흡수)."""
        assert _normalize_rerank([], n_docs=0) == []
        assert _normalize_rerank({}, n_docs=2) == []
        assert _normalize_rerank({"unknown": 1}, n_docs=2) == []

    def test_duplicate_index_keeps_highest(self) -> None:
        """같은 index가 중복되면 점수 높은 것만 남긴다."""
        raw = [
            {"index": 0, "score": 0.3},
            {"index": 0, "score": 0.9},
            {"index": 1, "score": 0.5},
        ]
        assert _normalize_rerank(raw, n_docs=2) == [(0, 0.9), (1, 0.5)]


# ===========================================================================
# 어댑터가 search 결과에 반영되는지(통합) — list 형식으로도 재정렬 일치
# ===========================================================================


def test_list_reranker_drives_search_order() -> None:
    """bare-list 리랭커(relevance_score)도 search 결과 순서를 정한다."""
    store = _store_with(_DOC_FRUIT, _DOC_CAR, _DOC_ANIMAL)
    reranker = ListReranker({_DOC_FRUIT: 0.1, _DOC_CAR: 0.9, _DOC_ANIMAL: 0.5})
    out = search("과일 자동차 동물", k=3, store=store, reranker=reranker)
    assert [r["text"] for r in out["results"]] == [_DOC_CAR, _DOC_ANIMAL, _DOC_FRUIT]


@pytest.mark.parametrize("k", [1, 3, 5])
def test_count_matches_results_length(k: int) -> None:
    """count는 항상 results 길이와 일치한다(여러 k에서)."""
    texts = [f"문서{i} 공통 본문 내용" for i in range(6)]
    store = _store_with(*texts)
    reranker = DictReranker({t: float(i) for i, t in enumerate(texts)})
    out = search("공통 본문 내용", k=k, store=store, reranker=reranker)
    assert out["count"] == len(out["results"])
    assert out["count"] == min(k, 5)
