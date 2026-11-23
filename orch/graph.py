"""orch.graph — the new LangGraph orchestration core (StateGraph).

Promotes the PROVEN spike seed ``tmp/lg_spike/graph_e2e.py`` to a repo module.
This is the REPLACE core: the old ``office/engine.py`` TaskQueue/EventStore/worker
machinery is rewritten here as a single StateGraph + checkpointer + interrupt.

Cycle shape (= renewal §25 Phase 3/6, mirrors office/coordinator.py STAGES):

    directive
      -> ceo_route      (gemma; Command(goto) into the stage chain or END)
      -> stage_research (KNOWLEDGE step: 1 seed search against the live knowledge
                         service -> cite the real source URL into the artifact)
      -> stage_design   (gemma artifact, consumes research's cited finding)
      -> stage_content  (gemma artifact, consumes design)
      -> hitl_gate      (interrupt() owner-approval gate INSIDE the cycle)
      -> stage_qa       (gemma artifact, consumes design+content)
      -> finalize       (BLUF report assembled from the threaded artifacts)

The Phase C ``stage_research`` node makes the **지식 (knowledge) dimension real**:
it calls the REUSE knowledge search tool (``office.knowledge_client.search`` over
HTTP -> the live knowledge service, dense + BGE rerank) with ONE seed query and
records the top result's **source URL** as a citation threaded into design. The
searcher is bound to a ContextVar (``use_searcher``) exactly like the model caller,
so deterministic tests inject a stub (no network) while live hits :8810.

Minimal persona set (the demo critical path, full 9 deferred to Phase E):
  CEO(route) / Research(knowledge cite) / ART/DEV-spec(design,content) / QA(qa).
  Every stage node
  runs on **gemma** (affinity; NO qwen mid-cycle — Phase A measured a 16.5s +
  6.15s swap-back stall, so qwen is reserved for rare hard decisions OUTSIDE a
  cycle). Coordinator STAGES pattern + per-stage "consume the prior artifact"
  contract are ported; artifacts thread through ``CycleState`` instead of disk.

Determinism seam: every node calls the model through the caller bound to a
``contextvars.ContextVar`` (``use_caller(...)``); tests bind a deterministic stub,
live leaves it unbound so the real bounded gemma gateway is used. The caller is
NOT threaded through ``CycleState`` because a callable is not msgpack-serializable
and would break checkpoint writes — a ContextVar keeps the checkpointed state
clean while propagating across langgraph's async + thread-executor boundaries
(langchain copies the context into ``run_in_executor``). The graph SHAPE, edges,
the single HITL interrupt, and CycleState are identical whether the caller is real
or stubbed — so checkpoint writes/reads and the resume path are exercised the same
way in tests and live.

API verified against installed langgraph 1.2.6:
  * Command / interrupt  <- langgraph.types  (interrupt(value) single-arg)
  * StateGraph / START / END  <- langgraph.graph
  * compiled with an (async) checkpointer passed to .compile(checkpointer=...)
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

# §20 자율 권한 — 결정론 분류기(LLM 0). authority 는 orch 의존 0(순환 없음).
# §19 숙의 트리거 술어 needs_deliberation 도 여기서 import(classify 불변·LLM 0).
from orch.authority import Tier, decide, escalate_payload, needs_deliberation

# §9.4 8-상태 발행 — 빌더-레벨 callable 래핑(노드 본문 byte 보존·HIGH-3). agent_status
# 는 orch 의존 0(순환 없음). add_node 인자만 래핑 → 노드 함수 정의 0수정.
from orch.agent_status import _with_status, node_status

# P8 §11.1② 스팟라이팅 — 회수 untrusted 데이터 경계 격리(stage_research add-only seam).
# spotlight 는 orch 의존 0(순수·결정론). provenance(E-1) 로 래핑 여부 결정.
from orch.spotlight import wrap_untrusted

# provider 라우팅 — 기본 caller 를 Azure-primary/로컬-fallback 으로 감싼다(Owner 지시).
# azure_llm 은 graph-agnostic(helper 가 fallback 을 인자로 받음·graph import 0) → 단방향(R1).
from orch.azure_llm import make_primary_fallback_caller

# ── Real gemma gateway (reuse the proven BENCH endpoint) ──────────────────────
# stdlib .env loader (python-dotenv is NOT in the orch env; keep env untouched).
_REPO_ROOT = Path(__file__).resolve().parents[1]  # /opt/studio


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader — does not overwrite existing os.environ."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'").strip('"'))


_load_env_file(_REPO_ROOT / ".env")

# gemma-affinity: the cycle is pinned to the general model. qwen is NOT used
# mid-cycle (Phase A swap-stall measurement). Read lazily so tests that only use
# a stubbed caller need no live env.
_GEMMA = os.environ.get("BENCH_GENERAL_MODEL", "gemma-4-e4b-it")

# Bounded stage caller defaults: low temp, modest tokens — the live system shares
# this endpoint, so keep every call small. Per-call hard timeout via ChatOpenAI.
_STAGE_TOKENS = 256
_STAGE_TIMEOUT = 45.0


# A model caller is ``(prompt, temperature, max_tokens) -> str``. The graph uses
# ONE caller for every node (gemma-affinity). Tests inject a deterministic stub.
ModelCaller = Callable[[str, float, int], str]

# A knowledge searcher is ``(query, k) -> {"results": [...], "count": int}`` — the
# EXACT office.knowledge_client.search contract. The research node calls it for the
# KNOWLEDGE dimension; tests bind a deterministic stub (no network), live hits the
# knowledge service over HTTP. Held in a ContextVar (NOT CycleState — a callable is
# not msgpack-serializable), same discipline as the model caller.
KnowledgeSearcher = Callable[[str, int], dict[str, Any]]


def _live_knowledge_search(query: str, k: int) -> dict[str, Any]:
    """Default searcher: REUSE office.knowledge_client.search (HTTP, unchanged).

    Lazy import so deterministic tests that bind a stub need no office import and
    never touch the live knowledge service. Raises httpx errors on a down service
    (connect=5s fast-fail) — the node degrades gracefully (see stage_research).
    """
    from orch.tools import knowledge_search  # lazy; wraps the unchanged client

    return knowledge_search(query, k=k)


_ACTIVE_SEARCHER: contextvars.ContextVar[KnowledgeSearcher] = contextvars.ContextVar(
    "orch_active_searcher", default=_live_knowledge_search
)


@contextlib.contextmanager
def use_searcher(searcher: KnowledgeSearcher) -> Iterator[None]:
    """Bind a knowledge searcher for the block (tests inject a no-network stub).

    Propagates across langgraph's async + thread-executor node calls just like
    ``use_caller``. Restores the previous searcher on exit.
    """
    token = _ACTIVE_SEARCHER.set(searcher)
    try:
        yield
    finally:
        _ACTIVE_SEARCHER.reset(token)


def _active_searcher() -> KnowledgeSearcher:
    """Return the searcher bound to the ContextVar (live HTTP search by default)."""
    return _ACTIVE_SEARCHER.get()


def _gemma_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """Default caller: one bounded REAL gemma call (gemma-affinity)."""
    llm = ChatOpenAI(
        base_url=os.environ["BENCH_API_URL"],
        api_key=os.environ["BENCH_API_KEY"],
        model=_GEMMA,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=_STAGE_TIMEOUT,
    )
    resp = llm.invoke(prompt)
    return (resp.content or "").strip()


# The active model caller (gemma-affinity: one caller for every node). Bound via
# ``use_caller`` for tests/live; default is the real gemma caller. Held in a
# ContextVar — NOT in CycleState — because a callable is not msgpack-serializable
# and would break checkpoint writes.
# ★provider 라우팅(Owner): 기본(unbound) 경로를 Azure-primary/gemma-fallback 으로 래핑한다.
# helper 는 call-time 에 ``ORCH_LLM_PRIMARY`` 를 읽으므로 import-시 래핑이 정합(off→gemma 직행).
# ``use_caller(stub)`` 는 ContextVar 전체를 override → 테스트/eval 격리는 영향 0(결정론 보존).
_ACTIVE_CALLER: contextvars.ContextVar[ModelCaller] = contextvars.ContextVar(
    "orch_active_caller",
    default=make_primary_fallback_caller(_gemma_caller, label="cycle"),
)


@contextlib.contextmanager
def use_caller(caller: ModelCaller) -> Iterator[None]:
    """Bind a model caller for the duration of the block (tests inject a stub).

    Propagates across langgraph's async + thread-executor node calls (langchain
    copies the context into run_in_executor). Restores the previous caller on exit.
    """
    token = _ACTIVE_CALLER.set(caller)
    try:
        yield
    finally:
        _ACTIVE_CALLER.reset(token)


class CycleState(TypedDict, total=False):
    """End-to-end cycle state (directive -> stages -> HITL -> BLUF)."""

    messages: Annotated[list, add_messages]
    directive: str
    route: str  # CEO routing decision ("build" | "end")
    route_reason: str  # gemma's one-line routing rationale
    decision_confidence: (
        float  # §20: ceo_route 가 산출한 confidence(미산출 시 authority proxy)
    )
    authority_tier: str  # §20.1 분류 결과(autonomous|notify|escalate) — digest/관찰용
    authority_inputs: dict[str, Any]  # §20.5 분류 입력(rev/cost/conf/scope) 기록
    deliberated: (
        bool  # §19 숙의 1회 실행 여부(one-shot 가드·재진입 needs_deliberation False)
    )
    deliberation_path: bool  # §19 숙의 경유 표식(BLUF/관찰·evidence)
    adr_payload: dict[
        str, Any
    ]  # §19.2⑤ 숙의 산출 ADR(재진입 전 기록·escalate sink 대비)
    artifacts: dict[str, str]  # stage_name -> small artifact text
    stage_timings: dict[str, float]  # stage_name -> seconds
    decision: str  # HITL owner approval result
    bluf: str  # final BLUF report
    model: str  # which model ran the stages (evidence: gemma)
    knowledge_source: str  # cited source URL from the research step (지식 evidence)
    knowledge_count: int  # how many knowledge hits the research step retrieved


# §4.1 Server-A 동시성 게이트 — gemma 워커 호출 동시 ≤2 (threading.BoundedSemaphore).
# 스펙 §4.1 은 ``asyncio.Semaphore(2)`` 를 명시하나, ``_gemma_caller`` 는 **sync** 이고 langgraph
# 노드는 thread-executor 로 실행되므로(org_graph map-reduce ``Send`` fan-out 에서 실제 동시 호출
# 발생) async semaphore 로 sync caller 를 감쌀 수 없다 → 동시성 ≤2 의도를 보존하는 **충실한 적응**
# 으로 threading.BoundedSemaphore 를 쓴다(P7d·ADR). CEO=qwen 은 별도 strategy seam 이라 영향 없음
# (게이트는 gemma 워커 슬롯 전용). 일반(cycle 직렬) 경로에선 동시 1 → no-op.
_SERVER_A_SEM = threading.BoundedSemaphore(2)


def _semaphore_wrapped(caller: ModelCaller) -> ModelCaller:
    """caller 호출만 감싸는 좁은 임계구역(노드 본문 미포함·release 누수 0·재진입 없음=leaf 호출)."""

    def _wrapped(prompt: str, temperature: float, max_tokens: int) -> str:
        with _SERVER_A_SEM:  # acquire 직전 / release 직후 — caller() 한 번만 보호
            return caller(prompt, temperature, max_tokens)

    return _wrapped


def _active_caller() -> ModelCaller:
    """Return the ContextVar caller (real gemma by default), wrapped with the §4.1 Server-A Semaphore(2).

    Node bodies are byte-preserved: every node still calls ``_timed_call(_active_caller(), ...)``;
    only the *returned* caller is now semaphore-wrapped (rollback = ``return _ACTIVE_CALLER.get()``).
    """
    return _semaphore_wrapped(_ACTIVE_CALLER.get())


def _timed_call(
    caller: ModelCaller, prompt: str, temperature: float, max_tokens: int
) -> tuple[str, float]:
    """Call the model, returning (content, wall_seconds)."""
    t = time.time()
    content = caller(prompt, temperature, max_tokens)
    return (content or "").strip(), round(time.time() - t, 2)


_CONF_RE = re.compile(r"conf(?:idence)?\s*[:=]?\s*([01](?:\.\d+)?)", re.IGNORECASE)


def _parse_confidence(text: str) -> float | None:
    """§20: route 라인의 emit confidence('conf 0.NN') 추출(0..1). 없으면 None(→proxy 폴백)."""
    m = _CONF_RE.search(text or "")
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if 0.0 <= v <= 1.0 else None


# ── CEO route node — ONE gemma call deciding the route ────────────────────────
def ceo_route(state: CycleState) -> Command:
    """CEO routes the owner directive into the stage chain (gemma-affinity).

    Asks the model to classify the directive as a game-build job; on the expected
    YES, Command(goto) enters the stage chain at stage_design. Defensive: any
    non-build answer ends the graph (no stages run).
    """
    directive = state.get("directive", "")
    prompt = (
        "You are the CEO router of an AI game studio. Decide if this owner "
        "directive is a request to BUILD A SMALL GAME, and rate your confidence. "
        "Answer on ONE line as 'ROUTE: build | conf 0.NN | <=10-word reason' or "
        "'ROUTE: end | conf 0.NN | <=10-word reason'.\n"
        f"Directive: {directive}"
    )
    content, secs = _timed_call(_active_caller(), prompt, 0.0, 48)
    low = content.lower()
    is_build = "route: build" in low or ("build" in low and "route: end" not in low)
    timings = dict(state.get("stage_timings") or {})
    timings["ceo_route"] = secs
    update: dict[str, Any] = {
        "route": "build" if is_build else "end",
        "route_reason": content,
        "model": _GEMMA,
        "stage_timings": timings,
        "messages": [{"role": "assistant", "content": f"[ceo] {content}"}],
    }
    # §20: parse the emitted confidence (proposer assessment). Absent/unparseable →
    # leave unset so authority.assess() falls back to its citation proxy (backward-compat).
    conf = _parse_confidence(content)
    if conf is not None:
        update["decision_confidence"] = conf
    # On build, enter the stage chain at the KNOWLEDGE research step (cites a real
    # source) BEFORE design, so design consumes grounded research (지식 dimension).
    return Command(goto="stage_research" if is_build else END, update=update)


# ── Generic stage runner (mirrors coordinator's "each stage = bounded work") ──
def _run_stage(
    state: CycleState, stage: str, instruction: str, upstream: list[str]
) -> dict[str, Any]:
    """Run one stage as a bounded model call producing a small artifact.

    Threads the prior stages' artifacts in as context (the coordinator contract:
    each stage consumes what the previous stage produced — here via graph state
    instead of game/data on disk). Returns a state update merging the artifact +
    timing, so the cycle's artifacts dict grows as the chain advances.
    """
    artifacts = dict(state.get("artifacts") or {})
    directive = state.get("directive", "")
    context = "\n".join(f"- {u}: {artifacts.get(u, '(missing)')}" for u in upstream)
    prompt = (
        f"You are the {stage} stage of a tiny game build. Owner directive: "
        f"{directive}\n"
        f"{('Upstream artifacts:' + chr(10) + context) if upstream else ''}\n"
        f"{instruction} Reply with a compact artifact, max 4 short lines, no preamble."
    )
    content, secs = _timed_call(_active_caller(), prompt, 0.2, _STAGE_TOKENS)
    artifacts[stage] = content
    timings = dict(state.get("stage_timings") or {})
    timings[stage] = secs
    return {
        "artifacts": artifacts,
        "stage_timings": timings,
        "messages": [
            {"role": "assistant", "content": f"[{stage}] ({secs}s) {content[:80]}"}
        ],
    }


def chat_reply(query: str) -> str:
    """One bounded gemma reply to a free-form chat directive (the ``chat`` workflow).

    The Discord inbound path (web_server/inbound.py) enqueues a ``chat`` workflow whose
    worker seeds the human's text as the query and a single chat node answers with an
    assistant TEXT_MESSAGE. orch reproduces that here as ONE bounded caller invocation
    (gemma-affinity, the same ContextVar caller every node uses), so a stub answers it in
    tests and the real bounded gemma answers it live. Pure w.r.t. the graph state — the
    outbound emit (TEXT_MESSAGE with the ``chat-{role}-{id}`` run_id) is done by the app.
    """
    q = (query or "").strip()
    prompt = (
        "You are a helpful AI employee of a small game studio replying to the owner in "
        "the team chat. Answer concisely in Korean, max 3 short lines, no preamble.\n"
        f"Owner: {q}"
    )
    content, _secs = _timed_call(_active_caller(), prompt, 0.3, _STAGE_TOKENS)
    return content


def _seed_query(directive: str) -> str:
    """Build the ONE knowledge seed query from the owner directive (지식 step).

    Fixed game-design framing keeps the seed deterministic for a given directive
    and on-corpus (the wiki holds game-design / F2P / live-service material), so the
    research step reliably returns a citable source. No model call (pure).
    """
    base = (directive or "small game").strip()
    return f"game design core loop player engagement monetization for: {base}"[:200]


def stage_research(state: CycleState) -> dict[str, Any]:
    """KNOWLEDGE step (지식): one seed search -> cite the real source into research.

    Calls the bound knowledge searcher (live: office.knowledge_client.search over
    HTTP -> dense + BGE rerank; tests: a no-network stub). The top hit's **source
    URL** is recorded as ``knowledge_source`` and woven into the research artifact
    so downstream design/finalize cite real grounding. The knowledge service being
    momentarily down must NOT break the cycle: on any searcher error (or empty
    corpus) the node degrades to an explicit '(no knowledge source)' citation and
    the cycle proceeds (the demo proves the LOOP; knowledge quality is evidence).
    """
    directive = state.get("directive", "")
    query = _seed_query(directive)
    source = "(no knowledge source)"
    count = 0
    finding = ""
    provenance: str | None = None  # P8 §11.1② — top hit 출처 신뢰표시(E-1 전파)
    t = time.time()
    try:
        res = _active_searcher()(query, 3)
        results = res.get("results") or []
        count = int(res.get("count") or len(results))
        if results:
            top = results[0]
            source = str(top.get("source") or "(unknown source)")
            finding = str(top.get("text") or "")[:240].replace("\n", " ").strip()
            provenance = top.get("provenance")  # P8: None/unknown → fail-safe untrusted
    except Exception as exc:  # noqa: BLE001 — knowledge down must not stop the cycle
        finding = f"(knowledge search unavailable: {type(exc).__name__})"
    secs = round(time.time() - t, 2)

    # P8 §11.1② 스팟라이팅(add-only seam): finding 을 다운스트림(design/content/finalize)
    # 프롬프트에 스레딩하기 전 provenance 로 untrusted 경계 격리(trusted/no-finding 은 비래핑).
    # ★기존 finding 추출 로직 byte 보존 — 회수→프롬프트 경계에 wrap 만 삽입(라이브 cycle 비파괴).
    finding_for_prompt = (
        wrap_untrusted(finding, source_url=source, provenance=provenance)
        if finding
        else finding
    )
    artifact = (
        f"finding: {finding_for_prompt or '(no finding)'}\n"
        f"source: {source}  (knowledge hits: {count})"
    )
    artifacts = dict(state.get("artifacts") or {})
    artifacts["research"] = artifact
    timings = dict(state.get("stage_timings") or {})
    timings["research"] = secs
    return {
        "artifacts": artifacts,
        "knowledge_source": source,
        "knowledge_count": count,
        "stage_timings": timings,
        "messages": [
            {"role": "assistant", "content": f"[research] cite={source} ({secs}s)"}
        ],
    }


def stage_design(state: CycleState) -> dict[str, Any]:
    """Stage 1 (design): produce a tiny clicker-game concept grounded in research."""
    return _run_stage(
        state,
        "design",
        "Using the research finding/source as grounding, define the core loop, the "
        "click reward, and ONE upgrade.",
        upstream=["research"],
    )


def stage_content(state: CycleState) -> dict[str, Any]:
    """Stage 2 (content): produce concrete content from the design artifact.

    ★발행 텍스트 윤문(orch.humanize): 산출 콘텐츠를 룰북 윤문 패스로 통과시켜 AI 티를
    제거한다(한국어일 때만·의미 불변·과윤문 시 원문 롤백·모델 실패 비차단). 영문 게임 아티팩트는
    _korean_ratio 가드가 자동 스킵하므로 무해(글 발행 등 한국어 콘텐츠에서만 작동).
    """
    out = _run_stage(
        state,
        "content",
        "Name the game and write 3 upgrade items with costs, building on design.",
        upstream=["design"],
    )
    from orch.humanize import humanize_korean  # noqa: PLC0415 (lazy·순환 회피)

    raw = (out.get("artifacts") or {}).get("content", "")
    res = humanize_korean(raw, caller=_active_caller())
    if res["applied"]:
        out["artifacts"]["content"] = res["text"]
        out["messages"].append(
            {
                "role": "assistant",
                "content": f"[content] humanized Δ{res['change_rate']:.0%}",
            }
        )
    return out


# ── §20 authority gate — 3-tier 권한(가역 무차단·비가역만 interrupt) ─────────────
# ★레거시 무조건 interrupt(hitl_gate·conf 0.55 하드코딩)를 대체(OD-E=ii 직접 재작성).
def authority_gate(state: CycleState) -> Command:
    """§20 의사결정 권한 게이트(spec §20.1/20.5) — "매 빌드 하드-interrupt"를 교체.

    decide()(결정론·LLM 0)로 (reversibility×reversal_cost×confidence×scope) → tier:
      ESCALATE → §20.4a 브리프 페이로드로 ``interrupt()`` (Owner 승인 대기·기존 resume 경로 보존·
        계산 confidence 가 하드코딩 0.55 대체).
      NOTIFY / AUTONOMOUS → **interrupt 없이** stage_qa 진행(§20 무차단). digest 기록은 app._run 이
        사후 수행(노드는 순수·동기·I/O 0 — blackboard_sink 동형). ★전 tier 가 stage_qa 로 수렴(N1).
    """
    directive = state.get("directive", "")
    tier, inputs, reason = decide(directive, state)
    # P3b §19: 숙의 우회(one-shot). needs_deliberation 내부에 not deliberated 가드 有 →
    # 2회차(deliberated=True)엔 False·갱신된 decision_confidence 로 decide() 가 새 tier 산출.
    # ★기존 ESCALATE/notify/autonomous 분기 본문은 0수정 — 이 분기 미통과 시 그대로.
    if needs_deliberation(directive, state, inputs):
        return Command(
            goto="deliberate",
            update={
                "messages": [
                    {"role": "assistant", "content": "[authority] -> deliberation(§19)"}
                ]
            },
        )
    # P3b §19.2.6 보정정지: 숙의가 escalate-class 를 surface(adr_payload.recommend_escalate)
    # → Owner interrupt. 2회차(deliberated=True·재진입 게이트가 호출)에만 평가 — needs_deliberation
    # 우회(가역 two_way) ↔ ESCALATE(one_way/owner_reserved/high_cost)는 상호배타라 decide()로는
    # "숙의→escalate"가 도달 불가하므로, judge 가 권고하면 이 분기로 surface(HIGH-1·MAJOR-1 A해석).
    # ★기존 ESCALATE/notify/autonomous/needs_deliberation 분기는 0수정(이 분기만 추가)·one-shot
    # (deliberated guard)이라 재진입 1회·종료성 유지(무한루프 0).
    adr = state.get("adr_payload") or {}
    if state.get("deliberated") and adr.get("recommend_escalate"):
        choice = interrupt(escalate_payload(directive, state, inputs))
        return Command(
            goto="stage_qa",
            update={
                "decision": str(choice),
                "authority_tier": "escalate",
                "authority_inputs": inputs,
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"[authority] deliberation->escalate '{choice}'",
                    }
                ],
            },
        )
    if tier is Tier.ESCALATE:
        choice = interrupt(escalate_payload(directive, state, inputs))
        return Command(
            goto="stage_qa",
            update={
                "decision": str(choice),
                "authority_tier": tier.value,
                "authority_inputs": inputs,
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"[authority] escalate -> owner '{choice}'",
                    }
                ],
            },
        )
    # 가역(Autonomous/Notify) → 무차단 진행(interrupt 0). decision 필드는 finalize 가 소비.
    return Command(
        goto="stage_qa",
        update={
            "decision": "auto" if tier is Tier.AUTONOMOUS else "notify",
            "authority_tier": tier.value,
            "authority_inputs": inputs,
            "messages": [
                {"role": "assistant", "content": f"[authority] {tier.value} ({reason})"}
            ],
        },
    )


# ── §19 숙의 래퍼 노드 — 컴파일 서브그래프 1회 실행 → authority_gate 재진입(MAJOR-1) ─
# ★래퍼 노드 채택(Opt 2A): 컴파일 서브그래프를 직접 add_node 하면 DeliberationState↔
# CycleState 키정렬 마찰 → 래퍼가 깔끔(서브그래프는 nested compiled·캡슐화 보존).
def deliberate(state: CycleState) -> Command:
    """§19 숙의 서브그래프 1회 실행 → authority_gate 재진입(MAJOR-1).

    ★재진입 전 adr_payload/갱신 conf/deliberated 를 update 에 실어 기록(escalate
    sequencing·HIGH-1: 2회차 게이트가 escalate interrupt 할 때 app.py if-interrupt 가
    snap.values 서 adr_payload 를 읽음·P3b-3 대비). 2회차엔 deliberated=True 라
    needs_deliberation False → 게이트가 갱신 conf 로 ESCALATE/notify/autonomous 분기.
    """
    from orch.deliberation import run_deliberation  # ★lazy(순환 import 회피)

    res = run_deliberation(state.get("directive", ""))
    return Command(
        goto="authority_gate",  # ★재진입 — one-shot 가드로 정확히 1회(무한루프 0)
        update={
            "decision_confidence": float(
                res.get("confidence", state.get("decision_confidence", 0.55))
            ),
            "deliberated": True,
            "deliberation_path": True,
            "adr_payload": res.get("adr_payload", {}),
            "messages": [
                {
                    "role": "assistant",
                    "content": "[deliberate] §19 synthesize -> re-eval",
                }
            ],
        },
    )


def stage_qa(state: CycleState) -> dict[str, Any]:
    """Stage 3 (qa): final-stage check over design+content."""
    return _run_stage(
        state,
        "qa",
        "List 2 pass checks and 1 risk for this concept. End with VERDICT: PASS or HOLD.",
        upstream=["design", "content"],
    )


# ── finalize — assemble the BLUF report (결론/필요행동 먼저, 근거 뒤) ──────────
def finalize(state: CycleState) -> dict[str, Any]:
    """Produce the BLUF-style report the Discord bot would post.

    BLUF = Bottom Line Up Front: 결론 + 필요행동 먼저, 근거(stage artifacts) 뒤.
    This is the cycle's delivered artifact, handed to the outbound bridge.
    """
    artifacts = state.get("artifacts") or {}
    decision = state.get("decision", "approve")
    source = state.get("knowledge_source", "(no knowledge source)")
    prompt = (
        "Write a BLUF status report in Korean for the studio owner. Format EXACTLY:\n"
        "결론: <one line>\n"
        "필요행동: <one line>\n"
        "근거:\n- research: <short, MUST end with the source URL in parentheses>\n"
        "- design: <short>\n- content: <short>\n- qa: <short>\n"
        f"Owner approval was: {decision}.\n"
        f"research finding+source: {artifacts.get('research', '')}\n"
        f"design artifact: {artifacts.get('design', '')}\n"
        f"content artifact: {artifacts.get('content', '')}\n"
        f"qa artifact: {artifacts.get('qa', '')}"
    )
    content, secs = _timed_call(_active_caller(), prompt, 0.2, 320)
    # Guarantee the cited source surfaces in the BLUF even if the model omits it
    # (지식 evidence must be verifiable in the delivered report, not just state).
    if source and source not in content and source != "(no knowledge source)":
        content = f"{content}\n(지식 출처: {source})"
    timings = dict(state.get("stage_timings") or {})
    timings["finalize"] = secs
    return {
        "bluf": content,
        "stage_timings": timings,
        "messages": [{"role": "assistant", "content": "[finalize] BLUF report ready"}],
    }


def build_cycle_graph(checkpointer):
    """Compile the e2e cycle graph with the given (async) checkpointer.

    Edges: START -> ceo_route ; ceo_route -Command(goto)-> stage_research | END ;
    stage_research -> stage_design -> stage_content -> authority_gate ;
    authority_gate -Command(goto)-> stage_qa -> finalize -> END.
    ★§20: authority_gate(권한 게이트)가 레거시 hitl_gate(무조건 interrupt)를 대체 — 가역은 무차단,
    비가역만 interrupt. 토폴로지 불변(전 tier stage_qa 수렴·Command goto 는 정적 엣지 불요).
    """
    g = StateGraph(CycleState)
    # ★§9.4: add_node 인자만 _with_status 로 래핑(노드 본문 byte 보존·전이/출력 불변).
    # deliberate 는 별도 reachable 노드라 진입 발행 불요(plan: 래핑 무관) — byte 그대로.
    # ★MINOR-2 deslop: (persona, state) 는 _NODE_STATUS 단일 SSoT 에서 node_status() 로
    # 소싱한다(리터럴 중복 제거·드리프트 차단). 값은 종전 리터럴과 동일(behavior 불변).
    g.add_node("ceo_route", _with_status(ceo_route, *node_status("ceo_route")))
    g.add_node(
        "stage_research", _with_status(stage_research, *node_status("stage_research"))
    )
    g.add_node("stage_design", _with_status(stage_design, *node_status("stage_design")))
    g.add_node(
        "stage_content", _with_status(stage_content, *node_status("stage_content"))
    )
    g.add_node(
        "authority_gate", _with_status(authority_gate, *node_status("authority_gate"))
    )
    g.add_node("deliberate", deliberate)  # §19 숙의 래퍼(P3b-2)·진입 발행 불요
    g.add_node("stage_qa", _with_status(stage_qa, *node_status("stage_qa")))
    g.add_node("finalize", _with_status(finalize, *node_status("finalize")))

    g.add_edge(START, "ceo_route")
    # ceo_route -> stage_research | END handled by Command(goto=) inside ceo_route.
    g.add_edge("stage_research", "stage_design")
    g.add_edge("stage_design", "stage_content")
    g.add_edge("stage_content", "authority_gate")
    # authority_gate -> stage_qa | deliberate handled by Command(goto=) (가역 무차단 /
    # escalate resume / §19 숙의 우회). deliberate 는 정적 엣지로 authority_gate 복귀
    # (2-in-edge: stage_content + deliberate → authority_gate·재진입 토폴로지·MAJOR-1).
    g.add_edge("deliberate", "authority_gate")
    g.add_edge("stage_qa", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
