"""office/event_store.py 단위 테스트 — §5 Unit 항목.

테스트 항목:
- SQLite write→read 라운드트립
- seq 단조증가 단언
- WAL 모드 적용 단언 (PRAGMA journal_mode 조회='wal')
- schema_v=1 단언
- JSONL export = DB 내용 일치
- pending interrupt 질의
- tmp_path 주입 — state/office.db 실파일 생성 금지
"""

from __future__ import annotations

import json
from pathlib import Path

from office.db import get_connection
from office.event_store import EventStore
from office.events import (
    FailReason,
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunFinished,
    RunStarted,
    StateSnapshot,
    TextMessage,
    ToolCallStart,
)


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _ts() -> str:
    return "2026-06-08T00:00:00"


def _store(tmp_path: Path) -> EventStore:
    """테스트용 EventStore — tmp_path/office.db 사용 (실 파일 생성 금지)."""
    return EventStore(db_path=tmp_path / "office.db")


# ── WAL 모드 단언 ─────────────────────────────────────────────────────────────


def test_wal_mode_applied(tmp_path: Path) -> None:
    """get_connection이 WAL 모드를 적용하는지 PRAGMA로 직접 확인한다."""
    db_path = tmp_path / "office.db"
    with get_connection(db_path) as conn:
        # EventStore 없이 연결만으로도 WAL이 설정된다
        row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal", f"journal_mode가 'wal'이 아님: {row[0]!r}"


def test_wal_mode_via_store(tmp_path: Path) -> None:
    """EventStore 생성 후에도 WAL 모드가 유지된다."""
    store = _store(tmp_path)
    store.append(RunStarted(run_id="r1", ts=_ts(), workflow="test"))
    with get_connection(tmp_path / "office.db") as conn:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


# ── write→read 라운드트립 ─────────────────────────────────────────────────────


def test_write_read_roundtrip_single(tmp_path: Path) -> None:
    """이벤트 1개 write→read 라운드트립."""
    store = _store(tmp_path)
    e = RunStarted(run_id="r1", ts=_ts(), workflow="crm_notify")
    store.append(e)

    events = store.read("r1")
    assert len(events) == 1
    restored = events[0]
    assert isinstance(restored, RunStarted)
    assert restored.run_id == "r1"
    assert restored.workflow == "crm_notify"


# ── head_seq — 콜드스타트 시드용 최대 seq (디스코드 재방송 방지) ─────────────────


def test_head_seq_zero_when_empty(tmp_path: Path) -> None:
    """이벤트가 없으면 head_seq()는 0 (빈 스토어)."""
    store = _store(tmp_path)
    assert store.head_seq() == 0


def test_head_seq_returns_max(tmp_path: Path) -> None:
    """append 후 head_seq()는 최대 seq를 반환한다(전순서 autoincrement)."""
    store = _store(tmp_path)
    store.append(RunStarted(run_id="r1", ts=_ts(), workflow="crm_notify"))
    store.append(TextMessage(run_id="r1", ts=_ts(), content="고객 조회 중"))
    assert store.head_seq() == 2


def test_write_read_roundtrip_multiple(tmp_path: Path) -> None:
    """여러 이벤트 write→read 라운드트립 — 발행 순서 보장."""
    store = _store(tmp_path)
    run_id = "r2"
    events_in = [
        RunStarted(run_id=run_id, ts=_ts(), workflow="crm_notify"),
        TextMessage(run_id=run_id, ts=_ts(), content="고객 조회 중"),
        ToolCallStart(
            run_id=run_id,
            ts=_ts(),
            node_id="n1",
            name="crm_get_customer",
            args={"email": "a@b.com"},
        ),
        StateSnapshot(run_id=run_id, ts=_ts(), accumulated={"customer_id": "C-3087"}),
        RunFinished(run_id=run_id, ts=_ts(), status="success"),
    ]
    for ev in events_in:
        store.append(ev)

    events_out = store.read(run_id)
    assert len(events_out) == 5
    # 발행 순서 = read 순서 확인
    types_out = [ev.type for ev in events_out]
    assert types_out == [
        "RUN_STARTED",
        "TEXT_MESSAGE",
        "TOOL_CALL_START",
        "STATE_SNAPSHOT",
        "RUN_FINISHED",
    ]


def test_read_returns_empty_for_unknown_run(tmp_path: Path) -> None:
    """존재하지 않는 run_id는 빈 리스트를 반환한다."""
    store = _store(tmp_path)
    assert store.read("nonexistent-run") == []


# ── seq 단조증가 단언 ─────────────────────────────────────────────────────────


def test_seq_monotonically_increasing(tmp_path: Path) -> None:
    """같은 run_id로 append한 이벤트의 seq가 단조증가한다."""
    store = _store(tmp_path)
    run_id = "r3"
    for i in range(5):
        store.append(TextMessage(run_id=run_id, ts=_ts(), content=f"메시지 {i}"))

    rows = store.read_rows(run_id)
    seqs = [row["seq"] for row in rows]
    # 단조증가: 다음 seq는 반드시 이전 seq보다 크다
    for i in range(1, len(seqs)):
        assert seqs[i] > seqs[i - 1], f"seq가 단조증가하지 않음: {seqs}"


def test_seq_monotonically_increasing_cross_runs(tmp_path: Path) -> None:
    """다른 run_id가 섞여도 각 run_id 내에서 seq는 단조증가한다."""
    store = _store(tmp_path)
    store.append(RunStarted(run_id="ra", ts=_ts()))
    store.append(RunStarted(run_id="rb", ts=_ts()))
    store.append(RunFinished(run_id="ra", ts=_ts(), status="success"))
    store.append(RunFinished(run_id="rb", ts=_ts(), status="success"))

    rows_a = store.read_rows("ra")
    rows_b = store.read_rows("rb")

    seqs_a = [row["seq"] for row in rows_a]
    seqs_b = [row["seq"] for row in rows_b]

    for i in range(1, len(seqs_a)):
        assert seqs_a[i] > seqs_a[i - 1]
    for i in range(1, len(seqs_b)):
        assert seqs_b[i] > seqs_b[i - 1]


# ── schema_v 단언 ─────────────────────────────────────────────────────────────


def test_schema_v_stored_as_1(tmp_path: Path) -> None:
    """append된 이벤트의 schema_v가 DB에 1로 저장된다."""
    store = _store(tmp_path)
    store.append(RunStarted(run_id="r4", ts=_ts()))

    rows = store.read_rows("r4")
    assert len(rows) == 1
    assert rows[0]["schema_v"] == 1


# ── JSONL export = DB 일치 ────────────────────────────────────────────────────


def test_export_jsonl_matches_db(tmp_path: Path) -> None:
    """export_jsonl로 내보낸 JSONL이 DB 내용과 정확히 일치한다."""
    store = _store(tmp_path)
    run_id = "r5"
    events_in = [
        RunStarted(run_id=run_id, ts=_ts(), workflow="crm_notify"),
        TextMessage(run_id=run_id, ts=_ts(), content="테스트"),
        RunFinished(run_id=run_id, ts=_ts(), status="success"),
    ]
    for ev in events_in:
        store.append(ev)

    out_path = tmp_path / "export.jsonl"
    count = store.export_jsonl(run_id, out_path)

    assert count == 3
    assert out_path.exists()

    # JSONL 각 줄을 파싱해 DB 내용과 비교
    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3

    db_rows = store.read_rows(run_id)
    for line, db_row in zip(lines, db_rows):
        jsonl_data = json.loads(line)
        db_data = json.loads(db_row["payload_json"])
        assert jsonl_data == db_data, (
            f"JSONL과 DB 내용 불일치: {jsonl_data} != {db_data}"
        )


def test_export_jsonl_preserves_order(tmp_path: Path) -> None:
    """export_jsonl은 seq 오름차순(발행 순서)으로 기록한다."""
    store = _store(tmp_path)
    run_id = "r6"
    for i in range(4):
        store.append(TextMessage(run_id=run_id, ts=_ts(), content=f"순서 {i}"))

    out_path = tmp_path / "ordered.jsonl"
    store.export_jsonl(run_id, out_path)

    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    contents = [json.loads(ln)["content"] for ln in lines]
    assert contents == ["순서 0", "순서 1", "순서 2", "순서 3"]


def test_export_jsonl_empty_run(tmp_path: Path) -> None:
    """이벤트가 없는 run_id는 빈 JSONL을 만들고 0을 반환한다."""
    store = _store(tmp_path)
    out_path = tmp_path / "empty.jsonl"
    count = store.export_jsonl("nonexistent", out_path)
    assert count == 0
    assert out_path.read_text(encoding="utf-8") == ""


# ── pending interrupt 질의 ────────────────────────────────────────────────────


def test_pending_interrupts_found(tmp_path: Path) -> None:
    """INTERRUPT_REQUESTED 후 INTERRUPT_RESOLVED 없으면 pending에 나온다."""
    store = _store(tmp_path)
    run_id = "r7"
    store.append(RunStarted(run_id=run_id, ts=_ts()))
    store.append(
        InterruptRequested(
            run_id=run_id,
            ts=_ts(),
            node_id="n3",
            tool="email_send",
            payload={"args": {"to": "a@b.com"}},
        )
    )

    pending = store.pending_interrupts(run_id)
    assert len(pending) == 1
    assert pending[0]["node_id"] == "n3"
    assert pending[0]["tool"] == "email_send"


def test_pending_interrupts_resolved(tmp_path: Path) -> None:
    """INTERRUPT_RESOLVED가 있으면 pending 목록에서 제거된다."""
    store = _store(tmp_path)
    run_id = "r8"
    store.append(
        InterruptRequested(
            run_id=run_id,
            ts=_ts(),
            node_id="n3",
            tool="email_send",
            payload={},
        )
    )
    store.append(
        InterruptResolved(
            run_id=run_id,
            ts=_ts(),
            node_id="n3",
            tool="email_send",
            decision="approve",
        )
    )

    pending = store.pending_interrupts(run_id)
    assert len(pending) == 0


def test_pending_interrupts_empty_for_no_requests(tmp_path: Path) -> None:
    """INTERRUPT_REQUESTED 없으면 pending은 빈 리스트."""
    store = _store(tmp_path)
    run_id = "r9"
    store.append(RunStarted(run_id=run_id, ts=_ts()))
    assert store.pending_interrupts(run_id) == []


# ── NODE_FAILED 저장·복원 ─────────────────────────────────────────────────────


def test_node_failed_roundtrip_via_store(tmp_path: Path) -> None:
    """NODE_FAILED(reason=enum)이 DB를 거쳐 올바르게 복원된다."""
    store = _store(tmp_path)
    run_id = "r10"
    store.append(
        NodeFailed(
            run_id=run_id,
            ts=_ts(),
            node_id="n2",
            reason=FailReason.escalation,
            detail="length 가드 소진",
        )
    )

    events = store.read(run_id)
    assert len(events) == 1
    restored = events[0]
    assert isinstance(restored, NodeFailed)
    assert restored.reason == FailReason.escalation
    assert restored.detail == "length 가드 소진"


# ── 두 커넥션 동시 write (WAL 2-writer 모의) ──────────────────────────────────


def test_two_writers_no_lock(tmp_path: Path) -> None:
    """디스패처 + 승인 CLI 2-writer 모의: 두 커넥션이 동시에 써도 locked 없음."""
    db_path = tmp_path / "office.db"
    store = EventStore(db_path=db_path)  # 테이블 생성

    # 커넥션 1 (디스패처 역할)
    store.append(RunStarted(run_id="rA", ts=_ts()))

    # 커넥션 2 (승인 CLI 역할) — 별도 get_connection
    with get_connection(db_path) as conn2:
        conn2.execute(
            """
            INSERT INTO events (run_id, family, type, payload_json, ts, schema_v)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "rA",
                "Interrupt",
                "INTERRUPT_RESOLVED",
                json.dumps(
                    {
                        "run_id": "rA",
                        "ts": _ts(),
                        "type": "INTERRUPT_RESOLVED",
                        "family": "Interrupt",
                        "schema_v": 1,
                        "node_id": "n3",
                        "tool": "email_send",
                        "decision": "approve",
                    }
                ),
                _ts(),
                1,
            ),
        )
        conn2.commit()

    # 두 write가 모두 DB에 반영돼야 한다
    rows = store.read_rows("rA")
    assert len(rows) == 2
    types = [row["type"] for row in rows]
    assert "RUN_STARTED" in types
    assert "INTERRUPT_RESOLVED" in types
