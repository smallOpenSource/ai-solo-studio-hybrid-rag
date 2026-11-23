"""P7d — §4.1 Server-A threading.BoundedSemaphore(2) caller-decorator unit.

스펙 §4.1: 서버 A(gemma) 동시 호출 ≤2. ``_active_caller()`` 가 ContextVar caller 를
semaphore-wrap 하여 노드 본문을 byte 보존한 채 동시성을 ≤2 로 바운드한다(org_graph map-reduce
Send fan-out 에서 실제 동시 호출 발생·cycle 직렬은 no-op).

검증: (1) 동시 N>2 호출이 ≤2 로 직렬화 · (2) 노드 반환 후 semaphore release 누수 0 ·
(3) ``_active_caller()`` 가 wrap 된 caller 를 반환(직접 _ACTIVE_CALLER.get() 가 아님).
"""

from __future__ import annotations

import threading
import time

from orch import graph
from orch.graph import _active_caller, _semaphore_wrapped, use_caller


def _run_concurrent(callable_, n: int) -> int:
    """callable_ 을 n 스레드에서 동시 호출하고 관측된 최대 동시 in-flight 를 반환."""
    state = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def slow(prompt: str, temperature: float, max_tokens: int) -> str:
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        return "ok"

    wrapped = callable_(slow)
    threads = [threading.Thread(target=lambda: wrapped("p", 0.0, 8)) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return state["max"]


def test_semaphore_bounds_concurrency_to_two():
    observed_max = _run_concurrent(_semaphore_wrapped, n=5)
    assert observed_max == 2, f"동시 in-flight 가 2 로 바운드되지 않음: {observed_max}"


def test_semaphore_no_release_leak():
    # 다회 호출 후 세마포어 값이 원복(2) — 누수/이중해제 0.
    # BoundedSemaphore 는 과해제 시 ValueError 를 던지므로 with 가 균형 보장.
    _run_concurrent(_semaphore_wrapped, n=4)
    # 모든 스레드 종료 후 acquire 가능 횟수가 2 여야 함.
    acquired = []
    for _ in range(2):
        ok = graph._SERVER_A_SEM.acquire(blocking=False)
        acquired.append(ok)
    third = graph._SERVER_A_SEM.acquire(blocking=False)
    for _ in acquired:
        graph._SERVER_A_SEM.release()
    if third:  # 가드: 3번째가 잡혔다면 풀어준다(누수 방지)
        graph._SERVER_A_SEM.release()
    assert acquired == [True, True]  # 2개 슬롯 가용(원복)
    assert third is False  # 3번째는 불가(바운드=2)


def test_active_caller_is_semaphore_wrapped():
    calls = {"n": 0}

    def stub(prompt: str, temperature: float, max_tokens: int) -> str:
        calls["n"] += 1
        return "stub-out"

    with use_caller(stub):
        caller = _active_caller()
        # wrap 되어 ContextVar 의 raw stub 과 동일 객체가 아님.
        assert caller is not stub
        out = caller("hi", 0.0, 8)
    assert out == "stub-out"
    assert calls["n"] == 1
    # 호출 후 세마포어 원복(2 슬롯 가용).
    got = [graph._SERVER_A_SEM.acquire(blocking=False) for _ in range(2)]
    for g in got:
        if g:
            graph._SERVER_A_SEM.release()
    assert got == [True, True]


def test_active_caller_signature_preserved():
    out_holder = {}

    def stub(prompt: str, temperature: float, max_tokens: int) -> str:
        out_holder["args"] = (prompt, temperature, max_tokens)
        return "x"

    with use_caller(stub):
        _active_caller()("prompt-text", 0.3, 64)
    assert out_holder["args"] == ("prompt-text", 0.3, 64)  # 시그니처 보존
