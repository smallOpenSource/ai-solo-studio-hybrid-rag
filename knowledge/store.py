"""Qdrant 벡터 store (Phase 2 K3 · SR/renewal §24.5 하이브리드).

직접 작성한 위키/vault 청크를 dense 임베딩 + sparse(BM25-ish) 벡터로 바꿔 검색
창고(Qdrant 서버)에 넣고, 질문이 들어오면 의미가 가까운 후보를 넓게 찾아 돌려준다.

검색 두 경로:
- `search_dense(query, top_n)` — dense named-vector 'dense' COSINE 순위(시그니처 보존·소비자 4곳 무회귀).
- `search_hybrid(query, top_n)` — dense + sparse named-vector 'text'(IDF)를 서버 RRF로 융합(§24.5).
  ★rerank(§24.5 마지막 단계)는 store가 아니라 호출자(knowledge/eval.py·service)가 기존 리랭커
  경로로 적용한다(헤드라인 post-rerank top-K는 eval에서 계산·관심사 분리·중복 0).

배포 = 서버(§5.2 · rootless Podman Qdrant 컨테이너·END STATE):
- `QdrantClient(url="http://127.0.0.1:2811")` — 서버 모드(프로덕션·유일).
- `QdrantClient(location=":memory:")` — 테스트 전용 인메모리(qdrant_url=":memory:").

sparse 인코더 = Qdrant-native IDF(dep-0·신규 Python 의존성 0): 클라이언트가 토큰 TF(빈도)를
sparse 벡터로 넣고, 컬렉션 `Modifier.IDF`가 질의 시 IDF 가중을 서버에서 적용한다(BM25-ish).
임베딩은 core.bge_client(BGEClient)를 주입해 재사용한다(복사 금지). 테스트는 mock embedder.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any, Protocol

from qdrant_client import QdrantClient, models

from core.config import load_endpoint_config

if TYPE_CHECKING:
    from knowledge.chunk import Chunk


# dense 임베딩 차원(bge-m3, 고정). 컬렉션 생성·업서트 시 단언용.
_FIXED_DIM = 1024

# named-vector 이름. dense='dense'(§24.5). sparse='text' — sparse_probe가 검증한 이름.
# ★(minor·cosmetic) §24.5 스켈레톤 예시는 sparse를 'sparse'로 쓰나, 본 store/probe는
# 'text'로 검증·사용한다(내부 식별자라 store 생성·query·eval에서 동일 이름이면 기능 영향 0).
_DENSE = "dense"
_SPARSE = "text"

# sparse 토큰화(dep-0): 한글 음절 블록·영숫자 런을 토큰으로. 토큰→u32 해시 인덱스.
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


class Embedder(Protocol):
    """임베딩 제공자 계약 — BGEClient.embed(texts)와 동일 시그니처.

    테스트는 이 계약을 만족하는 결정론 mock을 주입한다(실서버 불요).
    """

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class KnowledgeStore:
    """Qdrant 벡터 store — 컬렉션·업서트·dense/hybrid 검색(§24.5).

    기본 설정은 endpoint.yaml의 knowledge 블록을 따른다(qdrant_url·collection·dim).
    컬렉션 스키마 = named dense 'dense'(COSINE) + named sparse 'text'(IDF).
    """

    def __init__(
        self,
        qdrant_url: str | None = None,
        collection: str | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        """벡터 store를 연다 (SR/renewal §5.2: 임베디드→서버 전환·END STATE=서버 유일).

        Args:
            qdrant_url: Qdrant 서버 URL(예: http://127.0.0.1:2811). None이면
                endpoint.yaml knowledge.qdrant_url. 테스트는 ":memory:"를 넘겨
                인메모리 모드(서버 불요·결정론 격리)로 연다.
            collection: 컬렉션 이름. None이면 endpoint.yaml knowledge.collection
                (기본 'knowledge').
            embedder: 임베딩 제공자(BGEClient 등). 주입 안 하면 검색·업서트 시
                BGEClient를 지연 생성한다(테스트는 mock 주입).

        Raises:
            ValueError: qdrant_url 미지정 + endpoint.yaml에도 없을 때.
        """
        cfg = load_endpoint_config().get("knowledge", {}) or {}
        self._collection: str = collection or cfg.get("collection", "knowledge")
        self._dim: int = int(cfg.get("dim", _FIXED_DIM))
        self._embedder = embedder

        resolved = qdrant_url if qdrant_url is not None else cfg.get("qdrant_url")
        if resolved is None:
            raise ValueError(
                "qdrant_url 미지정 — endpoint.yaml knowledge.qdrant_url 또는 인자로 "
                "서버 URL을 주세요(테스트는 ':memory:'). "
                "SR/renewal §5.2: 디스크-임베디드 경로는 제거됨·서버가 유일."
            )
        if resolved == ":memory:":
            self._client = QdrantClient(location=":memory:")
        else:
            self._client = QdrantClient(url=resolved)
        self._url = resolved

    @property
    def collection(self) -> str:
        """컬렉션 이름."""
        return self._collection

    @property
    def dim(self) -> int:
        """벡터 차원(고정 1024)."""
        return self._dim

    def _get_embedder(self) -> Embedder:
        """주입된 embedder를 돌려주거나 BGEClient를 지연 생성한다."""
        if self._embedder is None:
            from core.bge_client import BGEClient

            self._embedder = BGEClient()
        return self._embedder

    def ensure_collection(self) -> None:
        """컬렉션이 없으면 named dense 'dense'(COSINE) + sparse 'text'(IDF)로 생성(멱등).

        이미 있으면 named 'dense' 차원이 1024인지 단언한다. 레거시 unnamed 컬렉션이면
        명시 실패(R-INDEX 필요 — §24.5 하이브리드는 named dense+sparse 스키마를 요구).
        """
        if self._client.collection_exists(self._collection):
            info = self._client.get_collection(self._collection)
            vectors = info.config.params.vectors
            existing_dim = (
                vectors[_DENSE].size
                if isinstance(vectors, dict) and _DENSE in vectors
                else None
            )
            if existing_dim != self._dim:
                raise ValueError(
                    f"컬렉션 '{self._collection}' 스키마가 named '{_DENSE}'(dim={self._dim})"
                    f"가 아님(기존 {existing_dim}). 레거시 unnamed → R-INDEX 재생성 필요"
                    " (§24.5 하이브리드는 named dense+sparse 요구)."
                )
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                _DENSE: models.VectorParams(
                    size=self._dim, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                _SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )

    def upsert(self, chunks: list[Chunk]) -> int:
        """청크 목록을 dense+sparse로 임베딩해 컬렉션에 업서트한다(있으면 갱신).

        dense는 주입 embedder(원격 bge), sparse는 Qdrant-native IDF용 토큰 TF(로컬·dep-0).
        source+chunk_id로 정한 결정론 id로 업서트한다 — 재인덱싱 멱등.

        Args:
            chunks: 업서트할 청크 목록(빈 목록이면 0 반환·호출 0).

        Returns:
            업서트한 포인트 수.

        Raises:
            ValueError: 임베딩 개수·차원이 기대와 다르면.
        """
        if not chunks:
            return 0
        self.ensure_collection()

        texts = [c.text for c in chunks]
        vectors = self._get_embedder().embed(texts)
        if len(vectors) != len(chunks):
            raise ValueError(
                f"임베딩 개수 불일치: 벡터 {len(vectors)} != 청크 {len(chunks)}"
            )

        points: list[models.PointStruct] = []
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self._dim:
                raise ValueError(
                    f"임베딩 차원 불일치: {len(vector)} != 컬렉션 dim {self._dim}"
                )
            points.append(
                models.PointStruct(
                    id=_point_id(chunk.source, chunk.chunk_id),
                    vector={
                        _DENSE: list(vector),
                        _SPARSE: _sparse_vector(chunk.text),
                    },
                    payload={
                        "text": chunk.text,
                        "source": chunk.source,
                        "chunk_id": chunk.chunk_id,
                        "meta": chunk.meta,
                    },
                )
            )
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def search_dense(self, query: str, top_n: int = 20) -> list[tuple[Chunk, float]]:
        """질의를 임베딩해 의미가 가까운 청크 top_n을 돌려준다(named 'dense' 벡터).

        ★시그니처·반환 형태 보존(소비자 4곳 무회귀) — 내부만 named-vector 쿼리.

        Args:
            query: 검색 질의.
            top_n: 가져올 후보 수(1차 회수). 기본 20.

        Returns:
            (Chunk, score) 목록 — score 내림차순(COSINE). 빈 컬렉션·결과 없으면 빈 목록.
        """
        if not self.ensure_collection_exists():
            return []
        query_vector = self._get_embedder().embed([query])[0]
        response = self._client.query_points(
            collection_name=self._collection,
            query=list(query_vector),
            using=_DENSE,
            limit=top_n,
            with_payload=True,
        )
        return _points_to_results(response.points)

    def search_hybrid(self, query: str, top_n: int = 20) -> list[tuple[Chunk, float]]:
        """dense + sparse(IDF)를 서버 RRF로 융합한 top_n 후보를 돌려준다(§24.5).

        dense(의미)와 sparse(정확 토큰)를 각각 top_n prefetch한 뒤 Reciprocal Rank
        Fusion으로 합친다 — sparse가 dense가 놓친 정확-토큰 앵커를 융합 후보셋에 넣는다.
        ★rerank(§24.5 마지막)는 호출자가 적용(eval/service의 기존 리랭커 경로). 빈
        컬렉션·결과 없으면 빈 목록(search_dense와 동형 가드).

        Args:
            query: 검색 질의.
            top_n: dense·sparse 각 prefetch 및 융합 후보 수. 기본 20.

        Returns:
            (Chunk, RRF점수) 목록 — 융합 점수 내림차순.
        """
        if not self.ensure_collection_exists():
            return []
        dense_vec = self._get_embedder().embed([query])[0]
        sparse_q = _sparse_vector(query)
        response = self._client.query_points(
            collection_name=self._collection,
            prefetch=[
                models.Prefetch(query=list(dense_vec), using=_DENSE, limit=top_n),
                models.Prefetch(query=sparse_q, using=_SPARSE, limit=top_n),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_n,
            with_payload=True,
        )
        return _points_to_results(response.points)

    def ensure_collection_exists(self) -> bool:
        """컬렉션이 실재하면 True(검색 전 빈-컬렉션 안전 확인용)."""
        return self._client.collection_exists(self._collection)

    def count(self) -> int:
        """컬렉션에 든 포인트 수(없으면 0)."""
        if not self.ensure_collection_exists():
            return 0
        return self._client.count(self._collection).count


def _sparse_vector(text: str) -> models.SparseVector:
    """텍스트를 토큰 TF sparse 벡터로(Qdrant-native IDF용·dep-0).

    토큰(한글 음절 블록·영숫자 런)을 안정 u32 해시 인덱스로 매핑하고 빈도를 값으로
    둔다. 컬렉션 `Modifier.IDF`가 질의 시 IDF 가중을 서버에서 적용한다(BM25-ish).
    토큰이 없으면 빈 sparse 벡터(sparse prefetch 무회수 → RRF가 dense로 자연 degrade).
    """
    counts: dict[int, int] = {}
    for tok in _TOKEN_RE.findall(text.lower()):
        idx = int.from_bytes(hashlib.sha1(tok.encode("utf-8")).digest()[:4], "big")
        counts[idx] = counts.get(idx, 0) + 1
    return models.SparseVector(
        indices=list(counts.keys()),
        values=[float(v) for v in counts.values()],
    )


def _points_to_results(points: list[Any]) -> list[tuple[Chunk, float]]:
    """query_points 응답 포인트를 (Chunk, score) 목록으로 복원한다."""
    results: list[tuple[Chunk, float]] = []
    for point in points:
        payload: dict[str, Any] = point.payload or {}
        results.append((_chunk_from_payload(payload), float(point.score)))
    return results


def _point_id(source: str, chunk_id: int) -> int:
    """source+chunk_id를 결정론 정수 id로 바꾼다(멱등 업서트용).

    Qdrant 포인트 id는 정수 또는 UUID여야 하므로, 출처+순번 문자열을 안정 해시해
    음이 아닌 64비트 정수로 만든다. 같은 청크는 항상 같은 id → 재인덱싱 멱등.
    """
    key = f"{source}::{chunk_id}".encode("utf-8")
    digest = hashlib.sha1(key).digest()
    return int.from_bytes(digest[:8], "big") >> 1  # 63비트(음수 회피)


def _chunk_from_payload(payload: dict[str, Any]) -> Chunk:
    """Qdrant payload를 Chunk로 복원한다(검색 결과용)."""
    from knowledge.chunk import Chunk

    return Chunk(
        text=payload.get("text", ""),
        source=payload.get("source", ""),
        chunk_id=int(payload.get("chunk_id", 0)),
        meta=payload.get("meta", {}) or {},
    )
