"""business 데이터 스토어 단위 테스트 — Phase 5 US-P5-1 (AC1·3).

테스트 범위 (계획 §5 Unit):
- 스키마/시드 멱등 (재초기화해도 행 1개 유지)
- crm/order 실조회 (시드 정확 반환 — 기존 mock 값 정합)
- 미존재 id → None (실도구가 error dict로 변환할 토대)
- 이메일 싱크 멱등키 UNIQUE (같은 키 2회 INSERT OR IGNORE → 1행, B2)

모두 tmp_path 임시 DB로 격리 — 실파일(state/business.db) 오염 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from office.business import BusinessStore


@pytest.fixture
def store(tmp_path: Path) -> BusinessStore:
    """임시 DB를 쓰는 BusinessStore (스키마+시드 자동)."""
    return BusinessStore(db_path=tmp_path / "business.db")


# ── 시드 정합 (기존 mock 값과 바이트 정합 — 4a 게이트 보존) ───────────────────


def test_seed_customer_matches_mock_values(store: BusinessStore) -> None:
    """시드 고객이 기존 mock_crm_get_customer 값과 정확히 정합한다."""
    row = store.get_customer("C-2041")
    assert row == {
        "customer_id": "C-2041",
        "name": "김민준",
        "email": "minjun.kim@example.com",
        "tier": "gold",
        "phone": "010-1234-5678",
    }


def test_seed_order_matches_mock_values(store: BusinessStore) -> None:
    """시드 주문이 기존 mock_order_get_latest 값과 정확히 정합한다."""
    row = store.get_latest_order("C-2041")
    assert row == {
        "order_id": "ORD-9981",
        "customer_id": "C-2041",
        "product": "노트북 Pro 15",
        "amount": 1_850_000,
        "status": "배송완료",
        "ordered_at": "2026-05-28T14:30:00Z",
    }


# ── 시드 라이브 게이트 (OFFICE_BUSINESS_SEED) — 라이브 더미 제거 ────────────────


def test_seed_disabled_no_dummy(tmp_path: Path) -> None:
    """seed=False면 더미(C-2041 김민준)를 넣지 않는다(라이브 경로)."""
    store = BusinessStore(db_path=tmp_path / "b.db", seed=False)
    assert store.get_customer("C-2041") is None
    assert store.get_latest_order("C-2041") is None


def test_seed_disabled_via_env(tmp_path: Path, monkeypatch) -> None:
    """OFFICE_BUSINESS_SEED=0 이면 시드를 끈다(라이브 서비스 설정)."""
    monkeypatch.setenv("OFFICE_BUSINESS_SEED", "0")
    store = BusinessStore(db_path=tmp_path / "b.db")
    assert store.get_customer("C-2041") is None


def test_seed_default_on(tmp_path: Path, monkeypatch) -> None:
    """환경변수 미설정이면 기본 시드 On(테스트/개발 결정론 유지)."""
    monkeypatch.delenv("OFFICE_BUSINESS_SEED", raising=False)
    store = BusinessStore(db_path=tmp_path / "b.db")
    assert store.get_customer("C-2041") is not None


def test_seed_is_idempotent(tmp_path: Path) -> None:
    """같은 DB에 BusinessStore를 두 번 만들어도 시드가 중복 삽입되지 않는다."""
    db = tmp_path / "business.db"
    BusinessStore(db_path=db)
    s2 = BusinessStore(db_path=db)  # 재초기화 — INSERT OR IGNORE라 중복 0
    with s2._conn() as conn:
        n_cust = conn.execute("SELECT COUNT(*) AS n FROM crm_customers").fetchone()["n"]
        n_ord = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    assert n_cust == 1
    assert n_ord == 1


# ── 미존재 조회 → None ────────────────────────────────────────────────────────


def test_get_customer_missing_returns_none(store: BusinessStore) -> None:
    """미존재 customer_id는 None을 돌려준다(실도구가 error dict로 변환)."""
    assert store.get_customer("C-9999") is None


def test_get_latest_order_missing_returns_none(store: BusinessStore) -> None:
    """미존재 customer_id의 주문은 None을 돌려준다."""
    assert store.get_latest_order("C-9999") is None


# ── 이메일 싱크 멱등키 UNIQUE (B2) ────────────────────────────────────────────


def test_record_email_inserts_row(store: BusinessStore) -> None:
    """record_email은 새 행을 기록하고 True를 돌려준다."""
    ok = store.record_email(
        to="a@b.com",
        subject="제목",
        body="본문",
        idempotency_key="run1:n3",
        sent_at="2026-06-09T00:00:00Z",
    )
    assert ok is True
    assert store.count_emails() == 1
    row = store.get_email_by_key("run1:n3")
    assert row is not None
    assert row["to"] == "a@b.com"
    assert row["subject"] == "제목"


def test_record_email_same_key_twice_keeps_one_row(store: BusinessStore) -> None:
    """같은 idempotency_key로 2회 기록 → INSERT OR IGNORE로 행 1개(이중 발송 차단, B2)."""
    first = store.record_email(
        to="a@b.com",
        subject="s",
        body="b",
        idempotency_key="run1:n3",
        sent_at="2026-06-09T00:00:00Z",
    )
    second = store.record_email(
        to="a@b.com",
        subject="s-다름",  # 내용이 달라도 같은 키면 무시돼야 한다
        body="b-다름",
        idempotency_key="run1:n3",
        sent_at="2026-06-09T00:00:01Z",
    )
    assert first is True
    assert second is False  # 멱등키 충돌 → 무시
    assert store.count_emails() == 1
    # 첫 기록 내용이 보존된다(둘째가 덮어쓰지 않음)
    row = store.get_email_by_key("run1:n3")
    assert row is not None
    assert row["subject"] == "s"
    assert row["body"] == "b"


def test_record_email_distinct_keys_two_rows(store: BusinessStore) -> None:
    """서로 다른 키는 각각 행을 만든다(정상 다건 발송)."""
    store.record_email(
        to="a@b.com",
        subject="s1",
        body="b1",
        idempotency_key="run1:n3",
        sent_at="2026-06-09T00:00:00Z",
    )
    store.record_email(
        to="c@d.com",
        subject="s2",
        body="b2",
        idempotency_key="run2:n3",
        sent_at="2026-06-09T00:00:01Z",
    )
    assert store.count_emails() == 2


# ── 고객 기록 (record_customer — 스모크 시드 경로) ────────────────────────────


def test_record_customer_inserts_and_reads_back(store: BusinessStore) -> None:
    """record_customer가 새 고객을 기록하고 get_customer로 되읽힌다."""
    ok = store.record_customer(
        customer_id="C-SMOKE-1",
        name="스모크수신",
        email="smoke@real.example",
        tier="test",
        phone="010-0000-0000",
    )
    assert ok is True
    row = store.get_customer("C-SMOKE-1")
    assert row is not None
    assert row["email"] == "smoke@real.example"
    assert row["name"] == "스모크수신"


def test_record_customer_idempotent_and_preserves_seed(store: BusinessStore) -> None:
    """같은 customer_id 2회 → 행 1개(둘째 False)·첫 기록 보존·시드 C-2041 공존."""
    first = store.record_customer("C-SMOKE-1", "a", "a@x.com", "test", "1")
    second = store.record_customer("C-SMOKE-1", "b", "b@x.com", "test", "2")
    assert first is True
    assert second is False  # PK 충돌 → 무시
    row = store.get_customer("C-SMOKE-1")
    assert row is not None
    assert row["email"] == "a@x.com"  # 첫 기록 보존(둘째가 덮어쓰지 않음)
    # ★프로덕션 시드 C-2041은 스모크 행과 충돌 없이 공존(다른 PK)
    assert store.get_customer("C-2041") is not None
