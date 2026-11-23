"""python -m adapters.discord 진입점.

.env 로드(python-dotenv) → bot.main() 호출. 토큰 부재 시 bot.main 이 "라이브
비활성" 로그를 남기고 정상 반환하므로 exit 0(크래시·NotImplementedError 0).

별도 워커 프로세스(conda env one_man_corp-discord). web_server 는 별도로 떠 있어야
한다(이 진입점은 그 SSE/REST 를 HTTP 로 소비할 뿐, import 하지 않는다).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from adapters.discord.bot import main


def _load_env() -> None:
    """프로젝트 루트 .env 를 환경변수로 로드(이미 설정된 값은 보존)."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


if __name__ == "__main__":
    _load_env()
    main()
