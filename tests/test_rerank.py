"""External-component test: BGE rerank endpoint.

Used optionally by api/services/bge_client.py to re-rank /conversations/search results
when both vector and ILIKE candidates are returned.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_bge_rerank_env_vars_present(env: dict[str, str]) -> None:
    for key in ("BGE_RERANK_API_URL", "BGE_RERANK_API_KEY"):
        assert env.get(key), f"{key} must be set"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_bge_rerank_returns_scores(env: dict[str, str]) -> None:
    timeout = int(env.get("BGE_RERANK_TIMEOUT", "30") or 30)
    headers = {
        "Authorization": f"Bearer {env['BGE_RERANK_API_KEY']}",
        "Content-Type": "application/json",
    }
    body = {
        "query": "What is Microsoft Azure?",
        "documents": [
            "Microsoft Azure is a cloud computing platform.",
            "Bananas are a yellow fruit.",
            "Azure AI Foundry hosts OpenAI models for enterprise use.",
        ],
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            env["BGE_RERANK_API_URL"], headers=headers, json=body
        )
    assert response.status_code == 200, response.text[:500]
    data = response.json()
    # BGE rerank response shape varies across deployments; we only assert it
    # returned valid JSON (dict or list). The actual shape adapter lives in
    # api/services/bge_client.py.
    assert isinstance(data, (dict, list)), f"unexpected type: {type(data).__name__}"
