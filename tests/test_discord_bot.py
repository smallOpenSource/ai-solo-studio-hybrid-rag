"""US-006a + US-006b — Discord 봇 통합 테스트 (mock + 진짜 asyncio 이벤트루프).

★★★ 안전 규칙(hang 사고 재발 차단) ★★★
- 모든 async 테스트는 asyncio.wait_for(coro, timeout=5) 로 bound.
- consume_events 는 실제(무기한)를 절대 돌리지 않는다 — 몇 프레임 yield 후 종료하는
  mock 비동기 제너레이터를 주입.
- wait_until_ready 는 즉시 반환하는 stub 으로 교체(게이트웨이 미연결).
- 실제 client.run / gateway 연결은 어느 테스트에서도 하지 않는다.

분해:
- US-006a (mock 한 사이클): _process_frame 으로 SSE→cursor가드→매핑→채널게시 검증 +
  버튼 클릭 시뮬→custom_id 파싱(seq)→REST approve 경로 단언→cursor 전진.
  토큰 부재 "라이브 비활성" 로그. 이중트리거 office-의존 모사. 비침투 정적 단언.
- US-006b (asyncio 실루프): 진짜 이벤트루프 + mock gateway 로 setup_hook 배선
  (create_task·wait_until_ready 위치) / 두 루프 교대(기아 0) / 예외 복원력 /
  persistent View 재등록 대상 선별(mock 한계 주석).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path


import adapters.discord.bot as bot_mod
from adapters.discord.bot import DiscordBot, _parse_custom_id

# 전체 테스트 bound 상한(초) — 어떤 async 테스트도 이를 넘기지 않는다.
_BOUND = 5.0


# ── mock 헬퍼 ──────────────────────────────────────────────────────────────────


class FakeChannel:
    """discord 채널 mock — send 호출을 기록한다."""

    def __init__(self) -> None:
        self.sends: list[dict] = []

    async def send(self, content: str | None = None, view: object = None) -> object:
        self.sends.append({"content": content, "view": view})
        return object()


class FakeResponse:
    """interaction.response mock — defer() 호출 여부 기록."""

    def __init__(self) -> None:
        self.deferred = False

    async def defer(self) -> None:
        self.deferred = True


class FakeFollowup:
    """interaction.followup mock — send 메시지 기록."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str, ephemeral: bool = False) -> None:
        self.messages.append(content)


class FakeMessage:
    """interaction.message mock — content + edit(버튼 제거) 기록."""

    def __init__(self, content: str = "[승인 요청] 노드=n3_email_send") -> None:
        self.content = content
        self.edited: dict | None = None

    async def edit(
        self, content: str | None = None, view: object = "__unset__"
    ) -> None:
        self.edited = {"content": content, "view": view}


class FakeInteraction:
    """discord.Interaction mock — response.defer + followup.send + message.edit 흉내."""

    def __init__(self) -> None:
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = FakeMessage()


def _mock_consume(frames: list[tuple[int, dict]]):
    """주어진 프레임을 순서대로 yield 후 **반드시 종료**하는 consume_events stub 팩토리.

    ★실제 무기한 consume_events 를 절대 돌리지 않기 위한 핵심 가드 — 유한 제너레이터.
    """

    async def _gen(
        base_url: str,
        last_event_id: int | None = None,
        max_retries: int | None = None,
        *,
        cookie: str | None = None,
        on_unauthorized: object = None,
    ) -> AsyncIterator[tuple[int, dict]]:
        for seq, payload in frames:
            yield seq, payload
            # 다른 태스크에 양보(두 루프 교대 검증용).
            await asyncio.sleep(0)

    return _gen


class _FakeRest:
    """rest_client.approve/reject mock — 호출 인자 기록 + handled=True 반환."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def approve(self, seq: int, base_url: str, *, cookie: str | None = None):
        self.calls.append(("approve", seq, base_url))
        return bot_mod.rest_client.ApproveResult(handled=True, status=200, error=None)

    async def reject(self, seq: int, base_url: str, *, cookie: str | None = None):
        self.calls.append(("reject", seq, base_url))
        return bot_mod.rest_client.ApproveResult(handled=True, status=200, error=None)


def _make_bot(channel: FakeChannel, **kw) -> DiscordBot:
    """게이트웨이 미연결 상태의 DiscordBot 생성 + 채널/대기 stub 주입."""
    bot = DiscordBot(channel_id=999, base_url="http://localhost:8800", **kw)
    # get_channel 이 fake 채널을 돌려주도록 — fetch_channel 경로 회피.
    bot.get_channel = lambda _cid: channel  # type: ignore[assignment]
    # wait_until_ready 즉시 반환(게이트웨이 미연결).
    bot.wait_until_ready = lambda: asyncio.sleep(0)  # type: ignore[assignment]
    return bot


# ════════════════════════════════════════════════════════════════════════════
# US-006a — mock 한 사이클
# ════════════════════════════════════════════════════════════════════════════


def test_parse_custom_id_formats() -> None:
    """custom_id 파싱: approve/reject:{seq} 만 (action, seq), 그 외 None."""
    assert _parse_custom_id("approve:42") == ("approve", 42)
    assert _parse_custom_id("reject:7") == ("reject", 7)
    assert _parse_custom_id("approve:notint") is None
    assert _parse_custom_id("delete:1") is None
    assert _parse_custom_id("nocolon") is None


# ════════════════════════════════════════════════════════════════════════════
# 콜드스타트 커서 — 커서 유실 시 0(전체 재방송) 대신 head 시드 (스팸 방지)
# ════════════════════════════════════════════════════════════════════════════


async def test_cold_start_seeds_head_when_cursor_absent(monkeypatch) -> None:
    """커서 부재(콜드스타트) → _resolve_start_cursor가 0이 아니라 head로 시드.

    과거 0..head 전체 재방송(라이브 디스코드 스팸)을 구조적으로 차단한다.
    """
    bot = _make_bot(FakeChannel())
    monkeypatch.setattr(
        bot_mod.cursor_mod, "cursor_initialized", lambda path=None: False
    )
    seeded: list[int] = []
    monkeypatch.setattr(
        bot_mod.cursor_mod, "advance_cursor", lambda seq, path=None: seeded.append(seq)
    )

    async def _head() -> int:
        return 774

    monkeypatch.setattr(bot, "_fetch_head_seq", _head)

    result = await bot._resolve_start_cursor()

    assert result == 774  # head부터 — 과거 재방송 안 함
    assert seeded == [774]  # cursor.json도 head로 전진(should_post 일관)


async def test_warm_start_resumes_from_cursor(monkeypatch) -> None:
    """커서 존재 → read_cursor 값으로 resume(head 무시·기존 at-least-once 보존)."""
    bot = _make_bot(FakeChannel())
    monkeypatch.setattr(
        bot_mod.cursor_mod, "cursor_initialized", lambda path=None: True
    )
    monkeypatch.setattr(bot_mod.cursor_mod, "read_cursor", lambda path=None: 500)

    async def _boom() -> int:
        raise AssertionError("warm start에서 head를 조회하면 안 됨")

    monkeypatch.setattr(bot, "_fetch_head_seq", _boom)

    assert await bot._resolve_start_cursor() == 500


async def test_cold_start_empty_store_returns_none(monkeypatch) -> None:
    """빈 스토어 콜드스타트(head=0) → None 반환(이벤트 없음)·커서 0 시드."""
    bot = _make_bot(FakeChannel())
    monkeypatch.setattr(
        bot_mod.cursor_mod, "cursor_initialized", lambda path=None: False
    )
    seeded: list[int] = []
    monkeypatch.setattr(
        bot_mod.cursor_mod, "advance_cursor", lambda seq, path=None: seeded.append(seq)
    )

    async def _head() -> int:
        return 0

    monkeypatch.setattr(bot, "_fetch_head_seq", _head)

    assert await bot._resolve_start_cursor() is None
    assert seeded == [0]


async def test_interrupt_requested_posts_with_buttons(monkeypatch) -> None:
    """INTERRUPT_REQUESTED 1프레임 → cursor가드 통과 → 매핑 → 채널에 버튼 메시지 게시."""
    # cursor: should_post=True(seq>0), advance_cursor 는 호출 기록만(파일 IO 회피).
    advanced: list[int] = []
    monkeypatch.setattr(bot_mod.cursor_mod, "should_post", lambda seq, path=None: True)
    monkeypatch.setattr(bot_mod.cursor_mod, "read_cursor", lambda path=None: 0)
    monkeypatch.setattr(
        bot_mod.cursor_mod,
        "advance_cursor",
        lambda seq, path=None: advanced.append(seq),
    )

    channel = FakeChannel()
    bot = _make_bot(channel)

    payload = {
        "type": "INTERRUPT_REQUESTED",
        "node_id": "n1",
        "tool": "send_email",
        "risk": "high",
    }
    await asyncio.wait_for(bot._process_frame(15, payload), timeout=_BOUND)

    assert len(channel.sends) == 1
    sent = channel.sends[0]
    assert "확인 부탁드려요" in sent["content"]  # 자연어 승인 요청(라벨 폐기)
    assert "send_email" in sent["content"]  # 도구는 자연어로 안내
    # 버튼이 달린 persistent View 가 첨부됨.
    assert isinstance(sent["view"], bot_mod.ApprovalView)
    assert sent["view"].timeout is None  # persistent 요건
    # 커서가 게시 성공 후 15 로 전진.
    assert advanced == [15]
    assert bot.posted_count == 1


async def test_cursor_guard_skips_already_posted(monkeypatch) -> None:
    """이미 게시한 seq(should_post=False) → 게시 0(재연결/재시작 스팸 방어)."""
    monkeypatch.setattr(bot_mod.cursor_mod, "should_post", lambda seq, path=None: False)
    advanced: list[int] = []
    monkeypatch.setattr(
        bot_mod.cursor_mod,
        "advance_cursor",
        lambda seq, path=None: advanced.append(seq),
    )

    channel = FakeChannel()
    bot = _make_bot(channel)

    payload = {
        "type": "INTERRUPT_REQUESTED",
        "node_id": "n",
        "tool": "t",
        "risk": "low",
    }
    await asyncio.wait_for(bot._process_frame(3, payload), timeout=_BOUND)

    assert channel.sends == []  # 중복 게시 0
    assert advanced == []  # 이미 처리한 seq → 커서 손대지 않음
    assert bot.skipped_count == 1


async def test_suppressed_event_advances_cursor_without_post(monkeypatch) -> None:
    """매핑 None(게시 절제) → 게시 0 + 커서는 전진(같은 seq 재평가 방지)."""
    monkeypatch.setattr(bot_mod.cursor_mod, "should_post", lambda seq, path=None: True)
    advanced: list[int] = []
    monkeypatch.setattr(
        bot_mod.cursor_mod,
        "advance_cursor",
        lambda seq, path=None: advanced.append(seq),
    )

    channel = FakeChannel()
    bot = _make_bot(channel)

    # STATE_SNAPSHOT → 항상 None.
    await asyncio.wait_for(
        bot._process_frame(8, {"type": "STATE_SNAPSHOT"}), timeout=_BOUND
    )

    assert channel.sends == []
    assert advanced == [8]  # 절제여도 커서 전진
    assert bot.skipped_count == 1
    assert bot.posted_count == 0


async def test_button_click_calls_rest_approve_path(monkeypatch) -> None:
    """버튼 클릭 시뮬 → defer ack → custom_id 파싱(seq) → REST approve 경로 단언."""
    fake_rest = _FakeRest()
    monkeypatch.setattr(bot_mod.rest_client, "approve", fake_rest.approve)
    monkeypatch.setattr(bot_mod.rest_client, "reject", fake_rest.reject)

    interaction = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(interaction, "approve:42", "http://localhost:8800"),
        timeout=_BOUND,
    )

    # 3초 ack(defer) 선행.
    assert interaction.response.deferred is True
    # REST approve 가 정확한 seq·base_url 로 1회 호출.
    assert fake_rest.calls == [("approve", 42, "http://localhost:8800")]
    # followup 으로 "처리됨" 안내.
    assert len(interaction.followup.messages) == 1
    assert "처리됨" in interaction.followup.messages[0]
    # 처리 후 원 메시지 버튼 제거(view=None) + 결과(✅) 표시 — 재클릭 방지.
    assert interaction.message.edited is not None
    assert interaction.message.edited["view"] is None
    assert "✅" in interaction.message.edited["content"]


async def test_button_click_reject_path(monkeypatch) -> None:
    """거부 버튼 → REST reject 경로."""
    fake_rest = _FakeRest()
    monkeypatch.setattr(bot_mod.rest_client, "approve", fake_rest.approve)
    monkeypatch.setattr(bot_mod.rest_client, "reject", fake_rest.reject)

    interaction = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(interaction, "reject:9", "http://localhost:8800"),
        timeout=_BOUND,
    )
    assert fake_rest.calls == [("reject", 9, "http://localhost:8800")]
    # 거부도 버튼 제거(view=None) + 🚫 결과 표시(approve와 대칭).
    assert interaction.message.edited is not None
    assert interaction.message.edited["view"] is None
    assert "🚫" in interaction.message.edited["content"]


async def test_button_click_rest_failure_followup(monkeypatch) -> None:
    """REST handled=False(5xx/네트워크) → followup '실패' 표시(크래시 0)."""

    async def _failing_approve(seq: int, base_url: str, *, cookie: str | None = None):
        return bot_mod.rest_client.ApproveResult(
            handled=False, status=500, error="서버 오류: HTTP 500"
        )

    monkeypatch.setattr(bot_mod.rest_client, "approve", _failing_approve)

    interaction = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(interaction, "approve:1", "http://localhost:8800"),
        timeout=_BOUND,
    )
    assert interaction.response.deferred is True
    assert len(interaction.followup.messages) == 1
    assert "실패" in interaction.followup.messages[0]


async def test_button_unknown_custom_id_no_rest_call(monkeypatch) -> None:
    """알 수 없는 custom_id → REST 미호출·크래시 0(defer 도 안 함)."""
    fake_rest = _FakeRest()
    monkeypatch.setattr(bot_mod.rest_client, "approve", fake_rest.approve)
    monkeypatch.setattr(bot_mod.rest_client, "reject", fake_rest.reject)

    interaction = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(interaction, "garbage", "http://localhost:8800"),
        timeout=_BOUND,
    )
    assert fake_rest.calls == []
    assert interaction.response.deferred is False


def test_token_absent_logs_live_disabled(caplog) -> None:
    """DISCORD_BOT_TOKEN 부재 → "라이브 비활성" 로그 + 정상 반환(크래시·run 0)."""
    # _read_config 가 토큰 없음을 반환하도록 stub(환경 무관 결정론).
    import adapters.discord.bot as b

    def _no_token_config() -> dict:
        return {
            "token": "",
            "channel_id": None,
            "base_url": "http://localhost:8800",
            "toggles": {},
            "password": "",
            "cookie_name": "omc_session",
        }

    orig = b._read_config
    b._read_config = _no_token_config  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.INFO):
            b.main()  # client.run 에 도달하면 안 됨(토큰 없음 → 조기 return)
    finally:
        b._read_config = orig  # type: ignore[assignment]

    assert any("라이브 비활성" in rec.message for rec in caplog.records)


async def test_double_trigger_office_dependency_simulation(monkeypatch) -> None:
    """이중 트리거 — web·discord 양쪽 승인 시뮬.

    어댑터 측 REST 는 양쪽 모두 호출되나(at-least-once), **도구 1회 보장은
    office 폴링 루프의 단일 return(run.py:241)에 의존**한다. 여기선 그 office 동작을
    "폴링 루프가 첫 결정만 resume 하고 두 번째는 무효"로 **정직하게 모사**한다.
    ★4a 실증 인용 금지 — 단일 승인에도 RESOLVED 2개라 어댑터가 차단하지 않는다.
    """
    fake_rest = _FakeRest()
    monkeypatch.setattr(bot_mod.rest_client, "approve", fake_rest.approve)
    monkeypatch.setattr(bot_mod.rest_client, "reject", fake_rest.reject)

    # discord 승인 + web 승인(같은 seq=20) 양쪽 REST 호출 시뮬.
    i1 = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(i1, "approve:20", "http://localhost:8800"),
        timeout=_BOUND,
    )
    # web 측은 직접 REST(same path) 한 번 더 — 어댑터는 막지 않는다.
    await asyncio.wait_for(
        bot_mod.rest_client.approve(20, "http://localhost:8800"), timeout=_BOUND
    )

    # 어댑터 관점: REST 는 2회 호출(차단 안 함 — RESOLVED 중복은 office 책임).
    assert fake_rest.calls.count(("approve", 20, "http://localhost:8800")) == 2

    # office 동작 모사: 폴링 루프는 첫 결정만 resume(단일 return) → 도구 1회.
    resume_calls: list[int] = []

    def _polling_loop_single_return(decisions: list[int]) -> None:
        # 첫 결정 감지 시 resume 1회 후 즉시 종료(run.py:231-246 단일 return).
        if decisions:
            resume_calls.append(decisions[0])  # 첫 승인만 resume → _run_tool 1회

    _polling_loop_single_return([20, 20])  # 두 번 승인됐어도
    assert resume_calls == [20]  # 도구는 1회(폴링 루프 단일 return 의존)


# ════════════════════════════════════════════════════════════════════════════
# DC-2 — on_message 인바운드 리스너 가드(★무한루프 1차 방벽)
# ════════════════════════════════════════════════════════════════════════════


class FakeAuthor:
    """message.author mock — bot 플래그 + 동등성(self.user 비교용)."""

    def __init__(self, *, bot: bool = False) -> None:
        self.bot = bot


class FakeChannelRef:
    """message.channel mock — id만."""

    def __init__(self, cid: int) -> None:
        self.id = cid


class FakeIncomingMessage:
    """discord.Message mock(on_message 입력) — author·channel·content·id."""

    def __init__(
        self,
        *,
        author: object,
        channel_id: int,
        content: str,
        message_id: int = 1,
        mentions: list | None = None,
    ) -> None:
        self.author = author
        self.channel = FakeChannelRef(channel_id)
        self.content = content
        self.id = message_id
        self.mentions = mentions or []  # @멘션 대상 목록(멀티봇 자기선택용).


class _FakeInbound:
    """rest_client.post_inbound mock — 호출 인자 기록 + handled=True 반환."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.target_roles: list[str | None] = []

    async def post_inbound(
        self,
        text: str,
        message_id: str,
        base_url: str,
        author: object = None,
        *,
        cookie: str | None = None,
        target_role: str | None = None,
    ):
        self.calls.append((text, message_id, base_url))
        self.target_roles.append(target_role)
        return bot_mod.rest_client.InboundResult(handled=True, status=200, error=None)


def _patch_inbound(monkeypatch) -> _FakeInbound:
    fake = _FakeInbound()
    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", fake.post_inbound)
    return fake


async def test_on_message_human_in_channel_calls_post_inbound(monkeypatch) -> None:
    """지정 채널의 사람 발화 → post_inbound 1회(text·message_id·base_url 정확)."""
    fake = _patch_inbound(monkeypatch)
    channel = FakeChannel()
    bot = _make_bot(channel)  # channel_id=999

    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False),
        channel_id=999,
        content="안녕하세요 봇",
        message_id=42,
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert fake.calls == [("안녕하세요 봇", "42", "http://localhost:8800")]


async def test_on_message_self_message_ignored(monkeypatch) -> None:
    """★봇 자신(self.user)의 발화는 무시 → post_inbound 미호출(에코/응답 재진입 차단)."""
    fake = _patch_inbound(monkeypatch)
    channel = FakeChannel()
    bot = _make_bot(channel)

    bot_user = FakeAuthor(bot=True)
    # self.user 를 sentinel 로 교체(읽기전용 property → 클래스 레벨 monkeypatch).
    monkeypatch.setattr(DiscordBot, "user", bot_user)

    msg = FakeIncomingMessage(author=bot_user, channel_id=999, content="에코")
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert fake.calls == []  # 자기 메시지 → 인바운드 0


async def test_on_message_bot_author_ignored(monkeypatch) -> None:
    """★타 봇/웹훅(author.bot=True)의 발화는 무시 → post_inbound 미호출(연쇄 차단)."""
    fake = _patch_inbound(monkeypatch)
    channel = FakeChannel()
    bot = _make_bot(channel)

    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=True), channel_id=999, content="타봇 발화"
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert fake.calls == []  # 봇 author → 인바운드 0


async def test_on_message_other_channel_ignored(monkeypatch) -> None:
    """지정 채널(999)이 아닌 채널의 발화는 무시 → post_inbound 미호출."""
    fake = _patch_inbound(monkeypatch)
    channel = FakeChannel()
    bot = _make_bot(channel)

    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False), channel_id=12345, content="다른 채널"
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert fake.calls == []  # 타 채널 → 인바운드 0


async def test_on_message_empty_content_ignored(monkeypatch) -> None:
    """빈/공백 내용은 무시 → post_inbound 미호출."""
    fake = _patch_inbound(monkeypatch)
    channel = FakeChannel()
    bot = _make_bot(channel)

    for content in ("", "   ", "\n\t"):
        msg = FakeIncomingMessage(
            author=FakeAuthor(bot=False), channel_id=999, content=content
        )
        await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert fake.calls == []  # 빈 내용 → 인바운드 0


async def test_on_message_echo_reentry_not_triggered(monkeypatch) -> None:
    """★v2 #7 에코 재진입: 봇이 게시한 에코 user 메시지(봇 author)가 다시 on_message로

    들어와도 post_inbound 미호출(무한 응답 루프 결정론 차단). 에코·응답은 전부
    channel.send(=봇 author·author.bot=True)라 author.bot 가드가 원천 차단한다.
    """
    fake = _patch_inbound(monkeypatch)
    channel = FakeChannel()
    bot = _make_bot(channel)

    # 봇이 게시한 메시지가 게이트웨이를 통해 on_message 로 되돌아오는 상황(author.bot=True).
    echoed = FakeIncomingMessage(
        author=FakeAuthor(bot=True), channel_id=999, content="[메시지:user] 안녕"
    )
    await asyncio.wait_for(bot.on_message(echoed), timeout=_BOUND)

    assert fake.calls == []  # 에코 재진입 → 인바운드 0(루프 차단)


async def test_on_message_exception_absorbed(monkeypatch) -> None:
    """post_inbound 예외가 on_message를 크래시시키지 않음(좀비봇 방어)."""

    async def _boom(text, message_id, base_url, author=None, *, cookie=None):
        raise RuntimeError("인바운드 폭발(주입)")

    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", _boom)
    channel = FakeChannel()
    bot = _make_bot(channel)

    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False), channel_id=999, content="내용"
    )
    # 예외를 흡수하고 정상 반환(크래시 0).
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)


def test_message_content_intent_enabled_in_build() -> None:
    """_build_bot이 message_content intent를 활성화한 봇을 만든다(DC-2 라이브 전제·@멘션 수신).

    ★멀티봇 구조: main()→_run_bots→_build_bot. 인텐트는 _build_bot이 켜므로 직접 검증
    (실 게이트웨이/asyncio.run 미경유). 각 직원 봇 모두 message_content=True여야 @멘션
    텍스트를 받는다.
    """
    cfg = {
        "role": "dev",
        "name": "한지훈",
        "avatar": "",
        "token": "fake-token",
        "channel_id": 999,
        "base_url": "http://localhost:8800",
        "toggles": {"post_text": True},
        "password": "",
        "cookie_name": "omc_session",
        "consume_sse": True,
    }
    bot = bot_mod._build_bot(cfg)
    assert bot.intents.message_content is True
    assert bot._role == "dev" and bot._name == "한지훈"  # 정체성도 함께 주입.


# ── 비침투 정적 단언 ──────────────────────────────────────────────────────────


def test_no_office_or_web_server_import_in_adapters() -> None:
    """adapters/ 어디에도 office/web_server import 0(HTTP 클라이언트로만 소비)."""
    adapters_dir = Path(bot_mod.__file__).parent.parent  # adapters/
    offenders: list[str] = []
    for py in adapters_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                if top in ("office", "web_server"):
                    offenders.append(f"{py.name}: import {m}")
    assert offenders == [], f"비침투 위반: {offenders}"


def test_only_bot_imports_discord() -> None:
    """discord import 는 bot.py 에만 — 다른 어댑터 모듈은 discord 무의존."""
    adapters_dir = Path(bot_mod.__file__).parent  # adapters/discord/
    discord_importers: list[str] = []
    for py in adapters_dir.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            if any(m.split(".")[0] == "discord" for m in mods):
                discord_importers.append(py.name)
    assert set(discord_importers) == {"bot.py"}, (
        f"discord import 는 bot.py 에만 허용: {sorted(set(discord_importers))}"
    )


def test_no_llm_call_in_adapters() -> None:
    """어댑터에 LLM 호출 경로 0(이벤트 받기 + 버튼 보내기뿐 — 동시성 1 자동 보호)."""
    adapters_dir = Path(bot_mod.__file__).parent.parent
    # LLM 클라이언트 후보 모듈명(프로젝트 LLM 경로). 어댑터엔 없어야 한다.
    llm_markers = ("openai", "litellm", "anthropic", "core.llm_client")
    offenders: list[str] = []
    for py in adapters_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if any(m.startswith(mk) for mk in llm_markers):
                    offenders.append(f"{py.name}: {m}")
    assert offenders == [], f"어댑터 LLM 호출 발견: {offenders}"


# ════════════════════════════════════════════════════════════════════════════
# US-006b — asyncio 실루프 배선 + persistent View
# ════════════════════════════════════════════════════════════════════════════


def _calls_wait_until_ready(func) -> bool:
    """함수 *코드*(docstring 제외)에 wait_until_ready 호출/속성접근이 있는가.

    docstring 의 'wait_until_ready 금지' 언급을 오탐하지 않도록 AST 로 본문만 검사.
    """
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    funcdef = tree.body[0]
    body = funcdef.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # docstring 제거
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Attribute) and node.attr == "wait_until_ready":
            return True
        if isinstance(node, ast.Name) and node.id == "wait_until_ready":
            return True
    return False


def test_setup_hook_wiring_static() -> None:
    """배선 정적 단언: setup_hook 은 create_task 사용 + 본문에 wait_until_ready 없음.

    wait_until_ready 는 _sse_consume_loop(별도 태스크 내부)에만 존재해야 한다
    (setup_hook 본문에서 호출하면 게이트웨이 연결 전 데드락 — 공식문서 경고).
    """
    setup_src = inspect.getsource(DiscordBot.setup_hook)
    assert "create_task" in setup_src
    # ★본문(코드)에 wait_until_ready 없음 — docstring 언급은 무시.
    assert _calls_wait_until_ready(DiscordBot.setup_hook) is False
    # ★_sse_consume_loop(태스크 내부)에는 wait_until_ready 있음.
    assert _calls_wait_until_ready(DiscordBot._sse_consume_loop) is True


async def test_setup_hook_schedules_consume_task(monkeypatch) -> None:
    """진짜 이벤트루프에서 setup_hook 가 SSE 소비 태스크를 create_task 로 기동.

    setup_hook 는 asyncio.get_running_loop().create_task 로 SSE 소비 태스크를 띄운다.
    그 실행 중 루프의 create_task 를 스파이해 정확히 1개가 스케줄됨을 단언한다.
    """
    # consume_events 를 빈 유한 stub 으로 교체(무기한 금지 — 즉시 종료).
    monkeypatch.setattr(bot_mod, "consume_events", _mock_consume([]))
    monkeypatch.setattr(bot_mod.cursor_mod, "read_cursor", lambda path=None: 0)

    channel = FakeChannel()
    bot = _make_bot(channel)
    # 백필 GET /api/inbox 는 빈 pending 반환(네트워크 회피).
    bot._fetch_pending_reviews = _empty_reviews  # type: ignore[assignment]

    loop = asyncio.get_running_loop()
    created: list[asyncio.Task] = []
    real_create_task = loop.create_task

    def _spy_create_task(coro, **kw):
        t = real_create_task(coro, **kw)
        created.append(t)
        return t

    monkeypatch.setattr(loop, "create_task", _spy_create_task)

    await asyncio.wait_for(bot.setup_hook(), timeout=_BOUND)
    # setup_hook 가 정확히 SSE 소비 태스크 1개를 스케줄.
    assert len(created) == 1
    # 태스크가 깔끔히 끝나도록 대기(유한 consume → 즉시 종료).
    await asyncio.wait_for(created[0], timeout=_BOUND)


async def _empty_reviews() -> list[dict]:
    return []


async def test_two_loops_no_starvation(monkeypatch) -> None:
    """두 루프 기아 0 — SSE 소비 태스크가 도는 동안 인터랙션 콜백도 같은 루프서 교대.

    진짜 이벤트루프에서 SSE 프레임 2개 처리 + 인터랙션 1회가 elapsed<bound 안에
    모두 완료됨을 단언(4a 데드락 테스트 정신). consume 은 유한 제너레이터.
    """
    from adapters.discord.commands import STAGE_LABELS

    _game_wf = next(
        iter(STAGE_LABELS)
    )  # game_* — 자연어 상태 게시(2 프레임 모두 게시).
    frames = [
        (1, {"type": "RUN_STARTED", "workflow": _game_wf}),
        (
            2,
            {"type": "INTERRUPT_REQUESTED", "node_id": "n", "tool": "t", "risk": "low"},
        ),
    ]
    monkeypatch.setattr(bot_mod, "consume_events", _mock_consume(frames))
    monkeypatch.setattr(bot_mod.cursor_mod, "read_cursor", lambda path=None: 0)
    monkeypatch.setattr(bot_mod.cursor_mod, "should_post", lambda seq, path=None: True)
    monkeypatch.setattr(
        bot_mod.cursor_mod, "advance_cursor", lambda seq, path=None: None
    )

    fake_rest = _FakeRest()
    monkeypatch.setattr(bot_mod.rest_client, "approve", fake_rest.approve)

    channel = FakeChannel()
    bot = _make_bot(channel)

    interaction = FakeInteraction()

    async def _drive() -> None:
        # SSE 소비 태스크와 인터랙션 콜백을 같은 루프에서 동시 진행.
        consume_task = asyncio.create_task(bot._sse_consume_loop())
        # 인터랙션은 SSE 와 교대로 실행(블로킹 0이면 둘 다 끝난다).
        await bot_mod._handle_decision(
            interaction, "approve:2", "http://localhost:8800"
        )
        await consume_task  # 유한 consume → 종료

    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(_drive(), timeout=_BOUND)
    elapsed = loop.time() - start

    # 두 프레임 모두 게시(RUN_STARTED + INTERRUPT_REQUESTED).
    assert len(channel.sends) == 2
    # 인터랙션 콜백도 완료(REST approve 1회 + defer).
    assert fake_rest.calls == [("approve", 2, "http://localhost:8800")]
    assert interaction.response.deferred is True
    # 기아 없음 — 블로킹 없이 빠르게 완료.
    assert elapsed < _BOUND


async def test_exception_resilience_next_frame_continues(monkeypatch) -> None:
    """예외 복원력 — 1프레임 게시 예외가 SSE 소비 루프를 죽이지 않음(좀비봇 방어).

    seq=1 게시에서 예외 주입 → seq=2 는 정상 게시됨을 단언(FanoutPoller 예외 흡수).
    """
    from adapters.discord.commands import STAGE_LABELS

    _game_wf = next(iter(STAGE_LABELS))
    frames = [
        # frame1: RUN_ERROR(error에 'boom' 포함) → 게시되며 _flaky_send가 예외 주입.
        (1, {"type": "RUN_ERROR", "error": "boom 폭발"}),
        # frame2: game_* RUN_FINISHED(success) → 자연어 완료 게시.
        (2, {"type": "RUN_FINISHED", "workflow": _game_wf, "status": "success"}),
    ]
    monkeypatch.setattr(bot_mod, "consume_events", _mock_consume(frames))
    monkeypatch.setattr(bot_mod.cursor_mod, "read_cursor", lambda path=None: 0)
    monkeypatch.setattr(bot_mod.cursor_mod, "should_post", lambda seq, path=None: True)
    monkeypatch.setattr(
        bot_mod.cursor_mod, "advance_cursor", lambda seq, path=None: None
    )

    channel = FakeChannel()
    bot = _make_bot(channel)

    # seq=1 게시 시 예외, seq=2 는 정상.
    good_sends: list[dict] = []

    async def _flaky_send(content: str | None = None, view: object = None):
        if content and "boom" in content:
            raise RuntimeError("게시 폭발(주입)")
        good_sends.append({"content": content})
        return object()

    channel.send = _flaky_send  # type: ignore[assignment]

    await asyncio.wait_for(bot._sse_consume_loop(), timeout=_BOUND)

    # seq=1 은 예외로 실패했으나 루프는 죽지 않고 seq=2 를 게시.
    assert len(good_sends) == 1
    assert "마쳤어요" in good_sends[0]["content"]  # game 완료 자연어(라벨 폐기).
    assert bot.posted_count == 1  # 성공 게시는 seq=2 1건


async def test_persistent_view_backfill_selects_only_pending(monkeypatch) -> None:
    """persistent View 재등록 대상 선별 — 현재 pending 만 add_view, 과거는 미재등록.

    ⚠️ mock 한계: unittest.mock/이 fake Client 는 add_view 의 **실영속**을 못 잡는다
    (라이브 §G 영역). 여기선 "재등록 호출 여부 + pending 만 선별" 로직만 검증한다.
    부팅 백필이 GET /api/inbox review(pending) 마다 add_view 1회 호출함을 단언.
    """

    # GET /api/inbox 가 pending 2건(seq 11, 12) 반환하도록 stub.
    async def _two_pending() -> list[dict]:
        return [
            {"interrupt_id": 11, "node_id": "a", "tool": "t1", "risk": "high"},
            {"interrupt_id": 12, "node_id": "b", "tool": "t2", "risk": "low"},
        ]

    channel = FakeChannel()
    bot = _make_bot(channel)
    bot._fetch_pending_reviews = _two_pending  # type: ignore[assignment]

    added_views: list[object] = []
    bot.add_view = lambda view, **kw: added_views.append(view)  # type: ignore[assignment]

    await asyncio.wait_for(bot._backfill_persistent_views(), timeout=_BOUND)

    # pending 2건 → add_view 2회(각 persistent View).
    assert len(added_views) == 2
    for v in added_views:
        assert isinstance(v, bot_mod.ApprovalView)
        assert v.timeout is None  # persistent 요건
    # 과거(해소된) 건은 입력에 없으므로 재등록되지 않음(선별).


async def test_backfill_failure_does_not_crash(monkeypatch) -> None:
    """백필 GET /api/inbox 실패해도 봇 기동을 막지 않음(예외 흡수)."""

    async def _boom() -> list[dict]:
        raise RuntimeError("inbox 조회 실패(주입)")

    channel = FakeChannel()
    bot = _make_bot(channel)
    bot._fetch_pending_reviews = _boom  # type: ignore[assignment]

    added_views: list[object] = []
    bot.add_view = lambda view, **kw: added_views.append(view)  # type: ignore[assignment]

    # 예외를 흡수하고 정상 반환(크래시 0).
    await asyncio.wait_for(bot._backfill_persistent_views(), timeout=_BOUND)
    assert added_views == []  # 조회 실패 → 재등록 0


# ════════════════════════════════════════════════════════════════════════════
# US-G6-3 — 승인 버튼 경로 세션 쿠키 + 401 바운드 재인증
# ════════════════════════════════════════════════════════════════════════════


async def test_handle_decision_threads_cookie(monkeypatch) -> None:
    """버튼 승인 시 cookie_provider 의 세션 쿠키가 REST approve 로 전달된다(US-G6-3)."""
    seen_cookies: list[str | None] = []

    async def _approve(seq: int, base_url: str, *, cookie: str | None = None):
        seen_cookies.append(cookie)
        return bot_mod.rest_client.ApproveResult(handled=True, status=200, error=None)

    monkeypatch.setattr(bot_mod.rest_client, "approve", _approve)

    interaction = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(
            interaction,
            "approve:42",
            "http://localhost:8800",
            cookie_provider=lambda: "omc_session=abc",
            reauth=None,
        ),
        timeout=_BOUND,
    )
    assert seen_cookies == ["omc_session=abc"]  # 세션 쿠키 첨부.
    assert "처리됨" in interaction.followup.messages[0]


async def test_handle_decision_401_reauth_bounded_single_retry(monkeypatch) -> None:
    """★버튼 승인 401 → reauth 1회 후 approve 1회만 재시도(무한 재인증 0·상한 1)."""
    results = [
        bot_mod.rest_client.ApproveResult(handled=False, status=401, error="401"),
        bot_mod.rest_client.ApproveResult(handled=True, status=200, error=None),
    ]
    approve_cookies: list[str | None] = []
    reauth_calls: list[int] = []

    async def _approve(seq: int, base_url: str, *, cookie: str | None = None):
        approve_cookies.append(cookie)
        return results[len(approve_cookies) - 1]

    async def _reauth() -> str:
        reauth_calls.append(1)
        return "omc_session=new"

    monkeypatch.setattr(bot_mod.rest_client, "approve", _approve)

    interaction = FakeInteraction()
    await asyncio.wait_for(
        bot_mod._handle_decision(
            interaction,
            "approve:7",
            "http://localhost:8800",
            cookie_provider=lambda: "omc_session=old",
            reauth=_reauth,
        ),
        timeout=_BOUND,
    )
    # 401 → reauth 정확히 1회 → approve 정확히 2회(원호출 + 1회 재시도·상한 1).
    assert len(reauth_calls) == 1
    assert len(approve_cookies) == 2
    assert approve_cookies[0] == "omc_session=old"  # 첫 호출=기존 쿠키.
    assert approve_cookies[1] == "omc_session=new"  # 재시도=갱신 쿠키.
    assert "처리됨" in interaction.followup.messages[0]  # 최종 처리됨.


# ── S-P0-2: 멀티봇 로스터·정체성·SSE 게이팅 (2026-06-16) ──────────────────────

_SHARED = {
    "channel_id": 999,
    "base_url": "http://localhost:8800",
    "toggles": {},
    "password": "",
    "cookie_name": "omc_session",
}


def test_parse_bot_roster_multi():
    """discord.bots 로스터 → 토큰 있는 직원만·정확히 1 primary(consume_sse)·shared 병합."""
    block = {
        "bots": [
            {
                "role": "secretary",
                "name": "이수빈",
                "token_env": "TOK_SEC",
                "primary": True,
            },
            {"role": "dev", "name": "한지훈", "token_env": "TOK_DEV"},
            {
                "role": "planner",
                "name": "강하은",
                "token_env": "TOK_MISSING",
            },  # 토큰 부재→제외
        ]
    }
    env = {"TOK_SEC": "s-token", "TOK_DEV": "d-token"}
    configs = bot_mod._parse_bot_roster(block, _SHARED, env)
    assert [c["role"] for c in configs] == [
        "secretary",
        "dev",
    ]  # planner 제외(토큰 없음).
    assert [c["token"] for c in configs] == ["s-token", "d-token"]
    assert [c["role"] for c in configs if c["consume_sse"]] == [
        "secretary"
    ]  # primary 1개.
    assert configs[0]["channel_id"] == 999  # shared 병합.
    assert configs[1]["name"] == "한지훈"


def test_parse_bot_roster_primary_preference():
    """명시 primary 없으면 secretary/pm가 consume_sse, 둘 다 없으면 첫 번째."""
    block = {
        "bots": [
            {"role": "dev", "token_env": "TOK_DEV"},
            {"role": "pm", "token_env": "TOK_PM"},
        ]
    }
    configs = bot_mod._parse_bot_roster(block, _SHARED, {"TOK_DEV": "d", "TOK_PM": "p"})
    assert [c["role"] for c in configs if c["consume_sse"]] == ["pm"]  # pm 선호.
    block2 = {
        "bots": [
            {"role": "dev", "token_env": "T1"},
            {"role": "data", "token_env": "T2"},
        ]
    }
    configs2 = bot_mod._parse_bot_roster(block2, _SHARED, {"T1": "a", "T2": "b"})
    assert configs2[0]["consume_sse"] is True and configs2[1]["consume_sse"] is False


def test_parse_bot_roster_empty_no_bots():
    """bots 미정의/빈 → [](단일봇 폴백은 _read_bot_configs가 처리)."""
    assert bot_mod._parse_bot_roster({}, _SHARED, {}) == []
    assert bot_mod._parse_bot_roster({"bots": []}, _SHARED, {}) == []


def test_discord_bot_identity_params():
    """DiscordBot가 role·name·consume_sse 정체성 저장(기본값 단일봇 역호환)."""
    bot = _make_bot(FakeChannel(), role="dev", name="한지훈", consume_sse=False)
    assert bot._role == "dev" and bot._name == "한지훈" and bot._consume_sse is False
    default_bot = _make_bot(FakeChannel())
    assert default_bot._role == "" and default_bot._name == ""
    assert default_bot._consume_sse is True  # 기본 primary(단일봇 역호환).


async def test_setup_hook_secondary_skips_sse(monkeypatch):
    """consume_sse=False(secondary) → setup_hook이 SSE 소비/백필 미시작(N× 중복 차단)."""
    bot = _make_bot(FakeChannel(), consume_sse=False)
    started = {"sse": False, "backfill": False}

    async def _noop() -> None:
        pass

    async def _spy_backfill() -> None:
        started["backfill"] = True

    async def _spy_sse() -> None:
        started["sse"] = True

    monkeypatch.setattr(bot, "_ensure_session", _noop)
    monkeypatch.setattr(bot, "_backfill_persistent_views", _spy_backfill)
    monkeypatch.setattr(bot, "_sse_consume_loop", _spy_sse)
    await asyncio.wait_for(bot.setup_hook(), timeout=_BOUND)
    await asyncio.sleep(0)  # create_task된 게 있으면 실행될 틈을 준다.
    assert started["backfill"] is False and started["sse"] is False


async def test_setup_hook_primary_starts_sse(monkeypatch):
    """consume_sse=True(primary·기본) → setup_hook이 백필 + SSE 소비 시작."""
    bot = _make_bot(FakeChannel())  # consume_sse 기본 True
    started = {"sse": False, "backfill": False}

    async def _noop() -> None:
        pass

    async def _spy_backfill() -> None:
        started["backfill"] = True

    async def _spy_sse() -> None:
        started["sse"] = True

    monkeypatch.setattr(bot, "_ensure_session", _noop)
    monkeypatch.setattr(bot, "_backfill_persistent_views", _spy_backfill)
    monkeypatch.setattr(bot, "_sse_consume_loop", _spy_sse)
    await asyncio.wait_for(bot.setup_hook(), timeout=_BOUND)
    await asyncio.sleep(0)  # create_task된 _sse_consume_loop 실행 틈.
    assert started["backfill"] is True and started["sse"] is True


# ── S-P0-3/4: @멘션 라우팅 · 역할 run_id 디스패치 (2026-06-16) ──────────────────


def test_role_from_run_id():
    """chat-{role}-{id} → role · 그 외(discord/uuid/default/빈)는 None."""
    assert bot_mod._role_from_run_id("chat-dev-12345") == "dev"
    assert bot_mod._role_from_run_id("chat-devops-9") == "devops"
    assert bot_mod._role_from_run_id("chat-default-12345") is None
    assert bot_mod._role_from_run_id("discord-12345") is None
    assert bot_mod._role_from_run_id("550e8400-e29b-41d4-a716-44665544") is None
    assert bot_mod._role_from_run_id("") is None


async def test_on_message_mention_routes_with_target_role(monkeypatch):
    """@멘션된 직원 봇 → 그 직원으로 chat 인바운드(target_role=자기 역할)."""
    fake = _patch_inbound(monkeypatch)
    bot = _make_bot(FakeChannel(), role="dev")  # self.user=None(미연결)
    # self.user(None)가 mentions에 포함 → mentioned_me True(테스트 모사).
    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False),
        channel_id=999,
        content="<@123> 빌드 어때?",
        message_id=7,
        mentions=[None],
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)
    assert len(fake.calls) == 1
    assert fake.target_roles == ["dev"]  # ★자기 역할로 라우팅.


async def test_on_message_secondary_ignores_unmentioned(monkeypatch):
    """secondary(consume_sse=False)는 미멘션 일반 발화 무시(16봇 중복 반응 차단)."""
    fake = _patch_inbound(monkeypatch)
    bot = _make_bot(FakeChannel(), role="dev", consume_sse=False)
    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False), channel_id=999, content="안녕하세요", mentions=[]
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)
    assert fake.calls == []


async def test_on_message_secondary_ignores_other_mention(monkeypatch):
    """secondary는 '다른 봇이 지목된' 발화도 무시(자기 멘션만 반응)."""
    fake = _patch_inbound(monkeypatch)
    bot = _make_bot(FakeChannel(), role="dev", consume_sse=False)
    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False),
        channel_id=999,
        content="<@999> 안녕",
        mentions=[FakeAuthor(bot=True)],  # 다른 봇(self.user=None 아님) 지목.
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)
    assert fake.calls == []


async def test_on_message_primary_handles_unmentioned(monkeypatch):
    """primary(consume_sse=True·비서)는 미멘션 일반 발화 접수(폴백)."""
    fake = _patch_inbound(monkeypatch)
    bot = _make_bot(FakeChannel(), role="secretary", consume_sse=True)
    msg = FakeIncomingMessage(
        author=FakeAuthor(bot=False), channel_id=999, content="안녕하세요", mentions=[]
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)
    assert len(fake.calls) == 1
    assert fake.target_roles == ["secretary"]  # 비서 페르소나로.


async def test_post_spec_dispatches_to_role_bot():
    """_post_spec(role)이 레지스트리의 그 직원 봇 채널로 게시(정체성 분리)."""
    primary_ch = FakeChannel()
    dev_ch = FakeChannel()
    primary = _make_bot(primary_ch, role="secretary")
    devbot = _make_bot(dev_ch, role="dev")
    primary._registry = {"secretary": primary, "dev": devbot}
    spec = bot_mod.DiscordMessageSpec(content="네, 빌드 잘 됩니다")
    await asyncio.wait_for(primary._post_spec(spec, role="dev"), timeout=_BOUND)
    assert len(dev_ch.sends) == 1
    assert dev_ch.sends[0]["content"] == "네, 빌드 잘 됩니다"
    assert primary_ch.sends == []  # ★dev 봇 정체성으로 게시(primary 채널 아님).


async def test_post_spec_unknown_role_falls_back_to_self():
    """미등록 role(또는 None) → self(primary)가 게시(일반·승인)."""
    primary_ch = FakeChannel()
    primary = _make_bot(primary_ch, role="secretary")
    primary._registry = {"secretary": primary}
    spec = bot_mod.DiscordMessageSpec(content="일반 알림")
    await asyncio.wait_for(primary._post_spec(spec, role=None), timeout=_BOUND)
    await asyncio.wait_for(primary._post_spec(spec, role="ghost"), timeout=_BOUND)
    assert len(primary_ch.sends) == 2  # None·미등록 둘 다 self 게시.


# ── P7: SSE 재연결 관측성 (영구다운 ERROR 격상·sse_client.py 0수정) ──────────────


def test_sse_reconnect_monitor_escalates_at_threshold():
    """sse_client 재연결 실패 WARNING이 threshold 연속 발생 → ERROR 격상(P7)."""
    captured: list[logging.LogRecord] = []
    cap = logging.Handler()
    cap.emit = captured.append  # type: ignore[method-assign]
    bot_mod._sse_monitor_logger.addHandler(cap)
    monitor = bot_mod._SSEReconnectMonitor(threshold=3)
    sse_logger = logging.getLogger("adapters.discord.sse_client")
    sse_logger.addHandler(monitor)
    try:
        sse_logger.warning("SSE 연결 실패 1")
        sse_logger.warning("SSE 연결 실패 2")
        assert captured == []  # 2회 → 격상 없음(자가치유 무한재시도 침묵)
        sse_logger.warning("SSE 연결 실패 3")
        assert len(captured) == 1  # 3회째 → ERROR 격상
        assert captured[0].levelno >= logging.ERROR
        assert "영구다운" in captured[0].getMessage()
    finally:
        sse_logger.removeHandler(monitor)
        bot_mod._sse_monitor_logger.removeHandler(cap)


def test_sse_reconnect_monitor_resets_on_alive():
    """note_alive(이벤트 수신=서버 정상)는 연속 카운터를 리셋한다(영구다운만 격상)."""
    monitor = bot_mod._SSEReconnectMonitor(threshold=3)
    sse_logger = logging.getLogger("adapters.discord.sse_client")
    sse_logger.addHandler(monitor)
    try:
        sse_logger.warning("fail 1")
        sse_logger.warning("fail 2")
        assert monitor.consecutive == 2
        monitor.note_alive()  # 서버 정상 신호 → 리셋
        assert monitor.consecutive == 0
        sse_logger.warning("fail 3")
        assert monitor.consecutive == 1  # 리셋 후 다시 1부터(연속성)
    finally:
        sse_logger.removeHandler(monitor)


def test_sse_reconnect_monitor_ignores_below_warning():
    """WARNING 미만(debug 등)은 세지 않는다 — 재연결 실패만 카운트(emit 가드)."""
    monitor = bot_mod._SSEReconnectMonitor(threshold=2)
    rec = logging.LogRecord("x", logging.DEBUG, __file__, 0, "parse skip", None, None)
    monitor.emit(rec)
    assert monitor.consecutive == 0


def test_sse_alert_threshold_from_config():
    """threshold는 endpoint.yaml discord 블록에서 읽고 안전 폴백(≥1 int)."""
    t = bot_mod._read_sse_alert_threshold()
    assert isinstance(t, int)
    assert t >= 1


def test_install_sse_reconnect_monitor_no_duplicate():
    """모니터 재설치 시 기존 모니터를 제거해 중복 핸들러를 만들지 않는다."""
    sse_logger = logging.getLogger("adapters.discord.sse_client")
    m1 = bot_mod._install_sse_reconnect_monitor(5)
    m2 = bot_mod._install_sse_reconnect_monitor(5)
    try:
        installed = [
            h
            for h in sse_logger.handlers
            if isinstance(h, bot_mod._SSEReconnectMonitor)
        ]
        # _install은 기존 모니터를 모두 제거 후 1개만 추가 → 항상 정확히 1개(불변·중복 0).
        assert len(installed) == 1
        assert m2 in installed and m1 not in installed
    finally:
        sse_logger.removeHandler(m2)
