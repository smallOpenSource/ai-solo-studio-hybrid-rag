"""Shared deterministic fixtures for orch tests (model STUBBED — no live gemma).

Every test drives the graph through a deterministic stub caller bound via
``orch.graph.use_caller`` (unit) or injected into ``create_app(caller=...)``
(integration), so the suite is fully deterministic and never touches the live
endpoint. ``slow``/live e2e is a separate lane (drive_cycle.py).
"""

from __future__ import annotations

import hashlib

import pytest


class FakeEmbedder:
    """토큰해시→고정벡터 결정론 embedder(dep-0·실 bge 미접촉). evals 테스트 공유 —
    Qdrant 색인/검색의 *배관·scorer 로직* 검증용(검색 *관련성*은 검증 못 함·M-1·실 bge=OBSERVE)."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            out.append([h[i % len(h)] / 255.0 for i in range(self.dim)])
        return out


def stub_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """Deterministic model stub keyed on the node's prompt (no network).

    Returns a small, contract-shaped artifact for each stage so the graph routes
    to BUILD, threads artifacts, passes QA, and produces a BLUF starting with
    '결론'. Same input -> same output (determinism). The research/knowledge step
    does NOT go through this caller — it uses the injected ``stub_searcher``.
    """
    p = prompt.lower()
    if "ceo router" in p:
        return "ROUTE: build | clicker game requested"
    if "design stage" in p:
        return "core loop: click. reward: +1 coin. upgrade: auto-clicker."
    if "content stage" in p:
        return "Name: Clicky Quest. items: Cursor(10) Grandma(20) Factory(30)"
    if "qa stage" in p:
        return "check1: loop ok. check2: costs ok. risk: balance. VERDICT: PASS"
    if "bluf" in p:
        return (
            "결론: 클리커 게임 컨셉 승인 가능.\n"
            "필요행동: 없음 — 다음 단계 진행.\n"
            "근거:\n- research: F2P 참여 설계 (https://example.test/wiki/F2P)\n"
            "- design: 코어 루프 명확\n- content: 업그레이드 3종\n- qa: PASS"
        )
    return "stub-artifact"


_STUB_SOURCE = "https://example.test/wiki/Free-to-play"


def stub_searcher(query: str, k: int) -> dict:
    """Deterministic knowledge searcher (no network) — returns a citable source.

    Mirrors the office.knowledge_client.search contract
    ({"results": [{"text","source","score"}], "count": int}) so stage_research
    records a real-shaped citation without touching the live knowledge service.
    """
    return {
        "results": [
            {
                "text": "F2P games are designed around sustained engagement.",
                "source": _STUB_SOURCE,
                "score": -0.5,
            }
        ],
        "count": 1,
    }


def empty_searcher(query: str, k: int) -> dict:
    """Searcher returning an empty corpus result (degrade-path coverage)."""
    return {"results": [], "count": 0}


def raising_searcher(query: str, k: int) -> dict:
    """Searcher that raises (knowledge service down) — node must not crash."""
    raise RuntimeError("knowledge service unreachable")


def route_end_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """Stub whose CEO route answers 'end' (non-build directive)."""
    p = prompt.lower()
    if "ceo router" in p:
        return "ROUTE: end | not a game build request"
    return "stub-artifact"


@pytest.fixture
def stub():
    """The default deterministic stub caller."""
    return stub_caller


@pytest.fixture
def end_stub():
    """A stub caller that routes to END."""
    return route_end_caller


@pytest.fixture
def searcher():
    """The default deterministic knowledge searcher (returns a citable source)."""
    return stub_searcher
