#!/usr/bin/env python
"""scripts/smoke_verify.py — 라이브 스모크 검증 헬퍼.

모드:
  --check-to   승인 **전** 자동 1차 게이트(NIT-2): GET /api/inbox의 미해소 email_send
               interrupt에서 payload.args.to 가 시드한 C-SMOKE-1 주소와 일치하는지 단언.
               gemma 비결정 `to`를 인간승인 전에 검증한다(불일치면 비0 종료 → 승인 거부 권고).
  --report     발사 후: outbox 행 수 + 이벤트 11종 무변(len(EVENT_REGISTRY)==11) 리포트.
  --key KEY    특정 멱등키(f"{run_id}:{node_id}")의 outbox 행 조회.

사용법:
    conda run -n one_man_corp-engine python scripts/smoke_verify.py --check-to
    conda run -n one_man_corp-engine python scripts/smoke_verify.py --report --key runABC:n3_email_send

종료코드: 0=정상/일치, 1=불일치·미발견·계약 위반.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

from office.events import EVENT_REGISTRY
from office.tools import get_business_store

_SMOKE_CUSTOMER_ID = "C-SMOKE-1"
_EMAIL_NODE_ID = "n3_email_send"


def _expected_to() -> str | None:
    """시드한 C-SMOKE-1 고객의 이메일(기대 수신자)."""
    row = get_business_store().get_customer(_SMOKE_CUSTOMER_ID)
    return row["email"] if row else None


def _get_inbox() -> dict:
    """GET /api/inbox (stdlib urllib·timeout=10으로 hang 차단)."""
    base = os.environ.get("WEB_BASE_URL", "http://127.0.0.1:8800")
    url = f"{base}/api/inbox"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 (로컬 고정 URL)
        return json.loads(resp.read().decode("utf-8"))


def _check_to() -> int:
    expected = _expected_to()
    if not expected:
        print("[verify] C-SMOKE-1 미시드 — smoke_seed.py 먼저 실행", file=sys.stderr)
        return 1
    review = _get_inbox().get("review", [])
    pending_email = [
        r
        for r in review
        if r.get("node_id") == _EMAIL_NODE_ID or r.get("tool") == "email_send"
    ]
    if not pending_email:
        print(
            "[verify] 미해소 email_send interrupt 없음(suspend 전이거나 이미 승인)",
            file=sys.stderr,
        )
        return 1
    for r in pending_email:
        to = (r.get("payload", {}) or {}).get("args", {}).get("to", "")
        if to == expected:
            print(f"[verify] ✅ to 일치: {to} (C-SMOKE-1) — 승인 진행 가능")
            return 0
        print(
            f"[verify] ❌ to 불일치: interrupt={r.get('interrupt_id')}"
            f" to={to!r} != 기대 {expected!r}",
            file=sys.stderr,
        )
    print(
        "[verify] ★승인 거부 권고 — 실 발송 수신자가 시드 주소와 다름.", file=sys.stderr
    )
    return 1


def _report(key: str | None) -> int:
    n_events = len(EVENT_REGISTRY)
    ok_contract = n_events == 11
    store = get_business_store()
    print(f"[verify] outbox 행 수: {store.count_emails()}")
    print(
        f"[verify] EVENT_REGISTRY 종수: {n_events}"
        f" ({'OK·11 무변' if ok_contract else '★계약 위반!'})"
    )
    if key:
        row = store.get_email_by_key(key)
        print(f"[verify] key={key}: {row if row else '(없음)'}")
    return 0 if ok_contract else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="라이브 스모크 검증 헬퍼")
    ap.add_argument("--check-to", action="store_true", help="승인 전 to 일치 게이트")
    ap.add_argument("--report", action="store_true", help="outbox·이벤트 11종 리포트")
    ap.add_argument("--key", default=None, help="특정 멱등키 outbox 행 조회")
    args = ap.parse_args()

    if args.check_to:
        return _check_to()
    return _report(args.key)


if __name__ == "__main__":
    raise SystemExit(main())
