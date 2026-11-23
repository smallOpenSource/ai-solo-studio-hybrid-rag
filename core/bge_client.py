"""BGE 임베딩·리랭크 클라이언트 — 커스텀 포맷 어댑터 (단일 진실).

Phase 0에서 bench/bge_client.py로 만든 BGEClient를 core로 승격한 것.
bench·knowledge가 같은 코드를 import 한다(복사 금지). bench.bge_client는
이 모듈을 재export 하므로 기존 import(bench.run 등)는 무변경으로 동작한다.
행위 변경 없이 bench/에서 그대로 옮긴 순수 이동(import 경로만 core.config로).
"""

from __future__ import annotations

from typing import Any

import httpx

from core.config import load_endpoint_config, load_env


_DEFAULT_TIMEOUT = 180.0


class BGEClient:
    """BGE-M3-ko 임베딩·리랭크 HTTP 클라이언트.

    요청/응답 형식:
      embed:  POST {"model": "...", "texts": [...]} → {"dense_embeddings": [[float]]}
      rerank: POST {"query": "...", "documents": [...]} → dict | list
    """

    def __init__(
        self,
        env: dict[str, str] | None = None,
    ) -> None:
        e = env or load_env()
        cfg = load_endpoint_config()
        bge = cfg["bge"]

        self._embed_url: str = e.get(bge["embedding_url_env"], "")
        self._embed_key: str = e.get(bge["embedding_key_env"], "")
        self._embed_model: str = e.get(bge["embedding_model_env"], "")
        self._rerank_url: str = e.get(bge["rerank_url_env"], "")
        self._rerank_key: str = e.get(bge["rerank_key_env"], "")

        embed_timeout = float(
            e.get("BGE_EMBEDDING_TIMEOUT", str(_DEFAULT_TIMEOUT)) or _DEFAULT_TIMEOUT
        )
        rerank_timeout = float(
            e.get("BGE_RERANK_TIMEOUT", str(_DEFAULT_TIMEOUT)) or _DEFAULT_TIMEOUT
        )
        self._embed_timeout = embed_timeout
        self._rerank_timeout = rerank_timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩 벡터로 변환한다.

        Returns:
            list[list[float]] — texts와 동일 순서
        """
        headers = {
            "Authorization": f"Bearer {self._embed_key}",
            "Content-Type": "application/json",
        }
        body = {"model": self._embed_model, "texts": texts}
        # connect/read 분리: connect hang(서버 다운/방화벽)에 read 상한(기본 180s,
        # BGE_EMBEDDING_TIMEOUT env override 유지)만큼 매달리지 않게 connect=10s로
        # 빠르게 실패. read는 임베딩 생성 시간이라 기존 값 유지(정상 동작 무변).
        timeout = httpx.Timeout(
            connect=10.0, read=self._embed_timeout, write=10.0, pool=10.0
        )
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(self._embed_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        embeddings: list[list[float]] = data["dense_embeddings"]
        return embeddings

    def rerank(
        self,
        query: str,
        documents: list[str],
    ) -> dict[str, Any] | list[Any]:
        """문서 목록을 쿼리 관련도 순으로 재정렬한다.

        Returns:
            서버 응답 원본 (dict 또는 list — 배포별 형식이 다름)
        """
        headers = {
            "Authorization": f"Bearer {self._rerank_key}",
            "Content-Type": "application/json",
        }
        body = {"query": query, "documents": documents}
        # connect/read 분리(embed와 동일 논리): connect=10s 빠른 실패,
        # read는 리랭크 시간(기본 180s, BGE_RERANK_TIMEOUT env override 유지).
        timeout = httpx.Timeout(
            connect=10.0, read=self._rerank_timeout, write=10.0, pool=10.0
        )
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(self._rerank_url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()
