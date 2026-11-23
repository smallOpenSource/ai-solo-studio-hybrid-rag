"""US-0 (라이브 SMTP 스모크 prerequisite) — send-reorder at-most-once 회귀.

real_email_send가 record 먼저 → inserted(첫 발송)일 때만 _try_smtp_send를 발사함을
**send-count로 직접** 단언한다. ★실 SMTP 0 — monkeypatch로 _try_smtp_send를 카운터
stub(True 반환·연결 없음)으로 대체한다.

기존 test_idempotency.py(크래시 창)는 outbox **행 수**만 단언해 이중발송에 눈멀어 있다.
이 파일은 그 맹점을 보강한다: reorder 후 resume 재진입(inserted=False)에서 실 발송이
증가 0임을 send-count로 증명한다.

★scripts/run_tests.sh engine 으로만 실행(bare pytest 금지·hang). 외부 연결 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from office.business import BusinessStore
from office.tools import real_email_send, reset_business_store


@pytest.fixture
def biz_store(tmp_path: Path) -> Iterator[BusinessStore]:
    """tmp business.db를 전역 스토어로 주입하고 끝나면 리셋한다(격리)."""
    store = BusinessStore(db_path=tmp_path / "business.db")
    reset_business_store(store)
    try:
        yield store
    finally:
        reset_business_store(None)


@pytest.fixture
def send_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """_try_smtp_send를 카운터 stub으로 대체한다(실 SMTP 0·발송 성공 가장)."""
    calls: list[str] = []

    def _counting_stub(to: str, subject: str, body: str) -> bool:
        calls.append(to)
        return True  # 연결 없이 "발송 성공"으로 가장 — 호출 자체만 카운트

    monkeypatch.setattr("office.tools._try_smtp_send", _counting_stub)
    return calls


def test_first_send_invokes_smtp_exactly_once(
    biz_store: BusinessStore, send_calls: list[str]
) -> None:
    """첫 발송(inserted=True) → _try_smtp_send 정확히 1회·outbox 1행."""
    result = real_email_send(
        {"to": "user@real.example", "subject": "s", "body": "b"},
        context={"run_id": "runX", "node_id": "n3_email_send"},
    )
    assert result["deduplicated"] is False
    assert biz_store.count_emails() == 1
    assert len(send_calls) == 1  # 실 발송 정확히 1회


def test_resume_reentry_does_not_resend(
    biz_store: BusinessStore, send_calls: list[str]
) -> None:
    """resume 재진입(같은 run/node·inserted=False) → 실 발송 증가 0(at-most-once)."""
    args = {"to": "user@real.example", "subject": "s", "body": "b"}
    ctx = {"run_id": "runY", "node_id": "n3_email_send"}

    first = real_email_send(args, context=ctx)
    assert first["deduplicated"] is False
    assert len(send_calls) == 1

    # 크래시 후 resume 재진입 시뮬: 같은 멱등키 → record_email inserted=False
    second = real_email_send(args, context=ctx)
    assert second["deduplicated"] is True
    assert biz_store.count_emails() == 1  # 행 추가 0 (B2)
    assert len(send_calls) == 1  # ★실 발송 증가 0 — reorder가 at-most-once 보장
