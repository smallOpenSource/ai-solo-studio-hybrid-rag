"""US-001(T1) 구독 어댑터 단위 테스트 — read_since + EventFanout.

오프라인 결정론(실서버·LLM 0). tmp_path db 주입으로 state/office.db 무오염.
slow 마커 없음.

테스트 범위(수용 기준):
- read_since: seq>last 증분 정확·빈 결과·last=0 경계·순서 단조·payload_json
  원문 문자열(파싱 안 된 str) 단언.
- EventFanout: poll_once 후 신규 이벤트가 큐에·subscribe(last_seq)가 그
  이후부터 재생·중복 0.
- 정적 단언: office/ 소스가 web_server를 import하지 않음(엔진 비침투).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from office.event_store import EventStore
from office.events import RunFinished, RunStarted, TextMessage
from web_server.sse import DEFAULT_POLL_INTERVAL, EventFanout


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _ts() -> str:
    return "2026-06-08T00:00:00"


def _store(tmp_path: Path) -> EventStore:
    """테스트용 EventStore — tmp_path/office.db (실 파일 생성 금지)."""
    return EventStore(db_path=tmp_path / "office.db")


def _seed(store: EventStore, run_id: str, n: int) -> list[int]:
    """TextMessage n개를 append하고 부여된 seq 리스트를 반환한다."""
    seqs: list[int] = []
    for i in range(n):
        seq = store.append(TextMessage(run_id=run_id, ts=_ts(), content=f"메시지 {i}"))
        seqs.append(seq)
    return seqs


# ── read_since: 증분 정확 ─────────────────────────────────────────────────────


def test_read_since_returns_only_after_seq(tmp_path: Path) -> None:
    """read_since(after)는 seq > after 인 이벤트만 돌려준다."""
    store = _store(tmp_path)
    seqs = _seed(store, "r1", 5)  # seq 1..5

    # 3번째 seq 이후만 — 4,5 두 개
    after = seqs[2]
    result = store.read_since(after)
    got_seqs = [seq for (seq, _) in result]
    assert got_seqs == [seqs[3], seqs[4]]


def test_read_since_last_zero_returns_all(tmp_path: Path) -> None:
    """경계: after_seq=0이면 처음부터 전부 반환한다."""
    store = _store(tmp_path)
    seqs = _seed(store, "r2", 4)

    result = store.read_since(0)
    got_seqs = [seq for (seq, _) in result]
    assert got_seqs == seqs
    assert len(result) == 4


def test_read_since_empty_when_nothing_newer(tmp_path: Path) -> None:
    """가장 큰 seq 이후를 물으면 빈 리스트."""
    store = _store(tmp_path)
    seqs = _seed(store, "r3", 3)

    result = store.read_since(seqs[-1])
    assert result == []


def test_read_since_empty_db(tmp_path: Path) -> None:
    """이벤트가 하나도 없으면 빈 리스트."""
    store = _store(tmp_path)
    assert store.read_since(0) == []


def test_read_since_order_monotonic(tmp_path: Path) -> None:
    """반환 순서가 seq 오름차순(단조)으로 정렬된다."""
    store = _store(tmp_path)
    _seed(store, "r4", 6)

    result = store.read_since(0)
    seqs = [seq for (seq, _) in result]
    for i in range(1, len(seqs)):
        assert seqs[i] > seqs[i - 1], f"seq 단조 위반: {seqs}"


def test_read_since_spans_all_run_ids(tmp_path: Path) -> None:
    """read_since는 run_id 구분 없이 전순서로 가로질러 읽는다(전역 커서)."""
    store = _store(tmp_path)
    s1 = store.append(RunStarted(run_id="ra", ts=_ts()))
    s2 = store.append(RunStarted(run_id="rb", ts=_ts()))
    s3 = store.append(RunFinished(run_id="ra", ts=_ts(), status="success"))

    result = store.read_since(0)
    got_seqs = [seq for (seq, _) in result]
    assert got_seqs == [s1, s2, s3]


# ── read_since: payload_json 원문 패스스루(파싱 안 된 str) ─────────────────────


def test_read_since_payload_is_raw_string(tmp_path: Path) -> None:
    """payload_json은 dict가 아니라 파싱 안 된 원문 str이어야 한다(M6 패스스루)."""
    store = _store(tmp_path)
    store.append(TextMessage(run_id="r5", ts=_ts(), content="원문"))

    result = store.read_since(0)
    assert len(result) == 1
    seq, payload_json = result[0]
    assert isinstance(seq, int)
    # 핵심: 문자열이지 dict가 아니다(event_from_dict 미경유).
    assert isinstance(payload_json, str)
    # 그 문자열을 파싱하면 원래 이벤트 dict와 일치한다.
    parsed = json.loads(payload_json)
    assert parsed["type"] == "TEXT_MESSAGE"
    assert parsed["content"] == "원문"


def test_read_since_payload_byte_for_byte_matches_stored(tmp_path: Path) -> None:
    """read_since가 돌려주는 payload_json이 저장된 원문과 글자 단위로 같다."""
    store = _store(tmp_path)
    store.append(TextMessage(run_id="r6", ts=_ts(), content="한글 content"))

    # read_rows의 payload_json(저장 원문)과 read_since의 문자열이 동일해야 한다.
    rows = store.read_rows("r6")
    stored_payload = rows[0]["payload_json"]

    result = store.read_since(0)
    _, since_payload = result[0]
    assert since_payload == stored_payload


def test_read_since_does_not_deserialize_bad_reason(tmp_path: Path) -> None:
    """잘못된 reason 같은 값이 있어도 read_since는 역직렬화하지 않아 예외 없음.

    원문 패스스루이므로 event_from_dict의 reason ValueError 경로를 타지 않는다.
    DB에 직접 잘못된 NODE_FAILED payload를 심고 그대로 회수되는지 확인.
    """
    store = _store(tmp_path)
    from office.db import get_connection

    bad_payload = json.dumps(
        {
            "run_id": "r7",
            "ts": _ts(),
            "type": "NODE_FAILED",
            "family": "Interrupt",
            "schema_v": 1,
            "node_id": "n1",
            "reason": "totally-invalid-reason",  # FailReason enum에 없는 값
            "detail": "x",
        },
        ensure_ascii=False,
    )
    with get_connection(tmp_path / "office.db") as conn:
        conn.execute(
            """
            INSERT INTO events (run_id, family, type, payload_json, ts, schema_v)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("r7", "Interrupt", "NODE_FAILED", bad_payload, _ts(), 1),
        )
        conn.commit()

    # 예외 없이 원문 그대로 회수된다.
    result = store.read_since(0)
    assert len(result) == 1
    _, payload_json = result[0]
    assert payload_json == bad_payload


# ── EventFanout: poll_once / subscribe / 중복 0 ──────────────────────────────


def test_fanout_default_poll_interval() -> None:
    """기본 폴링 간격이 0.5초다(계획 §4.3)."""
    assert DEFAULT_POLL_INTERVAL == 0.5


def test_fanout_poll_once_loads_new_events(tmp_path: Path) -> None:
    """poll_once 후 신규 이벤트가 큐에 적재되고 그 목록을 돌려준다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    seqs = _seed(store, "r1", 3)
    new = fanout.poll_once()

    got_seqs = [seq for (seq, _) in new]
    assert got_seqs == seqs
    # last_seq가 최대 seq로 전진했다.
    assert fanout.last_seq == seqs[-1]


def test_fanout_poll_once_empty_returns_empty(tmp_path: Path) -> None:
    """신규 이벤트가 없으면 poll_once는 빈 리스트, last_seq 불변."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    assert fanout.poll_once() == []
    assert fanout.last_seq == 0


def test_fanout_poll_once_incremental_no_duplicate(tmp_path: Path) -> None:
    """연속 poll_once는 이전에 본 이벤트를 다시 적재하지 않는다(중복 0)."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    _seed(store, "r2", 2)  # seq 1,2
    first = fanout.poll_once()
    assert [s for (s, _) in first] == [1, 2]

    # 두 번째 poll: 신규 없음 → 빈 리스트
    assert fanout.poll_once() == []

    # 이벤트 더 추가 후 poll: 신규분만
    s3 = store.append(TextMessage(run_id="r2", ts=_ts(), content="셋"))
    second = fanout.poll_once()
    assert [s for (s, _) in second] == [s3]

    # 전체 재생(subscribe last_seq=0)은 3개 — 중복 없이 정확히 누적.
    replay = fanout.subscribe(last_seq=0)
    assert [s for (s, _) in replay] == [1, 2, s3]


def test_fanout_subscribe_replays_from_last_seq(tmp_path: Path) -> None:
    """subscribe(last_seq)는 그 seq 이후만 재생한다(재연결 결손·중복 0)."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    seqs = _seed(store, "r3", 5)  # 1..5
    fanout.poll_once()

    # last_seq=2 이후 구독 → 3,4,5
    replay = fanout.subscribe(last_seq=seqs[1])
    assert [s for (s, _) in replay] == [seqs[2], seqs[3], seqs[4]]


def test_fanout_subscribe_zero_replays_all(tmp_path: Path) -> None:
    """subscribe(0)은 큐에 쌓인 전부를 재생한다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    seqs = _seed(store, "r4", 3)
    fanout.poll_once()

    replay = fanout.subscribe(last_seq=0)
    assert [s for (s, _) in replay] == seqs


def test_fanout_subscribe_payload_is_raw_string(tmp_path: Path) -> None:
    """재생되는 payload도 원문 str(패스스루)이다."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    store.append(TextMessage(run_id="r5", ts=_ts(), content="재생"))
    fanout.poll_once()

    replay = fanout.subscribe(last_seq=0)
    _, payload_json = replay[0]
    assert isinstance(payload_json, str)
    assert json.loads(payload_json)["content"] == "재생"


def test_fanout_subscribe_unsubscribe_tracking(tmp_path: Path) -> None:
    """subscribe/unsubscribe가 구독자 수를 추적한다(등록·해제)."""
    store = _store(tmp_path)
    fanout = EventFanout(store)

    assert fanout.subscriber_count == 0
    fanout.subscribe(last_seq=0)
    fanout.subscribe(last_seq=0)
    assert fanout.subscriber_count == 2

    # 토큰은 1부터 증가 — 첫 토큰 해제
    fanout.unsubscribe(1)
    assert fanout.subscriber_count == 1
    # 없는 토큰 해제는 조용히 무시
    fanout.unsubscribe(999)
    assert fanout.subscriber_count == 1


def test_fanout_custom_poll_interval(tmp_path: Path) -> None:
    """poll_interval을 주입하면 그 값을 노출한다(US-002 루프가 참고)."""
    store = _store(tmp_path)
    fanout = EventFanout(store, poll_interval=1.5)
    assert fanout.poll_interval == 1.5


# ── 정적 단언: 엔진 비침투(office가 web_server를 import하지 않음) ─────────────


def test_office_does_not_import_web_server() -> None:
    """office/ 소스 어디에도 web_server import가 없어야 한다(단방향 의존).

    web_server는 office를 import하지만, office는 web_server를 몰라야 한다
    (엔진 비침투 — 계획 결정 B1, 원칙 1).
    """
    office_dir = Path(__file__).parent.parent / "office"
    assert office_dir.is_dir(), f"office 디렉토리 없음: {office_dir}"

    forbidden = [
        re.compile(r"^\s*import\s+web_server"),
        re.compile(r"^\s*from\s+web_server"),
    ]
    violations: list[str] = []
    for py_file in sorted(office_dir.glob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        for line in source.splitlines():
            if any(pat.match(line) for pat in forbidden):
                violations.append(f"{py_file.name}: {line.strip()}")

    assert not violations, (
        "office/ 소스가 web_server를 import한다(엔진 비침투 위반):\n"
        + "\n".join(violations)
    )
