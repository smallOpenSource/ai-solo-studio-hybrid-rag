"""P2-rest-A US-A2 — store 하이브리드(§24.5) 단위. :memory:·mock embedder(실서버 0).

named dense 'dense' + sparse 'text'(IDF) 스키마·upsert dual·search_dense 시그니처 보존·
search_hybrid RRF 융합(sparse가 정확-토큰 회수)·_sparse_vector 토큰화 검증.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from knowledge.chunk import Chunk  # noqa: E402
from knowledge.store import KnowledgeStore, _sparse_vector  # noqa: E402


class _MockEmbedder:
    """결정론 1024-dim 벡터(실 bge 0). 텍스트별 안정 해시 기반."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha1(t.encode("utf-8")).digest()
            out.append([h[i % len(h)] / 255.0 for i in range(1024)])
        return out


def _store() -> KnowledgeStore:
    s = KnowledgeStore(qdrant_url=":memory:", embedder=_MockEmbedder())
    s.ensure_collection()
    return s


_CHUNKS = [
    Chunk(text="알파 베타 감마 코어루프", source="a", chunk_id=0, meta={}),
    Chunk(text="델타 엡실론 제타 리텐션", source="b", chunk_id=1, meta={}),
]


def test_ensure_collection_named_dense_and_sparse():
    s = _store()
    info = s._client.get_collection(s.collection)
    vectors = info.config.params.vectors
    assert isinstance(vectors, dict) and "dense" in vectors
    assert vectors["dense"].size == 1024
    sparse = info.config.params.sparse_vectors
    assert sparse is not None and "text" in sparse


def test_upsert_dual_and_count():
    s = _store()
    assert s.upsert(_CHUNKS) == 2
    assert s.count() == 2
    assert s.upsert([]) == 0  # 빈 목록 호출 0


def test_search_dense_signature_preserved():
    s = _store()
    s.upsert(_CHUNKS)
    res = s.search_dense("알파", top_n=5)
    assert isinstance(res, list) and res
    chunk, score = res[0]
    assert isinstance(chunk, Chunk) and isinstance(score, float)


def test_search_hybrid_rrf_surfaces_exact_token():
    s = _store()
    s.upsert(_CHUNKS)
    # '제타'는 chunk b 에만 있는 정확 토큰 → sparse 경로가 RRF로 b 를 회수.
    res = s.search_hybrid("제타", top_n=5)
    sources = [c.source for c, _ in res]
    assert "b" in sources
    assert res[0][0].source == "b"  # 정확-토큰 매칭이 상위


def test_search_empty_collection_guards():
    s = KnowledgeStore(qdrant_url=":memory:", embedder=_MockEmbedder())
    # ensure_collection 호출 전 — 컬렉션 없음 → 빈 목록(가드)
    assert s.search_dense("x") == []
    assert s.search_hybrid("x") == []


def test_sparse_vector_tokenizes_korean_and_alnum():
    v = _sparse_vector("알파 beta 123 알파")
    assert len(v.indices) == 3  # 알파(중복 1)·beta·123
    # '알파' 2회 → 값 2.0 포함
    assert 2.0 in v.values


def test_sparse_vector_empty_text():
    v = _sparse_vector("!!! ...")  # 토큰 없음
    assert v.indices == [] and v.values == []
