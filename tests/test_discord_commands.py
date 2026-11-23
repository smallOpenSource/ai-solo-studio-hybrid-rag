"""US-G6-1 — 자연어 게임제작 지시 분류기 + post_run + on_message 라우팅 테스트.

★★★ 안전 규칙(hang 사고 재발 차단) ★★★
- 모든 async 테스트는 asyncio.wait_for(coro, timeout=5) 로 bound.
- 실서버·라이브 게이트웨이·discord.run 0 — post_run 은 httpx MockTransport,
  on_message 라우팅은 rest_client monkeypatch + mock 채널로만 호출한다.
- 401 재인증은 ★1회 상한(_reauth)임을 라우팅 테스트로 확인(무한 재인증 0).

분해:
- classify_intent 단위(결정론): 5단계 각 ≥2 표현 GameStage / ★오탐 0(≥8 chat) /
  ambiguous / 같은 입력 2회 동일 출력.
- post_run 단위(MockTransport·실네트워크 0): 200/500/ConnectError.
- on_message 라우팅(mock): stage→post_run·ack / chat→post_inbound / ambiguous→안내.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest

import adapters.discord.bot as bot_mod
from adapters.discord import rest_client
from adapters.discord.bot import DiscordBot
from adapters.discord.commands import (
    GAME_STAGES,
    STAGE_LABELS,
    classify_intent,
)

# 진짜 AsyncClient 를 모듈 로드 시점에 캡처 — monkeypatch 이후 교체되어도 안전.
_REAL_ASYNC_CLIENT = httpx.AsyncClient
_BOUND = 5.0


# ── commands.py 무의존 정적 단언(discord/httpx 0 — pure) ──────────────────────
def test_commands_no_discord_no_httpx_import() -> None:
    """commands 임포트 시 discord/httpx 패키지를 요구하지 않아야 한다(pure)."""
    sys.modules.pop("adapters.discord.commands", None)

    orig_discord = sys.modules.get("discord")
    orig_httpx = sys.modules.get("httpx")

    class _Blocker:
        def __init__(self, name: str) -> None:
            self._name = name

        def __getattr__(self, name: str) -> object:
            raise ImportError(f"{self._name} must not be imported by commands")

    sys.modules["discord"] = _Blocker("discord")  # type: ignore[assignment]
    sys.modules["httpx"] = _Blocker("httpx")  # type: ignore[assignment]

    try:
        import adapters.discord.commands  # noqa: F401
    finally:
        for name, orig in (("discord", orig_discord), ("httpx", orig_httpx)):
            if orig is not None:
                sys.modules[name] = orig
            else:
                sys.modules.pop(name, None)
        sys.modules.pop("adapters.discord.commands", None)


# ── classify_intent 단위(결정론) ──────────────────────────────────────────────

# 5단계 각 ≥2 자연어 표현(KO+EN) → 올바른 GameStage 워크플로.
_STAGE_CASES = [
    ("게임 디자인 시작해줘", "game_design"),
    ("design phase start", "game_design"),
    ("컨셉 기획 작업 진행해줘", "game_design"),
    ("밸런스 작업 시작", "game_balance"),
    ("난이도 수치 조정해줘", "game_balance"),
    ("balance tuning run", "game_balance"),
    ("콘텐츠 만들어줘", "game_content"),
    ("대사 텍스트 추가해줘", "game_content"),
    ("flavor content generate", "game_content"),
    ("조립 진행해줘", "game_assemble"),
    ("런타임 빌드 시작", "game_assemble"),
    ("assemble build run", "game_assemble"),
    ("qa 검증 시작", "game_qa"),
    ("품질 검수 수행해줘", "game_qa"),
    ("quality 테스트 진행", "game_qa"),
]


@pytest.mark.parametrize("text,workflow", _STAGE_CASES)
def test_classify_stage_routes_to_workflow(text: str, workflow: str) -> None:
    """단계 키워드 + 제작 지시어 → 해당 game_* GameStage."""
    intent = classify_intent(text)
    assert intent.kind == "stage"
    assert intent.workflow == workflow
    assert intent.stage_label == STAGE_LABELS[workflow]


# ★오탐 0(true negative 다수) — 일반 대화·질문·캐주얼 언급은 절대 stage 가 아니다.
_CHAT_CASES = [
    "안녕하세요",
    "오늘 날씨 좋네",
    "이 게임 재밌어?",
    "게임 디자인 어때?",  # 질문 — 지시어 없음.
    "밸런스가 별로야",
    "고마워",
    "점심 뭐 먹지",
    "디자인 예쁘다",
    "이 콘텐츠 좋다",  # 단계어 있으나 지시어 없음.
    "qa 팀 어디 있어?",  # 단계어 있으나 지시어 없음·질문.
    # ★영어 오탐 회귀(do 제거·단어경계): 질문/평서문이 stage 로 새지 않아야 한다.
    "what do you think of the game design?",  # do→단어경계라 비지시어·질문.
    "the game design looks great",  # 게임맥락+단계어이나 지시어 없음.
    "i'm done with the qa work",  # done/work 비지시어·qa 단계어만.
    "running late but the balance is off",  # running≠run(단어경계)·비지시어.
    # ★Major-1 회귀(비명령 가드): 단계어+지시어가 있어도 질문/부정/과거/희망은 Chat.
    "이번 디자인 시작은 별로였어",  # 부정(별로)+과거(였어).
    "콘텐츠 만들기 싫다",  # 부정(싫).
    "그 빌드 실행됐어?",  # 질문(?)+과거(됐).
    "디자인 작업물 봤어",  # 과거(봤).
    "수치 생성 결과 어때",  # 질문(어때).
    "검증 진행 상황 궁금해",  # 질문(궁금).
    "should we start the design review?",  # 영어 질문(?).
    "the build run failed yesterday",  # 영어 과거보고(failed/yesterday).
    "let's not build anything",  # 영어 부정(not).
    "i want to make the balance better",  # 영어 희망(want).
]


@pytest.mark.parametrize("text", _CHAT_CASES)
def test_classify_chat_never_triggers_stage(text: str) -> None:
    """★오탐 0: 일반 대화·질문·캐주얼 언급은 전부 chat(stage/ambiguous 트리거 0)."""
    intent = classify_intent(text)
    assert intent.kind == "chat", f"오탐: {text!r} -> {intent.kind}"
    assert intent.workflow is None


# Ambiguous: 제작 지시어 + 게임 맥락어 있으나 단계 불명.
_AMBIGUOUS_CASES = ["게임 만들자", "게임 제작 시작하자", "게임 작업 해줘"]


@pytest.mark.parametrize("text", _AMBIGUOUS_CASES)
def test_classify_ambiguous_when_stage_unknown(text: str) -> None:
    """제작 지시어 + 게임 맥락어 있으나 단계 불명 → ambiguous."""
    intent = classify_intent(text)
    assert intent.kind == "ambiguous"
    assert intent.workflow is None


def test_classify_is_deterministic() -> None:
    """같은 입력 2회 호출 → 동일 출력(결정론·LLM 0)."""
    for text in [c[0] for c in _STAGE_CASES] + _CHAT_CASES + _AMBIGUOUS_CASES:
        first = classify_intent(text)
        second = classify_intent(text)
        assert first == second, f"비결정적: {text!r}"


def test_classify_empty_and_none_are_chat() -> None:
    """빈 문자열·None → chat(크래시 0)."""
    assert classify_intent("").kind == "chat"
    assert classify_intent(None).kind == "chat"  # type: ignore[arg-type]


def test_stage_labels_cover_all_game_stages() -> None:
    """GAME_STAGES 의 모든 워크플로가 STAGE_LABELS 에 라벨을 갖는다(안내 게시 안전)."""
    for wf in GAME_STAGES:
        assert wf in STAGE_LABELS


# ── post_run 단위(MockTransport — 실 네트워크 0) ──────────────────────────────


def _patch_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """rest_client 모듈의 httpx.AsyncClient 를 MockTransport(handler) 로 교체."""

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            kwargs.pop("transport", None)
            self._real = _REAL_ASYNC_CLIENT(
                transport=httpx.MockTransport(handler), **kwargs
            )

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._real.__aenter__()

        async def __aexit__(self, *exc: object) -> None:
            await self._real.__aexit__(*exc)

    monkeypatch.setattr(rest_client.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_post_run_200_enqueued_returns_task_id(monkeypatch) -> None:
    """200 {status:enqueued, task_id:7} → handled True · task_id 7."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "enqueued", "task_id": 7})

    _patch_mock_transport(monkeypatch, _handler)
    result = await asyncio.wait_for(
        rest_client.post_run("game_qa", "http://web"), timeout=_BOUND
    )
    assert result.handled is True
    assert result.status == 200
    assert result.task_id == 7
    assert result.error is None


@pytest.mark.asyncio
async def test_post_run_500_not_handled(monkeypatch) -> None:
    """500 → handled False · task_id None."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    _patch_mock_transport(monkeypatch, _handler)
    result = await asyncio.wait_for(
        rest_client.post_run("game_qa", "http://web"), timeout=_BOUND
    )
    assert result.handled is False
    assert result.status == 500
    assert result.task_id is None
    assert result.error is not None


@pytest.mark.asyncio
async def test_post_run_network_error_status_none(monkeypatch) -> None:
    """ConnectError → handled False · status None(크래시 0)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_mock_transport(monkeypatch, _handler)
    result = await asyncio.wait_for(
        rest_client.post_run("game_qa", "http://web"), timeout=_BOUND
    )
    assert result.handled is False
    assert result.status is None
    assert result.task_id is None
    assert result.error is not None


@pytest.mark.asyncio
async def test_post_run_sends_workflow_and_mock_body(monkeypatch) -> None:
    """body 에 workflow·mock 이 실린다(웹 enqueue 계약 확인)."""
    captured: list[bytes] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        return httpx.Response(200, json={"status": "enqueued", "task_id": 1})

    _patch_mock_transport(monkeypatch, _handler)
    await asyncio.wait_for(
        rest_client.post_run("game_design", "http://web", mock=True), timeout=_BOUND
    )
    assert captured
    blob = captured[0].decode("utf-8")
    assert "game_design" in blob
    assert "mock" in blob


# ── on_message 라우팅(mock) ───────────────────────────────────────────────────


class _FakeAuthor:
    """message.author mock — bot 플래그(self.user 와는 identity 비교)."""

    def __init__(self, *, bot: bool = False) -> None:
        self.bot = bot


class _FakeChannelRef:
    """message.channel mock — id만."""

    def __init__(self, cid: int) -> None:
        self.id = cid


class _FakeMessage:
    """discord.Message mock(on_message 입력)."""

    def __init__(
        self,
        *,
        author: object,
        channel_id: int,
        content: str,
        mid: int = 1,
        mentions: list | None = None,
    ):
        self.author = author
        self.channel = _FakeChannelRef(channel_id)
        self.content = content
        self.id = mid
        self.mentions = mentions or []  # @멘션 대상 목록(멀티봇 자기선택용).


class _RouteSpy:
    """rest_client.post_run/post_inbound mock + bot._send_text mock 호출 기록."""

    def __init__(self, *, run_result: rest_client.RunResult | None = None) -> None:
        self.run_calls: list[tuple[str, str, str | None]] = []
        self.inbound_calls: list[tuple[str, str]] = []
        self.sent: list[str] = []
        self._run_result = run_result or rest_client.RunResult(
            handled=True, status=200, task_id=11, error=None
        )

    async def post_run(self, workflow, base_url, *, cookie=None, mock=False):
        self.run_calls.append((workflow, base_url, cookie))
        return self._run_result

    async def post_inbound(
        self, text, message_id, base_url, author=None, *, cookie=None, target_role=None
    ):
        self.inbound_calls.append((text, message_id, target_role))
        return rest_client.InboundResult(handled=True, status=200, error=None)

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def _make_bot_with_spy(monkeypatch, spy: _RouteSpy) -> DiscordBot:
    """post_run/post_inbound monkeypatch + _send_text 가짜화한 DiscordBot(channel_id=999)."""
    monkeypatch.setattr(bot_mod.rest_client, "post_run", spy.post_run)
    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", spy.post_inbound)
    bot = DiscordBot(channel_id=999, base_url="http://web")
    # _send_text 가짜화(채널 fetch 회피 + ack/안내 게시 기록).
    monkeypatch.setattr(bot, "_send_text", spy.send_text)
    return bot


@pytest.mark.asyncio
async def test_on_message_stage_calls_post_run_and_acks(monkeypatch) -> None:
    """(a) 게임 단계 지시 발화 → post_run 1회(post_inbound 0) + ack 게시."""
    spy = _RouteSpy()
    bot = _make_bot_with_spy(monkeypatch, spy)

    msg = _FakeMessage(
        author=_FakeAuthor(bot=False),
        channel_id=999,
        content="게임 밸런스 작업 시작해줘",
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert len(spy.run_calls) == 1
    assert spy.run_calls[0][0] == "game_balance"
    assert spy.inbound_calls == []  # ★stage → 인바운드 0.
    assert len(spy.sent) == 1  # ack 게시.
    assert "game_balance" in spy.sent[0]


@pytest.mark.asyncio
async def test_on_message_chat_calls_post_inbound(monkeypatch) -> None:
    """(b) 일반 발화 → post_inbound 1회(post_run 0)."""
    spy = _RouteSpy()
    bot = _make_bot_with_spy(monkeypatch, spy)

    msg = _FakeMessage(
        author=_FakeAuthor(bot=False),
        channel_id=999,
        content="안녕하세요 봇",
        mid=42,
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert spy.run_calls == []  # ★chat → post_run 0.
    assert spy.inbound_calls == [
        ("안녕하세요 봇", "42", None)
    ]  # 단일봇 role="" → target_role None.
    assert spy.sent == []  # chat 은 ack/안내 게시 없음.


@pytest.mark.asyncio
async def test_on_message_ambiguous_sends_guidance_only(monkeypatch) -> None:
    """(c) ambiguous → 안내 게시(post_run·post_inbound 둘 다 0)."""
    spy = _RouteSpy()
    bot = _make_bot_with_spy(monkeypatch, spy)

    msg = _FakeMessage(
        author=_FakeAuthor(bot=False),
        channel_id=999,
        content="게임 제작 시작하자",
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert spy.run_calls == []
    assert spy.inbound_calls == []
    assert len(spy.sent) == 1  # 단계 안내 1회.
    # 안내에 모든 단계 라벨이 포함된다.
    for wf in GAME_STAGES:
        assert STAGE_LABELS[wf] in spy.sent[0]


@pytest.mark.asyncio
async def test_on_message_stage_failure_sends_failure_notice(monkeypatch) -> None:
    """stage 발화이나 post_run 실패 → 실패 안내 게시(크래시 0)."""
    spy = _RouteSpy(
        run_result=rest_client.RunResult(
            handled=False, status=500, task_id=None, error="서버 오류: HTTP 500"
        )
    )
    bot = _make_bot_with_spy(monkeypatch, spy)

    msg = _FakeMessage(
        author=_FakeAuthor(bot=False),
        channel_id=999,
        content="qa 검증 시작해줘",
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert len(spy.run_calls) == 1
    assert len(spy.sent) == 1
    assert "실패" in spy.sent[0]


@pytest.mark.asyncio
async def test_on_message_stage_401_reauth_bounded_single_retry(monkeypatch) -> None:
    """★stage 발화 401 → _reauth 1회 후 post_run 1회만 재시도(무한 재인증 0)."""
    run_results = [
        rest_client.RunResult(handled=False, status=401, task_id=None, error="401"),
        rest_client.RunResult(handled=True, status=200, task_id=99, error=None),
    ]
    run_calls: list[str] = []
    reauth_calls: list[int] = []

    async def _fake_run(workflow, base_url, *, cookie=None, mock=False):
        run_calls.append(workflow)
        return run_results[len(run_calls) - 1]

    async def _fake_reauth():
        reauth_calls.append(1)
        return "omc_session=new"

    sent: list[str] = []

    async def _fake_send(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(bot_mod.rest_client, "post_run", _fake_run)
    bot = DiscordBot(channel_id=999, base_url="http://web")
    monkeypatch.setattr(bot, "_reauth", _fake_reauth)
    monkeypatch.setattr(bot, "_send_text", _fake_send)

    msg = _FakeMessage(
        author=_FakeAuthor(bot=False),
        channel_id=999,
        content="게임 디자인 시작해줘",
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    # 401 → 재인증 정확히 1회 → post_run 정확히 2회(원호출 + 1회 재시도·상한 1).
    assert len(reauth_calls) == 1
    assert len(run_calls) == 2
    assert len(sent) == 1
    assert "task=99" in sent[0]


@pytest.mark.asyncio
async def test_on_message_self_author_ignored_no_routing(monkeypatch) -> None:
    """★봇 자신(self.user)의 ack/안내 발화는 무시 → 라우팅 0(재진입 차단)."""
    spy = _RouteSpy()
    bot = _make_bot_with_spy(monkeypatch, spy)

    bot_user = _FakeAuthor(bot=True)
    monkeypatch.setattr(DiscordBot, "user", bot_user)  # 읽기전용 property 회피.

    # 봇이 보낸 ack 처럼 보이는 stage 텍스트라도 self.user 면 무시.
    msg = _FakeMessage(
        author=bot_user,
        channel_id=999,
        content="[제작 지시 접수] 단계=밸런스 작업 시작",
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    assert spy.run_calls == []
    assert spy.inbound_calls == []
    assert spy.sent == []


def test_no_new_event_types_static() -> None:
    """★새 이벤트 타입 금지 — commands/post_run 은 이벤트 타입 문자열을 만들지 않는다.

    commands.py 는 워크플로 이름(game_*)·라벨만 다룬다. 이벤트 11종 상수를 늘리지
    않음을 정적으로 확인(회귀 가드 — RUN_*/TEXT_MESSAGE_* 등 미등장)."""
    import inspect

    import adapters.discord.commands as cmd_mod

    src = inspect.getsource(cmd_mod)
    for forbidden in ("RUN_STARTED", "RUN_FINISHED", "TEXT_MESSAGE", "INTERRUPT_"):
        assert forbidden not in src


@pytest.mark.asyncio
async def test_on_message_chat_401_reauth_bounded_single_retry(monkeypatch) -> None:
    """★chat 401 → _reauth 1회 후 post_inbound 1회만 재시도(stage·버튼과 동형·Minor-1)."""
    results = [
        rest_client.InboundResult(handled=False, status=401, error="401"),
        rest_client.InboundResult(handled=True, status=200, error=None),
    ]
    inbound_cookies: list[str | None] = []
    reauth_calls: list[int] = []

    async def _fake_inbound(
        text, message_id, base_url, author=None, *, cookie=None, target_role=None
    ):
        inbound_cookies.append(cookie)
        return results[len(inbound_cookies) - 1]

    async def _fake_reauth() -> str:
        reauth_calls.append(1)
        return "omc_session=new"

    monkeypatch.setattr(bot_mod.rest_client, "post_inbound", _fake_inbound)
    bot = DiscordBot(channel_id=999, base_url="http://web")
    monkeypatch.setattr(bot, "_reauth", _fake_reauth)

    msg = _FakeMessage(
        author=_FakeAuthor(bot=False), channel_id=999, content="안녕 반가워", mid=7
    )
    await asyncio.wait_for(bot.on_message(msg), timeout=_BOUND)

    # 401 → reauth 정확히 1회 → post_inbound 정확히 2회(원호출 + 1회 재시도·상한 1).
    assert len(reauth_calls) == 1
    assert len(inbound_cookies) == 2
