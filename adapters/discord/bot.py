"""US-006a/b — Discord 봇 껍데기 (discord.py Client + asyncio 배선).

이 모듈만 `import discord` 한다(다른 어댑터 모듈은 discord 무의존 유지).
office/web_server 는 import 하지 않는다 — SSE/REST HTTP 클라이언트로만 소비.

배선 요약(discord.py 2.7.1 공식 패턴):
- setup_hook : ① httpx GET /api/inbox 백필 → 현재 pending review 만 persistent
  View(add_view) 재등록(과거 해소건 미재등록 → 자연 사망) ② self.loop.create_task
  (_sse_consume_loop) 로 무기한 SSE 소비 태스크 기동(on_ready 아님).
  ★setup_hook 본문에선 wait_until_ready()/wait_for() 호출 금지(게이트웨이 연결 전
  → 데드락, 공식문서 경고).
- _sse_consume_loop : create_task 로 띄운 별도 코루틴 **내부**에서만 wait_until_ready()
  (채널 게시 전 1회). consume_events(base_url, last_event_id) 를 async for 로 소비 →
  should_post 가드 → event_to_message → None skip → 채널 게시(INTERRUPT_REQUESTED 면
  persistent View) → advance_cursor. ★예외 흡수(FanoutPoller web_server/sse.py:177-180
  패턴 — 1프레임 실패가 루프를 죽이지 않음 → 좀비봇 방어).
- 버튼 콜백 : custom_id "approve:{seq}"/"reject:{seq}" 파싱 → defer(3초 ack) →
  rest_client.approve/reject(seq, base_url) → followup("처리됨"/"실패").
- 토큰 부재 : client.run 호출 안 함 → "라이브 비활성(토큰 없음)" 로그 + 정상 반환
  (크래시·NotImplementedError 0).

LLM 호출 0(이벤트 받기 + 버튼 보내기뿐).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

import discord
import httpx
from discord import app_commands

from adapters.discord import cursor as cursor_mod
from adapters.discord import rest_client
from adapters.discord.commands import GAME_STAGES, STAGE_LABELS, classify_intent
from adapters.discord.mapping import DiscordMessageSpec, event_to_message
from adapters.discord.slash_commands import known_agent_ids, resolve_cmd_target
from adapters.discord.sse_client import consume_events

logger = logging.getLogger(__name__)

# ── P7 SSE 관측성: 재연결 영구실패 가시화(sse_client.py 0수정·max_retries=None 유지) ──
# web_server 영구다운 시 consume_events는 backoff로 무한 재시도하며 sse_client 로거에 WARNING을
# 쏟지만 ERROR 경보가 없다(좀비 reconnect가 ERROR 스캔에 안 잡힘). 이 모니터가 그 WARNING을 세서
# 연속 N회 시 ERROR로 격상한다 — 봇은 자가치유(무한 재시도) 그대로 두고 '영구다운'만 보이게 한다.
# 격상은 별도 로거(무재귀)·이벤트 수신 시 note_alive로 리셋·threshold는 endpoint.yaml.
_sse_monitor_logger = logging.getLogger("adapters.discord.sse_monitor")


class _SSEReconnectMonitor(logging.Handler):
    """sse_client 재연결 실패(WARNING+) 연속 카운터 — threshold 도달 시 ERROR 격상(P7)."""

    def __init__(self, threshold: int) -> None:
        super().__init__(level=logging.WARNING)
        self._threshold = max(1, threshold)
        self.consecutive = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        self.consecutive += 1
        if self.consecutive % self._threshold == 0:
            _sse_monitor_logger.error(
                "★SSE 재연결 %d회 연속 실패 — web_server 영구다운 의심. 봇은 자가치유를 위해 "
                "무한 재시도를 지속합니다(max_retries=None). web_server 상태를 확인하세요.",
                self.consecutive,
            )

    def note_alive(self) -> None:
        """이벤트 수신(서버 정상 확인) 시 연속 실패 카운터 리셋."""
        self.consecutive = 0


def _read_sse_alert_threshold(default: int = 5) -> int:
    """endpoint.yaml discord.sse_reconnect_alert_threshold (없으면 default·≥1)."""
    try:
        raw = _read_discord_block().get("sse_reconnect_alert_threshold", default)
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _install_sse_reconnect_monitor(threshold: int) -> "_SSEReconnectMonitor":
    """sse_client 로거에 재연결 모니터를 중복 없이 설치하고 반환한다(P7)."""
    sse_logger = logging.getLogger("adapters.discord.sse_client")
    for h in list(sse_logger.handlers):
        if isinstance(h, _SSEReconnectMonitor):
            sse_logger.removeHandler(h)
    monitor = _SSEReconnectMonitor(threshold)
    sse_logger.addHandler(monitor)
    return monitor


# localhost web_server로의 짧은 REST 호출 타임아웃(무명시 시 httpx 기본 5s 의존 회피).
_LOCAL_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# 디스코드 버튼 스타일 문자열(mapping.ButtonSpec.style) → discord.ButtonStyle 매핑.
_BUTTON_STYLES: dict[str, discord.ButtonStyle] = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def _parse_custom_id(custom_id: str) -> tuple[str, int] | None:
    """버튼 custom_id "approve:{seq}"/"reject:{seq}" 를 (action, seq) 로 파싱.

    형식이 맞지 않으면 None(크래시 0).
    """
    if ":" not in custom_id:
        return None
    action, _, raw_seq = custom_id.partition(":")
    if action not in ("approve", "reject"):
        return None
    try:
        return action, int(raw_seq)
    except (ValueError, TypeError):
        return None


async def _handle_decision(
    interaction: discord.Interaction,
    custom_id: str,
    base_url: str,
    *,
    cookie_provider: "Callable[[], str | None] | None" = None,
    reauth: "Callable[[], Awaitable[str | None]] | None" = None,
) -> None:
    """버튼 인터랙션 공통 처리 — defer ack → REST 승인/거부 → followup.

    interaction.response.defer() 로 3초 ack 후 rest_client 호출(localhost 빠름),
    결과를 followup 으로 알린다. 파싱/REST 예외는 흡수해 followup 으로만 알린다.

    US-G6-0: cookie_provider 가 주어지면 세션 쿠키를 REST 에 첨부한다. result.status
    가 401 이고 reauth 가 주어지면 ★1회 바운드 재인증 후 동일 호출을 1회만 재시도한다
    (무한 재인증 0). 둘 다 None(기본)이면 기존 동작 무변(후방호환).
    """
    parsed = _parse_custom_id(custom_id)
    if parsed is None:
        logger.warning("알 수 없는 버튼 custom_id: %s", custom_id)
        return
    action, seq = parsed

    # 3초 ack(처리 중) — 폴링 재개는 비동기라 즉시 응답 못 함.
    await interaction.response.defer()

    async def _do(cookie: str | None):
        if action == "approve":
            return await rest_client.approve(seq, base_url, cookie=cookie)
        return await rest_client.reject(seq, base_url, cookie=cookie)

    cookie = cookie_provider() if cookie_provider is not None else None
    result = await _do(cookie)
    # 401(세션 만료) + 재인증 콜백 → 1회 바운드 재인증 후 1회 재시도(상한 1).
    if result.status == 401 and reauth is not None:
        new_cookie = await reauth()
        result = await _do(new_cookie)

    if result.handled:
        # 200·404 둘 다 "처리됨"(멱등) — §4.5 ④.
        msg = f"처리됨 (seq={seq}, action={action}, status={result.status})"
    else:
        msg = f"실패 (seq={seq}, action={action}): {result.error}"
    await interaction.followup.send(msg, ephemeral=True)

    # 처리 후 원 메시지의 버튼을 제거하고 결과를 본문에 표시(재클릭 방지·UX).
    # edit 실패(권한·메시지 삭제 등)해도 승인 처리 자체는 이미 완료이므로 흡수한다.
    try:
        base = interaction.message.content or ""
        mark = (
            "✅"
            if (action == "approve" and result.handled)
            else ("🚫" if result.handled else "⚠️")
        )
        await interaction.message.edit(content=f"{base}\n{mark} {msg}", view=None)
    except Exception as exc:  # noqa: BLE001 — 게시 갱신 실패가 승인 처리를 무효화하지 않음
        logger.warning("처리 후 버튼 제거 실패(처리는 완료): %s", exc)


class ApprovalView(discord.ui.View):
    """INTERRUPT_REQUESTED 승인/거부 persistent View.

    persistent 요건: timeout=None + 모든 버튼 custom_id 명시(§4.5 ⑥).
    봇 부팅 시 bot.add_view(ApprovalView(...)) 로 재등록하면 메시지가 봇 프로세스
    수명보다 오래 살아도(채널 영구) 버튼이 작동한다.

    버튼 콜백은 custom_id 를 파싱해 _handle_decision 으로 위임한다.
    """

    def __init__(
        self,
        spec: DiscordMessageSpec,
        base_url: str,
        *,
        cookie_provider: "Callable[[], str | None] | None" = None,
        reauth: "Callable[[], Awaitable[str | None]] | None" = None,
    ) -> None:
        super().__init__(timeout=None)
        self._base_url = base_url
        # US-G6-0: 버튼 콜백이 세션 쿠키를 첨부·401 재인증할 수 있도록 보관(기본 None).
        self._cookie_provider = cookie_provider
        self._reauth = reauth
        for bspec in spec.buttons:
            style = _BUTTON_STYLES.get(bspec.style, discord.ButtonStyle.secondary)
            button: discord.ui.Button = discord.ui.Button(
                label=bspec.label,
                custom_id=bspec.custom_id,
                style=style,
            )
            button.callback = self._make_callback(bspec.custom_id)
            self.add_item(button)

    def _make_callback(self, custom_id: str):
        async def _callback(interaction: discord.Interaction) -> None:
            await _handle_decision(
                interaction,
                custom_id,
                self._base_url,
                cookie_provider=self._cookie_provider,
                reauth=self._reauth,
            )

        return _callback


def _build_view(
    spec: DiscordMessageSpec,
    base_url: str,
    *,
    cookie_provider: "Callable[[], str | None] | None" = None,
    reauth: "Callable[[], Awaitable[str | None]] | None" = None,
) -> "ApprovalView | None":
    """spec 이 버튼을 가지면 ApprovalView 를, 아니면 None 을 만든다."""
    if not spec.buttons:
        return None
    return ApprovalView(spec, base_url, cookie_provider=cookie_provider, reauth=reauth)


def _role_from_run_id(run_id: str) -> str | None:
    """chat-{role}-{id} → role(멀티봇 디스패치 키). 그 외(discord-·uuid·default)면 None.

    None이면 primary(비서) 봇이 게시(일반·승인). role이 레지스트리에 있으면 그 직원 봇이
    자기 정체성으로 게시(@멘션 가능). roles는 하이픈 없는 단일 토큰이라 split 안전.
    """
    if not isinstance(run_id, str):
        return None
    parts = run_id.split("-")
    if len(parts) >= 3 and parts[0] == "chat" and parts[1] and parts[1] != "default":
        return parts[1]
    return None


class DiscordBot(discord.Client):
    """Phase 4b Discord 봇 — SSE 소비 + 승인 게시 + persistent View 재등록.

    asyncio gateway 상시연결은 이 클래스에만 격리된다(다른 어댑터 모듈 무의존).
    """

    def __init__(
        self,
        *,
        channel_id: int,
        base_url: str,
        toggles: dict | None = None,
        intents: discord.Intents | None = None,
        password: str = "",
        cookie_name: str = "omc_session",
        role: str = "",
        name: str = "",
        consume_sse: bool = True,
    ) -> None:
        super().__init__(intents=intents or discord.Intents.default())
        self._channel_id = channel_id
        self._base_url = base_url
        self._toggles = toggles or {}
        # ★멀티봇 정체성(직원별 봇): role=역할키·name=직원 이름(디스코드 표시명과 동일하게
        # 설정 권장). consume_sse=True인 primary만 SSE 소비/승인 게시 — N봇이 같은 스트림을
        # N번 소비/게시하는 중복을 차단(secondary는 @멘션 수신·자기 발화만).
        self._role = role
        self._name = name
        self._consume_sse = consume_sse
        # ★멀티봇 디스패치: role→bot 레지스트리(러너가 채움). primary가 응답 이벤트의
        # run_id(chat-{role}-)로 그 직원 봇을 찾아 그 정체성으로 게시한다. 단일봇이면 {}.
        self._registry: dict[str, "DiscordBot"] = {}
        # US-G6-0 세션 인증: 로그인 암호·쿠키 이름·획득한 세션 토큰(opaque).
        # ★password 는 절대 로그하지 않는다(비밀 비노출).
        self._password = password
        self._cookie_name = cookie_name
        self._session_token: str | None = None
        # 관측·테스트용 카운터(좀비봇 아님/게시·skip 가시성).
        self.posted_count = 0
        self.skipped_count = 0
        # ★P6e 슬래시 명령(`/cmd`): app_commands 트리. setup_hook 에서 primary 봇만 sync
        # (N봇 중복 글로벌 등록 차단·consume_sse 가드와 동형). NL on_message 는 불변.
        self.tree = app_commands.CommandTree(self)
        self._register_cmd()
        self.synced_cmd = False  # 관측·테스트용(primary 만 True 가 됨)

    # ── US-G6-0 세션 인증 헬퍼 ────────────────────────────────────────────────
    def _cookie(self) -> str | None:
        """완성된 Cookie 헤더 값("omc_session=<token>") 또는 None(토큰 부재)."""
        if self._session_token:
            return f"{self._cookie_name}={self._session_token}"
        return None

    async def _ensure_session(self) -> None:
        """세션 토큰이 없으면 로그인해 획득한다(실패 시 None — 미인증 진행·크래시 0).

        ★auth_client 는 단순 httpx 호출(게이트웨이 무관)이라 setup_hook 본문에서
        호출해도 wait_until_ready 데드락 규칙과 무관하다.
        """
        if self._session_token is None:
            from adapters.discord import auth_client

            self._session_token = await auth_client.login(
                self._base_url, self._password, cookie_name=self._cookie_name
            )

    async def _reauth(self) -> str | None:
        """★바운드 재로그인 1회 — 토큰 폐기 후 1회만 재획득.

        SSE on_unauthorized 콜백 겸 REST 401 재시도용. 토큰을 None 으로 비우고
        _ensure_session 을 1회 호출한 뒤 갱신된 Cookie 헤더 값을 돌려준다(실패면
        None). 호출자(SSE/REST)가 1회만 호출하므로 무한 재인증 루프 0.
        """
        self._session_token = None
        await self._ensure_session()
        return self._cookie()

    # ── setup_hook (로그인 후·게이트웨이 연결 전 1회) ──────────────────────────
    async def setup_hook(self) -> None:
        """① pending 백필 add_view 재등록 ② SSE 소비 태스크 create_task.

        ★wait_until_ready()/wait_for() 호출 금지(게이트웨이 연결 전 데드락).
        add_view 는 게이트웨이 불요(custom_id 기반) → 여기서 호출 가능.

        ★태스크 기동은 현재 실행 중인 이벤트루프(=setup_hook 가 도는 그 루프)에
        create_task — self.loop 와 동일 루프지만 _LoopSentinel 접근을 피한다.
        """
        # US-G6-0: 백필 GET /api/inbox 가 인증을 요구하므로 먼저 세션을 확보한다.
        # auth_client.login 은 단순 httpx(게이트웨이 무관)라 wait_until_ready 금지
        # 규칙과 무관하다. 실패해도 None 진행(크래시 0).
        await self._ensure_session()
        # ★멀티봇: SSE 소비·승인 백필은 primary(consume_sse=True) 봇만 수행 — N봇이 같은
        # 스트림을 N번 소비/게시하는 중복을 차단한다(secondary는 @멘션 수신·자기 발화만).
        if self._consume_sse:
            await self._backfill_persistent_views()
            # ★P6e: `/cmd` 슬래시를 primary 봇만 글로벌 동기화(N봇 중복 등록 차단). sync 는
            # HTTP 애플리케이션-명령 API(게이트웨이 wait 불요)라 setup_hook 본문에서 안전.
            # 실패해도 흡수(봇 부팅·SSE 소비를 막지 않음 — `/cmd` 만 미등록).
            await self._sync_app_commands()
            # fire-and-forget — 게이트웨이를 기다리지 않는다.
            asyncio.get_running_loop().create_task(self._sse_consume_loop())

    async def _sync_app_commands(self) -> None:
        """`/cmd` 슬래시를 글로벌 동기화(primary 봇만 호출·예외 흡수).

        ★primary 가드: 이 메서드는 setup_hook 의 `if self._consume_sse:` 블록에서만
        호출된다 → non-primary 봇은 tree.sync 를 호출하지 않아 N봇이 같은 글로벌 명령을
        N번 등록하는 중복을 차단한다. sync 실패(권한·레이트리밋)는 흡수해 봇 부팅을
        막지 않는다(`/cmd` 만 미등록·NL on_message 는 불변).
        """
        # ★DISCORD_GUILD_ID 설정 시 길드-스코프 동기화(즉시 등록). 글로벌 sync 는 ~1h 전파
        # 지연 + 봇 초대에 applications.commands 스코프가 없으면 0 등록(조용히 실패)이라,
        # 길드 동기화로 즉시·확정 등록한다(copy_global_to 로 전역 트리를 길드에 복제 후 sync).
        # 미설정 시 글로벌 폴백(기존 동작). sync 실패(스코프 누락·레이트리밋)는 흡수.
        guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
        try:
            if guild_id.isdigit():
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                self.synced_cmd = True
                logger.info(
                    "`/cmd` 슬래시 길드 동기화 완료(primary·guild=%s)", guild_id
                )
            else:
                await self.tree.sync()
                self.synced_cmd = True
                logger.info("`/cmd` 슬래시 글로벌 동기화 완료(primary 봇)")
        except Exception as exc:  # noqa: BLE001 — sync 실패가 봇 부팅을 막지 않음
            logger.warning("`/cmd` 슬래시 동기화 실패 — 미등록 진행: %s", exc)

    # ── P6e 슬래시 `/cmd <agent> <지시>`(NL on_message 와 별도 추가 경로) ──────────
    def _register_cmd(self) -> None:
        """`CommandTree` 에 `/cmd <agent> <instruction>` 1개를 등록(생성자에서 1회).

        discord import 는 bot.py 에만 허용되므로 트리 등록·콜백 본문을 여기 둔다.
        검증·매핑 로직은 slash_commands(순수·discord 무의존)에 위임한다.
        """

        @self.tree.command(
            name="cmd", description="직원에게 작업을 지시합니다 (/cmd <agent> <지시>)"
        )
        @app_commands.describe(
            agent="대상 직원 id (예: dev, research, qa, art, librarian, intake, ceo, coo, dev_lead)",
            instruction="지시 내용(자연어)",
        )
        async def _cmd(
            interaction: discord.Interaction, agent: str, instruction: str
        ) -> None:
            await self._handle_cmd(interaction, agent, instruction)

    async def _handle_cmd(
        self, interaction: "discord.Interaction", agent: str, instruction: str
    ) -> None:
        """`/cmd` 콜백 본문 — 검증 → enqueue → ephemeral 회신(예외 흡수·크래시 0).

        agent(페르소나 id) → 디스패치 키(bot_role) 매핑(resolve_cmd_target). 미지 agent
        또는 빈 지시는 거부(enqueue 0). enqueue 는 기존 rest_client.post_inbound 재사용
        (target_role=bot_role). 401 이면 ★1회 바운드 재인증 후 1회만 재시도(무한 재인증 0·
        on_message 와 동형). 1건 실패가 봇을 죽이지 않는다(예외 흡수).
        """
        try:
            target_role = resolve_cmd_target(agent)
            if target_role is None:
                allowed = ", ".join(known_agent_ids()) or "(없음)"
                await interaction.response.send_message(
                    f"알 수 없는 직원: `{agent}`. 가능한 직원: {allowed}",
                    ephemeral=True,
                )
                return
            text = (instruction or "").strip()
            if not text:
                await interaction.response.send_message(
                    "지시 내용이 비어 있습니다.", ephemeral=True
                )
                return

            # 3초 ack(enqueue 는 비동기 워커가 claim) — ephemeral defer.
            await interaction.response.defer(ephemeral=True)
            # ★멱등키: 슬래시 인터랙션 id(메시지 id 없음)를 post_inbound 의 message_id 로.
            message_id = str(interaction.id)

            async def _enqueue(cookie: str | None) -> rest_client.InboundResult:
                return await rest_client.post_inbound(
                    text,
                    message_id,
                    self._base_url,
                    cookie=cookie,
                    target_role=target_role,
                )

            result = await _enqueue(self._cookie())
            # 401(세션 만료) → 1회 바운드 재인증 후 1회 재시도(상한 1·무한 루프 차단).
            if result.status == 401:
                await self._reauth()
                result = await _enqueue(self._cookie())

            if result.handled:
                msg = f"[지시 접수] 직원={agent} · 내용={text}"
            else:
                msg = f"[지시 실패] 직원={agent}: {result.error}"
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as exc:  # noqa: BLE001 — 1건 실패가 봇을 죽이지 않음(on_message 동형)
            logger.error("/cmd 처리 실패 — 무시하고 계속: %s", exc)

    async def _backfill_persistent_views(self) -> None:
        """GET /api/inbox 의 현재 pending review 만 persistent View 재등록.

        과거(이미 해소된) 메시지의 View 는 재등록하지 않는다 → 클릭 시 자연 사망
        (유령 버튼 무료 차단, §4.5 ⑥). 조회 실패는 흡수(봇 기동을 막지 않음).

        ⚠️ mock 한계: unittest.mock Client 는 add_view 실영속을 못 잡으므로
        테스트는 "재등록 호출 여부 + pending 만 선별" 만 검증한다(실영속은 라이브 §G).
        """
        try:
            pending = await self._fetch_pending_reviews()
        except Exception as exc:  # noqa: BLE001
            logger.warning("부팅 백필(GET /api/inbox) 실패 — 재등록 건너뜀: %s", exc)
            return

        for item in pending:
            seq = item.get("interrupt_id")
            if seq is None:
                continue
            # 매핑을 재사용해 동일 custom_id(approve:{seq}/reject:{seq}) 의 View 생성.
            spec = event_to_message(
                int(seq), {"type": "INTERRUPT_REQUESTED", **item}, self._toggles
            )
            if spec is None:
                continue
            view = _build_view(
                spec,
                self._base_url,
                cookie_provider=self._cookie,
                reauth=self._reauth,
            )
            if view is not None:
                self.add_view(view)
                logger.info("persistent View 재등록(pending seq=%s)", seq)

    async def _fetch_pending_reviews(self) -> list[dict]:
        """GET {base_url}/api/inbox → review[](pending interrupt) 리스트.

        US-G6-0: 세션 쿠키(있으면) 첨부. 401(세션 만료/미인증)이면 ★1회 바운드
        재인증(_reauth) 후 1회만 재시도한다(무한 재인증 0).
        """
        url = f"{self._base_url.rstrip('/')}/api/inbox"

        async def _get(cookie: str | None) -> httpx.Response:
            headers = {"Cookie": cookie} if cookie else {}
            async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
                return await client.get(url, headers=headers)

        resp = await _get(self._cookie())
        if resp.status_code == 401:
            # 1회 바운드 재인증 후 1회 재시도(상한 1 — 무한 루프 차단).
            await self._reauth()
            resp = await _get(self._cookie())
        resp.raise_for_status()
        data = resp.json()
        review = data.get("review", [])
        return review if isinstance(review, list) else []

    # ── 무기한 SSE 소비 루프(별도 태스크) ─────────────────────────────────────
    async def _sse_consume_loop(self) -> None:
        """create_task 로 띄운 SSE 소비 코루틴.

        ★wait_until_ready() 는 setup_hook 본문이 아니라 **이 태스크 내부에서만**
        호출(채널 게시 전 1회 — 게이트웨이 준비 대기). 이후 consume_events 를
        async for 로 소비하며 프레임마다 should_post → 매핑 → 게시 → advance_cursor.

        ★예외 흡수(FanoutPoller web_server/sse.py:177-180 이식): 1프레임 처리 중
        예외가 나도 루프를 멈추지 않는다(좀비봇 방어 — gateway 만 살고 SSE 만 죽는
        상태 회피). CancelledError 는 정상 전파.
        """
        await self.wait_until_ready()

        # ★P7 관측성: 재연결 영구실패 감시(연속 N회 → ERROR 격상). sse_client.py 0수정·
        #   max_retries=None(자가치유) 유지. 이벤트 수신 시 note_alive로 카운터 리셋.
        sse_monitor = _install_sse_reconnect_monitor(_read_sse_alert_threshold())

        try:
            # ★시작 커서: 커서 부재(콜드스타트·상태 유실)면 0(전체 재생) 금지 → 현재
            #   head 로 시드해 과거 재방송을 구조적으로 차단. head 조회 실패는 이 try 의
            #   except 가 흡수한다(0 폴백 재방송보다 소비 중단이 안전).
            last_event_id = await self._resolve_start_cursor()
            async for seq, payload in consume_events(
                self._base_url,
                last_event_id=last_event_id,
                cookie=self._cookie(),
                on_unauthorized=self._reauth,
            ):
                sse_monitor.note_alive()  # 이벤트 수신 = 서버 정상 → 연속실패 카운터 리셋
                try:
                    await self._process_frame(seq, payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    # 1프레임 실패가 SSE 소비 루프를 죽이지 않는다(다음 프레임 계속).
                    logger.error("프레임 처리 실패(seq=%s) — 계속 진행: %s", seq, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("SSE 소비 루프 종료(예상치 못한 오류): %s", exc)

    async def _process_frame(self, seq: int, payload: dict) -> None:
        """한 SSE 프레임 처리 — cursor 가드 → 매핑 → 게시 → cursor 전진.

        예외는 호출자(_sse_consume_loop)가 프레임 단위로 흡수한다.
        """
        # 멱등 가드: 이미 게시한 seq 는 건너뜀(재연결/재시작 스팸 방어).
        if not cursor_mod.should_post(seq):
            self.skipped_count += 1
            return

        spec = event_to_message(seq, payload, self._toggles)
        if spec is None:
            # 게시 절제(정책상 미게시)여도 커서는 전진(같은 seq 재평가 방지).
            self.skipped_count += 1
            cursor_mod.advance_cursor(seq)
            return

        # ★멀티봇 디스패치: 응답 run_id(chat-{role}-)면 그 직원 봇이 게시, 아니면 self(primary).
        role = _role_from_run_id(payload.get("run_id", ""))
        await self._post_spec(spec, role=role)
        self.posted_count += 1
        # 게시 성공 후 커서 전진(at-least-once — 게시 직후 크래시 시 1건 재게시).
        cursor_mod.advance_cursor(seq)

    async def _resolve_start_cursor(self) -> "int | None":
        """SSE 소비 시작 커서를 결정한다 — 콜드스타트 재방송 차단.

        커서 파일이 초기화돼 있으면(파일 존재) 그 seq부터 resume(기존 at-least-once
        유지). **커서 부재(상태 유실·신규 배포·마이그레이션)면 0(전체 재생)이 아니라
        현재 이벤트 head 로 시드**한다 — 디스코드는 영구 부작용 싱크라, 기록이 없을 때
        과거 전체를 재방송하는 것보다 '지금부터'가 압도적으로 안전하다(과거 스팸 차단).
        head 시드 후 cursor.json 도 전진시켜 should_post 게이트와 일관되게 만든다.

        head seq 조회(HTTP) 실패는 예외로 전파한다 — 0 으로 폴백해 재방송하는 것보다
        소비를 멈추는 편이 안전하다(호출부 _sse_consume_loop 의 except 가 흡수·로깅).
        """
        if cursor_mod.cursor_initialized():
            return cursor_mod.read_cursor() or None
        head = await self._fetch_head_seq()
        cursor_mod.advance_cursor(head)
        logger.warning(
            "discord 커서 부재(콜드스타트) → head=%s 로 시드(과거 재방송 차단)", head
        )
        return head or None

    async def _fetch_head_seq(self) -> int:
        """GET {base_url}/api/events/head → 현재 이벤트 head(최대 seq).

        ★어댑터 비침투 경계 유지(office/web_server 직접 import 금지) — head 도 HTTP 로
        받는다. 세션 쿠키 첨부·401(세션 만료)이면 1회 바운드 재인증 후 1회 재시도한다
        (_fetch_pending_reviews 동형·무한 재인증 0).
        """
        url = f"{self._base_url.rstrip('/')}/api/events/head"

        async def _get(cookie: "str | None") -> httpx.Response:
            headers = {"Cookie": cookie} if cookie else {}
            async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
                return await client.get(url, headers=headers)

        resp = await _get(self._cookie())
        if resp.status_code == 401:
            await self._reauth()
            resp = await _get(self._cookie())
        resp.raise_for_status()
        return int(resp.json()["head_seq"])

    async def _post_spec(
        self, spec: DiscordMessageSpec, role: str | None = None
    ) -> None:
        """DiscordMessageSpec 을 채널에 게시(버튼 있으면 persistent View).

        ★멀티봇: role이 레지스트리에 있으면 그 직원 봇의 Client로 게시 → 메시지가 그 직원
        정체성(이름·아바타·@멘션 가능)으로 나간다. 없으면 self(primary)가 게시(일반·승인).
        """
        poster = self._registry.get(role, self) if role else self
        channel = poster.get_channel(poster._channel_id)
        if channel is None:
            channel = await poster.fetch_channel(poster._channel_id)
        view = _build_view(
            spec,
            self._base_url,
            cookie_provider=self._cookie,
            reauth=self._reauth,
        )
        if view is not None:
            await channel.send(content=spec.content, view=view)
        else:
            await channel.send(content=spec.content)

    async def _send_text(self, text: str) -> None:
        """채널에 평문 메시지 게시(ack·안내용·버튼 없음)."""
        channel = self.get_channel(self._channel_id)
        if channel is None:
            channel = await self.fetch_channel(self._channel_id)
        await channel.send(content=text)

    def _strip_self_mention(self, content: str) -> str:
        """본문에서 이 봇의 멘션 토큰(<@id>/<@!id>)을 제거(순수 질문만 인바운드로)."""
        if self.user is None:
            return content
        uid = self.user.id
        for tok in (f"<@{uid}>", f"<@!{uid}>"):
            content = content.replace(tok, "")
        return content

    async def _route_chat(self, content: str, message: "discord.Message") -> None:
        """chat 인바운드 전송 — target_role=내 역할(primary=비서). 401 바운드 재인증 1회+재시도 1회."""
        role = self._role or None
        inbound = await rest_client.post_inbound(
            content,
            str(message.id),
            self._base_url,
            cookie=self._cookie(),
            target_role=role,
        )
        if inbound.status == 401:
            await self._reauth()
            await rest_client.post_inbound(
                content,
                str(message.id),
                self._base_url,
                cookie=self._cookie(),
                target_role=role,
            )

    # ── 인바운드 리스너(DC-2 · 양방향 대화) ──────────────────────────────────
    async def on_message(self, message: "discord.Message") -> None:
        """채널의 사람 발화를 분류해 워크플로 enqueue 또는 인바운드로 라우팅(US-G6-1·DC-2).

        ★무한루프 차단(메모리 hang 교훈 — 1차 방벽·결정론):
          - message.author == self.user 무시(봇 자신이 게시한 에코/응답·ack/안내 재진입 차단),
          - message.author.bot 무시(타 봇·웹훅 author 연쇄 차단),
          - self._channel_id 채널만(타 채널 무시),
          - 빈 내용 무시.
        봇 응답·에코·ack/안내는 전부 channel.send(=봇 author)라 위 가드가 재트리거를
        원천 차단한다(self.user author → on_message 재진입 안 됨).

        라우팅(classify_intent — pure·결정론·LLM 0·★오탐 0 우선):
          - stage     : 게임 단계 지시 → post_run(워크플로 enqueue) + ack 게시.
                        401이면 ★바운드 재인증(_reauth) 1회 후 1회만 재시도(무한 재인증 0).
          - ambiguous : 제작 의도는 있으나 단계 불명 → 단계 목록 안내 게시.
          - chat      : 그 외 전부(일반 대화·질문) → 기존 인바운드 폴백(보존).

        예외 흡수(좀비봇 방어 — SSE 소비 루프와 동형): 1건 실패가 봇을 죽이지 않는다
        (다음 메시지 계속). post_run/post_inbound 자체도 5xx/네트워크를 흡수한다.
        """
        try:
            # 자기 발화·봇/웹훅 발화는 절대 라우팅하지 않는다(루프 1차 차단).
            if message.author == self.user:
                return
            if getattr(message.author, "bot", False):
                return
            # 지정 채널만(타 채널 무시).
            if message.channel.id != self._channel_id:
                return
            # 빈 내용 무시.
            content = message.content or ""
            if not content.strip():
                return

            # ★멀티봇 @멘션 자기선택: 내가 지목되면 그 직원으로 chat 처리. 아무 직원도 지목
            # 안 된 일반 발화는 primary(비서)만 접수. 그 외(다른 직원 지목·내가 비-primary)는
            # 무시 — 16봇이 같은 메시지에 중복 반응하지 않게 한다.
            mentioned_me = self.user in message.mentions
            any_bot_mentioned = any(getattr(m, "bot", False) for m in message.mentions)
            if not mentioned_me and not (self._consume_sse and not any_bot_mentioned):
                return
            if mentioned_me:
                content = self._strip_self_mention(content).strip() or content
                await self._route_chat(content, message)
                return

            intent = classify_intent(content)
            if intent.kind == "stage":
                result = await rest_client.post_run(
                    intent.workflow, self._base_url, cookie=self._cookie()
                )
                if result.status == 401:  # ★바운드 재인증 1회 후 1회 재시도.
                    await self._reauth()
                    result = await rest_client.post_run(
                        intent.workflow, self._base_url, cookie=self._cookie()
                    )
                if result.handled:
                    await self._send_text(
                        f"[제작 지시 접수] 단계={intent.stage_label} · "
                        f"워크플로={intent.workflow} (task={result.task_id})"
                    )
                else:
                    await self._send_text(
                        f"[제작 지시 실패] {intent.stage_label}: {result.error}"
                    )
            elif intent.kind == "ambiguous":
                labels = " / ".join(STAGE_LABELS[s] for s in GAME_STAGES)
                await self._send_text(
                    f"어느 제작 단계를 진행할까요? ({labels}) 중 하나를 포함해 "
                    "말씀해 주세요. 예: '게임 밸런스 작업 시작해줘'"
                )
            else:  # chat — target_role=내 역할(primary=비서)로 인바운드(401 재인증 포함).
                await self._route_chat(content, message)
        except Exception as exc:  # noqa: BLE001 — 1건 실패가 봇을 죽이지 않음
            logger.error("on_message 라우팅 실패 — 무시하고 계속: %s", exc)


# ── 팩토리·진입점 ──────────────────────────────────────────────────────────────


def _read_config() -> dict:
    """endpoint.yaml discord 블록 + .env 환경변수에서 봇 설정을 읽는다.

    채널·토큰·base_url 은 환경변수 이름(endpoint.yaml)을 거쳐 값(.env/os.environ)으로.
    endpoint.yaml 이 없거나 discord 블록이 없으면 환경변수 직접 이름으로 폴백.

    Returns:
        {"token", "channel_id", "base_url", "toggles", "password", "cookie_name"} —
        token/channel_id 는 부재 시 빈 문자열/None. ★password 값은 절대 로그 금지.
    """
    block: dict = {}
    web_block: dict = {}
    try:
        import yaml

        # office/web_server·core 를 import 하지 않는다(비침투) — 프로젝트 루트는
        # 이 파일 위치에서 직접 계산(cursor.py 와 동일 방식).
        from pathlib import Path

        root = Path(__file__).parent.parent.parent
        cfg_path = root / "config" / "endpoint.yaml"
        if cfg_path.exists():
            full = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            block = full.get("discord", {}) or {}
            web_block = full.get("web", {}) or {}
    except Exception:  # noqa: BLE001
        # yaml 부재 등 — 환경변수 이름 기본값으로 폴백.
        block = {}
        web_block = {}

    token_env = block.get("bot_token_env", "DISCORD_BOT_TOKEN")
    channel_env = block.get("channel_id_env", "DISCORD_CHANNEL_ID")
    base_env = block.get("web_base_url_env", "WEB_BASE_URL")

    token = os.environ.get(token_env, "")
    channel_raw = os.environ.get(channel_env, "")
    base_url = os.environ.get(base_env, "") or "http://127.0.0.1:8800"

    channel_id: int | None
    try:
        channel_id = int(channel_raw) if channel_raw else None
    except (ValueError, TypeError):
        channel_id = None

    toggles = {
        "post_text": bool(block.get("post_text", False)),
        "post_tool": bool(block.get("post_tool", False)),
        "post_gating": bool(block.get("post_gating", False)),
    }

    # US-G6-0 세션 인증: web.auth 블록에서 password_env·cookie_name 을 읽는다.
    # 비번은 password_env 가 가리키는 env(기본 LOGIN_PASSWORD)에서 값을 읽는다.
    # ★password 값은 절대 로그하지 않는다(비밀 비노출).
    auth_block = web_block.get("auth", {}) or {}
    password_env = auth_block.get("password_env", "LOGIN_PASSWORD")
    cookie_name = auth_block.get("cookie_name", "omc_session")
    password = os.environ.get(password_env, "")

    return {
        "token": token,
        "channel_id": channel_id,
        "base_url": base_url,
        "toggles": toggles,
        "password": password,
        "cookie_name": cookie_name,
    }


def _read_discord_block() -> dict:
    """endpoint.yaml의 discord 블록을 반환한다(없으면 {}). _read_config와 동일 방식·비침투."""
    try:
        import yaml
        from pathlib import Path

        root = Path(__file__).parent.parent.parent
        cfg_path = root / "config" / "endpoint.yaml"
        if cfg_path.exists():
            full = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return full.get("discord", {}) or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _parse_bot_roster(discord_block: dict, shared: dict, env: dict) -> list[dict]:
    """endpoint.yaml discord.bots 로스터 → 직원별 봇 설정 목록(순수·테스트가능).

    bots 리스트가 있으면 각 엔트리(role·name·token_env·avatar·primary)를 읽어 **토큰이
    있는 직원만** 활성 봇으로 만든다(점진 — 토큰 미제공 직원 제외). primary(=consume_sse)는
    명시 primary → secretary/pm → 첫 번째 순으로 **정확히 1개** 지정한다(중복 SSE 소비 차단).
    bots 미정의면 [](단일봇 폴백은 _read_bot_configs가 처리). env는 토큰 조회용(주입가능).
    """
    spec = discord_block.get("bots")
    if not isinstance(spec, list) or not spec:
        return []
    configs: list[dict] = []
    for entry in spec:
        if not isinstance(entry, dict):
            continue
        token_env = entry.get("token_env", "")
        token = env.get(token_env, "") if token_env else ""
        if not token:
            continue  # 토큰 미제공 직원 비활성(점진 확장).
        configs.append(
            {
                "role": str(entry.get("role", "")),
                "name": str(entry.get("name", "")),
                "avatar": str(entry.get("avatar", "")),
                "token": token,
                "primary": bool(entry.get("primary", False)),
                **shared,
            }
        )
    if configs and not any(c["primary"] for c in configs):
        pref = next(
            (c for c in configs if c["role"] in ("secretary", "pm")), configs[0]
        )
        pref["primary"] = True
    for c in configs:
        c["consume_sse"] = c["primary"]
    return configs


def _read_bot_configs() -> list[dict]:
    """라이브 봇 설정 목록(멀티봇 로스터 또는 단일봇 폴백).

    discord.bots 로스터가 있으면 그것(토큰 있는 직원만), 없으면 기존 단일
    DISCORD_BOT_TOKEN 1봇(primary). 토큰이 하나도 없으면 [](라이브 비활성).
    """
    cfg = _read_config()
    shared = {
        k: cfg[k]
        for k in ("channel_id", "base_url", "toggles", "password", "cookie_name")
    }
    roster = _parse_bot_roster(_read_discord_block(), shared, dict(os.environ))
    if roster:
        return roster
    if cfg["token"]:
        return [
            {
                "role": "",
                "name": "",
                "avatar": "",
                "token": cfg["token"],
                "primary": True,
                "consume_sse": True,
                **shared,
            }
        ]
    return []


def _build_bot(c: dict) -> "DiscordBot":
    """봇 설정 1건 → DiscordBot(★Message Content Intent 활성 — @멘션 텍스트 수신 필수)."""
    intents = discord.Intents.default()
    intents.message_content = True
    return DiscordBot(
        channel_id=c["channel_id"],
        base_url=c["base_url"],
        toggles=c["toggles"],
        intents=intents,
        password=c["password"],
        cookie_name=c["cookie_name"],
        role=c["role"],
        name=c["name"],
        consume_sse=c["consume_sse"],
    )


def _run_bots(configs: list[dict]) -> None:
    """N개 봇을 단일 프로세스·단일 이벤트루프에서 구동한다(discord.py v2.x 검증 패턴).

    ★context7 공식 문서 확인: `async with client: await client.start(TOKEN)` + asyncio.run.
    N봇은 asyncio.gather로 동시 구동(각 async with로 graceful close·blocking run() 회피).
    토큰 값은 로그 0.
    """
    bots = [(_build_bot(c), c["token"]) for c in configs]
    # ★디스패치 레지스트리(role→bot)를 모든 봇에 공유 — primary가 응답을 그 직원 봇으로
    # 게시한다(chat-{role}- run_id). 단일봇이면 자기 1개.
    registry = {bot._role: bot for bot, _ in bots if bot._role}
    for bot, _ in bots:
        bot._registry = registry

    async def _run_all() -> None:
        async def _one(bot: "DiscordBot", token: str) -> None:
            async with bot:
                await bot.start(token)

        await asyncio.gather(*[_one(bot, token) for bot, token in bots])

    asyncio.run(_run_all())


def main() -> None:
    """봇 진입점 — 토큰 있으면 라이브 연결, 없으면 정직 종료.

    멀티봇(discord.bots 로스터·직원별 봇) 또는 단일봇(DISCORD_BOT_TOKEN 폴백)을 구동한다.
    토큰이 하나도 없으면 "라이브 비활성" 로그 + 정상 반환(exit 0 상응·크래시 0). 라이브
    연결은 사용자 토큰 제공 시에만(§제약 #2). Message Content Intent는 _run_bots가 켠다.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    configs = _read_bot_configs()

    if not configs:
        logger.info(
            "라이브 비활성(토큰 없음) — DISCORD_BOT_TOKEN/로스터 토큰 미설정. "
            "어댑터 순수 로직은 테스트로만 검증되며 라이브 게시는 하지 않는다."
        )
        return

    if any(c["channel_id"] is None for c in configs):
        logger.error(
            "DISCORD_CHANNEL_ID 미설정/잘못됨 — 라이브 게시 대상 채널 없음. 종료."
        )
        return

    roles = [c["role"] or "(default)" for c in configs]
    logger.info(
        "Discord 봇 라이브 기동(%d봇: %s, channel_id=%s, base_url=%s)",
        len(configs),
        roles,
        configs[0]["channel_id"],
        configs[0]["base_url"],
    )
    _run_bots(configs)
