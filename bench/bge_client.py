"""BGE 임베딩·리랭크 클라이언트 — core.bge_client로 승격됨(재export, 무중단).

실제 구현은 core/bge_client.py에 있다. 기존 공개 이름은 여기서 그대로
재노출하여 bench.bge_client를 import 하던 코드·테스트(bench.run 등)가
무변경으로 동작한다. (bench.config가 core.config를 재export 하는 선례와 동일.)
"""

from __future__ import annotations

from core.bge_client import BGEClient

__all__ = ["BGEClient"]
