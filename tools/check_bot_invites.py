"""봇 16개가 실제로 서버(guild)에 초대됐는지 확인하고, 미초대 봇의 초대 URL을 출력한다.

게이트웨이 연결(토큰만 있으면 됨) ≠ 서버 멤버십(OAuth2 초대 필요). 초대 안 된 봇은
메시지 이벤트를 못 받아 @멘션이 동작하지 않는다. ★토큰은 READ만(절대 출력 안 함).

실행: PYTHONPATH=/opt/studio \
      /opt/conda/envs/one_man_corp-discord/bin/python tools/check_bot_invites.py
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
# View Channels(1024) + Send Messages(2048) + Read Message History(65536).
_PERMS = 1024 + 2048 + 65536


def main() -> None:
    load_dotenv(_ROOT / ".env", override=False)
    cfg = yaml.safe_load(
        (_ROOT / "config" / "endpoint.yaml").read_text(encoding="utf-8")
    )
    bots = (cfg.get("discord", {}) or {}).get("bots", []) or []

    invited = missing = fail = 0
    not_invited_urls: list[str] = []
    for entry in bots:
        role = str(entry.get("role", "?"))
        name = str(entry.get("name", ""))
        token = os.environ.get(entry.get("token_env", ""), "")
        if not token:
            print(f"[skip] {role} ({name}): 토큰 없음")
            continue
        headers = {"Authorization": f"Bot {token}"}
        try:
            g = httpx.get(
                "https://discord.com/api/v10/users/@me/guilds",
                headers=headers,
                timeout=30.0,
            )
            me = httpx.get(
                "https://discord.com/api/v10/users/@me", headers=headers, timeout=30.0
            )
        except httpx.HTTPError as exc:
            print(f"[fail] {role} ({name}): 네트워크 {type(exc).__name__}")
            fail += 1
            continue
        if g.status_code != 200 or me.status_code != 200:
            print(
                f"[fail] {role} ({name}): guilds {g.status_code} / me {me.status_code}"
            )
            fail += 1
            continue
        guilds = g.json()
        app_id = me.json().get("id", "")
        names = [gg.get("name", "?") for gg in guilds]
        if guilds:
            print(f"  ✅ {role:11s} ({name}) — 서버 {len(guilds)}개: {names}")
            invited += 1
        else:
            invite = (
                f"https://discord.com/api/oauth2/authorize"
                f"?client_id={app_id}&permissions={_PERMS}&scope=bot"
            )
            print(f"  ❌ {role:11s} ({name}) — 미초대(0서버)")
            not_invited_urls.append(f"{role} ({name}): {invite}")
            missing += 1

    print(
        f"\n=== 요약: 초대됨={invited} 미초대={missing} 실패={fail} (총 {len(bots)}) ==="
    )
    if not_invited_urls:
        print("\n### 미초대 봇 초대 URL (클릭해 회사 서버 선택) ###")
        for line in not_invited_urls:
            print(line)


if __name__ == "__main__":
    main()
