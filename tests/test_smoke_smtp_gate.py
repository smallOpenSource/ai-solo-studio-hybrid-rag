"""라이브 SMTP 스모크 — 평소 테스트에서 실 발송이 구조적으로 불가능함을 박제.

자격증명(.env SMTP_*) 부재 시 `_try_smtp_send`는 False(발송 0)이고 `real_email_send`는
싱크(email_outbox)만 기록한다. 이 가드 테스트로 'run_tests.sh에서 실 SMTP 0'을 회귀
방지한다 — 누가 .env에 SMTP 키를 넣어도 이 테스트는 load_env를 빈 dict로 강제해
결정론적으로 '자격증명 부재' 경로만 검증한다.

★scripts/run_tests.sh engine 으로만 실행. 외부 연결 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from office.business import BusinessStore
from office.tools import _try_smtp_send, real_email_send, reset_business_store


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
def no_smtp_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_env를 빈 dict로 강제 — SMTP_* 부재(실 .env 상태와 무관하게 결정론)."""
    monkeypatch.setattr("core.config.load_env", lambda: {})


def test_try_smtp_send_false_without_credentials(no_smtp_credentials: None) -> None:
    """SMTP 자격증명 부재 → _try_smtp_send False(smtplib 미접속·발송 0)."""
    assert _try_smtp_send("a@b.com", "제목", "본문") is False


def test_real_email_send_sinks_only_without_credentials(
    biz_store: BusinessStore, no_smtp_credentials: None
) -> None:
    """자격증명 부재 → 싱크만 1행·결정론 message_id·멱등(이중 기록 0)."""
    args = {"to": "a@b.com", "subject": "s", "body": "b"}
    ctx = {"run_id": "g1", "node_id": "n3_email_send"}

    result = real_email_send(args, context=ctx)
    assert result["sent"] is True
    assert result["message_id"] == "OUTBOX-g1:n3_email_send"  # 결정론 키
    assert result["deduplicated"] is False
    assert biz_store.count_emails() == 1  # 싱크 1행(실 발송은 0)

    # 멱등: 같은 키 재호출 → 행 추가 0·deduplicated True
    again = real_email_send(args, context=ctx)
    assert again["deduplicated"] is True
    assert biz_store.count_emails() == 1
