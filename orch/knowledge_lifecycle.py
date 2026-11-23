"""SR/renewal P2-rest-A US-A5 — 지식 생애주기(§18.1 provenance · §18.2 Capture→Index→Retrieve).

아티팩트를 frontmatter provenance(trusted/untrusted + source_url · §11.1 IPI 토대)와 함께
Capture하고 Qdrant에 Index(store.upsert·증분 색인)한다. emit 직후 search_hybrid로 회수됨
(searchable-after-emit · §18.2 Index→Retrieve)을 보장한다 — asserted가 아니라 demonstrated.

store는 주입(duck-typed: ``upsert(chunks)->int`` · ``search_hybrid(query, top_n)->[(Chunk,float)]``).
코어 결합 최소(KnowledgeStore 직접 import 안 함). Chunk 구성만 knowledge.chunk 재사용(지연).
"""

from __future__ import annotations

from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

Provenance = Literal["trusted", "untrusted"]

# §18.1 frontmatter 직렬화 키 순서.
_FM_ORDER: tuple[str, ...] = ("provenance", "source_url", "author", "created_at")


class ArtifactProvenance(BaseModel):
    """§18.1 frontmatter provenance — 전파 신뢰표시(§11.1 IPI 토대).

    외부 적재물 = ``untrusted`` + ``source_url`` 필수(출처 검증 가능). 내부 산출물 = ``trusted``.
    """

    model_config = ConfigDict(extra="ignore")

    provenance: Provenance
    source_url: str | None = None
    author: str | None = None
    created_at: str | None = None

    @model_validator(mode="after")
    def _untrusted_needs_source(self) -> "ArtifactProvenance":
        if self.provenance == "untrusted" and not self.source_url:
            raise ValueError(
                "untrusted 적재물은 source_url 필수(§11.1 출처 검증·전파는 신뢰가 아니라 검증)"
            )
        return self

    def frontmatter(self) -> dict:
        """§18.1 필드를 정본 순서의 dict 로."""
        data = self.model_dump()
        return {key: data[key] for key in _FM_ORDER}


def render_provenance(p: ArtifactProvenance) -> str:
    """provenance를 YAML frontmatter 마크다운 헤더로 렌더(§18.1·결정론)."""
    fm = yaml.safe_dump(p.frontmatter(), allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n"


def parse_provenance(text: str) -> ArtifactProvenance:
    """frontmatter에서 provenance 복원 — ``parse_provenance(render_provenance(p)) == p``."""
    return ArtifactProvenance.model_validate(_extract_frontmatter(text))


def _extract_frontmatter(text: str) -> dict:
    """선두 ``---`` … ``---`` 블록을 YAML 매핑으로. 없으면 ``ValueError``."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter 시작 구분자 '---' 없음")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter 종료 구분자 '---' 없음")
    data = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter 가 매핑(dict)이 아님")
    return data


class _Store(Protocol):
    """주입 store 계약 — KnowledgeStore 또는 mock(코어 결합 최소·duck-typed)."""

    def upsert(self, chunks: list) -> int: ...

    def search_hybrid(self, query: str, top_n: int = 20) -> list: ...


def capture_and_index(
    store: _Store,
    text: str,
    source: str,
    provenance: ArtifactProvenance,
    chunk_id: int = 0,
) -> int:
    """§18.2 Capture→Index: 아티팩트를 provenance와 함께 Qdrant에 증분 색인(store.upsert).

    provenance를 ``chunk.meta['provenance']`` 에 실어 검색 결과가 출처 신뢰도를 동반하게 한다
    (§11.1 untrusted spotlighting 토대). 반환 = 업서트된 포인트 수.
    """
    from knowledge.chunk import Chunk

    chunk = Chunk(
        text=text,
        source=source,
        chunk_id=chunk_id,
        meta={"provenance": provenance.model_dump()},
    )
    return store.upsert([chunk])


def is_searchable(store: _Store, unique_token: str, top_n: int = 20) -> bool:
    """§18.2 Retrieve: 고유 토큰이 search_hybrid로 회수되는지(searchable-after-emit 확인).

    emit한 아티팩트의 고유 토큰을 질의해 결과 본문에 그 토큰이 든 청크가 있으면 True
    — Index→Retrieve 왕복이 *실제로* 닫혔음을 입증(asserted 아님).
    """
    return any(
        unique_token in c.text
        for c, _ in store.search_hybrid(unique_token, top_n=top_n)
    )
