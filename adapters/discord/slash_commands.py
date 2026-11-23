"""P6e — Discord `/cmd <agent> <지시>` 슬래시 명령의 **순수 로직**(discord/office 무의존).

스펙 §9.5 리터럴 슬래시 `/cmd`. 기존 봇은 `on_message` 자연어(NL)만 처리했고
슬래시 트리는 없었다 — `/cmd` 는 **추가** 입력 경로다(on_message NL 경로 0수정).

★어댑터 비침투 불변식(테스트 강제):
  - discord import 는 bot.py 에만 → 이 모듈은 discord 무의존(트리 등록·콜백 본문은 bot.py).
  - office/web_server import 0 → personas.yaml 을 **직접 yaml 로드**(bot._read_config 와
    동일 비침투 패턴·office.personas 미import).
이 모듈은 순수 매핑(agent id → 디스패치 키)만 담당해 단위 테스트가 discord 없이 돈다.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 프로젝트 루트 = 이 파일 위치 기준(cursor.py/bot.py 와 동일 방식·office import 회피).
_ROOT = Path(__file__).resolve().parents[2]
_PERSONAS_PATH = _ROOT / "config" / "personas.yaml"


def _load_personas() -> list[dict]:
    """config/personas.yaml 의 personas 리스트(비침투·yaml 직접 로드).

    office.personas 를 import 하지 않는다(어댑터 비침투 불변식). 조회 실패 시 빈 목록.
    """
    try:
        import yaml

        data = yaml.safe_load(_PERSONAS_PATH.read_text(encoding="utf-8")) or {}
        personas = data.get("personas")
        return personas if isinstance(personas, list) else []
    except Exception as exc:  # noqa: BLE001 — 설정 부재가 봇을 죽이지 않음(거부로 폴백)
        logger.warning("personas.yaml 로드 실패 — /cmd agent 목록 비움: %s", exc)
        return []


def known_agent_ids() -> list[str]:
    """`/cmd` 가 허용하는 agent(페르소나 id) 목록(personas.yaml 파생·결정론).

    수 하드코딩 0 — 파일에서 파생한다.
    """
    return [str(p.get("id")) for p in _load_personas() if p.get("id")]


def resolve_cmd_target(agent: str) -> str | None:
    """`/cmd` 의 agent 인자 → 디스패치 키(bot_role). 미지 agent 면 None(순수·결정론).

    agent 는 페르소나 id(예: dev/research/qa). personas.yaml 에 없으면 None →
    콜백이 거부한다(enqueue 0). 매핑 성공 시 bot_role 을 target_role 로 enqueue 한다 —
    응답 run_id(chat-{bot_role}-) 가 이 키로 인코딩돼 그 직원 봇이 자기 정체성으로
    게시한다(bot.py `_role_from_run_id`/`_registry` 와 정합).
    """
    target = (agent or "").strip()
    if not target:
        return None
    for persona in _load_personas():
        if persona.get("id") == target:
            bot_role = persona.get("bot_role")
            return str(bot_role) if bot_role else None
    return None
