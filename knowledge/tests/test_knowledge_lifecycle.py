"""P2-rest-A US-A5 — 지식 생애주기(§18.1 provenance · §18.2 searchable-after-emit) 단위.

knowledge env·:memory: store·mock embedder(실 bge 0). orch.knowledge_lifecycle를 실 store로 운동.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from knowledge.store import KnowledgeStore  # noqa: E402
from orch.knowledge_lifecycle import (  # noqa: E402
    ArtifactProvenance,
    capture_and_index,
    is_searchable,
    parse_provenance,
    render_provenance,
)


class _MockEmbedder:
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


# ── §18.1 provenance frontmatter ──────────────────────────────────────────
def test_provenance_roundtrip_trusted():
    p = ArtifactProvenance(provenance="trusted", author="DEV", created_at="2026-06-22")
    assert parse_provenance(render_provenance(p)) == p


def test_untrusted_requires_source_url():
    with pytest.raises(ValidationError):
        ArtifactProvenance(provenance="untrusted")  # source_url 없음 → §11.1 위반
    ok = ArtifactProvenance(provenance="untrusted", source_url="https://example.com/x")
    assert ok.source_url == "https://example.com/x"


def test_provenance_roundtrip_untrusted_with_source():
    p = ArtifactProvenance(
        provenance="untrusted", source_url="https://en.wikipedia.org/wiki/X"
    )
    assert parse_provenance(render_provenance(p)) == p


# ── §18.2 Capture/Index + searchable-after-emit ───────────────────────────
def test_capture_and_index_carries_provenance():
    s = _store()
    prov = ArtifactProvenance(provenance="trusted", author="RESEARCH")
    n = capture_and_index(
        s, "코어 루프 설계 노트", "vault/research/note.md", prov, chunk_id=0
    )
    assert n == 1 and s.count() == 1


def test_searchable_after_emit_roundtrip():
    # §18.2 Index→Retrieve: emit 직후 고유 토큰이 search_hybrid로 회수됨(왕복 입증).
    s = _store()
    token = "zqx9lifecycletoken"
    capture_and_index(
        s,
        f"증분 색인 검증 아티팩트 {token} 내용",
        "vault/quality/emit.md",
        ArtifactProvenance(provenance="trusted"),
        chunk_id=0,
    )
    assert is_searchable(s, token) is True
    # 색인 안 된 토큰은 회수 안 됨(대조).
    assert is_searchable(s, "absenttoken404zz") is False
