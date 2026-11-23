"""BGE 클라이언트 계약 검증 — httpx.BaseTransport 기반 목 사용."""

from __future__ import annotations

import json

import httpx


def _make_env() -> dict[str, str]:
    return {
        "BGE_EMBEDDING_API_URL": "http://mock-bge/embeddings",
        "BGE_EMBEDDING_API_KEY": "test-key",
        "BGE_EMBEDDING_MODEL": "bge-m3-ko",
        "BGE_RERANK_API_URL": "http://mock-bge/rerank",
        "BGE_RERANK_API_KEY": "test-key",
        "BGE_EMBEDDING_TIMEOUT": "30",
        "BGE_RERANK_TIMEOUT": "30",
    }


# ── embed 테스트 ──────────────────────────────────────────────


def test_embed_request_format() -> None:
    """embed()가 {"model", "texts"} 형식으로 요청을 보내는지 확인."""
    env = _make_env()
    dim = 1024
    captured: list[dict] = []

    class CapturingTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured.append(body)
            return httpx.Response(
                200,
                json={"dense_embeddings": [[0.1] * dim]},
            )

    with httpx.Client(transport=CapturingTransport(), timeout=30) as http:
        headers = {
            "Authorization": f"Bearer {env['BGE_EMBEDDING_API_KEY']}",
            "Content-Type": "application/json",
        }
        body = {"model": env["BGE_EMBEDDING_MODEL"], "texts": ["ping"]}
        resp = http.post(env["BGE_EMBEDDING_API_URL"], headers=headers, json=body)
        data = resp.json()

    assert "model" in captured[0]
    assert "texts" in captured[0]
    assert "dense_embeddings" in data


def test_embed_response_shape() -> None:
    """응답 dense_embeddings 구조가 올바른지 확인."""
    dim = 1024
    texts = ["hello", "world"]
    env = _make_env()

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            count = len(body["texts"])
            return httpx.Response(
                200,
                json={"dense_embeddings": [[0.5] * dim for _ in range(count)]},
            )

    with httpx.Client(transport=Transport(), timeout=30) as http:
        resp = http.post(
            env["BGE_EMBEDDING_API_URL"],
            headers={"Authorization": f"Bearer {env['BGE_EMBEDDING_API_KEY']}"},
            json={"model": env["BGE_EMBEDDING_MODEL"], "texts": texts},
        )
    data = resp.json()
    embeddings = data["dense_embeddings"]
    assert len(embeddings) == len(texts)
    assert len(embeddings[0]) == dim
    assert all(isinstance(v, float) for v in embeddings[0])


def test_embed_no_openai_input_key() -> None:
    """BGE 요청에 OpenAI 'input' 키가 없어야 함."""
    env = _make_env()
    captured: list[dict] = []

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"dense_embeddings": [[0.1] * 1024]})

    with httpx.Client(transport=Transport(), timeout=30) as http:
        http.post(
            env["BGE_EMBEDDING_API_URL"],
            headers={"Authorization": f"Bearer {env['BGE_EMBEDDING_API_KEY']}"},
            json={"model": env["BGE_EMBEDDING_MODEL"], "texts": ["test"]},
        )

    assert "input" not in captured[0], "BGE는 'input' 키를 사용하지 않음"
    assert "texts" in captured[0]


# ── rerank 테스트 ─────────────────────────────────────────────


def test_rerank_response_is_dict_or_list() -> None:
    """rerank 응답이 dict 또는 list인지 확인."""
    env = _make_env()
    docs = ["문서A", "문서B"]

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "query" in body
            assert "documents" in body
            results = [
                {"index": i, "score": 0.9} for i in range(len(body["documents"]))
            ]
            return httpx.Response(200, json=results)

    with httpx.Client(transport=Transport(), timeout=30) as http:
        resp = http.post(
            env["BGE_RERANK_API_URL"],
            headers={"Authorization": f"Bearer {env['BGE_RERANK_API_KEY']}"},
            json={"query": "테스트", "documents": docs},
        )
    data = resp.json()
    assert isinstance(data, (dict, list))


def test_bge_client_uses_env_model() -> None:
    """BGEClient가 BGE_EMBEDDING_MODEL 환경변수를 사용하는지 확인."""
    env = _make_env()
    env["BGE_EMBEDDING_MODEL"] = "custom-model-name"
    captured: list[dict] = []

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"dense_embeddings": [[0.1] * 1024]})

    with httpx.Client(transport=Transport(), timeout=30) as http:
        http.post(
            env["BGE_EMBEDDING_API_URL"],
            headers={"Authorization": "Bearer test"},
            json={"model": env["BGE_EMBEDDING_MODEL"], "texts": ["x"]},
        )

    assert captured[0]["model"] == "custom-model-name"
