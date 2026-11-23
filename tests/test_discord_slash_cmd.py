"""P6e — Discord `/cmd <agent> <지시>` 슬래시 명령 테스트(NL 무회귀·primary sync 가드).

★★★ 안전 규칙(hang 사고 재발 차단) ★★★
- 모든 async 테스트는 asyncio.wait_for(coro, timeout=5) 로 bound.
- 실 discord 게이트웨이·client.run·tree.sync(라이브) 0 — sync 는 monkeypatch 로 흉내.
- post_inbound 은 rest_client monkeypatch + mock interaction 으로만 호출.

분해:
- resolve_cmd_target 단위(순수·결정론): 페르소나 id→bot_role 매핑·미지/빈 None.
- known_agent_ids: personas.yaml 파생(수 하드코딩 0).
- _handle_cmd: 정상 enqueue(target_role=bot_role) / 미지 agent 거부(enqueue 0) /
  빈 지시 거부 / 401 → 바운드 재인증 1회 후 재시도(상한 1).
- primary 봇만 tree.sync 호출(non-primary 미호출) — setup_hook 가드 단언.
- NL on_message 회귀 불변(classify_intent stage/ambiguous/chat).
"""

from __future__ import annotations

import asyncio

import discord
import pytest

import adapters.discord.bot as bot_mod
from adapters.discord.bot import DiscordBot
from adapters.discord.commands import classify_intent
from adapters.discord.slash_commands import known_agent_ids, resolve_cmd_target

_BOUND = 5.0


# ── mock 헬퍼(test_discord_bot.py 패턴 계승) ─────────────────────────────────────


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.messages: list[dict] = []

    async def defer(self, ephemeral: bool = False) -> None:
        self.deferred = True

    async def send_message(self, content: str, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})


class _FakeInteraction:
    """discord.Interaction mock — response/followup + 인터랙션 id."""

    def __init__(self, interaction_id: int = 12345) -> None:
        self.id = interaction_id
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _make_cmd_bot() -> DiscordBot:
    """게이트웨이 미연결 DiscordBot + _cookie/_reauth stub(재인증 1회 카운트).

    `_handle_cmd` 는 DiscordBot 메서드(bot.py)라 실 봇 인스턴스로 호출한다(discord
    import 는 bot.py 에만 허용 불변식 준수). reauth_calls 로 재인증 상한을 단언한다.
    """
    intents = discord.Intents.default()
    intents.message_content = True
    bot = DiscordBot(channel_id=999, base_url="http://localhost:8800", intents=intents)
    bot._cookie_val: str | None = None  # type: ignore[attr-defined]
    bot.reauth_calls = 0  # type: ignore[attr-defined]
    bot._cookie = lambda: bot._cookie_val  # type: ignore[assignment]

    async def _reauth() -> str | None:
        bot.reauth_calls += 1  # type: ignore[attr-defined]
        bot._cookie_val = "omc_session=fresh"  # type: ignore[attr-defined]
        return bot._cookie_val  # type: ignore[attr-defined]

    bot._reauth = _reauth  # type: ignore[assignment]
    return bot


# ── 1. 순수 resolver / agent 목록 ────────────────────────────────────────────────


def test_resolve_cmd_target_maps_id_to_bot_role() -> None:
    """페르소나 id → 디스패치 키(bot_role) 결정론 매핑(personas.yaml)."""
    assert resolve_cmd_target("dev") == "dev"
    assert resolve_cmd_target("research") == "data"
    assert resolve_cmd_target("qa") == "devops"
    assert resolve_cmd_target("coo") == "pm"
    assert resolve_cmd_target("art") == "designer"


def test_resolve_cmd_target_unknown_and_empty_none() -> None:
    """미지 agent·빈 문자열·공백은 None(거부 트리거)."""
    assert resolve_cmd_target("nope") is None
    assert resolve_cmd_target("") is None
    assert resolve_cmd_target("   ") is None


def test_resolve_cmd_target_strips_whitespace() -> None:
    """앞뒤 공백은 무시하고 매핑(사용자 입력 관용)."""
    assert resolve_cmd_target("  dev  ") == "dev"


def test_known_agent_ids_derived_from_personas() -> None:
    """agent 목록은 personas.yaml 파생(수 하드코딩 0)·핵심 id 포함."""
    ids = known_agent_ids()
    assert "dev" in ids and "research" in ids and "qa" in ids
    # secretary(primary) 포함 — /cmd 는 모든 페르소나 id 허용(닉네임 제외와 무관).
    assert "secretary" in ids


# ── 2. /cmd 콜백 — enqueue / 거부 / 401 재인증 ───────────────────────────────────


async def test_cmd_enqueues_with_target_role(monkeypatch) -> None:
    """정상 `/cmd dev <지시>` → post_inbound(target_role='dev') 1회 + ephemeral 접수."""
    calls: list[dict] = []

    async def _fake_post_inbound(
        text, message_id, base_url, *, cookie=None, target_role=None
    ):
        calls.append(
            {
                "text": text,
                "message_id": message_id,
                "base_url": base_url,
                "cookie": cookie,
                "target_role": target_role,
            }
        )
        return bot_mod.rest_client.InboundResult(handled=True, status=200, error=None)

    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", _fake_post_inbound)

    bot = _make_cmd_bot()
    inter = _FakeInteraction(interaction_id=777)
    await asyncio.wait_for(bot._handle_cmd(inter, "dev", "버그 고쳐줘"), timeout=_BOUND)

    assert len(calls) == 1
    assert calls[0]["target_role"] == "dev"  # 페르소나 id dev → bot_role dev
    assert calls[0]["text"] == "버그 고쳐줘"
    assert calls[0]["message_id"] == "777"  # 인터랙션 id 가 멱등키
    assert inter.response.deferred is True
    assert any("지시 접수" in m["content"] for m in inter.followup.messages)
    assert all(m["ephemeral"] for m in inter.followup.messages)


async def test_cmd_unknown_agent_rejected_no_enqueue(monkeypatch) -> None:
    """미지 agent → ephemeral 거부 + post_inbound 미호출(enqueue 0)."""
    calls: list[dict] = []

    async def _fake_post_inbound(*a, **k):
        calls.append({"a": a, "k": k})
        return bot_mod.rest_client.InboundResult(handled=True, status=200, error=None)

    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", _fake_post_inbound)

    bot = _make_cmd_bot()
    inter = _FakeInteraction()
    await asyncio.wait_for(bot._handle_cmd(inter, "nobody", "뭐든"), timeout=_BOUND)

    assert calls == []  # enqueue 0
    assert inter.response.deferred is False  # defer 도 안 함(즉시 거부)
    assert any("알 수 없는 직원" in m["content"] for m in inter.response.messages)


async def test_cmd_empty_instruction_rejected(monkeypatch) -> None:
    """빈/공백 지시 → 거부 + enqueue 0."""
    calls: list = []

    async def _fake_post_inbound(*a, **k):
        calls.append(1)
        return bot_mod.rest_client.InboundResult(handled=True, status=200, error=None)

    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", _fake_post_inbound)

    bot = _make_cmd_bot()
    inter = _FakeInteraction()
    await asyncio.wait_for(bot._handle_cmd(inter, "dev", "   "), timeout=_BOUND)

    assert calls == []
    assert any("비어" in m["content"] for m in inter.response.messages)


async def test_cmd_401_reauth_once_then_retry(monkeypatch) -> None:
    """401 → 바운드 재인증 1회 후 1회만 재시도(상한 1·무한 재인증 0)."""
    statuses = [401, 200]
    calls: list[str | None] = []

    async def _fake_post_inbound(
        text, message_id, base_url, *, cookie=None, target_role=None
    ):
        calls.append(cookie)
        status = statuses[len(calls) - 1] if len(calls) <= len(statuses) else 200
        return bot_mod.rest_client.InboundResult(
            handled=(status == 200), status=status, error=None
        )

    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", _fake_post_inbound)

    bot = _make_cmd_bot()
    inter = _FakeInteraction()
    await asyncio.wait_for(
        bot._handle_cmd(inter, "research", "시장 조사해줘"), timeout=_BOUND
    )

    assert bot.reauth_calls == 1  # 정확히 1회 재인증
    assert len(calls) == 2  # 최초 + 재시도 1회(상한 1)
    assert calls[0] is None and calls[1] == "omc_session=fresh"  # 재인증 쿠키로 재시도
    assert any("지시 접수" in m["content"] for m in inter.followup.messages)


# ── 3. primary sync 가드(non-primary 미호출) ─────────────────────────────────────


def _make_bot(consume_sse: bool, role: str) -> DiscordBot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = DiscordBot(
        channel_id=999,
        base_url="http://localhost:8800",
        intents=intents,
        role=role,
        consume_sse=consume_sse,
    )
    # 게이트웨이 미연결 stub.
    bot.wait_until_ready = lambda: asyncio.sleep(0)  # type: ignore[assignment]
    return bot


async def test_primary_bot_syncs_cmd(monkeypatch) -> None:
    """primary(consume_sse=True) 봇은 setup_hook 에서 tree.sync 1회 호출."""
    bot = _make_bot(consume_sse=True, role="secretary")
    synced: list[int] = []

    async def _fake_sync():
        synced.append(1)

    async def _noop():
        return None

    monkeypatch.setattr(bot.tree, "sync", _fake_sync)
    monkeypatch.setattr(bot, "_ensure_session", _noop)
    monkeypatch.setattr(bot, "_backfill_persistent_views", _noop)
    # SSE 소비 루프는 띄우지 않게(create_task 차단) — 즉시 종료 stub.
    monkeypatch.setattr(bot, "_sse_consume_loop", _noop)

    await asyncio.wait_for(bot.setup_hook(), timeout=_BOUND)
    # create_task 로 띄운 _sse_consume_loop 가 깨끗이 끝나도록 한 틱 양보.
    await asyncio.sleep(0)

    assert synced == [1]
    assert bot.synced_cmd is True


async def test_non_primary_bot_does_not_sync_cmd(monkeypatch) -> None:
    """★non-primary(consume_sse=False) 봇은 tree.sync 미호출(N봇 중복 등록 차단)."""
    bot = _make_bot(consume_sse=False, role="dev")
    synced: list[int] = []

    async def _fake_sync():
        synced.append(1)

    async def _noop():
        return None

    monkeypatch.setattr(bot.tree, "sync", _fake_sync)
    monkeypatch.setattr(bot, "_ensure_session", _noop)

    await asyncio.wait_for(bot.setup_hook(), timeout=_BOUND)

    assert synced == []  # ★sync 미호출
    assert bot.synced_cmd is False


async def test_sync_failure_does_not_crash_setup(monkeypatch) -> None:
    """tree.sync 실패(권한·레이트리밋)는 흡수 — setup_hook 비중단(`/cmd` 만 미등록)."""
    bot = _make_bot(consume_sse=True, role="secretary")

    async def _boom():
        raise RuntimeError("rate limited")

    async def _noop():
        return None

    monkeypatch.setattr(bot.tree, "sync", _boom)
    monkeypatch.setattr(bot, "_ensure_session", _noop)
    monkeypatch.setattr(bot, "_backfill_persistent_views", _noop)
    monkeypatch.setattr(bot, "_sse_consume_loop", _noop)

    await asyncio.wait_for(bot.setup_hook(), timeout=_BOUND)
    await asyncio.sleep(0)

    assert bot.synced_cmd is False  # 흡수됨(크래시 0)


# ── 4. NL on_message 회귀 불변(/cmd 는 추가 경로) ────────────────────────────────


def test_nl_classify_intent_regression_unchanged() -> None:
    """classify_intent(NL) 분류가 /cmd 추가로 변하지 않음(stage/ambiguous/chat)."""
    assert classify_intent("게임 밸런스 작업 시작해줘").kind == "stage"
    assert classify_intent("게임 만들어줘").kind == "ambiguous"
    assert classify_intent("오늘 날씨 어때?").kind == "chat"
    # 비명령(질문/부정)은 chat 유지(워크플로 트리거 0).
    assert classify_intent("게임 디자인 어떻게 할까?").kind == "chat"


def test_on_message_source_unchanged_by_cmd() -> None:
    """on_message 본문에 /cmd 분기가 침투하지 않음(NL 경로 byte 보존 단언)."""
    import inspect

    src = inspect.getsource(DiscordBot.on_message)
    # on_message 는 classify_intent 기반 NL 라우팅만 — 슬래시/cmd 토큰 0.
    assert "classify_intent" in src
    assert "tree" not in src and "/cmd" not in src and "app_command" not in src


@pytest.mark.parametrize("text", ["", "x"])
def test_resolve_is_pure_repeatable(text: str) -> None:
    """같은 입력 → 같은 출력(순수·결정론)."""
    assert resolve_cmd_target(text) == resolve_cmd_target(text)
