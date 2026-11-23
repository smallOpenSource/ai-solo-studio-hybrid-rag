"""SQLite 연결 헬퍼 — state/office.db 단일 파일, WAL 모드.

이벤트 스토어(event_store)와 작업큐(task_queue)가 같은 DB 파일을 쓰므로
연결 규약(WAL + busy_timeout)을 이 한 곳에서만 정의한다(단일 진실).
2-프로세스(디스패처 + 승인 CLI)가 동시에 써도 안전하도록:
- journal_mode=WAL: 동시 reader/writer 허용
- busy_timeout=5000ms: 잠금 충돌 시 대기 (database is locked 방지)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB_PATH = _PROJECT_ROOT / "state" / "office.db"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """WAL+busy_timeout이 적용된 SQLite 연결을 돌려준다.

    Args:
        db_path: DB 파일 경로. None이면 state/office.db (테스트는 임시 경로 주입)
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn
