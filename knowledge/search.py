"""검색 한 벌 — dense 후보 → 리랭커 재정렬 → top-k (Phase 2 K4).

질문이 들어오면 두 단계로 답 후보를 고른다(계획 §3.2 파이프라인):
  ① store.search_dense로 의미가 가까운 후보를 넓게 top_n개 회수.
  ② BGEClient.rerank로 그 후보들을 관련도 높은 순으로 다시 줄 세운 뒤(리랭커),
     상위 top_k(≤5)개만 돌려준다 — Context Rot 대응(적게 돌려주기).

리랭커 응답 형식이 배포마다 다르므로(dict 또는 list, 점수 키 이름·정렬 여부가
제각각), `_normalize_rerank`가 그것을 한 형태로 정규화한다(아래 어댑터 설명 참조).

임베딩·리랭크는 core.bge_client(BGEClient)를 재사용한다(복사 금지). store·
reranker를 주입 안 하면 기본(endpoint.yaml knowledge 컬렉션 + BGEClient)을 쓰고,
테스트는 결정론 mock store·mock reranker를 주입한다(실서버 0). 호출은 순차다
(동시성 코드 없음).

결과 형식(knowledge_search 도구 출력 계약 — US-006이 그대로 노출):
    {"results": [{"text": str, "source": str, "score": float}, ...], "count": int}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from knowledge.chunk import Chunk


# dense 1차 회수 후보 수·최종 반환 상한의 기본값(endpoint.yaml knowledge에서 덮어씀).
_DEFAULT_TOP_N = 20
_DEFAULT_TOP_K = 5


class Reranker(Protocol):
    """리랭커 제공자 계약 — BGEClient.rerank(query, documents)와 동일 시그니처.

    응답은 배포별로 dict 또는 list다(점수 키·정렬 여부도 제각각).
    테스트는 이 계약을 만족하는 결정론 mock을 주입한다(실서버 불요).
    """

    def rerank(
        self, query: str, documents: list[str]
    ) -> dict[str, Any] | list[Any]: ...


class DenseStore(Protocol):
    """dense 검색 제공자 계약 — KnowledgeStore.search_dense와 동일 시그니처."""

    def search_dense(
        self, query: str, top_n: int = 20
    ) -> list[tuple[Chunk, float]]: ...


def search(
    query: str,
    k: int = 5,
    filter: dict[str, Any] | None = None,
    store: DenseStore | None = None,
    reranker: Reranker | None = None,
) -> dict[str, Any]:
    """질의에 대해 dense→리랭커 파이프라인으로 상위 k개 청크를 찾는다.

    Args:
        query: 검색 질의. 빈 문자열·공백뿐이면 빈 결과를 안전 반환(호출 0).
        k: 최종 반환 상한(top-k). 1 미만이면 빈 결과. 5를 넘겨도 그대로 상한.
        filter: 후보를 좁히는 선택 조건. {"source": "wiki/x.md"}면 그 출처만,
            그 외 키는 chunk.meta[키]==값으로 맞춘다(리랭크 전에 적용). None이면 전체.
        store: dense 검색 제공자. None이면 KnowledgeStore() 기본(endpoint.yaml).
        reranker: 리랭커 제공자. None이면 BGEClient() 기본.

    Returns:
        {"results": [{"text", "source", "score"}, ...], "count": int}
        — results는 리랭커 점수 내림차순 상위 k개. 후보가 없으면 count=0.
    """
    if not query or not query.strip() or k < 1:
        return {"results": [], "count": 0}

    if store is None:
        from knowledge.store import KnowledgeStore

        store = KnowledgeStore()

    top_n = _default_top_n()
    candidates = store.search_dense(query, top_n=top_n)
    if filter:
        candidates = [c for c in candidates if _matches_filter(c[0], filter)]
    if not candidates:
        return {"results": [], "count": 0}

    if reranker is None:
        from core.bge_client import BGEClient

        reranker = BGEClient()

    documents = [chunk.text for chunk, _ in candidates]
    raw = reranker.rerank(query, documents)
    ranked = _normalize_rerank(raw, n_docs=len(documents))

    limit = min(k, _DEFAULT_TOP_K)
    results: list[dict[str, Any]] = []
    for idx, score in ranked[:limit]:
        chunk = candidates[idx][0]
        results.append(
            {
                "text": chunk.text,
                "source": chunk.source,
                "score": score,
                "provenance": _chunk_provenance(chunk),
            }
        )
    return {"results": results, "count": len(results)}


def _chunk_provenance(chunk: Chunk) -> str | None:
    """청크 meta 에서 출처 신뢰표시 문자열을 뽑는다(P8 E-1 전파·§11.1①→②).

    P9(knowledge_lifecycle.capture_and_index)는 ``chunk.meta['provenance']`` 에
    ArtifactProvenance.model_dump() 즉 dict({"provenance":"untrusted","source_url":...})
    를 저장한다 → 안쪽 ``provenance`` 값(문자열)을 푼다. 값이 평문 문자열인 변형도 흡수.
    meta 에 키가 없으면(legacy ~178pt pre-P9 코퍼스) None → 소비자가 fail-safe untrusted
    로 간주(spotlight.py). 검색 행위는 0 변경(add-only).
    """
    raw = chunk.meta.get("provenance")
    if isinstance(raw, dict):
        raw = raw.get("provenance")
    if raw is None:
        return None
    val = str(raw).strip()
    return val or None


def _default_top_n() -> int:
    """endpoint.yaml knowledge.top_n(없으면 20)을 읽는다(1차 회수 폭)."""
    from core.config import load_endpoint_config

    cfg = load_endpoint_config().get("knowledge", {}) or {}
    return int(cfg.get("top_n", _DEFAULT_TOP_N))


def _matches_filter(chunk: Chunk, filter: dict[str, Any]) -> bool:
    """청크가 filter 조건을 모두 만족하는지 본다(source는 필드, 나머지는 meta)."""
    for key, value in filter.items():
        if key == "source":
            if chunk.source != value:
                return False
        elif chunk.meta.get(key) != value:
            return False
    return True


def _normalize_rerank(
    raw: dict[str, Any] | list[Any], n_docs: int
) -> list[tuple[int, float]]:
    """리랭커 응답을 (원본 문서 index, 점수) 목록으로 정규화한다 — 점수 내림차순.

    리랭커 응답은 배포마다 형식이 다르다(test_rerank.py 계약 = dict 또는 list).
    실측·테스트에서 확인된 형식을 모두 한 형태로 흡수한다:

      (가) dict + "results" 키(실서버 capture):
           {"results": [{"index": 0, "score": 7.53, "document": "..."}, ...]}
      (나) bare list(목 서버·test_bge_client):
           [{"index": i, "score": 0.9}, ...]  또는  "relevance_score" 키
      (다) 점수만 든 평행 리스트(혹시 모를 변형):
           [0.9, 0.2, ...] 또는 {"scores": [0.9, ...]}

    공통 규약: 각 원소가 원본 documents의 몇 번째인지(index)와 그 점수를 뽑아
    점수 내림차순으로 줄 세운다. index가 없으면 응답 순서를 원본 순서로 본다.
    index가 범위를 벗어나거나 점수를 못 읽는 원소는 건너뛴다(견고성).

    Args:
        raw: rerank 응답 원본(dict 또는 list).
        n_docs: 원본으로 보낸 documents 개수(index 범위 검증용).

    Returns:
        (index, score) 목록 — 점수 내림차순. 못 읽으면 빈 목록(검색이 빈 결과로).
    """
    items = _extract_items(raw)
    scored: list[tuple[int, float]] = []
    for position, item in enumerate(items):
        idx = _extract_index(item, position)
        score = _extract_score(item)
        if idx is None or score is None:
            continue
        if not (0 <= idx < n_docs):
            continue
        scored.append((idx, score))
    # 같은 index가 중복되면 첫(점수 높은) 것만 — 정렬 후 dedup.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    seen: set[int] = set()
    deduped: list[tuple[int, float]] = []
    for idx, score in scored:
        if idx in seen:
            continue
        seen.add(idx)
        deduped.append((idx, score))
    return deduped


def _extract_items(raw: dict[str, Any] | list[Any]) -> list[Any]:
    """응답에서 '문서당 점수' 원소 목록을 꺼낸다(dict면 results/data/scores 키)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("results", "data", "scores"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_index(item: Any, position: int) -> int | None:
    """원소에서 원본 문서 index를 뽑는다. 없으면 응답 위치(position)를 쓴다."""
    if isinstance(item, dict):
        for key in ("index", "idx", "document_index", "corpus_id"):
            if key in item:
                try:
                    return int(item[key])
                except (TypeError, ValueError):
                    return None
        # index 키가 없는 dict(점수만 든 형태)는 응답 순서를 원본 순서로 본다.
        return position
    # dict가 아니면(점수 스칼라 평행 리스트) 응답 순서가 곧 원본 순서.
    return position


def _extract_score(item: Any) -> float | None:
    """원소에서 점수를 뽑는다(dict면 score/relevance_score 키, 아니면 스칼라)."""
    if isinstance(item, dict):
        for key in ("score", "relevance_score", "relevance"):
            if key in item:
                try:
                    return float(item[key])
                except (TypeError, ValueError):
                    return None
        return None
    try:
        return float(item)
    except (TypeError, ValueError):
        return None
