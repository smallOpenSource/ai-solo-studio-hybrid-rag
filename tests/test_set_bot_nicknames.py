"""tools/set_bot_nicknames.py + office/personas.py 결정론 단위 + httpx mock 통합.

라이브 Discord 미접촉(mock·dry-run만). 비밀 부재 단언 포함.
실행: one_man_corp-discord env · PYTHONPATH=repo root.
"""

from __future__ import annotations

import httpx
import pytest

from office.personas import (
    NICK_MAX,
    load_personas,
    mappable_personas,
    nickname_for,
    persona_by_bot_role,
)
from tools.set_bot_nicknames import (
    build_nick_plan,
    classify_status,
    find_bot,
    main,
    parse_guild_from_channel,
    _patch_nick,
    _resolve_guild_id,
)

# 기존 16봇 로스터 stub(endpoint.yaml discord.bots 형상).
_BOTS = [
    {
        "role": "secretary",
        "name": "이수빈",
        "token_env": "DISCORD_TOKEN_SECRETARY",
        "primary": True,
    },
    {"role": "pm", "name": "정민준", "token_env": "DISCORD_TOKEN_PM"},
    {"role": "ceo", "name": "김도윤", "token_env": "DISCORD_TOKEN_CEO"},
    {"role": "strategist", "name": "이서연", "token_env": "DISCORD_TOKEN_STRATEGIST"},
    {"role": "data", "name": "윤시우", "token_env": "DISCORD_TOKEN_DATA"},
    {"role": "designer", "name": "서유진", "token_env": "DISCORD_TOKEN_DESIGNER"},
    {"role": "planner", "name": "강하은", "token_env": "DISCORD_TOKEN_PLANNER"},
    {"role": "cs", "name": "문가영", "token_env": "DISCORD_TOKEN_CS"},
    {"role": "dev", "name": "한지훈", "token_env": "DISCORD_TOKEN_DEV"},
    {"role": "devops", "name": "오태경", "token_env": "DISCORD_TOKEN_DEVOPS"},
]

_EXPECTED_BOT_ROLES = {
    "ceo",
    "pm",
    "strategist",
    "data",
    "designer",
    "planner",
    "cs",
    "dev",
    "devops",
}


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


# ── office.personas ───────────────────────────────────────────────────────────
def test_loader_counts() -> None:
    assert len(load_personas()) == 10  # 9 + secretary(skip_nick)
    assert len(mappable_personas()) == 9  # secretary 제외


def test_nickname_format_and_max() -> None:
    p = {"display_name": "김도윤", "label": "CEO"}
    assert nickname_for(p) == "김도윤 · CEO"
    assert len(nickname_for(p)) <= NICK_MAX
    # 모든 매핑 페르소나 닉네임 ≤ 한도
    for persona in mappable_personas():
        assert len(nickname_for(persona)) <= NICK_MAX
    with pytest.raises(ValueError):
        nickname_for({"display_name": "x" * 30, "label": "y" * 10})


def test_persona_by_bot_role() -> None:
    assert persona_by_bot_role("ceo")["role"] == "CEO"
    assert persona_by_bot_role("nonexistent") is None


# ── pure helpers ──────────────────────────────────────────────────────────────
def test_find_bot() -> None:
    assert find_bot(_BOTS, "ceo")["name"] == "김도윤"
    assert find_bot(_BOTS, "ghost") is None


def test_build_nick_plan_mapping_real_personas() -> None:
    plan = build_nick_plan(mappable_personas(), _BOTS)
    assert len(plan) == 9
    assert {item["role"] for item in plan} == _EXPECTED_BOT_ROLES
    ceo = next(i for i in plan if i["role"] == "ceo")
    assert ceo["nick"] == "김도윤 · CEO"
    assert ceo["token_env"] == "DISCORD_TOKEN_CEO"
    assert all(" · " in i["nick"] and len(i["nick"]) <= NICK_MAX for i in plan)


def test_build_nick_plan_restore_empties() -> None:
    plan = build_nick_plan(mappable_personas(), _BOTS, restore=True)
    assert len(plan) == 9
    assert all(item["nick"] == "" for item in plan)


def test_build_nick_plan_skips_unmapped_bot_role() -> None:
    # bot_role 이 로스터에 없으면 제외(부분 로스터)
    partial = [b for b in _BOTS if b["role"] in {"ceo", "dev"}]
    plan = build_nick_plan(mappable_personas(), partial)
    assert {i["role"] for i in plan} == {"ceo", "dev"}


def test_parse_guild_from_channel() -> None:
    assert parse_guild_from_channel({"guild_id": "12345"}) == "12345"
    assert parse_guild_from_channel({"id": "x"}) is None
    assert parse_guild_from_channel({}) is None


def test_classify_status() -> None:
    assert classify_status(200) == "ok"
    assert classify_status(204) == "ok"
    assert classify_status(429) == "rate-limited"
    assert classify_status(403) == "skip"
    assert classify_status(404) == "skip"
    assert classify_status(500) == "fail"


# ── httpx mock 통합 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("code", [200, 403, 429, 500])
def test_patch_nick_mock(monkeypatch, code) -> None:
    captured = {}

    def fake_patch(url, headers, json, timeout):
        captured["url"] = url
        captured["auth"] = headers["Authorization"]
        return _Resp(code, text='{"message":"x"}')

    monkeypatch.setattr(httpx, "patch", fake_patch)
    status, body = _patch_nick("TOK", "999", "김도윤 · CEO")
    assert status == code
    assert captured["url"].endswith("/guilds/999/members/@me")
    assert "Bot TOK" in captured["auth"]
    assert (body == "") == (code < 400)  # 에러만 바디 노출


def test_resolve_guild_prefers_env(monkeypatch) -> None:
    def boom(*a, **k):  # env 있으면 네트워크 0
        raise AssertionError("network should not be called when DISCORD_GUILD_ID set")

    monkeypatch.setattr(httpx, "get", boom)
    assert _resolve_guild_id(_BOTS, {"DISCORD_GUILD_ID": "777"}) == "777"


def test_resolve_guild_via_channel(monkeypatch) -> None:
    def fake_get(url, headers, timeout):
        assert "/channels/CHAN" in url
        return _Resp(200, payload={"guild_id": "888"})

    monkeypatch.setattr(httpx, "get", fake_get)
    env = {"DISCORD_CHANNEL_ID": "CHAN", "DISCORD_TOKEN_SECRETARY": "SECTOK"}
    assert _resolve_guild_id(_BOTS, env) == "888"


def test_resolve_guild_none_without_inputs(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(404))
    assert _resolve_guild_id(_BOTS, {}) is None


# ── 비밀 부재 (dry-run main) ──────────────────────────────────────────────────
def test_dry_run_no_secret_leak(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DISCORD_TOKEN_CEO", "SUPER_SECRET_SENTINEL_123")

    def boom(*a, **k):  # dry-run 은 네트워크 0
        raise AssertionError("dry-run must not call network")

    monkeypatch.setattr(httpx, "patch", boom)
    monkeypatch.setattr(httpx, "get", boom)
    rc = main([])  # 기본 dry-run
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert "SUPER_SECRET_SENTINEL_123" not in out  # 토큰 값 비노출
