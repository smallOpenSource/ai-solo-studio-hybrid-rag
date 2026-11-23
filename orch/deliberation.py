"""orch/deliberation.py — SR/renewal §19 구조화된 숙의 서브그래프.

§19.2 프로토콜을 **실제 컴포넌트**로 구현(요약/재해석 금지):
  ① 독립 제안(병렬·상호 비가시·MED-3: Send payload 에 ``proposals`` 키 절대 없음)
  ② 역할 Proponent(찬성)·Challenger=Devil's Advocate(반드시 이의)·Synthesizer/Judge(조율)
  ③ 라운드 상한(≤3·노드 내부 정수 카운터 for-loop) + 강제 합성(무한 토론 금지)
  ④ Premortem(독립 fan-out·상호 비가시·"6–9개월 뒤 완전 실패—왜?")
  ⑤ 출력 = ADR(옵션·찬반·premortem 리스크·선택·confidence·소수의견 disagree&commit)
  ⑥ 보정된 정지는 *서브그래프 밖*(부모 authority_gate) — 본 모듈은 합성 conf 와
     ``deliberated=True`` 를 반환만 한다.

★**종료성이 P0 불변식**(프로젝트 다운 2회 이력·[[resource-exhaustion-self-kill-guard]]).
  그래프 사이클 0(DAG·구조적 종료) · 라운드는 ``synthesize``/``debate`` 노드 내부의
  bounded ``for r in range(cap)`` · ``invoke`` 에 ``recursion_limit`` 명시(defense-in-depth).
  ★**Tension B 불변식:** CAP 도달 시 무조건 다음 노드(합의 여부 *무관*·종료는 카운터
  구동이지 합의 감지 아님). Challenger 가 "반드시 이의"(§19.2②)라 합의는 구조적으로
  미달 → **"합의 시 조기종료" 최적화 영구 금지**(추가하면 종료가 Challenger 동의에
  의존 = 다운 3회째).

★**순환 import 회피(중요):** 이 모듈은 ``orch.graph`` 의 gemma caller seam 만 import 한다.
  graph 는 모듈탑서 deliberation 을 import 하지 않는다(P3b-2 에서 build_cycle_graph 가
  *내부 lazy import* 로 deliberate 노드를 붙일 예정). **org_graph 는 import 금지**
  (P3b-5 에서 org_graph 가 deliberation 을 import 하므로 역방향 금지) → Judge(qwen)
  caller 는 이 모듈 자체 ContextVar seam(use_strategy_caller 와 *동형*·org_graph 의존 0).
"""

from __future__ import annotations

import contextlib
import contextvars
import operator
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.types import Command, Send

# ★graph.py 의 gemma caller seam 재사용(동일 ContextVar → 테스트 use_caller(stub) 가
# nested 서브그래프 노드에도 전파됨). graph.py 수정 0·org_graph import 0.
from orch.graph import _active_caller

# provider 라우팅 — Judge 기본을 Azure-primary/qwen-fallback 으로 래핑(Owner 지시·graph-agnostic).
from orch.azure_llm import make_primary_fallback_caller

_REPO_ROOT = Path(__file__).resolve().parents[1]

# ── 라운드/팬아웃 상한(종료성 P0) ──────────────────────────────────────────────
_CAP = 3  # §19.2③ 라운드 상한(≤3) — 노드 내부 정수 카운터(reducer 아님·누적오류 차단)
_N = (
    3  # premortem/propose fan-out 폭 기본(OD-P3b-6 Owner 튜너블·9까지·단일 wave·재귀 0)
)

# ── §19.2① 독립 제안 역할 프롬프트(상호 비가시·서로 다른 관점) ─────────────────
ROLE_PROMPTS = (
    "보수적·리스크 회피 관점에서",
    "공격적·기회 추구 관점에서",
    "사용자/고객 가치 관점에서",
)
# ── §19.2④ premortem 독립 관점(상호 비가시·서로 다른 실패 각도) ────────────────
ANGLES = (
    "기술/구현 실패",
    "시장/사용자 외면",
    "운영/비용/일정 붕괴",
)


# ── Judge(qwen) caller seam — org_graph._strategy_caller 와 동형(단 import 금지) ──
_STRATEGY_MODEL = os.environ.get("BENCH_PRIMARY_MODEL", "qwen3.6-35b-a3b")


def _judge_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """기본 Judge caller: BENCH_PRIMARY_MODEL(qwen) 1콜(라이브). 테스트는 stub 주입.

    §19.2② "고위험은 Judge=Qwen3.6". gemma-affinity: 숙의 1회당 qwen 콜 ≤1(synthesize).
    """
    llm = ChatOpenAI(
        base_url=os.environ["BENCH_API_URL"],
        api_key=os.environ["BENCH_API_KEY"],
        model=_STRATEGY_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=90.0,
    )
    return (llm.invoke(prompt).content or "").strip()


# ★provider 라우팅(Owner): Judge 기본 경로를 Azure-primary/qwen-fallback 으로 래핑.
# call-time 에 ORCH_LLM_PRIMARY 읽음(off→qwen 직행). use_judge_caller(stub) 는 전체 override
# → eval gate 는 로컬 _judge_caller 를 명시 바인딩하므로 Azure-wrapped 기본 미도달(C1 격리).
_ACTIVE_JUDGE: contextvars.ContextVar = contextvars.ContextVar(
    "deliberation_judge_caller",
    default=make_primary_fallback_caller(_judge_caller, label="judge"),
)


@contextlib.contextmanager
def use_judge_caller(caller: Callable[[str, float, int], str]) -> Iterator[None]:
    """Judge(qwen) caller 바인딩(테스트 stub 주입). org_graph.use_strategy_caller 와 동형."""
    token = _ACTIVE_JUDGE.set(caller)
    try:
        yield
    finally:
        _ACTIVE_JUDGE.reset(token)


def _gemma(prompt: str, temperature: float = 0.3, max_tokens: int = 200) -> str:
    """Proposer/Challenger/Premortem gemma caller(graph ContextVar seam 경유)."""
    return (_active_caller()(prompt, temperature, max_tokens) or "").strip()


def _judge(prompt: str, temperature: float = 0.2, max_tokens: int = 320) -> str:
    return (_ACTIVE_JUDGE.get()(prompt, temperature, max_tokens) or "").strip()


# ── 서브그래프 상태 ─────────────────────────────────────────────────────────────
class DeliberationState(TypedDict, total=False):
    """§19.2 숙의 상태. proposals/premortem_risks 는 병렬 fan-out 머지(operator.add)."""

    directive: str
    round: int
    proposals: Annotated[list[dict], operator.add]
    premortem_risks: Annotated[list[str], operator.add]
    debate: dict  # {"pro": [...], "con": [...]}
    minority: str
    confidence: float  # org assess 입력 키(authority.py:149)
    decision_confidence: float  # cycle assess 입력 키(authority.py:147)
    adr_payload: dict
    deliberated: bool
    deliberation_path: bool


# ── 노드 ────────────────────────────────────────────────────────────────────────
def _dispatch(state: DeliberationState) -> Command:
    """§19.2① 독립 제안 fan-out(상호 비가시·단일 wave).

    ★MED-3: 각 Send payload 는 ``{directive, role_prompt, idx}`` 만 — ``proposals`` 키
    절대 없음(proposer 가 서로의 제안을 못 봄 → herding/동조 차단·falsifiable).
    """
    directive = state.get("directive", "")
    sends = [
        Send(
            "propose",
            {
                "directive": directive,
                "role_prompt": ROLE_PROMPTS[i % len(ROLE_PROMPTS)],
                "idx": i,
            },
        )
        for i in range(_N)
    ]
    return Command(goto=sends)


def _propose(state: dict[str, Any]) -> dict[str, Any]:
    """proposer(×N 병렬·gemma 1콜): 자기 role_prompt+directive 만 보고 독립 제안."""
    idx = state.get("idx", 0)
    directive = state.get("directive", "")
    role = state.get("role_prompt", "")
    text = _gemma(
        f"[독립 제안 #{idx}] {role} 아래 결정에 대한 *하나의* 구체적 제안을 2~3줄로 작성하라"
        f"(다른 제안은 보지 못함·독립 판단).\n결정: {directive}",
        0.4,
        160,
    )
    return {"proposals": [{"idx": idx, "role": role, "text": text}]}


def _debate(state: DeliberationState) -> dict[str, Any]:
    """§19.2②③ Proponent(찬성)/Challenger(반드시 이의) — 노드 내부 bounded 라운드.

    ★종료성 P0: 라운드는 그래프 사이클이 아니라 ``for r in range(_CAP)`` Python 루프.
    CAP 도달 시 **무조건** 다음 노드(premortem_dispatch)로 — 합의 여부 *무관*(Tension B
    불변식). Challenger 프롬프트가 "반드시 최소 1개 반론" 강제(§19.2② Devil's Advocate).
    """
    proposals = state.get("proposals", [])
    summary = "\n".join(f"- #{p.get('idx')}: {p.get('text', '')}" for p in proposals)
    pro: list[str] = []
    con: list[str] = []
    for _r in range(_CAP):  # 하드 라운드캡(≤3) — 그래프 레벨 루프 없음
        pro.append(
            _gemma(
                "[Proponent] 아래 제안들의 *가장 강한 합의안*을 찬성 논거로 1~2줄 옹호하라.\n"
                + summary,
                0.3,
                120,
            )
        )
        con.append(
            _gemma(
                "[Challenger=Devil's Advocate] 아래 제안/합의에 **반드시 최소 1개 반론**을 "
                "1~2줄로 제기하라(이의 없음 금지·과신 차단).\n" + summary,
                0.5,
                120,
            )
        )
    return {"debate": {"pro": pro, "con": con}, "round": _CAP}


def _premortem_dispatch(state: DeliberationState) -> Command:
    """§19.2④ Premortem fan-out(독립·상호 비가시·Owner 비참석)."""
    directive = state.get("directive", "")
    sends = [
        Send(
            "premortem",
            {"directive": directive, "angle": ANGLES[i % len(ANGLES)], "idx": i},
        )
        for i in range(_N)
    ]
    return Command(goto=sends)


def _premortem(state: dict[str, Any]) -> dict[str, Any]:
    """premortem(×N 병렬·gemma 1콜): "6–9개월 뒤 완전 실패—왜?"(독립·상호 비가시)."""
    angle = state.get("angle", "")
    directive = state.get("directive", "")
    risk = _gemma(
        f"[Premortem·{angle}] 6–9개월 뒤 아래 결정이 **완전히 실패했다**고 가정하라. "
        f"그 관점에서 가장 가능성 높은 실패 원인 1개를 1줄로 기재하라(독립·상호 비가시).\n"
        f"결정: {directive}",
        0.5,
        100,
    )
    return {"premortem_risks": [risk]}


def _synthesize(state: DeliberationState) -> dict[str, Any]:
    """§19.2⑤ 강제 합성(Judge=qwen 1콜) → ADR payload + 갱신 conf + deliberated.

    ★강제 합성: 합의 여부 무관(Tension B)·항상 1개 선택 + 소수의견(disagree&commit) 산출.
    ★green-theater 차단: LLM 파싱 실패 시 **빈 ADR 폴백 금지** — options/premortem/minority
    를 *실데이터*(실제 proposals·취합된 premortem_risks)로 채운다.
    ★sequencing(escalate·HIGH-1): adr_payload 와 갱신 conf·deliberated 를 함께 반환 →
    부모 재진입 게이트가 escalate interrupt 할 때 adr_payload 가 이미 머지된 state 에 존재.
    """
    directive = state.get("directive", "")
    proposals = state.get("proposals", [])
    debate = state.get("debate", {}) or {}
    risks = [r for r in (state.get("premortem_risks", []) or []) if r]
    options = [
        p.get("text", "").strip() for p in proposals if p.get("text", "").strip()
    ]
    if len(options) < 2:  # §27.2 Considered Options ≥2 (실데이터 보강)
        options = options + [
            f"(대안 미달·기본 진행안 {i})" for i in range(2 - len(options))
        ]

    prop_block = "\n".join(f"- {o}" for o in options)
    pro_block = "; ".join(debate.get("pro", []) or [])
    con_block = "; ".join(debate.get("con", []) or [])
    raw = _judge(
        "[Synthesizer/Judge] 아래 제안·찬반·premortem 을 종합해 *하나*를 선택하고 결론을 "
        "내려라. 형식: 'DECISION: <선택 1~2줄> | conf 0.NN | MINORITY: <소수의견 1줄> "
        "| ESCALATE: yes|no'(고위험·비가역효과·Owner 승인 필요라 판단되면 ESCALATE: yes).\n"
        f"제안:\n{prop_block}\n찬성: {pro_block}\n반론: {con_block}\n"
        f"premortem: {'; '.join(risks)}\n결정 주제: {directive}",
        0.2,
        320,
    )
    decision, conf, minority = _parse_synthesis(raw, options)
    # ★§19.2.6 보정정지(HIGH-1·escalate 도달성): 숙의가 escalate-class 를 surface하면
    # 부모 게이트가 Owner interrupt 하도록 권고. judge ESCALATE:yes OR conf<0.35(§20.1
    # "비가역&저확신" 정신·하드 floor). 부모 게이트(graph.authority_gate 재진입)가 읽고
    # interrupt 분기·ADR 에도 기록. 기존 강제합성/실데이터 폴백/conf 2키는 0수정(이것만 추가).
    recommend_escalate = _parse_escalate(raw) or (conf < 0.35)

    payload = {
        "title": (directive or "결정")[:64],
        "directive": directive,
        "context": directive,
        "options": options,
        "decision": decision,
        "pro": debate.get("pro", []) or [],
        "con": debate.get("con", []) or [],
        "premortem_risks": risks,
        "minority": minority,
        "confidence": conf,
        "reversibility": "two_way",  # 숙의 진입은 가역 결정(§19.1·needs_deliberation)
        "deciders": "CEO, COO",
        "recommend_escalate": recommend_escalate,  # ★부모 게이트/ADR 가 읽음(add-only)
    }
    return {
        "adr_payload": payload,
        # ★cycle=decision_confidence·org=confidence 둘 다 기록(부모 assess 재구동 입력·
        # HIGH-2 보정정지·authority.assess 가 decision_confidence→confidence 순으로 읽음).
        "confidence": conf,
        "decision_confidence": conf,
        "deliberated": True,
        "deliberation_path": True,
    }


_SYN_CONF_RE = re.compile(r"conf(?:idence)?\s*[:=]?\s*([01](?:\.\d+)?)", re.IGNORECASE)
_SYN_ESC_RE = re.compile(r"ESCALATE\s*[:=]?\s*(yes|no|true|false)", re.IGNORECASE)


def _parse_escalate(raw: str) -> bool:
    """Judge 출력의 'ESCALATE: yes|no' 추출(§19.2.6). 미표기/파싱불가 → False(보수·기본 commit)."""
    m = _SYN_ESC_RE.search(raw or "")
    return bool(m and m.group(1).lower() in ("yes", "true"))


def _parse_synthesis(raw: str, options: list[str]) -> tuple[str, float, str]:
    """Judge 출력 파싱 → (decision, confidence, minority). ★실패해도 실데이터 기반 폴백."""
    decision = ""
    minority = ""
    for line in (raw or "").splitlines():
        up = line.upper()
        if "DECISION:" in up and not decision:
            decision = line.split(":", 1)[1].split("|", 1)[0].strip()
        if "MINORITY:" in up:
            minority = line.split("MINORITY:", 1)[1].strip(" :|")
    if not decision:  # 파싱 실패 → 첫 제안을 선택으로(실데이터·빈 껍데기 금지)
        decision = options[0] if options else "(합성 실패·기본 진행안)"
    m = _SYN_CONF_RE.search(raw or "")
    conf = float(m.group(1)) if m and 0.0 <= float(m.group(1)) <= 1.0 else 0.6
    if not minority:
        minority = "(합의·소수의견 없음)"
    return decision, conf, minority


def build_deliberation_graph(*, n: int = _N, cap: int = _CAP):
    """§19.2 숙의 서브그래프 컴파일 — **DAG(그래프 사이클 0·구조적 종료·stateless)**.

    토폴로지: dispatch -Send×n-> propose -join-> debate -> premortem_dispatch -Send×n->
    premortem -join-> synthesize -> END. 라운드는 debate/synthesize *노드 내부* bounded
    for-loop(그래프 레벨 루프 없음·종료성 P0). ``checkpointer=None``(stateless·부모 호출 간
    리셋). ★n/cap 은 클로저 상수(_dispatch/_premortem_dispatch/_debate 가 모듈 _N/_CAP 참조)
    이므로 인자는 시그니처 호환·테스트 가독성용(기본=모듈 상한). invoke 시 recursion_limit 명시.
    """
    g = StateGraph(DeliberationState)
    g.add_node("dispatch", _dispatch)
    g.add_node("propose", _propose)
    g.add_node("debate", _debate)
    g.add_node("premortem_dispatch", _premortem_dispatch)
    g.add_node("premortem", _premortem)
    g.add_node("synthesize", _synthesize)
    g.set_entry_point("dispatch")
    g.add_edge("propose", "debate")  # n개 proposer join → debate 1회
    g.add_edge("debate", "premortem_dispatch")
    g.add_edge("premortem", "synthesize")  # n개 premortem join → synthesize 1회
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=None)


# DAG 라 자연종료하나 defense-in-depth(off-by-one 시 GraphRecursionError·종료성 P0).
_RECURSION_LIMIT = 50


def run_deliberation(directive: str) -> dict[str, Any]:
    """§19 숙의 서브그래프 1회 실행 → 결과 dict(confidence·adr_payload·deliberated·deliberation_path).

    cycle ``deliberate`` / org ``deliberate_org`` 래퍼가 공유(중복 제거·``recursion_limit``
    단일 출처). 래퍼는 반환 dict 를 각자 state 키(decision_confidence|confidence)로 매핑한다.
    """
    sub = build_deliberation_graph()
    return sub.invoke(
        {"directive": directive, "round": 0},
        config={"recursion_limit": _RECURSION_LIMIT},
    )


# ── §27.2 + §19.2⑤ ADR 직렬화 ──────────────────────────────────────────────────
def render_adr(payload: dict, *, number: int, slug: str) -> str:
    """§27.2 MADR + §19.2⑤ **합집합** → ADR 마크다운 문자열.

    §27.2 섹션(Status/Date/Deciders/Reversibility/Confidence/Context/Considered Options≥2/
    Decision/Consequences) + §19.2⑤ 추가 섹션(## 찬반·## 소수의견·## Premortem). 번호 zero-pad4.
    ★date 는 payload 에서 받는다(argless ``datetime.now()`` 금지·결정론). 두 추가 섹션 non-empty.
    """
    title = str(payload.get("title") or payload.get("directive") or slug)[:80]
    date = str(payload.get("date") or "")
    deciders = str(payload.get("deciders") or "CEO")
    rev = str(payload.get("reversibility") or "two_way")
    conf = float(payload.get("confidence", 0.6))
    context = str(payload.get("context") or payload.get("directive") or "")
    options = list(payload.get("options") or [])
    if len(options) < 2:  # §27.2 Considered Options ≥2(부정변이 게이트 대상)
        options = options + [f"(대안 {i})" for i in range(2 - len(options))]
    decision = str(payload.get("decision") or "(결정 없음)")
    pro = payload.get("pro") or []
    con = payload.get("con") or []
    risks = payload.get("premortem_risks") or []
    minority = str(payload.get("minority") or "(소수의견 없음)")

    opt_lines = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
    pro_lines = "\n".join(f"- (찬성) {p}" for p in pro) or "- (찬성 논거 없음)"
    con_lines = "\n".join(f"- (반론) {c}" for c in con) or "- (반론 없음)"
    risk_lines = "\n".join(f"- {r}" for r in risks) or "- (premortem 리스크 없음)"

    return (
        f"# ADR-{number:04d}: {title}\n"
        f"- Status: proposed\n"
        f"- Date: {date}\n"
        f"- Deciders: {deciders}\n"
        f"- Reversibility: {rev} · Confidence: {conf:.2f}\n\n"
        f"## Context\n{context}\n\n"
        f"## Considered Options\n{opt_lines}\n\n"
        f"## Decision\n{decision}\n\n"
        f"## Consequences\n- (+) 합의안 진행\n- (−) 소수의견/리스크는 아래 기록\n\n"
        f"## 찬반 (Proponent / Challenger)\n{pro_lines}\n{con_lines}\n\n"
        f"## 소수의견 (disagree & commit)\n{minority}\n\n"
        f"## Premortem (§19)\n{risk_lines}\n"
    )


# 패딩 마커(실데이터 결핍 시 _synthesize/render_adr 가 채우는 placeholder).
_PAD_MARKERS = ("(대안", "(합성 실패", "(결정 없음")


def adr_is_substantive(payload: dict) -> bool:
    """§19 숙의 ADR 이 *실데이터* 기반인지 판정(green-theater 차단 게이트·부정변이 [5]).

    실제 deliberation 은 ≥2 **비-패딩** 옵션(실 proposals) + ≥1 premortem 리스크를 낳는다.
    빈 proposals/premortem 이 placeholder 로 채워진 껍데기 ADR 은 ``False`` → verify 게이트 FAIL.
    ★이 함수가 항상 True 면 게이트가 무의미(연극) → 부정변이 테스트가 degenerate→False 를 실증한다.
    """
    options = [
        o
        for o in (payload.get("options") or [])
        if isinstance(o, str) and o.strip() and not o.strip().startswith(_PAD_MARKERS)
    ]
    risks = [
        r
        for r in (payload.get("premortem_risks") or [])
        if isinstance(r, str) and r.strip()
    ]
    return len(options) >= 2 and len(risks) >= 1


_ADR_NUM_RE = re.compile(r"ADR-(\d{4})-")  # anchored: NNNN만 추출(uuid 접미 무시)


async def next_adr_number(
    store: Any, vault_dir: str | Path | None = None
) -> tuple[int, str | None]:
    """다음 ADR 번호 + uuid 접미 슬러그 → ``(number, suffix)``.

    ★``store.incr("adr:seq")`` 우선(원자적 INCR·N=2 동시 완료[cycle+org] 충돌 0·suffix=
    None=신뢰 카운터라 접미 불요). 폴백(store=None 또는 incr 미지원/실패): ``vault/decisions/
    ADR-*.md`` glob → anchored regex ``ADR-(\\d{4})-`` 로 NNNN 만 추출(uuid 접미가 있어도
    혼동 0·``ADR-0007-foo-ab12.md``→7) max+1 + uuid 접미(REDIS 미설정 동시쓰기 충돌 회피).
    """
    if store is not None:
        incr = getattr(store, "incr", None)
        if incr is not None:
            with contextlib.suppress(Exception):
                return int(await incr("adr:seq")), None
    vdir = Path(vault_dir) if vault_dir is not None else (_REPO_ROOT / "vault")
    decisions = vdir / "decisions"
    mx = 0
    if decisions.is_dir():
        for p in decisions.glob("ADR-*.md"):
            m = _ADR_NUM_RE.search(p.name)
            if m:
                mx = max(mx, int(m.group(1)))
    return mx + 1, uuid.uuid4().hex[:8]
