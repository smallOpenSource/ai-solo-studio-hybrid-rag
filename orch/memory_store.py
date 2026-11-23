"""orch/memory_store.py — §10 LangGraph Store 세션간 메모리 (큐레이션 장기기억).

§10 3계층 책임 경계:
  - memory_store (본 모듈): 큐레이션 장기메모리 — 부서 산출물/intake 결과 영속
  - orch.state_store (aigs:*): 실행 상태 — task 큐/agent status/blackboard
  - knowledge (Qdrant): 검색 인덱스 — 시맨틱 검색/하이브리드 리트리벌

``InMemoryStore`` 를 래핑하며 namespace 분리(tuple)로 dept/intake 가 독립 키스페이스를
사용한다. 예: ``("intake", "research")`` 는 intake 큐에서 처리된 research 산출물.

Redis Store 백엔드: AsyncRedisStore(langgraph-checkpoint-redis) 로 교체 가능 — 동일
put/get 인터페이스 유지(OBSERVE/Follow-up·현재 InMemory).
"""

from __future__ import annotations

from typing import Any

from langgraph.store.memory import InMemoryStore

_DEFAULT_STORE: InMemoryStore | None = None


class MemoryStore:
    """§10 LangGraph InMemoryStore 래퍼 — namespace 분리 큐레이션 장기메모리.

    namespace: tuple[str, ...] — 예 ("intake", "research")·("org", "artifacts")
    key: str — 산출물 식별자
    value: dict — 구조화 페이로드(자유형식 prose 금지·구조화 dict 필수)

    ★인스턴스 간 공유: 기본 ``get_default()`` 팩토리로 프로세스 단위 단일 스토어 공유.
    독립 인스턴스는 ``MemoryStore()`` 직접 생성(테스트 격리 용도).
    """

    def __init__(self) -> None:
        self._store = InMemoryStore()

    async def put(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any]
    ) -> None:
        """namespace + key 에 value 저장(upsert).

        ★value 는 구조화 dict 필수 — str/None 허용 안 함(§10 큐레이션 규약).
        """
        await self._store.aput(namespace, key, value)

    async def get(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        """namespace + key 에서 value 회수. 미존재 시 None(graceful).

        반환: dict(value 필드) 또는 None. InMemoryStore.Item 래핑 제거.
        """
        item = await self._store.aget(namespace, key)
        if item is None:
            return None
        return item.value


def get_default() -> MemoryStore:
    """프로세스 단위 기본 MemoryStore 인스턴스 반환(lazy 생성·싱글톤).

    테스트는 독립 ``MemoryStore()`` 인스턴스를 직접 생성해 격리한다.
    """
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = MemoryStore()
    return _DEFAULT_STORE
