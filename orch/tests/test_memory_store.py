"""US-9d-2 — §10 LangGraph Store 세션간 메모리 (u7).

★내용 왕복 검증(falsifiable): 실 value put → 새 인스턴스/thread에서 get 회수.
빈 put/get 아님 — 실 value 일치를 단언한다.

§10 3계층 책임 경계(memory_store.py docstring ADR):
  - MemoryStore: 큐레이션 장기메모리 (본 모듈)
  - state_store: 실행 상태 (aigs:* Redis)
  - knowledge/Qdrant: 검색 인덱스

결정론·LLM 0·orch env.
pytest-asyncio 미설치 → asyncio.run 구동.
"""

from __future__ import annotations

import asyncio

from orch.memory_store import MemoryStore, get_default


# ── 기본 put/get 왕복 ───────────────────────────────────────────────────────


def test_put_get_roundtrip():
    """put(namespace, key, value) → get(namespace, key) 실 value 회수."""
    store = MemoryStore()

    async def run():
        payload = {
            "dept": "research",
            "directive": "시장 조사",
            "finding": "경쟁사 3사 분석 완료",
            "source": "intake",
        }
        await store.put(("intake", "research"), "run-001", payload)
        result = await store.get(("intake", "research"), "run-001")
        assert result is not None, "put 후 get이 None 반환 — 왕복 실패"
        assert result == payload, f"반환값 불일치: {result} != {payload}"

    asyncio.run(run())


def test_put_get_different_namespaces():
    """namespace 분리: 다른 namespace에 같은 key → 독립 저장."""
    store = MemoryStore()

    async def run():
        v1 = {"dept": "intake", "finding": "intake 산출물"}
        v2 = {"dept": "art", "finding": "art 산출물"}
        await store.put(("intake", "intake"), "k1", v1)
        await store.put(("org", "art"), "k1", v2)
        r1 = await store.get(("intake", "intake"), "k1")
        r2 = await store.get(("org", "art"), "k1")
        assert r1 == v1
        assert r2 == v2
        assert r1 != r2, "namespace 분리 실패: 다른 namespace가 같은 값 반환"

    asyncio.run(run())


def test_get_missing_key_returns_none():
    """미존재 key → None 반환(graceful·예외 0)."""
    store = MemoryStore()

    async def run():
        result = await store.get(("intake", "research"), "nonexistent-key")
        assert result is None

    asyncio.run(run())


def test_get_missing_namespace_returns_none():
    """미존재 namespace → None 반환(graceful)."""
    store = MemoryStore()

    async def run():
        result = await store.get(("never", "existed"), "k")
        assert result is None

    asyncio.run(run())


# ── ★핵심: 새 인스턴스/thread 회수 (InMemoryStore = 인스턴스 범위) ─────────


def test_new_instance_isolated():
    """★InMemoryStore: 각 MemoryStore 인스턴스는 독립(세션간 공유는 get_default).

    새 인스턴스에서는 이전 인스턴스의 데이터를 get 불가 — 설계 명시.
    세션간 공유가 필요하면 get_default() 사용.
    """
    store1 = MemoryStore()
    store2 = MemoryStore()

    async def run():
        await store1.put(("intake", "research"), "shared-key", {"data": "value"})
        # store2는 store1과 독립 인스턴스 → None
        result = await store2.get(("intake", "research"), "shared-key")
        assert result is None, "독립 인스턴스 간 데이터 노출 — 격리 실패"

    asyncio.run(run())


def test_get_default_singleton_shares_data():
    """★get_default(): 프로세스 단위 공유 스토어 — 세션간 메모리 패턴.

    같은 get_default() 인스턴스에서 put → get 왕복 성공.
    """
    store_a = get_default()
    store_b = get_default()

    async def run():
        payload = {"finding": "get_default 공유 테스트", "run": "singleton"}
        await store_a.put(("org", "singleton-test"), "key-sg", payload)
        result = await store_b.get(("org", "singleton-test"), "key-sg")
        assert result is not None, "get_default() 싱글톤 put→get 왕복 실패"
        assert result["finding"] == payload["finding"]

    asyncio.run(run())


# ── upsert / 덮어쓰기 ────────────────────────────────────────────────────────


def test_put_overwrites_existing():
    """같은 key에 두 번 put → 최신 value 반환(upsert)."""
    store = MemoryStore()

    async def run():
        await store.put(("intake", "dev"), "k", {"v": 1})
        await store.put(("intake", "dev"), "k", {"v": 2})
        result = await store.get(("intake", "dev"), "k")
        assert result == {"v": 2}, "upsert 실패: 이전 값 반환"

    asyncio.run(run())


# ── §10 3계층 책임 경계 ADR 주석 검증 ────────────────────────────────────────


def test_module_docstring_contains_responsibility_boundary():
    """memory_store.py 모듈 docstring에 §10 3계층 책임 경계 언급."""
    import orch.memory_store as mm

    doc = mm.__doc__ or ""
    assert "§10" in doc, "§10 참조 없음"
    assert "state_store" in doc or "aigs" in doc, "state_store 경계 구분 없음"
    assert "Qdrant" in doc or "검색" in doc, "Qdrant 경계 구분 없음"
