"""US-003 K2·K3 — 청킹(chunk) + Qdrant 임베디드 store 단위 테스트.

오프라인 전용 (bge mock + 인메모리 Qdrant `:memory:`). 실서버 접촉 0 → slow 아님.

검증:
- chunk_markdown: 마크다운→청크 수·경계·source 보존·Docling 폴백(단순분할) 동작.
- KnowledgeStore(:memory:, 결정론 mock embedder):
  ensure_collection·upsert→search_dense 라운드트립·dim=1024·top_n·빈 컬렉션 안전.
"""

from __future__ import annotations

import math

import pytest

from knowledge.chunk import Chunk, chunk_markdown
from knowledge.store import KnowledgeStore

# ---------------------------------------------------------------------------
# 결정론 mock embedder — 실서버(bge) 없이 의미 라운드트립을 흉내낸다.
# ---------------------------------------------------------------------------

_DIM = 1024


class FakeEmbedder:
    """텍스트를 결정론 1024차원 벡터로 바꾸는 mock(bge 대체).

    같은 텍스트는 항상 같은 벡터(결정론). 토큰을 1024 버킷에 해싱해 빈도를
    채우므로, 단어가 겹치는 텍스트끼리 코사인 유사도가 높아진다 →
    "질의와 본문이 비슷하면 그 청크가 상위"라는 검색 라운드트립을 흉내낼 수 있다.
    """

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = [tok for tok in text.lower().split() if tok]
        for tok in tokens:
            bucket = (hash(tok) % self.dim + self.dim) % self.dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # 빈 텍스트 — 0 벡터는 코사인이 정의 안 되므로 첫 칸만 1로(안정).
            vec[0] = 1.0
            norm = 1.0
        return [v / norm for v in vec]


class BadDimEmbedder:
    """일부러 차원이 틀린 벡터를 돌려주는 mock(차원 검증 테스트용)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 8 for _ in texts]


@pytest.fixture
def store() -> KnowledgeStore:
    """인메모리 Qdrant + 결정론 mock embedder로 연 store."""
    return KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())


# ===========================================================================
# chunk_markdown 테스트
# ===========================================================================


class TestChunkMarkdown:
    """마크다운 청킹(단순분할 폴백 경로) 단위 검증."""

    def test_empty_text_returns_empty(self) -> None:
        """빈 입력이면 빈 청크 목록."""
        assert chunk_markdown("", "wiki/x.md") == []

    def test_returns_chunk_dataclass(self) -> None:
        """반환 원소가 Chunk dataclass(text·source·chunk_id·meta)이다."""
        chunks = chunk_markdown("# 제목\n\n본문 문단입니다.", "wiki/x.md")
        assert len(chunks) >= 1
        first = chunks[0]
        assert isinstance(first, Chunk)
        assert isinstance(first.text, str) and first.text
        assert isinstance(first.chunk_id, int)
        assert isinstance(first.meta, dict)

    def test_source_preserved_on_all_chunks(self) -> None:
        """모든 청크에 source가 그대로 보존된다."""
        text = "# A\n\n" + ("문단 가나다 " * 40) + "\n\n## B\n\n" + ("다른 문단 " * 40)
        chunks = chunk_markdown(text, "wiki/doc.md")
        assert len(chunks) >= 1
        assert all(c.source == "wiki/doc.md" for c in chunks)

    def test_chunk_ids_are_sequential(self) -> None:
        """chunk_id는 0부터 1씩 증가하는 순번이다."""
        text = "\n\n".join(f"## 섹션{i}\n\n" + ("내용 " * 50) for i in range(4))
        chunks = chunk_markdown(text, "wiki/doc.md")
        assert [c.chunk_id for c in chunks] == list(range(len(chunks)))

    def test_heading_boundary_splits_sections(self) -> None:
        """서로 다른 헤딩 섹션은 다른 청크로 갈린다(경계 존중)."""
        text = (
            "# 첫째 제목\n\n첫째 섹션의 고유한 내용 알파.\n\n"
            "# 둘째 제목\n\n둘째 섹션의 고유한 내용 베타.\n"
        )
        chunks = chunk_markdown(text, "wiki/doc.md")
        # 두 섹션의 고유 토큰이 서로 다른 청크에 들어가야 한다.
        alpha_chunk = [c for c in chunks if "알파" in c.text]
        beta_chunk = [c for c in chunks if "베타" in c.text]
        assert alpha_chunk and beta_chunk
        assert alpha_chunk[0].chunk_id != beta_chunk[0].chunk_id

    def test_long_section_splits_into_multiple_chunks(self) -> None:
        """한 섹션이 충분히 길면 여러 청크로 나뉜다(목표 글자수 경계)."""
        long_body = "\n\n".join(
            f"문단 번호 {i} 내용 채우기 텍스트." * 6 for i in range(8)
        )
        text = f"# 큰 섹션\n\n{long_body}"
        chunks = chunk_markdown(text, "wiki/big.md")
        assert len(chunks) >= 2

    def test_fallback_simple_split_marks_meta(self) -> None:
        """Docling 미설치/실패 시 단순분할 폴백이 동작하고 meta에 표식을 남긴다."""
        chunks = chunk_markdown("# 제목\n\n본문.", "wiki/x.md")
        # Docling이 없으면 simple_split, 있으면 docling_hybrid — 둘 다 유효.
        assert all(
            c.meta.get("chunker") in {"simple_split", "docling_hybrid"} for c in chunks
        )

    def test_real_wiki_doc_produces_chunks(self) -> None:
        """실제 위키 문서 형태(헤딩+표+문단 혼합)에서 청크가 다수 나온다."""
        text = (
            "# 지식 레이어\n\n"
            "## 채택 결정\n\n"
            "벡터 RAG를 1순위로 채택한다. 소형 로컬모델에 안전하다.\n\n"
            "리랭커는 신규 채택. 가장 싸고 큰 승이다.\n\n"
            "## 파이프라인\n\n"
            "수집 → 청킹 → 임베딩 → Qdrant 업서트. 검색은 dense top-N 후 리랭커.\n\n"
            "## 점진 도입\n\n"
            "한꺼번에 풀스택 금지. 과설계 금지.\n"
        )
        chunks = chunk_markdown(text, "wiki/03_knowledge_layer.md")
        assert len(chunks) >= 2
        # 본문 내용이 어딘가에 보존돼야 한다.
        joined = " ".join(c.text for c in chunks)
        assert "리랭커" in joined and "파이프라인" in joined


# ===========================================================================
# KnowledgeStore 테스트 (:memory: + mock embedder)
# ===========================================================================


def _chunks(*texts: str, source: str = "wiki/doc.md") -> list[Chunk]:
    """테스트용 Chunk 목록 헬퍼."""
    return [Chunk(text=t, source=source, chunk_id=i) for i, t in enumerate(texts)]


class TestKnowledgeStore:
    """Qdrant 임베디드 store 단위 검증(인메모리 + mock embedder)."""

    def test_default_dim_is_1024(self, store: KnowledgeStore) -> None:
        """store.dim 기본은 1024(bge-m3 dense 고정)."""
        assert store.dim == 1024

    def test_ensure_collection_creates_with_dim_1024(
        self, store: KnowledgeStore
    ) -> None:
        """ensure_collection이 dim=1024 컬렉션을 만든다."""
        assert store.ensure_collection_exists() is False
        store.ensure_collection()
        assert store.ensure_collection_exists() is True
        # 재호출 멱등(예외 없음)
        store.ensure_collection()

    def test_ensure_collection_rejects_dim_mismatch(self) -> None:
        """기존 컬렉션 차원이 1024가 아니면 ensure_collection이 거부한다.

        collection 메타에 dim을 다르게 박은 두 store를 같은 인메모리에 쓸 수
        없으므로, dim 단언 경로는 dim을 강제로 바꾼 store로 검증한다.
        """
        s = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
        s._dim = 512  # 컬렉션을 512로 생성
        s.ensure_collection()
        s._dim = 1024  # 기대를 1024로 바꿔 불일치 유발
        # P2-rest §24.5 에서 메시지가 'named dense 스키마 불일치(기존 512)' 로 바뀜 → regex 갱신
        # (테스트 의도=dim 불일치 거부는 그대로·코드는 정상 거부·pre-existing 스테일 regex 교정).
        with pytest.raises(ValueError, match="기존 512"):
            s.ensure_collection()

    def test_upsert_empty_is_noop(self, store: KnowledgeStore) -> None:
        """빈 청크 업서트는 0을 돌려주고 임베딩 호출도 안 한다."""
        embedder = store._get_embedder()
        assert isinstance(embedder, FakeEmbedder)
        n = store.upsert([])
        assert n == 0
        assert embedder.calls == []

    def test_upsert_returns_point_count(self, store: KnowledgeStore) -> None:
        """업서트가 넣은 포인트 수를 돌려주고 count에 반영된다."""
        n = store.upsert(_chunks("첫째 청크 내용", "둘째 청크 내용", "셋째 청크 내용"))
        assert n == 3
        assert store.count() == 3

    def test_upsert_dim_mismatch_raises(self) -> None:
        """임베딩 차원이 1024가 아니면 업서트가 거부한다(pre-mortem 2)."""
        s = KnowledgeStore(qdrant_url=":memory:", embedder=BadDimEmbedder())
        with pytest.raises(ValueError, match="차원 불일치"):
            s.upsert(_chunks("내용"))

    def test_upsert_is_idempotent(self, store: KnowledgeStore) -> None:
        """같은 청크를 2회 업서트해도 포인트 수가 늘지 않는다(멱등)."""
        chunks = _chunks("멱등 테스트 청크 하나", "멱등 테스트 청크 둘")
        store.upsert(chunks)
        store.upsert(chunks)
        assert store.count() == 2

    def test_search_roundtrip_returns_relevant_chunk_first(
        self, store: KnowledgeStore
    ) -> None:
        """업서트→search_dense 라운드트립: 질의와 겹치는 청크가 상위로 온다."""
        store.upsert(
            _chunks(
                "사과 바나나 포도 과일 가게",
                "자동차 엔진 바퀴 정비소",
                "고양이 강아지 토끼 동물 병원",
            )
        )
        results = store.search_dense("과일 가게 사과", top_n=3)
        assert len(results) == 3
        top_chunk, top_score = results[0]
        assert "과일" in top_chunk.text or "사과" in top_chunk.text
        # 점수 내림차순 보장
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_returns_chunk_and_float_score(self, store: KnowledgeStore) -> None:
        """search_dense 반환은 (Chunk, float) 튜플 목록이다."""
        store.upsert(_chunks("내용 알파 베타", "내용 감마 델타"))
        results = store.search_dense("알파 베타", top_n=2)
        assert results
        chunk, score = results[0]
        assert isinstance(chunk, Chunk)
        assert isinstance(score, float)
        # source·chunk_id가 payload에서 복원된다
        assert chunk.source == "wiki/doc.md"

    def test_search_respects_top_n(self, store: KnowledgeStore) -> None:
        """top_n이 반환 개수 상한이다."""
        store.upsert(_chunks(*[f"청크 번호 {i} 고유 내용" for i in range(10)]))
        results = store.search_dense("청크 내용", top_n=4)
        assert len(results) == 4

    def test_search_empty_collection_is_safe(self, store: KnowledgeStore) -> None:
        """컬렉션이 없거나 비어 있어도 search_dense가 빈 목록을 안전 반환."""
        # 컬렉션 미생성 상태
        assert store.search_dense("아무 질의", top_n=5) == []
        # 컬렉션 생성했으나 포인트 0
        store.ensure_collection()
        assert store.search_dense("아무 질의", top_n=5) == []

    def test_count_empty_is_zero(self, store: KnowledgeStore) -> None:
        """컬렉션이 없으면 count는 0."""
        assert store.count() == 0

    def test_collection_name_from_endpoint_default(self) -> None:
        """collection 미지정 시 endpoint.yaml knowledge.collection 기본을 쓴다."""
        s = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
        assert s.collection == "knowledge"

    def test_collection_name_override(self) -> None:
        """collection 인자를 주면 그 이름을 쓴다."""
        s = KnowledgeStore(
            qdrant_url=":memory:", collection="custom_coll", embedder=FakeEmbedder()
        )
        assert s.collection == "custom_coll"
