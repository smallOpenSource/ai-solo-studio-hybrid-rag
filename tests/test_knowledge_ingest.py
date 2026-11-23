"""US-005 K5 — 직접 위키 인덱싱(ingest) 단위 테스트.

오프라인 전용 (인메모리 Qdrant `:memory:` + bge mock). 실서버 접촉 0 → slow 아님.

검증(계획 산출물 1·수용5):
- 디렉토리 *.md → 청킹 → 임베딩 → 업서트, 업서트 청크 수 반환.
- ★멱등: 같은 디렉토리를 2회 인덱싱해도 동일 상태(포인트 수 불변).
- mtime payload: 현재 비-git이라 청크 meta에 파일 mtime이 들어간다(git_commit 없음).
- 빈/없는 디렉토리·source 보존 안전.
"""

from __future__ import annotations

import math
from pathlib import Path

from knowledge.ingest import ingest
from knowledge.store import KnowledgeStore

_DIM = 1024


class FakeEmbedder:
    """텍스트를 결정론 1024차원 벡터로 바꾸는 mock(bge embed 대체).

    같은 텍스트는 항상 같은 벡터(멱등 검증에 필요). 토큰을 1024 버킷에 해싱.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
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


def _make_wiki(tmp_path: Path) -> Path:
    """임시 위키 디렉토리에 마크다운 2편을 쓴다(청크가 여럿 나오게)."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "01_intro.md").write_text(
        "# 소개\n\n"
        "이 프로젝트의 코드명은 one_man_corp 이다.\n\n"
        "## 하드웨어\n\n"
        "온프레미스 단일 GPU, VRAM 12GB 이하로 제한한다.\n",
        encoding="utf-8",
    )
    (wiki / "02_model.md").write_text(
        "# 모델 라인업\n\n"
        "기본 모델은 gemma-4-e4b-it 이다.\n\n"
        "## 임베딩\n\n"
        "bge-m3-ko 임베딩 서버는 포트 48802, dim 1024 이다.\n",
        encoding="utf-8",
    )
    return wiki


def _store() -> KnowledgeStore:
    """인메모리 store(결정론 mock embedder)."""
    return KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())


# ===========================================================================
# ingest 기본 동작
# ===========================================================================


class TestIngestBasic:
    """디렉토리→청킹→업서트 기본 경로."""

    def test_ingest_returns_chunk_count_and_populates_store(
        self, tmp_path: Path
    ) -> None:
        """ingest가 업서트한 청크 수를 돌려주고 store에 반영된다."""
        wiki = _make_wiki(tmp_path)
        store = _store()
        n = ingest(wiki, store)
        assert n > 0
        assert store.count() == n

    def test_ingest_missing_dir_returns_zero(self, tmp_path: Path) -> None:
        """없는 디렉토리는 0(업서트 0)."""
        store = _store()
        assert ingest(tmp_path / "no_such_dir", store) == 0
        assert store.count() == 0

    def test_ingest_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        """*.md가 0개인 디렉토리는 0."""
        empty = tmp_path / "empty"
        empty.mkdir()
        store = _store()
        assert ingest(empty, store) == 0

    def test_ingest_preserves_source_as_relative_path(self, tmp_path: Path) -> None:
        """검색 시 source가 파일 경로로 보존된다(절대경로 아님이면 그대로)."""
        wiki = _make_wiki(tmp_path)
        store = _store()
        ingest(wiki, store)
        results = store.search_dense("one_man_corp 코드명", top_n=10)
        assert results
        # tmp_path는 프로젝트 루트 밖이라 절대경로 source가 된다(파일명 포함).
        assert any("01_intro.md" in c.source for c, _ in results)


# ===========================================================================
# ★멱등 — 2회 실행 동일 상태
# ===========================================================================


class TestIdempotency:
    """같은 디렉토리를 2회 인덱싱해도 포인트 수가 늘지 않는다(수용5)."""

    def test_double_ingest_keeps_point_count(self, tmp_path: Path) -> None:
        """2회 ingest 후 포인트 수 = 1회 후 포인트 수(멱등)."""
        wiki = _make_wiki(tmp_path)
        store = _store()
        first = ingest(wiki, store)
        count_after_first = store.count()
        second = ingest(wiki, store)
        count_after_second = store.count()
        assert first == second  # 같은 청크 수 업서트
        assert count_after_first == count_after_second  # 포인트 수 불변

    def test_double_ingest_same_count_value(self, tmp_path: Path) -> None:
        """두 번 모두 같은 청크 수를 돌려준다."""
        wiki = _make_wiki(tmp_path)
        store = _store()
        n1 = ingest(wiki, store)
        n2 = ingest(wiki, store)
        assert n1 == n2 and n1 > 0


# ===========================================================================
# mtime payload (현재 비-git → git_commit 없음, mtime만)
# ===========================================================================


class TestVersionMeta:
    """버전 메타: mtime은 항상, git_commit은 git 저장소일 때만."""

    def test_chunk_meta_has_mtime(self, tmp_path: Path) -> None:
        """업서트된 청크 payload meta에 파일 mtime이 들어간다."""
        wiki = _make_wiki(tmp_path)
        store = _store()
        ingest(wiki, store)
        results = store.search_dense("gemma 모델", top_n=10)
        assert results
        # 검색으로 복원된 청크 meta에 mtime이 보존된다.
        assert any("mtime" in c.meta for c, _ in results)
        for chunk, _ in results:
            if "mtime" in chunk.meta:
                assert isinstance(chunk.meta["mtime"], (int, float))

    def test_non_git_dir_has_no_git_commit(self, tmp_path: Path) -> None:
        """비-git 임시 디렉토리는 meta에 git_commit이 없다(mtime 경로)."""
        wiki = _make_wiki(tmp_path)  # tmp_path는 git 저장소 아님
        store = _store()
        ingest(wiki, store)
        results = store.search_dense("VRAM 하드웨어", top_n=10)
        assert results
        # git rev-parse 실패 → git_commit 키 없음.
        assert all("git_commit" not in c.meta for c, _ in results)
