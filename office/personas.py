"""office.personas — SR/renewal §6.2 9-페르소나 정의 로더 (config/personas.yaml).

★데이터 주도: 페르소나·봇 매핑 SSoT = config/personas.yaml. 이 모듈은 읽기/조회 헬퍼만.
토큰·비밀 0 (bot_role 로 endpoint.yaml discord.bots 를 가리킬 뿐). graph 배선 없음(범위 밖·다음 cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_PERSONAS_PATH = _ROOT / "config" / "personas.yaml"

NICK_MAX = 32  # Discord 길드 닉네임 길이 한도
_NICK_SEP = " · "


def load_personas(path: Path | None = None) -> list[dict[str, Any]]:
    """config/personas.yaml 의 personas 리스트(파일 순서 보존)."""
    p = path or _PERSONAS_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    personas = data.get("personas")
    if not isinstance(personas, list):
        raise ValueError("personas.yaml: 'personas' 는 리스트여야 함")
    return personas


def mappable_personas(path: Path | None = None) -> list[dict[str, Any]]:
    """닉네임 설정 대상(skip_nick 이 truthy 가 아닌) 페르소나만."""
    return [p for p in load_personas(path) if not p.get("skip_nick")]


def nickname_for(persona: dict[str, Any]) -> str:
    """표시 닉네임 = '{display_name} · {label}'. NICK_MAX 초과 시 ValueError(순수·결정론)."""
    name = str(persona.get("display_name", "")).strip()
    label = str(persona.get("label", "")).strip()
    nick = f"{name}{_NICK_SEP}{label}" if label else name
    if len(nick) > NICK_MAX:
        raise ValueError(f"닉네임 {NICK_MAX}자 초과: {nick!r} ({len(nick)}자)")
    return nick


def persona_by_bot_role(
    bot_role: str, path: Path | None = None
) -> dict[str, Any] | None:
    """주어진 bot_role(endpoint.yaml discord.bots[].role) 에 매핑된 페르소나(없으면 None)."""
    for persona in load_personas(path):
        if persona.get("bot_role") == bot_role:
            return persona
    return None
