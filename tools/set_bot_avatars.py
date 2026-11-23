"""일회성: 직원 봇 16개의 아바타를 portrait로 설정한다(REST PATCH /users/@me).

★게이트웨이 불요(실행 중인 라이브 봇 무간섭)·봇당 REST 1회(토큰별 독립 rate-limit
버킷이라 안전)·아바타는 Discord에 영속(재실행 불요). 토큰은 .env에서 READ만 하고
절대 출력/로그하지 않는다(role·name·HTTP 상태만 출력).

실행: PYTHONPATH=/opt/studio \
      /opt/conda/envs/one_man_corp-discord/bin/python tools/set_bot_avatars.py
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
_API = "https://discord.com/api/v10/users/@me"


def main() -> None:
    load_dotenv(_ROOT / ".env", override=False)
    cfg = yaml.safe_load(
        (_ROOT / "config" / "endpoint.yaml").read_text(encoding="utf-8")
    )
    bots = (cfg.get("discord", {}) or {}).get("bots", []) or []

    ok = skip = fail = 0
    for entry in bots:
        role = str(entry.get("role", "?"))
        name = str(entry.get("name", ""))
        token = os.environ.get(entry.get("token_env", ""), "")
        if not token:
            print(f"[skip] {role} ({name}): 토큰 없음")
            skip += 1
            continue
        avatar_rel = str(entry.get("avatar", "")).lstrip("/")
        avatar_path = _ROOT / "web" / "public" / avatar_rel
        if not avatar_rel or not avatar_path.exists():
            print(f"[skip] {role} ({name}): 아바타 파일 없음 ({avatar_path})")
            skip += 1
            continue
        b64 = base64.b64encode(avatar_path.read_bytes()).decode("ascii")
        data_uri = f"data:image/png;base64,{b64}"
        try:
            resp = httpx.patch(
                _API,
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                json={"avatar": data_uri},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            print(f"[fail] {role} ({name}): 네트워크 {type(exc).__name__}")
            fail += 1
            continue
        if resp.status_code == 200:
            print(f"[ok] {role} ({name}) 아바타 설정")
            ok += 1
        elif resp.status_code == 429:
            retry = resp.headers.get("retry-after", "?")
            print(
                f"[rate-limited] {role} ({name}): 429 retry-after={retry}s — 잠시 후 재실행"
            )
            fail += 1
        else:
            # ★Discord 오류 body엔 토큰이 없다(상태/메시지만). 안전하게 일부만 출력.
            print(f"[fail] {role} ({name}): HTTP {resp.status_code} {resp.text[:120]}")
            fail += 1

    print(f"\n=== 요약: ok={ok} skip={skip} fail={fail} (총 {len(bots)}) ===")


if __name__ == "__main__":
    main()
