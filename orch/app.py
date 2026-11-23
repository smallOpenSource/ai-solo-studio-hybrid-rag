"""orch.app — FastAPI seam holding the LIVE LangGraph cycle graph in-process.

Promotes the proven spike seed ``tmp/lg_spike/app_e2e.py`` to a repo module.
The compiled StateGraph + a single ``AsyncSqliteSaver`` (opened via __aenter__ in
the lifespan and held open for the process lifetime — sqlite-with-discipline,
Phase A) live in-process so HITL interrupt->resolve->resume happens in ONE
process. Point-reads use ``aget_state``; a full ``alist()`` scan is NEVER done
(single-connection deadlock — Phase A discipline).

Routes (the EXISTING Discord rest_client contract drives them unchanged):
  POST /api/run                      -> accept post_run's body {workflow, mock,
                                        ...}; return {task_id} (+thread_id).
  GET  /threads/by_task/{task_id}    -> task_id -> thread_id correlation.
  GET  /decisions/pending            -> bounded per-thread aget_state POINT-READs.
  POST /decisions/{thread_id}/resolve -> Command(resume=) in this live process;
                                        on completion emit the BLUF as an AG-UI
                                        TEXT_MESSAGE (chat-{role}-{id} run_id).
  GET  /outcome/{thread_id}          -> stored final BLUF + artifacts + timings.
  GET  /outbound/frames              -> the fanout-readable emitted frames.
  GET  /healthz                      -> readiness + span count.

Observability: openinference LangChain auto-instrumentation + a console OTLP
exporter (Phoenix server NOT required; it is only a viewer). Spans are counted
for evidence.

Bind 127.0.0.1:28202 only (per Phase B).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from orch.graph import (
    KnowledgeSearcher,
    ModelCaller,
    build_cycle_graph,
    chat_reply,
    use_caller,
    use_searcher,
)
from orch.outbound import OutboundEmitter, encode_chat_run_id
from orch import session as _session
from orch.org_graph import blackboard_sink, build_org_graph, use_strategy_caller
from orch.state_store import make_state_store
from orch.agent_status import AgentState, memory_status
from orch.digest import append_decision
from orch.intent import (
    classify_directive,
    graph_kind_of,
    kind_for_intent,
    make_thread_id,
)

# Observability (auto-spans; no manual instrumentation in node code).
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent  # /opt/studio
_DEFAULT_CKPT_DB = str(_HERE / "orch_ckpt.db")
# ★ Phase E — durable outbound seq high-water mark (survives orch restarts so the bot's
# persisted SSE cursor is never stranded above a reset in-memory counter). Lives beside
# the bot cursor under state/ (gitignored runtime state, not code/.env). Tests leave this
# None (pure in-memory); production wires it at the module-level app construction below.
_DEFAULT_EMITTER_SEQ_PATH = str(_REPO_ROOT / "state" / "orch" / "emitter_seq.json")

# Bind target per Phase B.
_HOST = "127.0.0.1"
_PORT = 28202

_SPAN_COUNT = {"n": 0}

# ── RO/RW 2-token x-api-key auth (Phase C, §6.8) ──────────────────────────────
# Two opaque tokens gate the seam: the RW token authorizes WRITE routes (start a
# cycle / resolve a decision); the RO token authorizes READ routes only. Both are
# read from the env (names only — values never hardcoded, mirrors the repo's
# *_env convention). If unset, deterministic test defaults are used so the suite
# runs without env; live injects real secrets. A request presents one token in the
# ``x-api-key`` header; write routes require the RW token, read routes accept RO
# OR RW.
_RW_ENV = "ORCH_API_KEY_RW"
_RO_ENV = "ORCH_API_KEY_RO"
_DEFAULT_RW = "orch-rw-dev"  # deterministic test default (overridden by env)
_DEFAULT_RO = "orch-ro-dev"

# ── §6.8 CORS (WA origin) + rate-limit env (P6a·add-only) ─────────────────────
# CORS allow-origins come from the env (NEVER hardcoded — same *_env convention as
# the tokens); live reads the WA public origin(s) from app.env. Tests inject explicit
# origins via create_app(cors_origins=[...]). Comma-separated env value -> list.
_CORS_ENV = "ORCH_CORS_ORIGINS"
# Rate-limit: a lightweight in-process sliding-window (NO new dependency — the plan's
# "의존 추가 회피"). Default window/limit are deterministic; live overrides via env.
_RATE_LIMIT_ENV = "ORCH_RATE_LIMIT"  # max requests per window (int; 0/unset => off)
_RATE_WINDOW_ENV = "ORCH_RATE_WINDOW_S"  # window seconds (float)
_DEFAULT_RATE_WINDOW_S = 1.0
# Routes EXEMPT from rate-limiting: the long-lived SSE stream (a single connection
# must not be throttled to death · M-5) + the health probes + the WA in-world panel
# feed (`/agents` polled ≤3s by the iframe · `/agent_panel.html` panel asset · §9.4
# in-world status is public-by-design — same visibility as the public WA office).
_RATE_EXEMPT_PATHS = frozenset(
    {"/events/stream", "/health", "/healthz", "/agents", "/agent_panel.html"}
)

# WA in-world 좌측 패널(§9.4) — renewal 9-페르소나 8-state 상태판. orch 가 same-origin
# (프록시 :33158 경유)으로 서빙하고, 패널이 /agents 를 ≤2.5s 폴링한다. 데이터는 전적으로
# /agents(canonical agent_status·미전이=정직 Idle) 기반 — 패널엔 하드코딩 로스터 0(날조 0).
_AGENT_PANEL_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIGS — Agent Office</title>
<style>
 :root{color-scheme:dark}html,body{margin:0;height:100%}
 body{font:12px/1.45 ui-monospace,Menlo,monospace;background:rgba(13,17,23,.96);color:#e6edf3}
 .wrap{padding:8px 10px}.hdr{font-weight:700;color:#3fb950;margin:0 0 5px;display:flex;align-items:center;gap:6px}
 .dot{width:8px;height:8px;border-radius:50%;background:#3fb950;box-shadow:0 0 6px #3fb950}
 .dot.off{background:#f85149;box-shadow:0 0 6px #f85149}
 .meta{font-size:9px;color:#8b949e;margin:0 0 7px}
 table{width:100%;border-collapse:collapse}td{padding:3px 6px 3px 0;border-bottom:1px solid #161b22;white-space:nowrap}
 td.ic{width:14px;text-align:center}td.name{color:#b9c2cf;padding-right:10px}td.st{text-align:right;font-weight:600}
 .foot{font-size:9px;color:#6e7681;margin-top:7px}.err{color:#f85149}
</style></head><body><div class="wrap">
 <div class="hdr"><span class="dot" id="dot"></span><span>AIGS Agent Office</span></div>
 <div class="meta" id="meta">connecting…</div>
 <table><tbody id="rows"></tbody></table>
 <div class="foot" id="foot">source: orch · /agents</div>
</div><script>
var C={Idle:["⬜","#586069"],Researching:["\U0001f50d","#58a6ff"],Coding:["⚙️","#3fb950"],
 Reviewing:["\U0001f441️","#d2a8ff"],Meeting:["\U0001f91d","#f0a868"],Waiting:["⏳","#e3b341"],
 Blocked:["\U0001f6ab","#f85149"],Sleeping:["\U0001f634","#4a535d"]};
var rowsEl=document.getElementById("rows"),metaEl=document.getElementById("meta"),
 dotEl=document.getElementById("dot"),footEl=document.getElementById("foot");
function render(agents,connected){
 dotEl.className=connected?"dot":"dot off";var live=0,html="";
 for(var i=0;i<agents.length;i++){var a=agents[i];var ic=C[a.state]||["⬜","#586069"];
  if(a.state&&a.state!=="Idle")live++;
  html+="<tr><td class='ic' style='color:"+ic[1]+"'>"+ic[0]+"</td><td class='name'>"+
   (a.label||a.id)+"</td><td class='st' style='color:"+ic[1]+"'>"+(a.state||"Idle")+"</td></tr>";}
 rowsEl.innerHTML=html;metaEl.textContent="live: "+live+" / "+agents.length+" personas";
}
async function poll(){try{
 var r=await fetch("/agents",{cache:"no-store"});if(!r.ok)throw new Error("HTTP "+r.status);
 var d=await r.json();render(d.agents||[],true);
 footEl.textContent="source: "+(d.source||"?")+" · live "+(d.live||0);footEl.className="foot";
}catch(e){render([],false);
 footEl.innerHTML="<span class='err'>⚠ disconnected — "+(e.message||"no source")+"</span>";footEl.className="foot err";}}
poll();setInterval(poll,2500);
</script></body></html>"""


def _parse_cors_origins(env: str | None) -> list[str]:
    """Comma/space-separated origin list -> clean list (empty => no CORS middleware)."""
    if not env:
        return []
    return [o.strip() for o in env.replace(",", " ").split() if o.strip()]


def _monotonic() -> float:
    """Default rate-limit clock (monotonic seconds · injectable seam for tests)."""
    return time.monotonic()


class _SlidingWindowLimiter:
    """Lightweight in-process sliding-window rate limiter (NO new dependency).

    Per-client (keyed by the request's client host) timestamp deque; a request is
    allowed iff fewer than ``limit`` timestamps fall inside the trailing ``window_s``
    seconds. ``limit <= 0`` disables limiting (``allow`` always True). Bounded memory:
    timestamps older than the window are evicted on each check. Deterministic with an
    injectable ``clock`` (tests pin it; default = monotonic). Single-process only (the
    orch seam binds one process · §6.8) — no cross-process coordination needed.
    """

    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._limit = int(limit)
        self._window = float(window_s)
        self._clock = clock or _monotonic
        self._hits: dict[str, list[float]] = {}

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def allow(self, key: str) -> bool:
        if self._limit <= 0:
            return True
        now = self._clock()
        cutoff = now - self._window
        hits = [t for t in self._hits.get(key, ()) if t > cutoff]
        if len(hits) >= self._limit:
            self._hits[key] = hits  # evict-only (request rejected)
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


# Auth endpoints open WITHOUT a session/key (mirror web_server/app.py:_AUTH_OPEN_PATHS) —
# everything else under /api/ + the SSE stream is gated.
_AUTH_OPEN_PATHS = frozenset({"/api/login", "/api/logout", "/api/auth/status"})

# WA in-world 패널 표면 — 인증 면제(공개). 패널은 공개 WA 오피스 브라우저에서 무자격증명으로
# 로드/폴링하므로 open 이어야 한다. 노출 데이터는 benign(persona id·display_name·8-state 토큰)만
# — current_task 등 민감 필드는 agents_panel_feed 에서 제외. §9.4 in-world 상태는 공개-설계.
_PANEL_OPEN_PATHS = frozenset({"/agents", "/agent_panel.html"})

# Write routes are RW-only (for the x-api-key path); everything else is a read (RO or RW).
# The cookie-auth path (the Discord adapter) is treated as RW for these (it does writes:
# run / approve / reject / inbound).
_WRITE_PATHS = frozenset({"/api/run", "/directives", "/api/inbound"})


def _write_path(path: str) -> bool:
    """True if the path is a WRITE route (RW token required on the x-api-key path).

    /api/run, /directives, /api/inbound start a cycle; /decisions/{thread_id}/resolve and
    /api/inbox/{seq}/approve|reject are also writes (matched by shape since the id is a
    path param).
    """
    if path in _WRITE_PATHS:
        return True
    if path.startswith("/decisions/") and path.endswith("/resolve"):
        return True
    if path.startswith("/api/inbox/") and (
        path.endswith("/approve") or path.endswith("/reject")
    ):
        return True
    return False


class _LoginBody(BaseModel):
    """POST /api/login body — a single password (no username; shared-secret gate).

    Mirrors web_server/app.py::_LoginBody so the adapter's ``{"password": ...}`` body
    (auth_client.login) deserializes identically.
    """

    password: str = ""


def _json_error(status: int, detail: str) -> "StreamingResponse":
    """A tiny JSON error response for the middleware (FastAPI HTTPException can't be
    raised from an http middleware, so we return a Response directly)."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"detail": detail})


class _CountingConsoleExporter(ConsoleSpanExporter):
    """ConsoleSpanExporter that tallies spans (observability evidence)."""

    def export(self, spans):  # type: ignore[override]
        _SPAN_COUNT["n"] += len(spans)
        return super().export(spans)


def _init_tracing() -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_CountingConsoleExporter()))
    # §6.7 Phoenix OTLP: PHOENIX_OTLP(엔드포인트) 설정 시 BatchSpanProcessor로 추가 export.
    # console exporter는 span-count 증거·테스트 결정론용으로 유지. 미설정/미설치/연결불가 시
    # console만 — 관찰성 강화이지 핫패스 의존 아님(비치명).
    otlp_ep = (
        (
            os.environ.get("PHOENIX_OTLP")
            or os.environ.get("PHOENIX_OTLP_ENDPOINT")
            or ""
        )
        .replace("http://", "")
        .replace("https://", "")
        .strip()
    ) or None
    if otlp_ep:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_ep, insecure=True))
            )
        except Exception:  # OTLP 미설치/엔드포인트 불가 — console만(비치명)
            pass
    trace.set_tracer_provider(provider)
    LangChainInstrumentor().instrument(tracer_provider=provider)


async def _ensure_store(st: Any):
    """Lazy ``StateStore`` on ``app.state`` (§5.1·§27.3-5 첫 소비자). add-only Tier-2.

    ★lifespan **byte-identical** 보존을 위해 lifespan 을 건드리지 않고 첫 사용 시점에 1회 생성·
    캐시한다(1-튜플 sentinel 로 "미초기화"와 "None 으로 초기화됨"을 구별). ``REDIS_ENABLED`` 미설정
    이면 ``make_state_store`` 가 ``(None, ...)`` 을 즉시 반환 → store=None 캐시(graceful·블로킹 0).
    실 Sentinel 클라이언트는 saver 와 동일하게 프로세스 수명 동안 유지. 절대 raise 안 함(라이브 비중단).
    """
    cached = getattr(st, "_state_store_cache", None)
    if cached is not None:
        return cached[0]
    store = None
    with contextlib.suppress(Exception):
        store, _ok, _err = await make_state_store()
    st._state_store_cache = (store,)
    return store


def _vault_dir() -> Path:
    """ADR/digest 가 쓰는 vault 루트. ORCH_VAULT_DIR 우선(테스트 격리·실파일 검증)·없으면 repo/vault.

    ★os.getcwd() 아님 — repo 루트 기준 절대경로(_REPO_ROOT). 테스트는 ORCH_VAULT_DIR=tmp_path 로
    override 해 실 vault/decisions 누적 오염을 막는다(green-fake 아닌 실파일 검증).
    """
    env = os.environ.get("ORCH_VAULT_DIR")
    return Path(env) if env else (_REPO_ROOT / "vault")


def _slugify(text: str) -> str:
    """ADR 파일명용 안전 슬러그(영숫자-하이픈·소문자·≤48). 빈 결과 → 'decision'."""
    import re as _re

    s = _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48].strip("-")) or "decision"


async def _adr_sink(st: Any, values: dict[str, Any], thread_id: str) -> str | None:
    """§19.2⑤ ADR sink(HIGH-1): 숙의 산출 adr_payload 를 vault/decisions/ADR-*.md 로 영속.

    if-interrupt(숙의→escalate) AND else(가역 숙의 완료) 양쪽 분기에서 호출 — 무음유실 0.
    adr_payload 없으면 no-op(None). 예외 suppress(라이브 비중단). 성공 시 'ADR-NNNN' 반환.
    ★date 는 payload 에 없으면 주입 안 함(render_adr 기본 처리·argless datetime 금지).
    """
    payload = values.get("adr_payload")
    if not payload:
        return None
    from orch.deliberation import (
        next_adr_number,
        render_adr,
    )  # 함수내(순환·지연 import)

    try:
        store = await _ensure_store(st)
        vdir = _vault_dir()
        num, suffix = await next_adr_number(store, vdir)
        slug = _slugify(str(payload.get("title") or payload.get("directive") or ""))
        text = render_adr(payload, number=num, slug=slug)
        name = f"ADR-{num:04d}-{slug}{('-' + suffix) if suffix else ''}.md"
        dest = vdir / "decisions" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return f"ADR-{num:04d}"
    except Exception:  # 라이브 비중단(ADR 기록 실패가 BLUF/interrupt 를 못 막게)
        return None


async def _digest_append(
    st: Any,
    tier: Any,
    summary: str,
    role: str,
    confidence: float | None = None,
    *,
    marker: bool = False,
    ref: str = "",
) -> None:
    """§20.4b 일일 다이제스트 누적(store 확보 + suppress). per-decision 핑 0·라이브 비중단.

    ★MED-5: marker=True(숙의 경유) 면 summary 앞에 '(숙의)' 표식·ref(ADR-NNNN) 있으면 다이제스트
    엔트리의 [ref] 로 기록(일반 NOTIFY 와 구별·append_decision 의 ref 인자 재사용·add-only).
    """
    store = await _ensure_store(st)
    summ = f"(숙의) {summary}" if marker else summary
    with contextlib.suppress(Exception):
        await append_decision(
            tier, summ, confidence=confidence, ref=ref, role=role, store=store
        )


def create_app(
    *,
    ckpt_db: str | None = None,
    caller: Optional[ModelCaller] = None,
    searcher: Optional[KnowledgeSearcher] = None,
    strategy_caller: Optional[ModelCaller] = None,
    tracing: bool = True,
    auth: bool = True,
    rw_key: str | None = None,
    ro_key: str | None = None,
    emitter_seq_path: str | None = None,
    cors_origins: list[str] | None = None,
    rate_limit: int | None = None,
    rate_window_s: float | None = None,
    enable_docs: bool = False,
    rate_clock: Callable[[], float] | None = None,
) -> FastAPI:
    """Build the FastAPI app holding the live graph + checkpointer.

    Args:
        ckpt_db:  SQLite checkpoint path (tests inject an isolated tmp db). None =>
                  the default orch_ckpt.db beside this module.
        caller:   optional deterministic model caller injected into every cycle
                  (tests pass a stub; live passes None => real gemma, affinity).
        searcher: optional deterministic knowledge searcher for the research step
                  (tests pass a no-network stub; live passes None => the real HTTP
                  knowledge client against the live service, dense + BGE rerank).
        tracing:  register the OTEL console exporter + openinference instrumentor.
                  Tests disable it (avoid global tracer-provider churn).
        auth:     enforce the RO/RW x-api-key gate (Phase C §6.8). Tests that drive
                  the contract pass the header; auth=False disables the gate for the
                  legacy Phase B integration tests (kept GREEN, no header churn).
        rw_key/ro_key: override the RW/RO tokens (else env ORCH_API_KEY_{RW,RO} or
                  the deterministic dev defaults).
        emitter_seq_path: file the OutboundEmitter persists its monotonic high-water
                  ``seq`` to (Phase E durability). None (tests) => pure in-memory emitter
                  that resets to 0 each construction; the production module-level app
                  passes _DEFAULT_EMITTER_SEQ_PATH so the seq survives orch restarts and
                  never sits below the bot's persisted SSE cursor (no silent drop).
        cors_origins: §6.8 CORS allow-origins (WA public origin). None => parsed from
                  the env (ORCH_CORS_ORIGINS, comma/space-separated; live app.env). An
                  empty list => no CORSMiddleware mounted. Values are NEVER hardcoded.
        rate_limit: §6.8 in-process rate limit (max requests per window per client).
                  None => env ORCH_RATE_LIMIT (0/unset => limiting off). ``/events/stream``
                  + ``/health`` + ``/healthz`` are exempt (M-5: long-lived SSE/probes).
        rate_window_s: rate-limit window seconds (None => env ORCH_RATE_WINDOW_S or 1.0).
        enable_docs: keep the FastAPI ``/docs``+``/redoc`` (default False => docs_url=None /
                  redoc_url=None so ``/docs`` is 404 · §6.8 attack-surface reduction).
        rate_clock: injectable monotonic clock for the limiter (tests pin it; orch
                  determinism convention — no Date.now()-style call sites in tests).

    The returned app exposes the new core's contract routes. The graph is compiled
    ONCE in the lifespan against the single AsyncSqliteSaver held open for the
    process lifetime.
    """
    # §6.8 docs_url=None/redoc_url=None by default (add-only FastAPI ctor args) — /docs 404.
    app = FastAPI(
        title="orch_core",
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
    )
    db_path = ckpt_db or _DEFAULT_CKPT_DB
    rw_token = rw_key or os.environ.get(_RW_ENV, _DEFAULT_RW)
    ro_token = ro_key or os.environ.get(_RO_ENV, _DEFAULT_RO)

    # §6.8 CORS (WA origin) — explicit arg wins, else env (literal-free). Empty => skip.
    _origins = (
        cors_origins
        if cors_origins is not None
        else _parse_cors_origins(os.environ.get(_CORS_ENV))
    )
    if _origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # §6.8 rate-limit — explicit arg wins, else env. limit<=0 disables (allow always).
    _limit = (
        rate_limit
        if rate_limit is not None
        else int(os.environ.get(_RATE_LIMIT_ENV, "0") or 0)
    )
    _window = (
        rate_window_s
        if rate_window_s is not None
        else float(os.environ.get(_RATE_WINDOW_ENV, "") or _DEFAULT_RATE_WINDOW_S)
    )
    _limiter = _SlidingWindowLimiter(_limit, _window, clock=rate_clock)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if tracing:
            _init_tracing()
        saver_cm = AsyncSqliteSaver.from_conn_string(db_path)
        saver = await saver_cm.__aenter__()  # held OPEN for the process lifetime
        _app.state.saver_cm = saver_cm
        # ── cutover A: 두 그래프 공컴파일(단일 saver) + flag ──────────────────
        # ORCH_ORG_ROUTING: off(기본·현 동작 byte-identical) | coexist | replace.
        # state.graph 는 cycle_graph 로 둬서 기존 state.graph 참조가 무변(off).
        _app.state.cycle_graph = build_cycle_graph(saver)
        _app.state.org_graph = build_org_graph(saver)
        _app.state.graph = _app.state.cycle_graph
        _app.state.org_routing = (
            os.environ.get("ORCH_ORG_ROUTING", "off").strip().lower()
        )
        _app.state.caller = caller
        _app.state.searcher = searcher
        _app.state.strategy_caller = strategy_caller  # org CEO(qwen) seam·테스트 stub용
        _app.state.emitter = OutboundEmitter(seq_path=emitter_seq_path)
        _app.state.threads = []  # thread_ids seen (bounded per-thread point-reads)
        _app.state.outcomes = {}  # thread_id -> final cycle dict
        _app.state.task_to_thread = {}  # task_id -> thread_id correlation
        # task_id -> message_id used to build the chat-{role}-{id} reply run_id.
        _app.state.task_to_message = {}
        # /api/inbound idempotency: message_id -> the prior {task_id, run_id} response
        # (mirrors web_server/inbound.py's persistent dedup on run_id="discord-{id}").
        _app.state.inbound_seen = {}
        try:
            yield
        finally:
            await saver_cm.__aexit__(None, None, None)
            if tracing:
                with contextlib.suppress(Exception):
                    LangChainInstrumentor().uninstrument()

    app.router.lifespan_context = lifespan

    if auth:

        @app.middleware("http")
        async def _api_key_gate(request: Request, call_next):
            """Dual auth gate: a valid ``omc_session`` COOKIE OR an ``x-api-key``.

            This is the DROP-IN requirement: the live Discord adapter authenticates with
            the ``omc_session`` cookie (auth_client.login -> Set-Cookie -> passed on every
            call), while the dashboard uses ``x-api-key``. orch accepts EITHER so the
            adapter works UNCHANGED.

            * /healthz + the auth endpoints (/api/login, /api/logout, /api/auth/status)
              are open (readiness probe + the login handshake itself).
            * A valid session cookie authorizes the request, including WRITE routes (the
              adapter does writes: run / approve / reject / inbound) — same trust the live
              web_server grants a logged-in session.
            * Otherwise the ``x-api-key`` path applies: WRITE routes demand the RW token,
              READ routes accept RO OR RW. A missing/unknown key (and no valid cookie) is
              401; an RO key on a write route is 403.
            """
            path = request.url.path
            if (
                path in ("/healthz", "/health")
                or path in _AUTH_OPEN_PATHS
                or path in _PANEL_OPEN_PATHS  # WA in-world 패널(공개 표면·benign)
            ):
                return await call_next(request)
            # Cookie-auth path (the Discord adapter): a valid opaque session is full access.
            if _session.verify_session(request.cookies.get(_session.cookie_name())):
                return await call_next(request)
            # x-api-key path (the dashboard / RW tooling).
            key = request.headers.get("x-api-key", "")
            if key not in (rw_token, ro_token):
                return _json_error(401, "missing or invalid x-api-key")
            if _write_path(path) and key != rw_token:
                return _json_error(403, "write route requires the RW api key")
            return await call_next(request)

    if _limiter.enabled:

        @app.middleware("http")
        async def _rate_limit_gate(request: Request, call_next):
            """§6.8 in-process rate limit (429 over limit). EXEMPT: SSE + health (M-5).

            ``/events/stream`` is a long-lived single connection (one ``allow`` would
            otherwise be charged repeatedly across its lifetime via reconnects/pings) and
            the health probes must never be throttled — both are exempt by path so a live
            dashboard/bot bridge stays connected. All other paths are counted per client
            host in a trailing window; over the limit returns 429 (Retry-After hint).
            """
            path = request.url.path
            if path in _RATE_EXEMPT_PATHS:
                return await call_next(request)
            client = request.client.host if request.client else "anon"
            if not _limiter.allow(client):
                resp = _json_error(429, "rate limit exceeded")
                resp.headers["Retry-After"] = str(int(_window) or 1)
                return resp
            return await call_next(request)

    def _graph_of_kind(st, kind: str):
        """``kind`` ('cycle'|'org') → 컴파일된 그래프. 미컴파일(레거시 테스트) → state.graph."""
        if kind == "org":
            return getattr(st, "org_graph", None) or st.graph
        return getattr(st, "cycle_graph", None) or st.graph

    def _route_kind(st, directive: str) -> str:
        """신규 directive → 그래프 종류. off → 항상 'cycle'(현 동작). replace → 'org'.

        coexist → 결정론 분류기(classify_directive·LLM 0). ★flag 는 *신규 시작*만 좌우.
        """
        routing = getattr(st, "org_routing", "off")
        if routing == "replace":
            return "org"
        if routing == "coexist":
            return kind_for_intent(classify_directive(directive))
        return "cycle"

    def _mint_thread_id(st, kind: str) -> str:
        """off → 무접두 bare uuid(현 동작 byte-identical). 그 외 → cyc:/org: 접두(durable 마커)."""
        if getattr(st, "org_routing", "off") == "off":
            return str(uuid.uuid4())
        return make_thread_id(kind)

    def _graph_for_thread(st, thread_id: str):
        """resume용: thread_id 접두사(마커)로 그래프 선택. ★flag 무관(marker-wins-over-flag)."""
        return _graph_of_kind(st, graph_kind_of(thread_id))

    def _log_affinity(thread_id: str, kind: str) -> None:
        """grep 가능·비밀 없는 라우팅 affinity 로그(thread_id=uuid·kind=cycle|org 만)."""
        print(f"route_affinity thread={thread_id} graph={kind}", flush=True)

    def _bind_cycle(request: Request):
        """Bind the injected caller AND searcher (stubs) for the cycle, or no-op.

        Live (caller=None, searcher=None) leaves both ContextVars at their real
        defaults (gemma caller / HTTP knowledge searcher). Tests inject stubs via
        create_app(caller=..., searcher=...). Both are bound INSIDE the cycle
        coroutine (ContextVars are copied at task creation, so binding must wrap the
        ainvoke that actually runs). Returns a single combined context manager.
        """
        st = request.app.state
        caller_fn: Optional[ModelCaller] = getattr(st, "caller", None)
        searcher_fn: Optional[KnowledgeSearcher] = getattr(st, "searcher", None)
        strategy_fn: Optional[ModelCaller] = getattr(st, "strategy_caller", None)
        stack = contextlib.ExitStack()
        if caller_fn is not None:
            stack.enter_context(use_caller(caller_fn))
        if searcher_fn is not None:
            stack.enter_context(use_searcher(searcher_fn))
        if strategy_fn is not None:  # org CEO(전략 seam)도 stub 가능(테스트 결정론)
            stack.enter_context(use_strategy_caller(strategy_fn))
        return stack

    async def _start_cycle(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Start the cycle on a fresh thread; return thread_id + an echoed task_id.

        Accepts the EXISTING rest_client.post_run body ({"workflow", "mock", ...})
        AND an optional "directive"/"target_role"/"message_id". 'workflow' is the
        directive when no explicit directive is given, so the existing Discord
        client drives this seam unchanged. task_id is echoed (post_run reads it).
        """
        directive = body.get("directive") or body.get("workflow") or ""
        target_role = body.get("target_role")
        message_id = body.get("message_id") or uuid.uuid4().hex
        st = request.app.state
        kind = _route_kind(st, directive)  # off→cycle(현 동작)·coexist→분류·replace→org
        thread_id = _mint_thread_id(st, kind)  # off→bare uuid·그 외→cyc:/org: 마커
        task_id = int(uuid.uuid4().int % 1_000_000)  # contract id (post_run reads)
        cfg = {"configurable": {"thread_id": thread_id}}
        graph = _graph_of_kind(st, kind)
        _log_affinity(thread_id, kind)
        st.threads.append(thread_id)
        st.task_to_thread[task_id] = thread_id
        st.task_to_message[task_id] = (target_role, message_id)
        bind = _bind_cycle(request)

        async def _run():
            # cycle: ceo_route -> design -> content -> HITL interrupt(정지). 나머지는 resolve.
            # org: ceo_route -> {부서|escalate}. escalate 만 interrupt; 그 외는 즉시 finalize.
            with contextlib.suppress(Exception):
                with bind:
                    await graph.ainvoke({"directive": directive}, cfg)
            run_id = encode_chat_run_id(target_role, message_id)
            with contextlib.suppress(Exception):
                snap = await graph.aget_state(cfg)
                if snap.interrupts:
                    # HITL/escalate 게이트 → AG-UI INTERRUPT_REQUESTED(기존 타입). seq->thread
                    # 기록으로 /api/inbox/{seq}/approve(어댑터 버튼) resume 가능.
                    st.emitter.emit_interrupt_requested(
                        run_id=run_id,
                        tool="game_concept_approval",
                        payload=dict(snap.interrupts[0].value),
                        thread_id=thread_id,
                    )
                    # ★HIGH-1 ADR sink: 숙의→escalate(graph.authority_gate 재진입 interrupt)면
                    # adr_payload 가 snap.values 에 있음 → ADR 영속(무음유실 0). org-escalate 는
                    # adr_payload 없어 no-op(안전). cycle 숙의만 adr_payload 보유(org 숙의는 P3b-5).
                    adr_ref = await _adr_sink(st, snap.values or {}, thread_id)
                    delib = bool((snap.values or {}).get("deliberation_path"))
                    # §20.4b 다이제스트: 대기 중 승인(🔴) 1건 누적(per-decision 핑 아님·묶음).
                    # cycle/org 양쪽 escalate 가 이 분기를 경유 → render_digest 의 🔴 가 실집계(dead 아님).
                    await _digest_append(
                        st,
                        "escalate",
                        (directive or "")[:80],
                        "org" if kind == "org" else "cycle",
                        marker=delib,  # MED-5: 숙의→escalate 면 '(숙의)' 표식
                        ref=adr_ref or "",  # 있으면 [ADR-NNNN]
                    )
                elif kind == "org":
                    # org 비-escalate: 인터럽트 없이 즉시 완료 → BLUF 직접 방출(graph-aware).
                    bluf = (snap.values or {}).get("bluf") or ""
                    if bluf:
                        st.emitter.emit_text_message(run_id=run_id, content=bluf)
                    st.emitter.emit_run_finished(run_id=run_id, workflow="org")
                    # §27.5 Blackboard sink (★프로덕션 호출부·add-only Tier-2·post-invoke).
                    # dept-keyed shared_facts → aigs:blackboard(REDIS_ENABLED 시) + vault 스냅샷
                    # (항상). sink 실패는 BLUF/run_finished(위)를 못 막게 suppress(라이브 비중단).
                    store = await _ensure_store(st)
                    with contextlib.suppress(Exception):
                        await blackboard_sink(snap.values, store, proj=thread_id)
                    # ★US-9d-1 add-only: intake enqueue sink (blackboard_sink 동형·post-invoke).
                    # org_graph dept 노드가 방출한 intake_requests(★declared 채널·operator.add reducer
                    # →snapshot 보존·동시 intake+research fan-out 누적)를 드레인해 push_task("intake") 호출.
                    # REDIS_ENABLED 미설정(store=None) 이면 graceful no-op — 예외 suppress(라이브 비중단).
                    # ★★ 이것이 유일 enqueue 지점(노드 본문 in-process enqueue 0·경계·§15).
                    try:
                        if store is not None:
                            for _ir in (snap.values or {}).get("intake_requests", []):
                                await store.push_task(_ir.get("queue", "intake"), _ir)
                    except Exception:  # noqa: BLE001 — 라이브 비중단(BLUF/run_finished 보호)
                        # MINOR-2: push_task 실패 관측성(blackboard_sink 와 동형 개선). exc_info=True
                        # 는 스택만(payload 평문 0)·라이브 _run 은 계속(BLUF/run_finished 이미 방출).
                        import logging  # noqa: PLC0415

                        logging.getLogger(__name__).warning(
                            "intake enqueue sink 실패(라이브 비중단)", exc_info=True
                        )
                    # ★HIGH-1 ADR sink(P3b-5): org 가역 숙의(commit&proceed)도 adr_payload 가
                    # 있으면 ADR 영속(숙의 결정 추적성·cycle else 와 동형). org escalate(숙의→
                    # recommend_escalate)는 위 if-interrupt 분기에 이미 _adr_sink 有 → if/else/org
                    # 3분기 모두 _adr_sink 보유(HIGH-1 완전). 비숙의 org 는 adr_payload 없어 no-op.
                    ovals = snap.values or {}
                    adr_ref = await _adr_sink(st, ovals, thread_id)
                    odelib = bool(ovals.get("deliberation_path"))  # MED-5 숙의 마커
                    # §20.4b 다이제스트: org 가역 결정(🟢/🟡) 누적(escalate 는 위 if 분기서 처리).
                    otier = ovals.get("authority_tier")
                    if otier in ("autonomous", "notify"):
                        await _digest_append(
                            st,
                            otier,
                            (ovals.get("bluf") or directive)[:80],
                            "org",
                            (ovals.get("authority_inputs") or {}).get("confidence"),
                            marker=odelib,  # 숙의 경유면 '(숙의)' 표식(일반 NOTIFY 와 구별)
                            ref=adr_ref or "",  # 있으면 [ADR-NNNN]
                        )
                else:
                    # ★§20 P3 (Architect#1 HIGH·add-only Tier-2): cycle 이 인터럽트 없이 완료
                    # (Autonomous/Notify)된 경우 BLUF 를 방출한다. 레거시는 cycle 이 항상 hitl_gate
                    # 에서 정지(resolve 경로가 방출)했으므로 이 분기가 없어 무음이었다. route=end
                    # (빌드 아님)은 bluf 가 없어 무방출(레거시 동작 보존).
                    bluf = (snap.values or {}).get("bluf") or ""
                    if bluf:
                        st.emitter.emit_text_message(run_id=run_id, content=bluf)
                        st.emitter.emit_run_finished(
                            run_id=run_id, workflow="game_build"
                        )
                        # ★HIGH-1 ADR sink: 가역 숙의 완료(interrupt 없이 finalize)도 adr_payload
                        # 가 있으면 ADR 영속(숙의 결정 추적성). 비숙의 cycle 은 adr_payload 없어 no-op.
                        vals = snap.values or {}
                        adr_ref = await _adr_sink(st, vals, thread_id)
                        delib = bool(vals.get("deliberation_path"))  # MED-5 숙의 마커
                        # §20.3 일일 다이제스트 누적(per-decision 핑 0·Notify/Autonomous).
                        tier = vals.get("authority_tier")
                        if tier in ("autonomous", "notify"):
                            await _digest_append(
                                st,
                                tier,
                                bluf[:80],
                                "cycle",
                                (vals.get("authority_inputs") or {}).get("confidence"),
                                marker=delib,  # 숙의 경유면 '(숙의)' 표식(일반과 구별)
                                ref=adr_ref or "",  # 있으면 [ADR-NNNN]
                            )

        asyncio.create_task(_run())
        return {"thread_id": thread_id, "task_id": task_id, "status": "enqueued"}

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        return {
            "ok": getattr(request.app.state, "graph", None) is not None,
            "spans_emitted": _SPAN_COUNT["n"],
        }

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        """§6.8 line305 literal ``GET /health`` — alias of /healthz (200·open probe).

        The spec names ``/health``; orch historically exposed only ``/healthz`` (kept
        as the internal readiness probe). Same body. Both are auth-open + rate-limit
        exempt (M-5 probes never throttled).
        """
        return {
            "ok": getattr(request.app.state, "graph", None) is not None,
            "spans_emitted": _SPAN_COUNT["n"],
        }

    @app.get("/knowledge/search")
    async def knowledge_search_route(request: Request, q: str = "") -> dict[str, Any]:
        """§6.8 ``GET /knowledge/search?q=`` (RO) — top-K over the live knowledge store.

        Reuses the SAME ``KnowledgeSearcher`` seam as the research node (the injected
        ``app.state.searcher`` in tests · the live HTTP dense+BGE-rerank client when
        None), so tests drive it with a no-network stub. Empty ``q`` => 400. The result
        shape mirrors office.knowledge_client.search ({"results":[...], "count":int}).
        """
        if not q or not q.strip():
            raise HTTPException(400, "q is required")
        searcher_fn: Optional[KnowledgeSearcher] = getattr(
            request.app.state, "searcher", None
        )
        try:
            k = int(request.query_params.get("k", "5"))
        except ValueError:
            k = 5
        k = max(1, min(k, 50))
        if searcher_fn is None:
            from orch.tools import (
                knowledge_search as _live_search,
            )  # lazy (no office at import)

            searcher_fn = _live_search
        result = await asyncio.to_thread(searcher_fn, q, k)
        return {"q": q, **(result if isinstance(result, dict) else {"results": result})}

    @app.get("/agents/status")
    async def agents_status(request: Request) -> dict[str, Any]:
        """§9.4 / §27.4 — per-persona 8-state status (RO · real data · no fabrication).

        Personas come from ``config/personas.yaml`` (file-derived · NO count hardcode);
        ``secretary`` is excluded by its ``skip_nick`` attribute (NOT an id literal ·
        LOW-C). For each persona the §27.4 record ({agent,state,current_task,since}) is
        read from the live ``aigs:status:<agent>`` store; a persona that has never
        transitioned reports ``state=Idle`` / ``current_task=null`` honestly (a fixed 8
        or a fabricated active state would be a lie — the integration test asserts the
        read reflects the ACTUAL last transition). ``store=None`` (REDIS off) falls back
        to the process-memory map (200 · graceful).
        """
        from office.personas import load_personas

        store = await _ensure_store(request.app.state)
        mem = memory_status()
        agents: list[dict[str, Any]] = []
        for persona in load_personas():
            if persona.get("skip_nick"):  # secretary (primary SSE consumer) — excluded
                continue
            pid = str(persona.get("id", ""))
            rec: dict[str, Any] | None = None
            if store is not None:
                with contextlib.suppress(Exception):
                    rec = await store.get_status(pid)
            if rec is None:
                rec = mem.get(pid)
            if rec is None:  # never transitioned -> honest Idle/null (no fabrication)
                rec = {
                    "agent": pid,
                    "state": AgentState.Idle.value,
                    "current_task": None,
                    "since": None,
                }
            agents.append(
                {
                    "agent": rec.get("agent", pid),
                    "state": rec.get("state", AgentState.Idle.value),
                    "current_task": rec.get("current_task"),
                    "since": rec.get("since"),
                }
            )
        return {"agents": agents}

    @app.get("/agents")
    async def agents_panel_feed(request: Request) -> dict[str, Any]:
        """WA in-world 패널(§9.4) 라이브 피드 — ``/agents/status`` 와 동일 canonical 데이터에
        패널 렌더용 ``label``(display_name)·집계를 덧붙인다(날조 0·미전이=정직 Idle).

        WA 좌측 패널(`/agent_panel.html`)이 same-origin(프록시 :33158 경유)으로 ≤2.5s 폴링한다.
        personas.yaml − secretary(skip_nick) · 라이브 ``aigs:status:*`` (REDIS) → mem 폴백.
        rate-limit 면제(공개 in-world 표면). store=None 이면 mem 폴백(200·graceful).
        """
        from office.personas import load_personas

        store = await _ensure_store(request.app.state)
        mem = memory_status()
        agents: list[dict[str, Any]] = []
        for persona in load_personas():
            if persona.get("skip_nick"):  # secretary(primary SSE consumer) 제외(LOW-C)
                continue
            pid = str(persona.get("id", ""))
            label = str(persona.get("display_name") or persona.get("name") or pid)
            rec: dict[str, Any] | None = None
            if store is not None:
                with contextlib.suppress(Exception):
                    rec = await store.get_status(pid)
            if rec is None:
                rec = mem.get(pid)
            state = (rec or {}).get("state") or AgentState.Idle.value
            # 공개 표면 — benign 필드만(id·label·state). current_task 등 민감필드 비노출.
            agents.append({"id": pid, "label": label, "state": state})
        live = sum(1 for a in agents if a["state"] != AgentState.Idle.value)
        return {"agents": agents, "source": "orch", "live": live}

    @app.get("/agent_panel.html")
    async def agent_panel_html() -> Any:
        """WA in-world 9-페르소나 상태 패널(renewal 택소노미·8-state). same-origin /agents 폴링."""
        from fastapi.responses import HTMLResponse

        return HTMLResponse(_AGENT_PANEL_HTML)

    @app.post("/api/run")
    async def post_api_run(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """EXISTING contract path — the live Discord rest_client.post_run hits THIS.

        Honors the client's hardcoded '{base_url}/api/run' (rest_client.py:164)
        with ZERO client edits. Returns {"task_id"} (what post_run reads) plus
        {"thread_id"} for HITL driving.
        """
        return await _start_cycle(request, body)

    @app.post("/directives")
    async def post_directive(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Renewal-native alias of /api/run (same behavior)."""
        return await _start_cycle(request, body)

    @app.get("/threads/by_task/{task_id}")
    async def thread_by_task(request: Request, task_id: int) -> dict[str, Any]:
        """Resolve task_id -> thread_id (mirrors office.run.load_run_id)."""
        tid = request.app.state.task_to_thread.get(task_id)
        if tid is None:
            raise HTTPException(404, "unknown task_id")
        return {"task_id": task_id, "thread_id": tid}

    @app.get("/decisions/pending")
    async def pending(request: Request) -> dict[str, Any]:
        """List threads paused at the HITL interrupt (bounded per-thread reads).

        POINT-READ ONLY: iterate known thread_ids and ``aget_state`` each. NEVER
        ``alist()`` (single-connection deadlock — Phase A discipline).
        """
        st = request.app.state
        out = []
        for tid in list(st.threads):
            cfg = {"configurable": {"thread_id": tid}}
            state = await _graph_for_thread(st, tid).aget_state(cfg)
            if state.interrupts:
                intr = state.interrupts[0]
                out.append(
                    {
                        "thread_id": tid,
                        "interrupt_id": intr.id,
                        "value": intr.value,
                        "next": list(state.next),
                    }
                )
        return {"pending": out}

    @app.get("/tasks")
    async def list_tasks(request: Request) -> dict[str, Any]:
        """§27.3 task 큐 RO 뷰(대시보드). add-only(기존 라우트 0수정)·READ 경로
        (_write_path=False → RO 토큰 허용). ★REDIS 미설정 → 200 + 빈 목록(graceful·503 아님·
        RO 패널 비중단·minor 1). store.ok 시 ``peek_tasks`` 위임. queue/n 은 쿼리파람.
        """
        st = request.app.state
        store = await _ensure_store(st)
        if store is None:
            return {"tasks": [], "redis": "disabled"}
        queue = request.query_params.get("queue", "default")
        try:
            n = int(request.query_params.get("n", "10"))
        except ValueError:
            n = 10
        tasks = await store.peek_tasks(queue, n=max(1, min(n, 100)))
        return {"tasks": tasks, "redis": "ok"}

    async def _resolve_thread(
        request: Request, thread_id: str, choice: str
    ) -> dict[str, Any]:
        """Resume the paused cycle in THIS process; run qa+finalize; emit the BLUF.

        Shared by BOTH ``/decisions/{thread_id}/resolve`` (the renewal-native route) and
        ``/api/inbox/{seq}/approve|reject`` (the Discord adapter button) so the resume +
        outbound emit is identical regardless of which contract drove it. On completion,
        emit the BLUF as an AG-UI TEXT_MESSAGE (role=assistant) with a ``chat-{role}-{id}``
        run_id so the addressed employee's bot posts it (multi-bot dispatch), then a
        RUN_FINISHED. Existing event types only.

        Raises HTTPException(409) if the thread has no pending interrupt.
        """
        cfg = {"configurable": {"thread_id": thread_id}}
        st = request.app.state
        # ★마커(thread_id 접두사)가 그래프를 선택 — 현 ORCH_ORG_ROUTING 무관
        # (marker-wins-over-flag: 정지중 thread 는 시작 그래프로 재개). 미지 접두사 → raise.
        try:
            kind = graph_kind_of(thread_id)
        except ValueError as exc:  # 미지 마커 접두사 → 무음 폴백 금지(명시 거부)
            raise HTTPException(422, "unknown thread graph marker") from exc
        graph = _graph_of_kind(st, kind)
        _log_affinity(thread_id, kind)
        state = await graph.aget_state(cfg)
        if not state.interrupts:
            raise HTTPException(409, f"thread {thread_id} has no pending interrupt")
        # Resume runs in this live process. Bind the same caller+searcher (stub or
        # gemma/HTTP) so qa+finalize keep gemma-affinity / stub parity.
        with _bind_cycle(request):
            final = await graph.ainvoke(Command(resume=choice), cfg)

        # Correlate this thread back to its (role, message_id) for the run_id.
        target_role, message_id = None, thread_id
        for tid_task, tid in st.task_to_thread.items():
            if tid == thread_id:
                target_role, message_id = st.task_to_message.get(
                    tid_task, (None, thread_id)
                )
                break
        run_id = encode_chat_run_id(target_role, message_id)

        bluf = final.get("bluf", "")
        if bluf:
            st.emitter.emit_text_message(run_id=run_id, content=bluf)
        st.emitter.emit_run_finished(
            run_id=run_id, workflow=("org" if kind == "org" else "game_build")
        )

        if kind == "org":
            # graph-aware: org 은 'game_build' 라벨/cycle 전용키(stage_timings 등) 금지.
            outcome: dict[str, Any] = {
                "thread_id": thread_id,
                "graph": "org",
                "resumed_with": choice,
                "decision": final.get("decision"),
                "departments": final.get("departments"),
                "routing_trace": final.get("routing_trace"),
                "artifacts": final.get("artifacts"),
                "bluf": bluf,
                "run_id": run_id,
            }
        else:
            outcome = {
                "thread_id": thread_id,
                "resumed_with": choice,
                "decision": final.get("decision"),
                "route": final.get("route"),
                "route_reason": final.get("route_reason"),
                "model": final.get("model"),
                "artifacts": final.get("artifacts"),
                "stage_timings": final.get("stage_timings"),
                "knowledge_source": final.get("knowledge_source"),
                "knowledge_count": final.get("knowledge_count"),
                "bluf": bluf,
                "run_id": run_id,
            }
        st.outcomes[thread_id] = outcome
        return outcome

    @app.post("/decisions/{thread_id}/resolve")
    async def resolve(
        request: Request, thread_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Resume the paused cycle in THIS process; run qa+finalize; emit BLUF."""
        choice = body.get("choice", "approve")
        return await _resolve_thread(request, thread_id, choice)

    # ── /api/login + cookie session (DROP-IN for the Discord adapter) ─────────
    @app.post("/api/login")
    async def login(body: _LoginBody, request: Request) -> JSONResponse:
        """Constant-time password check -> Set-Cookie ``omc_session=<opaque>``.

        Byte-compatible with web_server/app.py::login so auth_client.login reads the
        cookie unchanged: success => ``{"ok": True}`` + Set-Cookie; a wrong password is
        ``{"ok": False}`` with 200 (never 401, matching the live route's US-G3-4 note).
        """
        if not _session.check_password(body.password):
            return JSONResponse({"ok": False}, status_code=200)
        token = _session.create_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            key=_session.cookie_name(),
            value=token,
            httponly=True,
            secure=(request.url.scheme == "https"),
            samesite="strict",
            path="/",
            max_age=_session.ttl_seconds(),
        )
        return resp

    @app.post("/api/logout")
    async def logout(request: Request) -> JSONResponse:
        """Invalidate the session + clear the cookie (safe even if unauthenticated)."""
        _session.destroy_session(request.cookies.get(_session.cookie_name()))
        resp = JSONResponse({"authed": False})
        resp.delete_cookie(key=_session.cookie_name(), path="/")
        return resp

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        """Current cookie auth state (the front-end gate polls this)."""
        return {
            "authed": _session.verify_session(
                request.cookies.get(_session.cookie_name())
            )
        }

    # ── /api/inbox (GET review) + approve|reject (the adapter button) ─────────
    @app.get("/api/inbox")
    async def get_inbox(request: Request) -> dict[str, Any]:
        """Inbox in the live web_server shape — ``review`` carries ``interrupt_id``.

        The bot's boot backfill (bot.py:_backfill_persistent_views) reads
        ``review[i]['interrupt_id']`` to re-register persistent approve/reject Views.
        orch's review = the recorded INTERRUPT_REQUESTED seqs still pending. notify /
        question are empty (orch surfaces lifecycle via the SSE stream).
        """
        emitter = request.app.state.emitter
        return {"review": emitter.pending_interrupts(), "notify": [], "question": []}

    async def _decide_seq(request: Request, seq: int, choice: str) -> JSONResponse:
        """Resolve the interrupt correlated to ``seq`` (the adapter's approve/reject).

        Maps ``seq -> thread_id`` (recorded when the INTERRUPT_REQUESTED frame was
        emitted), then resumes via the shared ``_resolve_thread`` (Command(resume=)).
        Matches rest_client's status policy: 200 == handled; an unknown/already-resolved
        seq is 404 (which the adapter ALSO treats as handled/idempotent), so a double
        approve never 5xx's. The seq mapping is forgotten on success (re-approve -> 404).
        """
        emitter = request.app.state.emitter
        thread_id = emitter.thread_for_seq(seq)
        if thread_id is None:
            # Never an interrupt, or already resolved+forgotten -> handled/idempotent.
            raise HTTPException(404, f"no pending interrupt for seq {seq}")
        try:
            outcome = await _resolve_thread(request, thread_id, choice)
        except HTTPException as exc:
            if exc.status_code == 409:
                # Already resolved (no pending interrupt) -> 404 == handled (idempotent).
                emitter.forget_interrupt_seq(seq)
                raise HTTPException(
                    404, f"interrupt seq {seq} already resolved"
                ) from exc
            raise
        emitter.forget_interrupt_seq(seq)
        return JSONResponse(
            {
                "status": "resolved",
                "seq": seq,
                "decision": choice,
                "thread_id": thread_id,
                "run_id": outcome.get("run_id"),
            }
        )

    @app.post("/api/inbox/{seq}/approve")
    async def inbox_approve(request: Request, seq: int) -> JSONResponse:
        """Approve the interrupt for ``seq`` (rest_client.approve hits THIS, unchanged)."""
        return await _decide_seq(request, seq, "approve")

    @app.post("/api/inbox/{seq}/reject")
    async def inbox_reject(request: Request, seq: int) -> JSONResponse:
        """Reject the interrupt for ``seq`` (rest_client.reject hits THIS, unchanged)."""
        return await _decide_seq(request, seq, "reject")

    # ── /api/inbound (Discord human chat -> orch chat cycle) ──────────────────
    @app.post("/api/inbound")
    async def post_inbound(request: Request, body: dict[str, Any]) -> JSONResponse:
        """Discord human utterance -> echo + a chat reply (rest_client.post_inbound).

        Byte-compatible with web_server/inbound.py: body ``{text, message_id, author?,
        target_role?}``; validates text/message_id (400 if missing); idempotent on
        message_id (a repeat returns 200 ``duplicate`` with the prior task_id/run_id).
        The reply run_id is ``chat-{role}-{message_id}`` (encode_chat_run_id) so the
        addressed employee's bot posts it. The assistant reply is emitted as a
        TEXT_MESSAGE (role=assistant) the SSE bridge/mapping deliver (post_text on).
        Returns ``{status: accepted, task_id, run_id}``.
        """
        body = body or {}
        text = body.get("text")
        message_id = body.get("message_id")
        target_role = body.get("target_role")
        if not isinstance(text, str) or not text:
            return JSONResponse({"detail": "text is required"}, status_code=400)
        if not isinstance(message_id, str) or not message_id:
            return JSONResponse({"detail": "message_id is required"}, status_code=400)

        st = request.app.state
        # Persistent-style idempotency on message_id (mirrors the live run_id dedup).
        prior = st.inbound_seen.get(message_id)
        if prior is not None:
            return JSONResponse(
                {
                    "status": "duplicate",
                    "run_id": prior["run_id"],
                    "task_id": prior["task_id"],
                },
                status_code=200,
            )

        conv_role = (
            target_role if isinstance(target_role, str) and target_role else "default"
        )
        reply_run_id = encode_chat_run_id(conv_role, message_id)
        task_id = int(uuid.uuid4().int % 1_000_000)
        st.inbound_seen[message_id] = {"task_id": task_id, "run_id": reply_run_id}

        async def _chat():
            # One bounded reply (gemma-affinity; stub in tests). Emit it as the
            # addressed employee's assistant TEXT_MESSAGE + a RUN_FINISHED.
            with contextlib.suppress(Exception):
                with _bind_cycle(request):
                    reply = await asyncio.to_thread(chat_reply, text)
                if reply:
                    st.emitter.emit_text_message(run_id=reply_run_id, content=reply)
                st.emitter.emit_run_finished(run_id=reply_run_id, workflow="chat")

        asyncio.create_task(_chat())
        return JSONResponse(
            {"status": "accepted", "task_id": task_id, "run_id": reply_run_id},
            status_code=200,
        )

    @app.get("/outcome/{thread_id}")
    async def outcome(request: Request, thread_id: str) -> dict[str, Any]:
        """Return the stored final BLUF report (what the Discord bot posts)."""
        oc = request.app.state.outcomes.get(thread_id)
        if oc is None:
            raise HTTPException(404, "no outcome yet")
        return oc

    @app.get("/outbound/frames")
    async def outbound_frames(request: Request, after: int = 0) -> dict[str, Any]:
        """Return the fanout-readable emitted frames (seq > after)."""
        frames = request.app.state.emitter.frames_after(after)
        return {"frames": [{"seq": f.seq, "payload": f.payload} for f in frames]}

    @app.get("/events/stream")
    async def events_stream(request: Request) -> StreamingResponse:
        """AG-UI SSE stream — the dashboard/bot bridge (Phase C bridge route).

        Drains the OutboundEmitter frames in the EXACT live web_server SSE format
        (``id: {seq}\\ndata: {payload_json}\\n\\n``, media_type text/event-stream),
        so the EXISTING React dashboard SSE client and the Discord sse_client parser
        consume orch's stream byte-for-byte unchanged. ``Last-Event-ID`` (header or
        ``?last_event_id=``) resumes after a seq (idempotent cursor). ``idle_close_ms``
        bounds the stream for polling/tests (omit => long-lived; closes on client
        disconnect). A ``: ping`` keep-alive is sent during idle gaps.

        This is a READ route (RO or RW token); the middleware gates it.
        """
        last_seq = _parse_last_event_id(request)
        idle_close_ms = _parse_idle_close_ms(request)
        emitter = request.app.state.emitter
        return StreamingResponse(
            _drain_sse(emitter, last_seq, request, idle_close_ms),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# ── SSE helpers (mirror web_server/app.py:134-140 format exactly) ─────────────
def _parse_last_event_id(request: Request) -> int:
    """Resume cursor (0 default), from any of the live consumers' mechanisms:

    * the ``Last-Event-ID`` header — what the browser EventSource sends on auto
      reconnect (the React dashboard, web/src/api/sse.ts) and what the Discord
      sse_client sends (sse_client.py:144).
    * ``?last_seq=`` — the React client's explicit belt-and-suspenders query
      (web/src/api/sse.ts:42).
    * ``?last_event_id=`` — convenience alias for tooling/tests.
    """
    raw = (
        request.headers.get("Last-Event-ID")
        or request.query_params.get("last_seq")
        or request.query_params.get("last_event_id")
    )
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_idle_close_ms(request: Request) -> float | None:
    """Optional ?idle_close_ms= bound (None => long-lived stream)."""
    raw = request.query_params.get("idle_close_ms")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _format_sse(seq: int, payload: dict[str, Any]) -> str:
    """One SSE frame: ``id: {seq}\\ndata: {json}\\n\\n`` (web_server format)."""
    return f"id: {seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _drain_sse(emitter, last_seq, request, idle_close_ms):
    """Yield SSE frames for emitter frames with seq > last_seq, then tail new ones.

    Replays any already-emitted frames after last_seq first (reconnect => no loss),
    then polls the emitter for newer frames as the cycle emits them. Idle gaps send
    a ``: ping`` keep-alive; closes on client disconnect or idle_close_ms timeout.
    """
    cursor = last_seq
    idle_ms = 0.0
    poll = 0.1  # 100ms poll cadence
    # Initial replay (frames already present from an in-flight/finished cycle).
    for f in emitter.frames_after(cursor):
        cursor = f.seq
        yield _format_sse(f.seq, f.payload)
    while True:
        if request is not None and await request.is_disconnected():
            return
        new = emitter.frames_after(cursor)
        if new:
            for f in new:
                cursor = f.seq
                yield _format_sse(f.seq, f.payload)
            idle_ms = 0.0
        else:
            yield ": ping\n\n"
            idle_ms += poll * 1000
            if idle_close_ms is not None and idle_ms >= idle_close_ms:
                return
        await asyncio.sleep(poll)


# Module-level app for ``uvicorn orch.app:app`` (live run uses real gemma + OTEL).
# Production wires the durable emitter seq path so the monotonic outbound ``seq`` survives
# restarts (Phase E) — the bot's persisted cursor is never stranded above a reset counter.
app = create_app(emitter_seq_path=_DEFAULT_EMITTER_SEQ_PATH)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=_HOST, port=_PORT, log_level="info")
