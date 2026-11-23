"""External-component test: BGE-M3-ko embeddings endpoint.

Mirrors M1.8 boot probe #5: dim=1024.

NOTE: BGE-M3-ko at studio.example.com:48802 is **NOT OpenAI-compatible** despite the
URL suffix `/embeddings`. The wire format is custom:
  Request:  {"model": "bge-m3-ko", "texts": [...]}    (NOT `input`)
  Response: {"dense_embeddings": [[1024 floats], ...]} (NOT `data[].embedding`)

This test asserts the actual contract; api/services/bge_client.py must adapt
this shape into the downstream embedding list[list[float]] form. See
memory/project_external_dependencies.md for the corrected spec.
"""

from __future__ import annotations

import httpx
import pytest

EXPECTED_DIM = 1024  # Single source of truth shared with api/app/config.py


@pytest.mark.asyncio
async def test_bge_env_vars_present(env: dict[str, str]) -> None:
    for key in (
        "BGE_EMBEDDING_API_URL",
        "BGE_EMBEDDING_API_KEY",
        "BGE_EMBEDDING_MODEL",
    ):
        assert env.get(key), f"{key} must be set"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_bge_embeddings_shape_and_dim(env: dict[str, str]) -> None:
    timeout = int(env.get("BGE_EMBEDDING_TIMEOUT", "30") or 30)
    headers = {
        "Authorization": f"Bearer {env['BGE_EMBEDDING_API_KEY']}",
        "Content-Type": "application/json",
    }
    inputs = ["ping", "pong"]
    body = {"model": env["BGE_EMBEDDING_MODEL"], "texts": inputs}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            env["BGE_EMBEDDING_API_URL"], headers=headers, json=body
        )
    assert response.status_code == 200, response.text[:500]
    data = response.json()
    assert "dense_embeddings" in data, (
        f"missing 'dense_embeddings' key (BGE custom format); got {list(data)[:10]}"
    )
    embeddings = data["dense_embeddings"]
    assert isinstance(embeddings, list) and len(embeddings) == len(inputs), (
        f"expected {len(inputs)} embeddings, got {len(embeddings) if isinstance(embeddings, list) else type(embeddings).__name__}"
    )
    first = embeddings[0]
    assert isinstance(first, list), f"expected list[float], got {type(first).__name__}"
    assert len(first) == EXPECTED_DIM, f"expected dim={EXPECTED_DIM}, got {len(first)}"
    assert all(isinstance(x, (int, float)) for x in first[:8]), (
        f"non-numeric values in embedding head: {first[:8]}"
    )
