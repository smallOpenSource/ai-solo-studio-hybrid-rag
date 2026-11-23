"""이벤트 스토어 — SQLite append-only 이벤트 로그.

state/office.db의 events 테이블에 모든 이벤트를 순서대로 쌓는다.
이 테이블이 이벤트버스의 단일 진실(single source of truth).

스키마:
  events(
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,  -- 전순서 보장
    run_id       TEXT NOT NULL,                      -- 실행 식별자
    family       TEXT NOT NULL,                      -- 이벤트 패밀리
    type         TEXT NOT NULL,                      -- 이벤트 타입
    payload_json TEXT NOT NULL,                      -- 전체 이벤트 JSON
    ts           TEXT NOT NULL,                      -- 발생 시각 (ISO-8601)
    schema_v     INTEGER NOT NULL DEFAULT 1          -- 스키마 버전
  )

연결 규약(WAL + busy_timeout)은 office/db.py의 get_connection이 보장.
이 파일에서 PRAGMA를 재정의하지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from office.db import DEFAULT_DB_PATH, get_connection
from office.events import BaseEvent, event_from_dict

# events 테이블 생성 DDL
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT    NOT NULL,
    family       TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    ts           TEXT    NOT NULL,
    schema_v     INTEGER NOT NULL DEFAULT 1
)
"""

# run_id 조회 성능을 위한 인덱스
_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, seq)
"""


def _ensure_table(db_path: str | Path | None = None) -> None:
    """events 테이블과 인덱스가 없으면 생성한다."""
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)
        conn.commit()


class EventStore:
    """SQLite append-only 이벤트 로그.

    사용 예:
        store = EventStore(db_path=tmp_path / "test.db")
        store.append(RunStarted(run_id="r1", ts="2026-01-01T00:00:00"))
        events = store.read("r1")
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        """
        Args:
            db_path: SQLite 파일 경로. None이면 state/office.db.
                     테스트에서는 tmp_path를 주입해 실 파일 생성을 막는다.
        """
        self._db_path = db_path if db_path is not None else DEFAULT_DB_PATH
        _ensure_table(self._db_path)

    def append(self, event: BaseEvent) -> int:
        """이벤트를 events 테이블에 추가하고 seq를 반환한다.

        append-only: 삽입만 하고 수정·삭제는 하지 않는다.

        Returns:
            새로 삽입된 행의 seq (AUTOINCREMENT 값)
        """
        payload = event.to_dict()
        payload_json = json.dumps(payload, ensure_ascii=False)

        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (run_id, family, type, payload_json, ts, schema_v)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    payload.get("family", ""),
                    payload.get("type", ""),
                    payload_json,
                    event.ts,
                    event.schema_v,
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def read(self, run_id: str) -> list[BaseEvent]:
        """지정 run_id의 이벤트를 발행 순서(seq 오름차순)대로 반환한다.

        seq 순서가 곧 발행 순서 — 재구성 시 ORDER BY seq를 보장한다.
        """
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()

        result: list[BaseEvent] = []
        for row in rows:
            data = json.loads(row["payload_json"])
            result.append(event_from_dict(data))
        return result

    def read_rows(self, run_id: str) -> list[dict[str, Any]]:
        """지정 run_id의 행을 원시 dict(seq 포함)로 반환한다.

        seq 단조증가 검증·JSONL export 등 내부 용도.
        """
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT seq, run_id, family, type, payload_json, ts, schema_v
                FROM events
                WHERE run_id = ?
                ORDER BY seq ASC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def read_since(self, after_seq: int) -> list[tuple[int, str]]:
        """seq가 after_seq보다 큰 이벤트를 (seq, payload_json) 튜플로 반환한다.

        실시간 구독(SSE fan-out)이 마지막으로 본 seq 이후의 신규 이벤트만
        증분으로 읽기 위한 커서 메서드. read_rows와 달리 dict로 재구성하지
        않고 저장된 payload_json 원문 문자열을 그대로 넘긴다(패스스루 M6 —
        event_from_dict 미경유). 따라서 잘못된 reason 같은 값이 들어 있어도
        여기서 역직렬화 예외가 나지 않는다.

        모든 run_id를 가로질러 전순서(seq 오름차순)로 읽는다 — fan-out 큐는
        run_id 구분 없이 전역 커서 하나로 동작한다.

        Args:
            after_seq: 이 값보다 큰 seq만 반환. 0이면 처음부터 전부.

        Returns:
            (seq, payload_json) 튜플 리스트. seq 오름차순. 신규가 없으면 빈 리스트.
        """
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT seq, payload_json FROM events WHERE seq > ? ORDER BY seq ASC",
                (after_seq,),
            ).fetchall()
        return [(row["seq"], row["payload_json"]) for row in rows]

    def head_seq(self) -> int:
        """현재 저장된 최대 seq를 반환한다. 이벤트가 없으면 0.

        커서 부재(콜드스타트)인 구독자가 '과거 전체 재생' 대신 '지금부터'를 고를 수
        있게 현재 head를 알려준다 — 디스코드처럼 영구 부작용 싱크의 재방송 스팸 방지.
        """
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS head FROM events"
            ).fetchone()
        return int(row["head"])

    def has_tool_result(self, run_id: str, node_id: str) -> bool:
        """(run_id, node_id)의 TOOL_CALL_RESULT가 이미 있으면 True (B1 dedup, AC6).

        멱등성 가드용 읽기 전용 질의 — 새 이벤트 타입을 만들지 않는다(11종 무변).
        json_extract로 payload_json 안의 node_id를 SQL에서 직접 필터링한다
        (read_since/pending_interrupts엔 node_id 필터가 없어 신규 메서드로 추가).
        TOOL_CALL_RESULT는 도구를 실제 실행했다는 마커이므로, 존재하면 그 노드의
        도구가 이미 1회 실행됐다는 뜻 → engine이 재실행을 skip한다(부수효과 0).

        Args:
            run_id : 실행 식별자.
            node_id: 도구 노드 id.

        Returns:
            True면 해당 노드의 TOOL_CALL_RESULT가 events에 1건 이상 존재.
        """
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM events
                WHERE run_id = ?
                  AND type = 'TOOL_CALL_RESULT'
                  AND json_extract(payload_json, '$.node_id') = ?
                LIMIT 1
                """,
                (run_id, node_id),
            ).fetchone()
        return row is not None

    def pending_interrupts(self, run_id: str) -> list[dict[str, Any]]:
        """아직 INTERRUPT_RESOLVED가 없는 INTERRUPT_REQUESTED 행을 반환한다.

        4a Agent Inbox 구독 등에서 승인 대기 목록을 질의하는 용도.
        """
        with get_connection(self._db_path) as conn:
            # INTERRUPT_REQUESTED seq 목록
            requested = conn.execute(
                """
                SELECT seq, payload_json FROM events
                WHERE run_id = ? AND type = 'INTERRUPT_REQUESTED'
                ORDER BY seq ASC
                """,
                (run_id,),
            ).fetchall()

            # INTERRUPT_RESOLVED payload의 node_id 집합
            resolved_rows = conn.execute(
                """
                SELECT payload_json FROM events
                WHERE run_id = ? AND type = 'INTERRUPT_RESOLVED'
                """,
                (run_id,),
            ).fetchall()

        resolved_node_ids: set[str] = set()
        for row in resolved_rows:
            data = json.loads(row["payload_json"])
            node_id = data.get("node_id", "")
            if node_id:
                resolved_node_ids.add(node_id)

        pending = []
        for row in requested:
            data = json.loads(row["payload_json"])
            node_id = data.get("node_id", "")
            if node_id not in resolved_node_ids:
                pending.append({"seq": row["seq"], **data})

        return pending

    def export_jsonl(self, run_id: str, path: str | Path) -> int:
        """지정 run_id의 이벤트를 JSONL 파일로 내보낸다.

        각 줄이 하나의 이벤트 JSON.
        DB 내용과 일치함을 보장하기 위해 DB에서 직접 읽어 쓴다.

        Returns:
            내보낸 이벤트 줄 수
        """
        rows = self.read_rows(run_id)
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with out_path.open("w", encoding="utf-8") as f:
            for row in rows:
                # payload_json이 이미 JSON 문자열이므로 그대로 씀
                f.write(row["payload_json"] + "\n")

        return len(rows)
