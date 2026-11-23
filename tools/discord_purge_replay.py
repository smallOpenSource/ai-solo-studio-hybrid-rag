#!/usr/bin/env python3
"""tools/discord_purge_replay.py — 커서유실 재방송 스팸 메시지 정리 (REST·자기메시지).

디스코드 어댑터 커서유실 재방송(2026-06-21 인시던트)으로 라이브 채널에 옛 데모/스모크
이벤트가 대량 재게시됐다. 그 봇 메시지를 정리한다.

안전장치(되돌릴 수 없는 외부작업):
- **드라이런 기본**(--apply 없으면 절대 삭제 0). 대상 인벤토리만 출력.
- **시간창 필수**(--after-kst / --before-kst). 창 밖 메시지는 손대지 않는다.
- **우리 봇 author만**(16 토큰의 user id 집합). 사람/타 봇 메시지 보호.
- REST만(게이트웨이 IDENTIFY 0 → 가동 중 라이브 어댑터와 충돌 없음).
- 시크릿(토큰)은 config/app.env에서 읽기만·출력/로그 0.
- 삭제는 토큰별 자기메시지 DELETE(MANAGE_MESSAGES 불요)·429 백오프.

사용:
  python tools/discord_purge_replay.py --after-kst 2026-06-21T17:00 --before-kst 2026-06-21T19:30
  python tools/discord_purge_replay.py --after-kst ... --before-kst ... --apply   # 실제 삭제
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

ROOT = "/opt/studio"
APP_ENV = f"{ROOT}/config/app.env"
API = "https://discord.com/api/v10"
KST = timezone(timedelta(hours=9))


def _load_env(path: str) -> dict[str, str]:
    d: dict[str, str] = {}
    with open(path, errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            d[k] = v
    return d


def _kst_to_utc(s: str) -> datetime:
    return (
        datetime.strptime(s, "%Y-%m-%dT%H:%M")
        .replace(tzinfo=KST)
        .astimezone(timezone.utc)
    )


def _bot_id(token: str) -> str | None:
    """토큰의 봇 user id (GET /users/@me). 실패 시 None."""
    try:
        r = httpx.get(
            f"{API}/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            return str(r.json()["id"])
    except Exception:
        pass
    return None


def _iter_messages(channel: str, token: str, after_utc: datetime, before_utc: datetime):
    """채널 메시지를 before 페이지네이션으로 역순 순회(창 교집합만 yield)."""
    before: str | None = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        r = httpx.get(
            f"{API}/channels/{channel}/messages",
            headers={"Authorization": f"Bot {token}"},
            params=params,
            timeout=20,
        )
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 1.0)) + 0.2)
            continue
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return
        for m in batch:
            ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
            if ts < after_utc:
                return  # 더 과거 → 종료(역순)
            if ts <= before_utc:
                yield m, ts
        before = batch[-1]["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--after-kst", required=True, help="예: 2026-06-21T17:00")
    ap.add_argument("--before-kst", required=True, help="예: 2026-06-21T19:30")
    ap.add_argument("--apply", action="store_true", help="실제 삭제(없으면 드라이런)")
    args = ap.parse_args()

    after_utc = _kst_to_utc(args.after_kst)
    before_utc = _kst_to_utc(args.before_kst)

    env = _load_env(APP_ENV)
    channel = env.get("DISCORD_CHANNEL_ID", "").strip()
    m = re.search(r"\d{17,20}", channel)
    if not m:
        print("ERROR: DISCORD_CHANNEL_ID 없음/형식오류", file=sys.stderr)
        return 2
    channel = m.group(0)

    # 우리 봇 토큰 (primary + 역할별) → user id 집합
    tokens = {
        k: v
        for k, v in env.items()
        if k == "DISCORD_TOKEN" or k.startswith("DISCORD_TOKEN_")
    }
    id_to_token: dict[str, str] = {}
    for _k, tok in tokens.items():
        bid = _bot_id(tok)
        if bid:
            id_to_token[bid] = tok
    if not id_to_token:
        print("ERROR: 봇 user id 0 (토큰/네트워크 확인)", file=sys.stderr)
        return 2
    print(f"우리 봇 {len(id_to_token)}개 식별. 채널={channel}")
    print(
        f"창(KST): {args.after_kst} ~ {args.before_kst}  (UTC {after_utc:%H:%M}~{before_utc:%H:%M})"
    )

    # 인벤토리: 채널 history를 읽을 수 있는 봇 토큰을 찾는다(primary가 채널 미소속=401일 수 있음).
    reader = None
    for tok in id_to_token.values():
        try:
            rr = httpx.get(
                f"{API}/channels/{channel}/messages",
                headers={"Authorization": f"Bot {tok}"},
                params={"limit": 1},
                timeout=15,
            )
            if rr.status_code == 200:
                reader = tok
                break
        except Exception:
            continue
    if reader is None:
        print("ERROR: 채널 history를 읽을 수 있는 봇 토큰 없음", file=sys.stderr)
        return 2
    targets: list[
        tuple[str, str, datetime, str]
    ] = []  # (msg_id, author_id, ts, snippet)
    by_author: dict[str, int] = {}
    for msg, ts in _iter_messages(channel, reader, after_utc, before_utc):
        aid = str(msg["author"]["id"])
        if aid not in id_to_token:
            continue  # 우리 봇 아님 → 보호
        name = msg["author"].get("username", "?")
        snippet = (msg.get("content", "") or "").replace("\n", " ")[:60]
        targets.append((str(msg["id"]), aid, ts, f"[{name}] {snippet}"))
        by_author[name] = by_author.get(name, 0) + 1

    print(f"\n=== 대상(우리봇·창내) {len(targets)}건 ===")
    for name, n in sorted(by_author.items(), key=lambda x: -x[1]):
        print(f"  {name}: {n}")
    spam_pat = re.compile(
        r"Aether|김민준|노트북 Pro|email_send|근거 없음|작업 시작할게요|작업 마쳤어요"
        r"|meta\.json|F2P|서바이버|배송|처리됨|seq=|위험도|one_man_corp|품질 엔진"
    )
    nonspam = [
        (ts, desc) for _id, _aid, ts, desc in targets if not spam_pat.search(desc)
    ]
    print(
        f"\n--- 스팸패턴 매칭: {len(targets) - len(nonspam)}/{len(targets)}"
        f" (비매칭 {len(nonspam)}건=삭제 전 검토 대상) ---"
    )
    if nonspam:
        print("  ⚠ 비-스팸 의심(정상 메시지일 수 있음 — 시간순):")
        for ts, desc in sorted(nonspam):
            print(f"    {ts.astimezone(KST):%H:%M:%S} {desc}")
    else:
        print("  ✓ 전부 스팸패턴 매칭(데모/재방송) — 정상 메시지 0")

    if not args.apply:
        print(f"\n[DRY-RUN] 삭제 0. 실제 삭제하려면 --apply. (대상 {len(targets)}건)")
        return 0

    # 실제 삭제: 각 메시지를 그 author 봇 토큰으로 DELETE(자기메시지·MANAGE 불요)
    print(f"\n[APPLY] {len(targets)}건 삭제 시작...")
    ok = 0
    fail = 0
    for mid, aid, _ts, desc in targets:
        tok = id_to_token[aid]
        while True:
            r = httpx.delete(
                f"{API}/channels/{channel}/messages/{mid}",
                headers={"Authorization": f"Bot {tok}"},
                timeout=20,
            )
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 1.0)) + 0.2)
                continue
            break
        if r.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {r.status_code} {desc[:40]}")
        time.sleep(0.35)  # 레이트리밋 여유
    print(f"\n삭제 완료: ok={ok} fail={fail} / 전체 {len(targets)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
