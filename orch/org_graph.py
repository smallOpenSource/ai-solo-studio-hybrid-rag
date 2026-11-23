"""orch.org_graph — SR/renewal §6.2 9-노드 조직그래프 (라이브 프로덕션 권위·비파괴 additive).

★org_graph 는 Redis cutover A 로 **라이브 프로덕션 권위**다(route_affinity org→org_graph·
app.py:238/302/394). 따라서 수정은 **비파괴 additive**(토폴로지·goto·reducer·노드 시그니처 불변·
필드/함수 추가만)로 한다. orch/graph.py(7노드 사이클)는 별개 라이브 코어로 0수정. outbound·adapters 0수정.

§27.5 Blackboard: 부서가 ``shared_facts``(dept-keyed·operator.or_)에 **자기 dept 키만** 기여하므로
병렬 Send fan-out 에서 키 충돌 0(동시쓰기 손실 0). 실 ``aigs:blackboard`` 기록은
``blackboard_sink(state_values, store)`` — 프로덕션 호출부 = app.py ``_run`` ``elif kind=='org'``
(app.py:394·post-invoke·add-only Tier-2). REDIS 미설정 시 vault 스냅샷만(graceful).
§18.6 플레이북: ``_persona_prompt`` 가 ``vault/playbook/<id>.md`` 존재 시 절차기억을 비파괴 주입.

조직 위계(§6.2):
    START -> ceo_route -> {coo | dev_lead | escalate | END}
      coo      -map-reduce Send-> {research·art·librarian·intake 中 1+} -> finalize
      dev_lead -map-reduce Send-> {dev·qa 中 1+} -> finalize
      escalate -(interrupt)-> finalize
      finalize -> END

다부서 협업: COO/DevLead 는 `Command(goto=[Send(...)])` 로 1+ 부서를 **병렬** 디스패치. 병렬 부서가
``artifacts``/``departments`` 에 동시 기록하므로 **reducer**(operator.or_ / operator.add)로 머지한다.

모델: 부서 = gemma(`orch.graph._active_caller`·import 재사용). **CEO = 전략모델(qwen·BENCH_PRIMARY_MODEL)**
via 별도 ``use_strategy_caller`` seam. ★gemma-affinity: CEO 1콜/사이클만 qwen(스왑 6-21s 수용).
결정성: 테스트가 use_caller(부서)/use_strategy_caller(CEO) 로 stub 주입.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import operator
import os
from datetime import date as _date
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, Send, interrupt

# ★graph.py 재사용 — 동일 ContextVar 결정성 seam + gemma caller. graph.py 수정 0.
from orch.graph import (  # noqa: F401  (use_caller 재노출=테스트용)
    _active_caller,
    _parse_confidence,
    use_caller,
)

# provider 라우팅 — CEO 전략 caller 기본을 Azure-primary/qwen-fallback 으로 래핑(Owner 지시).
from orch.azure_llm import make_primary_fallback_caller

# §20 자율 권한 — 결정론 분류기(LLM 0·escalate 발화조건을 키워드→τ 로 격상).
# §19 숙의 트리거 술어 needs_deliberation 도 여기서 import(classify 불변·LLM 0·P3b-5).
from orch.authority import Tier, decide, escalate_payload, needs_deliberation

# §9.4 8-상태 발행 — 빌더-레벨 callable 래핑(노드 본문 byte 보존·HIGH-3·add_node 인자만).
from orch.agent_status import _with_status, dept_state, node_status

from office.personas import load_personas

_REPO_ROOT = Path(__file__).resolve().parents[1]

_DEPARTMENTS = ("research", "art", "librarian", "intake", "dev", "qa")
_COO_DEPTS = ("research", "art", "librarian", "intake")
_DEVLEAD_DEPTS = ("dev", "qa")


def _load_persona_prompts() -> dict[str, str]:
    """id -> §26 프롬프트 텍스트 (config/personas.yaml + prompts/personas/<id>.md)."""
    prompts: dict[str, str] = {}
    for persona in load_personas():
        pid = str(persona.get("id", ""))
        rel = persona.get("prompt_file")
        if not pid or not rel:
            continue
        path = _REPO_ROOT / str(rel)
        if path.is_file():
            prompts[pid] = path.read_text(encoding="utf-8")
    return prompts


_PERSONA_PROMPTS = _load_persona_prompts()


def _persona_prompt(pid: str) -> str:
    """§26 페르소나 프롬프트 + §18.6 플레이북 자동주입(vault/playbook/<id>.md 존재 시).

    ★비파괴 단일 seam: 모든 org 노드가 이 함수를 경유하므로 여기 한 곳만 바꿔 §18.6 자동주입을
    전 노드에 적용한다. 플레이북 파일이 **없으면** 반환은 기존(_PERSONA_PROMPTS.get)과 byte-identical
    (회귀 0·unit 단언). 있으면 절차기억을 append.
    """
    base = _PERSONA_PROMPTS.get(pid, f"# {pid}\n(역할 프롬프트 없음)")
    playbook = _REPO_ROOT / "vault" / "playbook" / f"{pid}.md"
    if playbook.is_file():
        return (
            base + "\n\n## 플레이북(절차기억)\n" + playbook.read_text(encoding="utf-8")
        )
    return base


def _call(prompt: str, temperature: float = 0.2, max_tokens: int = 256) -> str:
    """부서/finalize gemma caller. ★_active_caller() accessor 경유만(_gemma_caller 직접 금지)."""
    return (_active_caller()(prompt, temperature, max_tokens) or "").strip()


# ── CEO 전략(qwen) caller seam ────────────────────────────────────────────────
# CEO 는 전략모델(qwen·BENCH_PRIMARY_MODEL). 부서는 gemma. ★gemma-affinity: CEO 1콜/사이클만
# qwen(스왑 비용 수용). 결정성: 테스트가 use_strategy_caller 로 stub 주입.
_STRATEGY_MODEL = os.environ.get("BENCH_PRIMARY_MODEL", "qwen3.6-35b-a3b")


def _strategy_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """기본 전략 caller: BENCH_PRIMARY_MODEL(qwen) 1콜(라이브). 테스트는 stub 주입."""
    llm = ChatOpenAI(
        base_url=os.environ["BENCH_API_URL"],
        api_key=os.environ["BENCH_API_KEY"],
        model=_STRATEGY_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=90.0,
    )
    return (llm.invoke(prompt).content or "").strip()


# ★provider 라우팅(Owner): CEO 전략 기본 경로를 Azure-primary/qwen-fallback 으로 래핑.
# call-time 에 ORCH_LLM_PRIMARY 읽음(off→qwen 직행). use_strategy_caller(stub) 는 전체 override.
_ACTIVE_STRATEGY: contextvars.ContextVar = contextvars.ContextVar(
    "org_strategy_caller",
    default=make_primary_fallback_caller(_strategy_caller, label="org-ceo"),
)


@contextlib.contextmanager
def use_strategy_caller(caller: Callable[[str, float, int], str]) -> Iterator[None]:
    """CEO 전략(qwen) caller 바인딩(테스트 stub 주입). use_caller 와 동형."""
    token = _ACTIVE_STRATEGY.set(caller)
    try:
        yield
    finally:
        _ACTIVE_STRATEGY.reset(token)


def _strategy_call(prompt: str, temperature: float = 0.0, max_tokens: int = 48) -> str:
    return (_ACTIVE_STRATEGY.get()(prompt, temperature, max_tokens) or "").strip()


class OrgState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    directive: str
    route: str  # ceo: coo | dev_lead | escalate | end
    route_reason: str
    departments: Annotated[list[str], operator.add]  # 다부서(fan-out) — list reducer
    artifacts: Annotated[dict[str, str], operator.or_]  # 병렬 부서 머지 reducer
    # §27.5 Blackboard: dept-keyed shared_facts. 각 부서는 자기 dept 키만 채움 → 병렬 Send
    # fan-out 에서 키 충돌 0(operator.or_ last-writer-wins 손실 회피·동시쓰기 안전).
    shared_facts: Annotated[dict[str, Any], operator.or_]
    routing_trace: Annotated[list[str], operator.add]
    decision: str  # escalate resolve
    confidence: float  # §20: ceo_route 가 산출(authority 입력·기존 필드 재사용)
    authority_tier: str  # §20.1 분류 결과(autonomous|notify|escalate) — 관찰/digest
    authority_inputs: dict[str, Any]  # §20.5 분류 입력 기록
    # §19 숙의(P3b-5·cycle 미러). deliberate_org 가 set·ceo_route 2회차 재진입 가드.
    deliberated: bool  # §19.2⑥ one-shot(재진입 needs_deliberation False)
    deliberation_path: bool  # §19 숙의 경유 표식(BLUF/digest·evidence)
    adr_payload: dict[
        str, Any
    ]  # §19.2⑤ 숙의 산출 ADR(재진입 전 기록·escalate sink 대비)
    # ★US-9d-1 CRITICAL-fix: intake/research 부서가 방출하는 인입요청. ★반드시 채널 선언 +
    # reducer — LangGraph 는 declared 채널만 체크포인트 보존(미선언 시 finalize/snapshot 전 드롭→
    # app.py sink 가 항상 None 읽음). research+intake 가 _COO_DEPTS 에서 **동시 fan-out** 가능하므로
    # bare key 금지(concurrent-write 손실/오류)→operator.add 로 append-merge(동시쓰기 안전).
    intake_requests: Annotated[list[dict], operator.add]
    bluf: str


def _pick(text: str, options: tuple[str, ...], default: str) -> str:
    """옵션 키워드 1개 선택(소문자 contains·결정론·defensive default)."""
    low = (text or "").lower()
    for opt in options:
        if opt in low:
            return opt
    return default


def _pick_many(text: str, options: tuple[str, ...]) -> list[str]:
    """응답에 등장한 옵션들(순서 보존). 없으면 [options[0]]. 다부서 fan-out 용."""
    low = (text or "").lower()
    picked = [opt for opt in options if opt in low]
    return picked or [options[0]]


# ── CEO route (qwen 전략모델) ─────────────────────────────────────────────────
def ceo_route(state: OrgState) -> Command:
    """CEO(qwen·전략모델)가 지시를 조직 분기로 라우팅. Command(goto).

    ★P3b-5 §19 숙의 미러(cycle authority_gate 재진입과 동형):
      ① 1회차(deliberated 미set): qwen 1콜 라우팅 → decide() 직후 needs_deliberation 이면
         deliberate_org 로 우회(qwen 콜은 이미 1회 소비). 그 외엔 기존 route 확정.
      ② 2회차(deliberated=True·재진입): **qwen 재호출 0**(gemma-affinity). deliberate_org 가
         갱신한 confidence + adr_payload.recommend_escalate 로 재라우팅(escalate 발화 포함).
    """
    directive = state.get("directive", "")
    # ── ② 2회차(재진입) 분기 — qwen 재호출 0(§19.2.6·gemma-affinity) ──
    if state.get("deliberated"):
        decide_state = dict(state)  # confidence 는 deliberate_org 가 갱신해둠
        tier, inputs, _r = decide(directive, decide_state)
        adr = state.get("adr_payload") or {}
        if adr.get("recommend_escalate") or tier is Tier.ESCALATE:
            route, atier = "escalate", "escalate"
        else:
            # 1회차 qwen content(route_reason)로 재라우팅 — 신규 qwen 콜 없음.
            route = _pick(
                state.get("route_reason", ""), ("dev_lead", "coo", "end"), "coo"
            )
            atier = tier.value
        return Command(
            goto=END if route == "end" else route,
            update={
                "route": route,
                "authority_tier": atier,
                "authority_inputs": inputs,
                "routing_trace": ["ceo_route(post-deliberation)"],
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"[ceo] post-deliberation -> {route}",
                    }
                ],
            },
        )
    # ── ① 1회차: 기존 qwen 라우팅(무수정) ──
    prompt = (
        _persona_prompt("ceo")
        + "\n\n[라우팅 작업] 아래 Owner 지시를 한 줄로 분류하고 확신을 매겨라. "
        "기술/코드/빌드/QA = dev_lead. 연구/기획/콘텐츠/지식/인입 = coo. "
        "비가역·금전·대외공개·법적 = escalate. 회사 일이 아니면 = end.\n"
        "정확히 'ROUTE: <coo|dev_lead|escalate|end> | conf 0.NN | <10단어 이내 이유>' 형식으로만 답하라.\n"
        f"Owner 지시: {directive}"
    )
    content = _strategy_call(prompt, 0.0, 64)  # ★CEO=qwen
    conf = _parse_confidence(content)
    # ★§20 격상: escalate 발화조건을 키워드(deterministic·authority.decide)로 — 비가역/금전/대외공개/
    # owner-reserved 면 ESCALATE(qwen 판단에만 의존하지 않음). 그 외는 qwen 의 coo|dev_lead|end 라우팅.
    decide_state = dict(state)
    if conf is not None:
        decide_state["confidence"] = conf  # OrgState.confidence 재사용(MINOR#3)
    tier, inputs, _reason = decide(directive, decide_state)
    # ★P3b §19: 숙의 우회(one-shot). needs_deliberation 내부에 not deliberated 가드 有 →
    # 1회차(가역+저확신+고위험신호)만 트리거. deliberate_org → ceo_route 재진입(2회차 분기).
    # ★기존 ESCALATE/route 확정은 이 분기 미통과 시 그대로(0수정).
    if needs_deliberation(directive, decide_state, inputs):
        return Command(
            goto="deliberate_org",
            update={
                "routing_trace": ["ceo_route->deliberation"],
                "messages": [
                    {"role": "assistant", "content": "[ceo] -> deliberation(§19)"}
                ],
            },
        )
    if tier is Tier.ESCALATE:
        route = "escalate"
    else:
        route = _pick(content, ("dev_lead", "coo", "end"), "coo")
    update: dict[str, Any] = {
        "route": route,
        "route_reason": content,
        "authority_tier": tier.value,
        "authority_inputs": inputs,
        "routing_trace": ["ceo_route"],
        "messages": [{"role": "assistant", "content": f"[ceo] {content}"}],
    }
    if conf is not None:
        update["confidence"] = conf
    return Command(goto=END if route == "end" else route, update=update)


def coo(state: OrgState) -> Command:
    """COO 가 비기술 부서 1+개로 병렬 디스패치(map-reduce Send)."""
    directive = state.get("directive", "")
    prompt = (
        _persona_prompt("coo")
        + "\n\n[디스패치] 이 지시에 필요한 부서를 1개 이상 고르라(쉼표 구분): "
        "research(조사) · art(에셋) · librarian(지식정리) · intake(외부인입).\n"
        "정확히 'pick: <부서 목록>' 로만 답하라.\n"
        f"지시: {directive}"
    )
    content = _call(prompt, 0.0, 32)
    depts = _pick_many(content, _COO_DEPTS)
    return Command(
        goto=[Send(dept, {"directive": directive}) for dept in depts],
        update={
            "routing_trace": ["coo"],
            "messages": [{"role": "assistant", "content": f"[coo] -> {depts}"}],
        },
    )


def dev_lead(state: OrgState) -> Command:
    """Dev Lead 가 기술 부서(dev·qa) 1+개로 병렬 디스패치."""
    directive = state.get("directive", "")
    prompt = (
        _persona_prompt("dev_lead")
        + "\n\n[디스패치] 이 지시에 필요한 기술 부서를 1개 이상 고르라(쉼표 구분): "
        "dev(구현 스펙) · qa(검증).\n"
        "정확히 'pick: <부서 목록>' 로만 답하라.\n"
        f"지시: {directive}"
    )
    content = _call(prompt, 0.0, 32)
    depts = _pick_many(content, _DEVLEAD_DEPTS)
    return Command(
        goto=[Send(dept, {"directive": directive}) for dept in depts],
        update={
            "routing_trace": ["dev_lead"],
            "messages": [{"role": "assistant", "content": f"[dev_lead] -> {depts}"}],
        },
    )


def _make_department_node(dept_id: str) -> Callable[[OrgState], dict[str, Any]]:
    """부서 노드 팩토리: 페르소나 프롬프트로 1 산출물. ★partial 반환(reducer 머지·동시쓰기 안전)."""

    def _node(state: OrgState) -> dict[str, Any]:
        directive = state.get("directive", "")
        prompt = (
            _persona_prompt(dept_id)
            + "\n\n[작업] 위 역할로서 아래 Owner 지시에 대한 산출물을 4줄 이내로 작성하라.\n"
            f"Owner 지시: {directive}"
        )
        content = _call(prompt, 0.2, 256)
        result: dict[str, Any] = {
            "artifacts": {dept_id: content},  # ★자기 항목만 — operator.or_ 가 머지
            # §27.5 Blackboard: 자기 dept 키만(구조화 dict·자유형식 prose 금지·telephone game 차단).
            "shared_facts": {dept_id: {"by": dept_id, "finding": content}},
            "departments": [dept_id],  # operator.add 누적
            "routing_trace": [dept_id],
            "messages": [
                {"role": "assistant", "content": f"[{dept_id}] {content[:60]}"}
            ],
        }
        # ★US-9d-1 add-only: INTAKE/RESEARCH 부서만 intake 요청을 방출.
        # 노드는 판단(산출물 반환)만 — enqueue(push_task) 는 app.py _run sink 에서만 수행.
        # ★★in-process 워커/agent.run 호출 절대 금지(경계·§15 노드 판단≠워커 실행).
        # ★CRITICAL-fix: list(``intake_requests``)로 방출 — operator.add reducer 가 동시 fan-out
        # (intake+research)을 append-merge(드롭/충돌 0). 단일 dept 도 1-원소 리스트.
        if dept_id in {"intake", "research"}:
            result["intake_requests"] = [
                {
                    "queue": "intake",
                    "dept": dept_id,
                    "directive": directive,
                    "payload": content,
                }
            ]
        return result

    _node.__name__ = f"dept_{dept_id}"
    return _node


# ── §19 숙의 래퍼 노드 (cycle graph.deliberate 미러·MAJOR-1) ─────────────────────
def deliberate_org(state: OrgState) -> Command:
    """§19 숙의 1회 → ceo_route 재진입(org·cycle deliberate 미러).

    adr_payload·갱신 conf·deliberated 를 재진입 전 기록(escalate sequencing·app.py
    if-interrupt ADR sink 가 읽음). 2회차 ceo_route 가 갱신 conf+recommend_escalate 로 재라우팅.
    ★org 은 OrgState.confidence 키(authority.assess 가 decision_confidence 없으면 confidence
    읽음). deliberation.py 가 confidence/decision_confidence 둘 다 반환하므로 confidence 사용.
    """
    from orch.deliberation import run_deliberation  # ★lazy(순환 import 회피)

    res = run_deliberation(state.get("directive", ""))
    return Command(
        goto="ceo_route",  # ★재진입(2-in-edge: START + deliberate_org)·one-shot 가드로 1회
        update={
            "confidence": float(res.get("confidence", state.get("confidence", 0.55))),
            "deliberated": True,
            "deliberation_path": True,
            "adr_payload": res.get("adr_payload", {}),
            "routing_trace": ["deliberate_org"],
            "messages": [
                {
                    "role": "assistant",
                    "content": "[deliberate] §19 synthesize -> re-eval",
                }
            ],
        },
    )


# ── ESCALATE ──────────────────────────────────────────────────────────────────
def escalate(state: OrgState) -> Command:
    """고위험·비가역 결정 → Owner interrupt → 재개 후 finalize.

    ★스펙 §6.6/§24.2 escalate 스켈레톤은 ``Command(update=...)`` 로 **goto 가 없다**. 그러나
    goto 없이 구현하면 interrupt resume 후 그래프가 finalize 에 도달하지 못하고 **무음 종료**한다
    (BLUF 누락). graph.py:hitl_gate 패턴대로 **반드시 ``goto="finalize"``** 를 둔다
    (architect#2 · critic Major). 스펙 스켈레톤을 그대로 베끼지 말 것.
    """
    directive = state.get("directive", "")
    # §20.4a 브리프 + §24.2 rich 페이로드(계산 confidence 가 하드코딩 0.5 대체). inputs 는 ceo_route
    # 가 적재(authority_inputs)·없으면 결정론 재도출(decide·LLM 0).
    inputs = state.get("authority_inputs") or decide(directive, dict(state))[1]
    decision = interrupt(escalate_payload(directive, dict(state), inputs))
    return Command(
        goto="finalize",
        update={
            "decision": str(decision),
            "routing_trace": ["escalate"],
            "messages": [
                {"role": "assistant", "content": f"[escalate] owner={decision}"}
            ],
        },
    )


def finalize(state: OrgState) -> dict[str, Any]:
    """조직 산출물(다부서)을 BLUF 보고로 합성(결론·필요행동 먼저). gemma."""
    artifacts = state.get("artifacts") or {}
    trace = state.get("routing_trace") or []
    depts = state.get("departments") or []  # ★list(다부서)
    decision = state.get("decision", "")
    body = "\n".join(f"- {k}: {v}" for k, v in artifacts.items()) or "- (산출물 없음)"
    prompt = (
        "다음 조직 산출물(여러 부서)을 Owner용 BLUF 보고로 합성하라. 형식:\n"
        "결론: <한 줄>\n필요행동: <한 줄>\n근거:\n<부서별 산출물 요약>\n"
        f"라우팅: {' -> '.join(trace)}\n"
        f"부서: {', '.join(depts) or '해당없음'}  승인: {decision or '해당없음'}\n"
        f"산출물:\n{body}"
    )
    content = _call(prompt, 0.2, 320)
    # ★발행 텍스트 윤문(orch.humanize): Owner용 BLUF 보고를 윤문 패스로 통과시켜 AI 티를
    # 제거한다(한국어일 때만·의미 불변·과윤문 시 원문 롤백·모델 실패 비차단). graph.stage_content
    # 와 동형 — finalize BLUF 는 Discord 등 외부로 발행되는 두 번째 발행 지점이다.
    from orch.humanize import humanize_korean  # noqa: PLC0415 (lazy·순환 회피)

    res = humanize_korean(content, caller=_active_caller())
    msg = "[finalize] BLUF ready"
    if res["applied"]:
        content = res["text"]
        msg = f"[finalize] BLUF ready · humanized Δ{res['change_rate']:.0%}"
    return {
        "bluf": content,
        # §27.5 Blackboard: 예약키에 합성 BLUF 기여(부서 키와 충돌 0).
        "shared_facts": {"_finalize": {"by": "finalize", "bluf": content}},
        "routing_trace": ["finalize"],
        "messages": [{"role": "assistant", "content": msg}],
    }


# ── §27.5 Blackboard sink (org state → aigs:blackboard + vault 스냅샷) ──────────
async def blackboard_sink(
    state_values: dict[str, Any],
    store: Any,
    *,
    proj: str,
    vault_dir: str | Path | None = None,
    today: str | None = None,
) -> Path:
    """org 사이클의 dept-keyed ``shared_facts`` 를 §27.5 Blackboard 로 sink 한다.

    (1) Redis(``store`` 있으면=REDIS_ENABLED): dept 별 ``bb_set(proj, dept, fact)`` — hset 필드
        원자 쓰기(동시성 안전·``aigs:blackboard:<proj>`` 키). ``store=None``(미설정) 이면 skip(graceful).
    (2) vault 스냅샷: ``vault/meetings/blackboard-<date>.md`` 에 항상 append(휴먼 미러·store 무관).
    ★프로덕션 호출부 = app.py ``_run`` ``elif kind=='org'``(post-invoke·add-only Tier-2). 핫패스 LLM 0.
    반환 = 작성한 스냅샷 경로.
    """
    facts = (state_values or {}).get("shared_facts") or {}
    # (1) Redis blackboard (REDIS_ENABLED 게이트: store is not None)
    if store is not None:
        for dept, fact in facts.items():
            # sink 실패(일시적 Redis 오류 등)가 라이브 _run/BLUF 를 막지 않게 흡수.
            with contextlib.suppress(Exception):
                await store.bb_set(proj, dept, fact)
    # (2) vault 스냅샷 (항상·파일쓰기)
    vdir = Path(vault_dir) if vault_dir is not None else (_REPO_ROOT / "vault")
    day = today or _date.today().isoformat()
    meetings = vdir / "meetings"
    meetings.mkdir(parents=True, exist_ok=True)
    path = meetings / f"blackboard-{day}.md"
    section = [f"\n## proj={proj}", ""]
    for dept, fact in facts.items():
        # 구조화 dict 는 json 으로 보존(휴먼 미러)·그 외는 문자열.
        rendered = (
            json.dumps(fact, ensure_ascii=False, indent=2)
            if isinstance(fact, dict)
            else str(fact)
        )
        section.append(f"### {dept}")
        section.append(rendered)
        section.append("")
    body = "\n".join(section) + "\n"
    prior = (
        path.read_text(encoding="utf-8")
        if path.is_file()
        else f"# Blackboard 회의록 — {day}\n"
    )
    path.write_text(prior + body, encoding="utf-8")
    return path


def build_org_graph(checkpointer):
    """9-노드 조직그래프 컴파일(미승격·검증용). 다부서 Send fan-out + 부서->finalize 조인.

    Edges: START->ceo_route ; ceo_route -Command(goto)-> coo|dev_lead|escalate|END ;
    coo/dev_lead -Command(goto=[Send,...])-> 1+ 부서(병렬) ; 각 부서 -add_edge-> finalize(조인) ;
    escalate -Command(goto)-> finalize ; finalize->END.
    """
    g = StateGraph(OrgState)
    # ★§9.4: add_node 인자만 _with_status 로 래핑(노드 본문 byte 보존·전이/출력 불변).
    # 부서 노드는 dept_id→state(dept_state)·라우터/게이트는 결정론 매핑. deliberate_org 는
    # 진입 발행 무관(cycle deliberate 와 동형으로 wrapping 불요)이라 byte 그대로.
    # ★MINOR-2 deslop: 라우터/게이트 (persona,state) 는 _NODE_STATUS(node_status) 단일 SSoT·
    # 부서는 dept_state(dept_id→state). 값 동일(behavior 불변)·add_node 인자만 래핑.
    g.add_node("ceo_route", _with_status(ceo_route, *node_status("ceo_route")))
    g.add_node("coo", _with_status(coo, *node_status("coo")))
    g.add_node("dev_lead", _with_status(dev_lead, *node_status("dev_lead")))
    for dept in _DEPARTMENTS:
        g.add_node(
            dept, _with_status(_make_department_node(dept), dept, dept_state(dept))
        )
    g.add_node("deliberate_org", deliberate_org)  # §19 숙의 래퍼(P3b-5)
    g.add_node("escalate", _with_status(escalate, *node_status("escalate")))
    g.add_node("finalize", _with_status(finalize, *node_status("finalize")))

    g.add_edge(START, "ceo_route")
    # ceo_route -Command(goto)-> deliberate_org | coo|dev_lead|escalate|END(숙의 우회 포함).
    # deliberate_org -> ceo_route 재진입(★2-in-edge: START + deliberate_org → ceo_route·
    # one-shot 가드[deliberated]로 무한루프 0). 기존 노드/엣지·escalate goto=finalize 0수정.
    g.add_edge("deliberate_org", "ceo_route")
    for dept in _DEPARTMENTS:
        g.add_edge(dept, "finalize")  # 다부서 병렬 -> finalize 1회 조인
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
