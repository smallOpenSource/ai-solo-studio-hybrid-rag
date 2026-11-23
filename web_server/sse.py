"""인메모리 fan-out 큐 — EventStore를 폴링해 신규 이벤트를 구독자에게 배포.

설계(계획 §4.3 결정 B1):
- EventStore.read_since(seq)로 seq 증분 폴링 → 신규 (seq, payload_json)을
  인메모리 리스트에 적재. 외부 브로커 0(순수 인메모리).
- 구독자(SSE 연결)는 last_seq를 주고 등록(subscribe)하면 그 이후 이벤트를
  처음부터 재생받을 수 있다. 등록 해제(unsubscribe)도 제공.
- 엔진 비침투: office의 EventStore를 받아 읽기만 한다. office는 이 모듈을
  전혀 모른다(단방향 의존).

동시성 주의(불변 제약):
- 이 fan-out은 단순 큐다. threading/asyncio 같은 동시성 코드 경로를 두지
  않는다(워크플로 스레드 격리는 US-004 runner의 책임이지 여기가 아니다).
- 이 스토리에서는 폴링을 수동 tick(poll_once) 단위로 호출해 테스트한다.
  실제 백그라운드 주기 구동(0.5s 루프)은 US-002 SSE 엔드포인트가 배선한다.
"""

from __future__ import annotations

import asyncio

from office.event_store import EventStore

# 기본 폴링 간격(초). 실제 루프는 US-002가 이 값으로 돌린다.
DEFAULT_POLL_INTERVAL = 0.5


class EventFanout:
    """EventStore를 폴링해 신규 이벤트를 인메모리 큐에 모으고 구독자에게 배포한다.

    사용 예:
        fanout = EventFanout(store)
        fanout.poll_once()                 # 신규 이벤트를 큐에 적재
        new_events = fanout.subscribe(last_seq=0)  # last_seq 이후부터 재생

    공개 API(US-002가 배선할 계약):
        poll_once() -> list[tuple[int, str]]   # 신규 (seq, payload_json) 반환·적재
        subscribe(last_seq: int) -> list[tuple[int, str]]  # last_seq 이후 재생
        unsubscribe(token) -> None             # 구독 해제
        last_seq -> int (property)             # 지금까지 본 최대 seq
    """

    def __init__(
        self,
        store: EventStore,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """
        Args:
            store: 폴링 대상 EventStore. 테스트는 tmp_path db를 주입한다.
            poll_interval: 폴링 간격(초). 큐 자체는 이 값을 쓰지 않고,
                           US-002의 백그라운드 루프가 참고한다.
        """
        self._store = store
        self._poll_interval = poll_interval
        # 지금까지 폴링으로 읽어들인 신규 이벤트 큐 (seq, payload_json) 누적.
        self._queue: list[tuple[int, str]] = []
        # 마지막으로 본 최대 seq — 다음 폴링의 커서.
        self._last_seq = 0
        # 활성 구독자 토큰 집합 (등록/해제 추적용). 토큰은 단순 정수.
        self._subscribers: set[int] = set()
        self._next_token = 1

    @property
    def poll_interval(self) -> float:
        """폴링 간격(초)."""
        return self._poll_interval

    @property
    def last_seq(self) -> int:
        """지금까지 폴링으로 본 최대 seq (다음 폴링 커서)."""
        return self._last_seq

    def poll_once(self) -> list[tuple[int, str]]:
        """EventStore에서 last_seq 이후 신규 이벤트를 1회 읽어 큐에 적재한다.

        read_since(원문 패스스루)로 (seq, payload_json)을 가져와 큐에 추가하고
        last_seq를 전진시킨다. 같은 이벤트를 두 번 적재하지 않는다(중복 0 —
        커서가 seq 오름차순으로만 전진).

        Returns:
            이번에 새로 적재된 (seq, payload_json) 리스트. 없으면 빈 리스트.
        """
        new_events = self._store.read_since(self._last_seq)
        if new_events:
            self._queue.extend(new_events)
            # read_since는 seq 오름차순이므로 마지막 튜플의 seq가 최대값.
            self._last_seq = new_events[-1][0]
        return new_events

    def subscribe(self, last_seq: int = 0) -> list[tuple[int, str]]:
        """구독자를 등록하고 last_seq 이후의 이벤트를 재생용으로 돌려준다.

        재연결 시 클라이언트가 마지막으로 받은 seq를 주면 그 이후부터
        결손·중복 없이 이어받는다. 등록만 추적하고, 실제 배포 루프는
        US-002가 last_seq를 갱신하며 돌린다.

        Args:
            last_seq: 이 값보다 큰 seq부터 재생. 0이면 큐에 쌓인 전부.

        Returns:
            last_seq 이후 (seq, payload_json) 리스트. (등록 토큰은 별도 반환하지
            않고, 해제는 subscribe가 돌려준 마지막 seq나 호출자 관리로 충분 —
            본 스토리는 재생 정확성만 검증)
        """
        token = self._next_token
        self._next_token += 1
        self._subscribers.add(token)
        return self.replay_from(last_seq)

    def replay_from(self, last_seq: int) -> list[tuple[int, str]]:
        """큐에서 last_seq보다 큰 seq의 이벤트만 골라 재생용으로 반환한다.

        subscribe 내부와 재연결 양쪽이 쓰는 순수 조회. 큐 상태를 바꾸지 않는다.
        """
        return [(seq, payload) for (seq, payload) in self._queue if seq > last_seq]

    def unsubscribe(self, token: int) -> None:
        """구독자를 해제한다(추적 집합에서 제거). 없는 토큰은 조용히 무시."""
        self._subscribers.discard(token)

    @property
    def subscriber_count(self) -> int:
        """현재 활성 구독자 수(테스트·관측용)."""
        return len(self._subscribers)


class FanoutPoller:
    """EventFanout.poll_once를 주기적으로 호출하는 백그라운드 구동부(US-002).

    US-001 EventFanout은 수동 poll_once만 있었다. SSE 엔드포인트가 실시간으로
    신규 이벤트를 흘리려면 누군가 0.5초마다 poll_once를 불러 DB 증분을 큐에
    적재해야 한다. 그 "누군가"가 이 클래스다 — asyncio 태스크 1개로 그 폴링
    루프만 돌린다.

    ⚠️ 동시성 주의(불변 제약): 이 asyncio 폴링은 "SSE fan-out 0.5초"이지
    "워크플로 실행"이 아니다. 여기서는 LLM 호출이 0이고 단순 DB 증분 읽기만
    한다(read_since). 워크플로(run_full)는 US-004가 전용 스레드로 띄우며, 이
    폴링과는 완전히 별개의 독립 루프다. 혼동 금지.

    poll_once는 동기(블로킹 SQLite 읽기)지만 매우 짧다. 이벤트루프를 길게
    막지 않도록 read_since 1회만 돌고 await asyncio.sleep으로 양보한다.

    사용(app 시작/종료 lifespan):
        poller = FanoutPoller(fanout)
        await poller.start()   # 백그라운드 폴링 태스크 기동
        ...
        await poller.stop()    # 태스크 취소·정리
    """

    def __init__(self, fanout: EventFanout) -> None:
        """
        Args:
            fanout: 주기 호출 대상 EventFanout. poll_interval은 fanout에서 읽는다.
        """
        self._fanout = fanout
        # 백그라운드 폴링 태스크. start 전·stop 후에는 None.
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """폴링 태스크가 살아 있는지(관측·테스트용)."""
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        """poll_interval마다 poll_once를 1회 호출하는 영구 루프.

        취소(CancelledError)되면 조용히 종료한다. poll_once에서 예기치 못한
        예외가 나도 루프가 죽지 않도록 흡수한다(폴링은 멈추면 안 되는 인프라).
        """
        interval = self._fanout.poll_interval
        while True:
            try:
                self._fanout.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 폴링 1회 실패가 전체 fan-out을 멈추게 두지 않는다.
                # (DB 일시 잠금 등은 다음 tick에서 회복) — 조용히 넘긴다.
                pass
            await asyncio.sleep(interval)

    async def start(self) -> None:
        """백그라운드 폴링 태스크를 기동한다(이미 돌고 있으면 무시)."""
        if self.is_running:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """폴링 태스크를 취소하고 정리한다(없으면 조용히 무시)."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
