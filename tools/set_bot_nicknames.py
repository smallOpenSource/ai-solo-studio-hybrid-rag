"""tools/set_bot_nicknames.py — 9 페르소나의 길드 닉네임을 기존 봇에 일괄 설정.

★set_bot_avatars.py 패턴: 게이트웨이 불요(라이브 봇 무간섭)·봇당 REST 소수회·토큰별 독립
rate-limit 버킷. 토큰은 .env 에서 READ만·절대 출력/로그 금지(role·name·HTTP 상태만 출력).

매핑·닉네임 SSoT = config/personas.yaml (office.personas). 매핑 봇 = config/endpoint.yaml discord.bots.
guild_id = DISCORD_GUILD_ID 우선, 없으면 secretary(primary) 토큰으로 GET /channels/{DISCORD_CHANNEL_ID}.

모드:
  (기본)     : dry-run — 네트워크 0, 설정 계획만 출력.
  --apply    : 실제 PATCH /guilds/{id}/members/@me {nick}.
  --restore  : 닉네임 = 빈 문자열(username 복원). --apply 와 함께면 실제 적용.

실행: PYTHONPATH=/opt/studio \
      /opt/conda/envs/one_man_corp-discord/bin/python tools/set_bot_nicknames.py [--apply] [--restore]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

from office.personas import mappable_personas, nickname_for

_ROOT = Path(__file__).resolve().parents[1]
_API = "https://discord.com/api/v10"
_UA = "DiscordBot (https://example.com/bot, 1.0)"  # Cloudflare 1010 회피


def _load_bots() -> list[dict[str, Any]]:
    cfg = yaml.safe_load(
        (_ROOT / "config" / "endpoint.yaml").read_text(encoding="utf-8")
    )
    return (cfg.get("discord", {}) or {}).get("bots", []) or []


def find_bot(bots: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    """endpoint.yaml discord.bots 에서 role 일치 봇(없으면 None). 순수."""
    for bot in bots:
        if bot.get("role") == role:
            return bot
    return None


def build_nick_plan(
    personas: list[dict[str, Any]], bots: list[dict[str, Any]], *, restore: bool = False
) -> list[dict[str, str]]:
    """매핑 페르소나 → {role,name,token_env,nick} 계획(순수·결정론).

    restore=True 면 nick='' (username 복원). bot_role 이 bots 에 없으면 제외.
    """
    plan: list[dict[str, str]] = []
    for persona in personas:
        bot = find_bot(bots, str(persona.get("bot_role", "")))
        if not bot:
            continue
        plan.append(
            {
                "role": str(bot.get("role", "")),
                "name": str(bot.get("name", "")),
                "token_env": str(bot.get("token_env", "")),
                "nick": "" if restore else nickname_for(persona),
            }
        )
    return plan


def parse_guild_from_channel(channel_json: dict[str, Any]) -> str | None:
    """GET /channels/{id} 응답에서 guild_id 추출(순수). 없으면 None."""
    gid = channel_json.get("guild_id")
    return str(gid) if gid else None


def classify_status(code: int) -> str:
    """HTTP 상태 → 결과 분류(순수): ok / rate-limited / skip(권한·미소속) / fail."""
    if code in (200, 204):
        return "ok"
    if code == 429:
        return "rate-limited"
    if code in (403, 404):
        return "skip"
    return "fail"


def _resolve_guild_id(bots: list[dict[str, Any]], env: Any) -> str | None:
    """DISCORD_GUILD_ID 우선, 없으면 secretary(primary) 토큰으로 channel→guild 유도."""
    gid = env.get("DISCORD_GUILD_ID")
    if gid:
        return str(gid)
    channel_id = env.get("DISCORD_CHANNEL_ID")
    if not channel_id:
        return None
    sec = find_bot(bots, "secretary") or next(
        (b for b in bots if b.get("primary")), None
    )
    token = env.get(str((sec or {}).get("token_env", ""))) if sec else None
    if not token:
        return None
    try:
        resp = httpx.get(
            f"{_API}/channels/{channel_id}",
            headers={"Authorization": f"Bot {token}", "User-Agent": _UA},
            timeout=15.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return parse_guild_from_channel(resp.json())


def _patch_nick(token: str, guild_id: str, nick: str) -> tuple[int, str]:
    """PATCH 길드 닉네임. (status, 짧은 에러바디[토큰 무포함]) 반환."""
    resp = httpx.patch(
        f"{_API}/guilds/{guild_id}/members/@me",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": _UA,
            "Content-Type": "application/json",
        },
        json={"nick": nick},
        timeout=30.0,
    )
    return resp.status_code, (resp.text[:120] if resp.status_code >= 400 else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="봇 길드 닉네임 일괄 설정(페르소나)")
    ap.add_argument("--apply", action="store_true", help="실제 PATCH(미지정=dry-run)")
    ap.add_argument(
        "--restore", action="store_true", help="닉네임을 빈 문자열로(username 복원)"
    )
    args = ap.parse_args(argv)
    dry = not args.apply

    load_dotenv(_ROOT / ".env", override=False)
    env = os.environ
    bots = _load_bots()
    plan = build_nick_plan(mappable_personas(), bots, restore=args.restore)

    label = "복원(빈 닉네임)" if args.restore else "설정"
    print(
        f"== 봇 닉네임 {label} · {'DRY-RUN(네트워크 0)' if dry else 'APPLY'} · 대상 {len(plan)}봇 =="
    )

    if dry:
        for item in plan:
            print(
                f"[plan] {item['role']} ({item['name']}) → '{item['nick'] or '(빈=복원)'}'"
            )
        print(
            "\n(dry-run: --apply 로 실제 적용. guild_id 는 apply 시 DISCORD_GUILD_ID/channel 유도.)"
        )
        return 0

    guild_id = _resolve_guild_id(bots, env)
    if not guild_id:
        print(
            "[fail] guild_id 미해결 — DISCORD_GUILD_ID 또는 DISCORD_CHANNEL_ID/secretary 토큰 확인"
        )
        return 1

    counts = {"ok": 0, "skip": 0, "rate-limited": 0, "fail": 0}
    for item in plan:
        token = env.get(item["token_env"], "")
        if not token:
            print(f"[skip] {item['role']} ({item['name']}): 토큰 없음")
            counts["skip"] += 1
            continue
        try:
            code, body = _patch_nick(token, guild_id, item["nick"])
        except httpx.HTTPError as exc:
            print(
                f"[fail] {item['role']} ({item['name']}): 네트워크 {type(exc).__name__}"
            )
            counts["fail"] += 1
            continue
        result = classify_status(code)
        counts[result] += 1
        if result == "ok":
            print(
                f"[ok] {item['role']} ({item['name']}) → '{item['nick'] or '(복원)'}'"
            )
        elif result == "rate-limited":
            print(
                f"[rate-limited] {item['role']} ({item['name']}): 429 — 잠시 후 재실행"
            )
        elif result == "skip":
            print(
                f"[skip] {item['role']} ({item['name']}): HTTP {code}(길드 미소속/권한) — 초대 확인"
            )
        else:
            print(f"[fail] {item['role']} ({item['name']}): HTTP {code} {body}")

    print(
        f"\n== 요약: ok={counts['ok']} skip={counts['skip']} "
        f"rate-limited={counts['rate-limited']} fail={counts['fail']} (총 {len(plan)}) =="
    )
    return 0 if counts["fail"] == 0 and counts["rate-limited"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
