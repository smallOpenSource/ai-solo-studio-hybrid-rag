"""business 데이터 스토어 — state/business.db (office.db와 격리, 계획 §9 결정 E).

Phase 5 US-P5-1(실도구): 실도구(crm/order/email)가 조회·기록하는 온프레미스
로컬 스토어. 이벤트/큐 DB(state/office.db)와 **물리적으로 분리**해 도메인 데이터와
오케스트레이션 로그를 격리한다(AC16).

테이블:
- crm_customers : 고객 마스터 (customer_id PK)
- orders        : 주문 (order_id PK, customer_id FK 느슨)
- email_outbox  : 이메일 싱크 — idempotency_key UNIQUE로 이중 발송 차단(B2 토대)

★시드는 기존 mock 값(office/tools.py mock_crm_get_customer C-2041/김민준,
mock_order_get_latest ORD-9981)과 정확히 정합한다 — 4a 게이트(_FakeSmokeLLM)가
같은 결과를 기대하므로 불일치 시 게이트가 깨진다. 시드는 멱등(존재 시 재삽입 0).

연결 규약은 office/db.py와 동일(WAL + busy_timeout) — 별도 PRAGMA 진실 없이
get_connection을 재사용한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from office.db import get_connection

_PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_BUSINESS_DB_PATH = _PROJECT_ROOT / "state" / "business.db"


# ── 시드 데이터 (기존 mock 값과 정합 — 4a 게이트 보존) ─────────────────────────
# office/tools.py mock_crm_get_customer / mock_order_get_latest 와 바이트 정합.
_SEED_CUSTOMERS: list[dict[str, Any]] = [
    {
        "customer_id": "C-2041",
        "name": "김민준",
        "email": "minjun.kim@example.com",
        "tier": "gold",
        "phone": "010-1234-5678",
    },
]

_SEED_ORDERS: list[dict[str, Any]] = [
    {
        "order_id": "ORD-9981",
        "customer_id": "C-2041",
        "product": "노트북 Pro 15",
        "amount": 1_850_000,
        "status": "배송완료",
        "ordered_at": "2026-05-28T14:30:00Z",
    },
]


def _should_seed_default() -> bool:
    """라이브에서 더미 시드를 끄는 게이트 (OFFICE_BUSINESS_SEED 환경변수).

    기본(미설정)=True — 테스트/개발은 결정론 더미 시드(C-2041 김민준)를 유지한다.
    라이브 서비스는 app.env 에서 OFFICE_BUSINESS_SEED=0 으로 꺼, 프로덕션 business.db
    에 더미를 넣지 않는다(실 CRM 백엔드 연결 전까지 라이브=실데이터만).
    """
    val = os.environ.get("OFFICE_BUSINESS_SEED", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


class BusinessStore:
    """SQLite 기반 business 데이터 스토어.

    모든 메서드는 동기 호출만 제공한다(동시성 1 전제, office 골격과 동일).
    """

    def __init__(
        self, db_path: str | Path | None = None, seed: bool | None = None
    ) -> None:
        """business 스토어 초기화 + 스키마 생성 + (옵션) 멱등 시드.

        Args:
            db_path: SQLite 파일 경로. None이면 state/business.db.
                     테스트는 tmp_path로 임시 경로를 주입한다.
            seed: 더미 시드 주입 여부. None이면 OFFICE_BUSINESS_SEED 환경변수로 결정
                  (기본 True — 테스트/개발 결정론 유지). 라이브는 0으로 끈다(더미 제거).
        """
        self._db_path = db_path if db_path is not None else DEFAULT_BUSINESS_DB_PATH
        self._init_schema()
        do_seed = seed if seed is not None else _should_seed_default()
        if do_seed:
            self._seed()

    def _conn(self):
        """WAL+busy_timeout이 적용된 연결을 새로 반환한다(office/db.py 재사용)."""
        return get_connection(self._db_path)

    def _init_schema(self) -> None:
        """테이블이 없으면 생성한다 (CREATE IF NOT EXISTS — 멱등)."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS crm_customers (
                    customer_id TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    tier        TEXT NOT NULL,
                    phone       TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id    TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    product     TEXT NOT NULL,
                    amount      INTEGER NOT NULL,
                    status      TEXT NOT NULL,
                    ordered_at  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_outbox (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    "to"            TEXT NOT NULL,
                    subject         TEXT NOT NULL,
                    body            TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sent_at         TEXT NOT NULL
                )
            """)
            conn.commit()

    def _seed(self) -> None:
        """시드 데이터를 멱등 삽입한다 (INSERT OR IGNORE — 존재 시 재삽입 0)."""
        with self._conn() as conn:
            for c in _SEED_CUSTOMERS:
                conn.execute(
                    "INSERT OR IGNORE INTO crm_customers"
                    " (customer_id, name, email, tier, phone) VALUES (?,?,?,?,?)",
                    (c["customer_id"], c["name"], c["email"], c["tier"], c["phone"]),
                )
            for o in _SEED_ORDERS:
                conn.execute(
                    "INSERT OR IGNORE INTO orders"
                    " (order_id, customer_id, product, amount, status, ordered_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        o["order_id"],
                        o["customer_id"],
                        o["product"],
                        o["amount"],
                        o["status"],
                        o["ordered_at"],
                    ),
                )
            conn.commit()

    # ── 조회 (가역) ─────────────────────────────────────────────────────────

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        """customer_id로 고객을 조회한다. 없으면 None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT customer_id, name, email, tier, phone"
                " FROM crm_customers WHERE customer_id=?",
                (customer_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_latest_order(self, customer_id: str) -> dict[str, Any] | None:
        """customer_id의 최근 주문(ordered_at 내림차순)을 조회한다. 없으면 None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT order_id, customer_id, product, amount, status, ordered_at"
                " FROM orders WHERE customer_id=? ORDER BY ordered_at DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
            return dict(row) if row else None

    def record_customer(
        self,
        customer_id: str,
        name: str,
        email: str,
        tier: str,
        phone: str,
    ) -> bool:
        """고객을 crm_customers에 기록한다 (INSERT OR IGNORE — 멱등, record_email 미러).

        라이브 SMTP 스모크가 스모크 전용 고객행(C-SMOKE-1 → 사용자 실 수신주소)을
        주입하는 유일한 공개 쓰기 경로다. 같은 customer_id가 이미 있으면 행 추가 0.
        ★프로덕션 시드(_seed의 C-2041)와 다른 PK라 충돌 0 — raw SQL 우회를 제거한다.

        Returns:
            True  — 새 행 기록(첫 주입)
            False — PK(customer_id) 충돌로 무시됨(이미 존재)
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO crm_customers"
                " (customer_id, name, email, tier, phone) VALUES (?,?,?,?,?)",
                (customer_id, name, email, tier, phone),
            )
            conn.commit()
            return cursor.rowcount == 1

    # ── 이메일 싱크 (비가역 — 멱등키 UNIQUE) ───────────────────────────────

    def record_email(
        self,
        to: str,
        subject: str,
        body: str,
        idempotency_key: str,
        sent_at: str,
    ) -> bool:
        """이메일을 outbox에 기록한다 (INSERT OR IGNORE — 멱등키 UNIQUE, B2).

        같은 idempotency_key가 이미 있으면 행을 추가하지 않는다(rowcount==0).

        Returns:
            True  — 새 행을 기록함(첫 발송)
            False — 멱등키 충돌로 무시됨(이미 발송된 것 — 이중 발송 차단)
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO email_outbox"
                ' ("to", subject, body, idempotency_key, sent_at)'
                " VALUES (?,?,?,?,?)",
                (to, subject, body, idempotency_key, sent_at),
            )
            conn.commit()
            return cursor.rowcount == 1

    def get_email_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        """idempotency_key로 기록된 이메일 행을 조회한다. 없으면 None."""
        with self._conn() as conn:
            row = conn.execute(
                'SELECT id, "to", subject, body, idempotency_key, sent_at'
                " FROM email_outbox WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            return dict(row) if row else None

    def count_emails(self) -> int:
        """email_outbox 전체 행 수 (테스트 단언용)."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM email_outbox").fetchone()
            return int(row["n"])
