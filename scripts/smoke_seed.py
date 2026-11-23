#!/usr/bin/env python
"""scripts/smoke_seed.py — 라이브 SMTP 스모크 수신자(C-SMOKE-1) 시드.

C-SMOKE-1 고객행(이메일 = 사용자 통제 실 수신주소)을 business.db에 멱등 주입한다
(BusinessStore.record_customer · INSERT OR IGNORE · raw SQL 0). 프로덕션 시드 C-2041은
다른 PK라 무손상. ★사용자가 자기 실주소로 직접 실행한다(에이전트 자동 금지).

사용법:
    conda run -n one_man_corp-engine python scripts/smoke_seed.py --email you@real.example
    conda run -n one_man_corp-engine python scripts/smoke_seed.py --email you@x.com --name 홍길동

종료코드: 0=성공(주입/이미존재), 1=실패.
"""

from __future__ import annotations

import argparse
import sys

from office.tools import get_business_store


def main() -> int:
    ap = argparse.ArgumentParser(description="스모크 수신자(C-SMOKE-1) 시드")
    ap.add_argument("--email", required=True, help="실 수신 inbox 주소(본인 통제)")
    ap.add_argument("--name", default="스모크수신", help="고객명(기본 '스모크수신')")
    args = ap.parse_args()

    store = get_business_store()
    inserted = store.record_customer(
        customer_id="C-SMOKE-1",
        name=args.name,
        email=args.email,
        tier="test",
        phone="010-0000-0000",
    )
    row = store.get_customer("C-SMOKE-1")
    if row is None:
        print("[smoke_seed] 실패 — C-SMOKE-1 주입 후에도 미존재", file=sys.stderr)
        return 1

    status = "새로 주입" if inserted else "이미 존재(멱등 — 기존 행 보존)"
    print(f"[smoke_seed] C-SMOKE-1 → {row['email']} ({status})")
    if not inserted and row["email"] != args.email:
        print(
            f"[smoke_seed] ⚠️ 기존 C-SMOKE-1 이메일({row['email']})이 요청({args.email})과"
            " 다름 — INSERT OR IGNORE라 덮어쓰지 않음. 바꾸려면 행 삭제 후 재시드.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
