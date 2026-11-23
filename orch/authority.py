"""orch/authority.py — SR/renewal §20 자율·의사결정 권한 (결정론 분류기·LLM 0).

§20.1 3계층(Autonomous|Notify|Escalate) · §20.2 근거의무(RPI) · §20.5 가역성 τ 진리표 · §20.4a BLUF.

- ``classify(reversibility, reversal_cost, confidence, scope)`` = **순수함수**(LLM 0·진리표 테스트).
  §20.5 규칙: one-way OR 되돌림비용=높음 OR Owner-reserved → Escalate(셋 다 *co-equal* 트리거).
- ``assess(directive, state)`` = directive/state → (reversibility, reversal_cost, confidence, scope)
  derivation(키워드 결정론 + confidence emit/proxy). ``decide()`` = assess→classify→RPI/보수 demote.
- ``render_escalate_brief`` / ``escalate_payload`` = §20.4a 🔴 브리프 + §24.2 rich interrupt 페이로드
  (계산 confidence 가 기존 하드코딩 0.55/0.5 를 대체).

★τ seed {0.75/0.60/0.80} 은 §20.5 "보정 전제·절대값 아님" — **OBSERVE-ONLY**(캘리브레이션은 출시후·
override-rate 순환의존). 본 모듈의 진리표 테스트는 *규칙 메커닉(배관)* 증명이지 라이브 gradient 증명이 아니다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

# ── §20.5 τ seed (보정 전제·OBSERVE-ONLY·절대값 아님) ──────────────────────────
TAU_AUTO = 0.75  # 가역 & conf ≥ → Autonomous
TAU_NOTIFY = 0.60  # 가역 & conf ≥ → Notify (그 미만도 가역이면 진행 → Notify)

# ── 결정론 키워드(한+영·LLM 0). §20.1 Owner-reserved / one-way / 되돌림비용=높음 ──
_OWNER_RESERVED = (
    "steam",
    "출시",
    "publish",
    "금전",
    "실금전",
    "money",
    "지출",
    "결제",
    "payment",
    "유료",
    "예산",
    "대외공개",
    "발행",
    "법적",
    "legal",
    "브랜드",
    "brand",
    "계약",
    "contract",
    "신규사업",
)
_ONE_WAY = ("삭제", "delete", "drop", "제거", "wipe", "비가역", "irreversible", "영구")
_HIGH_COST = (
    "infra",
    "인프라",
    "migration",
    "마이그",
    "rebrand",
    "리브랜드",
    "bulk",
    "전량",
    "일괄",
    "rename-all",
    "전체이름",
    "대량",
)
# 미키워드 비가역이 Autonomous 로 새지 않게 하는 보수 안전망(§20.5 "애매하면 Notify"·MINOR#2).
_HIGH_IMPACT_VERB = (
    "drop",
    "delete",
    "삭제",
    "제거",
    "wipe",
    "email all",
    "전체메일",
    "대량발송",
    "broadcast",
)

_NO_SOURCE = "(no knowledge source)"

# ── §19.1 숙의 트리거 고위험 신호(한+영·substring·바운드·SAFE 방향) ─────────────
# 고비용(_HIGH_COST) + 전략/대외/CEO·DevLead 플래그류. 중복 무방(any-match). 저확신
# *단독*으론 부족 — 이 신호 동반이 필수(§19.1 "고위험·저확신·CEO/DevLead 플래그").
_DELIBERATE_SIGNAL = _HIGH_COST + (
    "전략",
    "strategy",
    "방향",
    "major",
    "중대",
    "대외",
    "외부공개",
    "ceo",
    "dev lead",
    "devlead",
    "리스크 큰",
    "high-stakes",
    "high stakes",
    "장기",
)


class Tier(str, Enum):
    """§20.1 의사결정 3계층."""

    AUTONOMOUS = "autonomous"  # 가역+근거+conf≥τ_auto → 즉시 실행, 차단 없음
    NOTIFY = "notify"  # 가역+중간 → 실행 후 일일 다이제스트
    ESCALATE = "escalate"  # 비가역/금전/대외공개/owner-reserved → interrupt 대기


def classify(
    reversibility: str,
    reversal_cost: str,
    confidence: float,
    scope: str,
    *,
    tau_auto: float = TAU_AUTO,
    tau_notify: float = TAU_NOTIFY,
) -> Tier:
    """§20.5 진리표(순수함수·LLM 0). 입력 도메인:

    reversibility ∈ {"two_way","one_way"} · reversal_cost ∈ {"low","mid","high"} ·
    scope ∈ {"in_scope","owner_reserved"} · confidence ∈ [0,1].

    규칙(§20.5:773 "one-way OR 되돌림비용=높음 OR Owner-reserved → Escalate" = co-equal):
      owner_reserved → ESCALATE ; one_way → ESCALATE ; reversal_cost==high → ESCALATE ;
      (two_way & low/mid): conf ≥ τ_auto → AUTONOMOUS ; conf ≥ τ_notify → NOTIFY ;
      conf < τ_notify → NOTIFY(가역이므로 진행·숙의는 P3b). tie-break = ``>=``.
    """
    if scope == "owner_reserved":
        return Tier.ESCALATE
    if reversibility == "one_way":
        return Tier.ESCALATE
    if reversal_cost == "high":  # ★co-equal 트리거(되돌림비용 큰 가역도 Escalate)
        return Tier.ESCALATE
    if confidence >= tau_auto:
        return Tier.AUTONOMOUS
    if confidence >= tau_notify:
        return Tier.NOTIFY
    return Tier.NOTIFY  # <τ_notify 가역 → Notify(가역이라 진행·P3b 숙의 트리거는 별도)


def _has_citation(state: dict[str, Any]) -> bool:
    """§20.2 RPI: 근거(citation/provenance) 존재 여부. cycle=knowledge_source·org=research artifact."""
    src = state.get("knowledge_source")
    if src and src != _NO_SOURCE:
        return True
    # org best-effort: research 부서 산출물/shared_facts 존재(타입드 citation 부재·OD-P3-3).
    arts = state.get("artifacts") or {}
    if isinstance(arts, dict) and arts.get("research"):
        return True
    facts = state.get("shared_facts") or {}
    return bool(isinstance(facts, dict) and facts.get("research"))


def assess(directive: str, state: dict[str, Any]) -> tuple[str, str, float, str]:
    """directive/state → (reversibility, reversal_cost, confidence, scope) 결정론 derivation.

    confidence = state["decision_confidence"](ceo_route emit) 우선 · 없으면 proxy
    (citation 있으면 0.75·없으면 0.55). proxy 는 2값이라 τ gradient 미도달(OBSERVE-ONLY·정직).
    """
    low = (directive or "").lower()
    # ★키워드 = 부분일치(substring). "brand-new"⊃"brand" 같은 false-positive 과(過)escalate 가능하나,
    # §20.5 "애매하면 Notify(보수)로 기운다" 의 SAFE 방향(차단 과다 > 위험 통과)이라 accepted limitation.
    # 승인 피로가 관측되면 word-boundary 매칭으로 전환(출시후 튜닝·완료게이트 아님).
    scope = "owner_reserved" if any(k in low for k in _OWNER_RESERVED) else "in_scope"
    reversibility = "one_way" if any(k in low for k in _ONE_WAY) else "two_way"
    reversal_cost = "high" if any(k in low for k in _HIGH_COST) else "low"

    # cycle=decision_confidence · org=confidence(기존 OrgState 필드 재사용·MINOR#3) · 둘 다 없으면 proxy.
    conf = state.get("decision_confidence")
    if not isinstance(conf, (int, float)):
        conf = state.get("confidence")
    if not isinstance(conf, (int, float)):
        conf = 0.75 if _has_citation(state) else 0.55  # proxy(폴백)
    return reversibility, reversal_cost, float(conf), scope


def decide(directive: str, state: dict[str, Any]) -> tuple[Tier, dict[str, Any], str]:
    """assess → classify → §20.2 RPI demote → 보수 demote(MINOR#2). 반환 (tier, inputs, reason)."""
    reversibility, reversal_cost, confidence, scope = assess(directive, state)
    tier = classify(reversibility, reversal_cost, confidence, scope)
    reason_bits = [
        f"rev={reversibility}",
        f"cost={reversal_cost}",
        f"conf={confidence:.2f}",
        f"scope={scope}",
    ]

    # §20.2 근거의무: 자율은 근거(citation)가 전제 — 없으면 강등(Notify).
    if tier is Tier.AUTONOMOUS and not _has_citation(state):
        tier = Tier.NOTIFY
        reason_bits.append("RPI-demote(no-citation)")
    # 보수 안전망(MINOR#2·§20.5 "애매하면 Notify"): 미escalate 고위험동사 → 자율 금지.
    low = (directive or "").lower()
    if tier is Tier.AUTONOMOUS and any(v in low for v in _HIGH_IMPACT_VERB):
        tier = Tier.NOTIFY
        reason_bits.append("conservative-demote(high-impact-verb)")

    inputs = {
        "reversibility": reversibility,
        "reversal_cost": reversal_cost,
        "confidence": confidence,
        "scope": scope,
        "tier": tier.value,
    }
    return tier, inputs, " ".join(reason_bits)


# ── §19.1 숙의 트리거 술어(별도 술어·classify 불변·OD-P3b-1 Opt 1B) ──────────────
def needs_deliberation(
    directive: str, state: dict[str, Any], inputs: dict[str, Any] | None = None
) -> bool:
    """§19.1 구조화 숙의(§19.2) 진입 여부 — 결정론·신규 LLM 0(assess 재사용).

    트리거(§19.1 "고위험·저확신·CEO/Dev Lead 플래그") = **보수적 AND**:
      (가역 two_way) AND (conf < τ_deliberate=TAU_NOTIFY) AND (고위험 신호 동반)
      AND (not state["deliberated"]).

    - ★저확신 *단독*으론 부족(고위험 신호 `_DELIBERATE_SIGNAL` 부분일치 필수) —
      일상 가역 작업이 숙의로 새는 핫패스 오염 차단(canonical `assess("make a clicker
      game")`=가역·신호없음 → False).
    - ★비가역/owner_reserved/high_cost 는 **Escalate(§20)가 처리** → 여기선 *가역만*.
      owner_reserved scope 면 False(Escalate 경로). 즉 숙의는 "가역인데 저확신+고위험"
      이라 *행동해도 가역적으로 안전하나 신중이 필요한* 결정에 한정.
    - ★**one-shot(HIGH-2·§19.2⑥):** `not state.get("deliberated")` — 게이트 방문당
      정확히 1회. synthesize 가 `deliberated=True` 를 set → conf 불변이어도 재진입 0
      (없으면 needs_deliberation 영속 True → 서브그래프 재-디스패치 무한루프).
    - τ_deliberate/트리거-rate 는 **OBSERVE-ONLY**(spec:780 "<0.60 가역→숙의"·캘리브레이션
      출시후). 본 술어는 *규칙 메커닉* 이지 라이브 gradient 보정이 아니다.
    """
    if state.get("deliberated"):  # HIGH-2 one-shot
        return False
    inputs = inputs or {}
    rev = inputs.get("reversibility")
    scope = inputs.get("scope")
    conf = inputs.get("confidence")
    if rev is None or scope is None or not isinstance(conf, (int, float)):
        a_rev, _cost, a_conf, a_scope = assess(directive, state)
        rev = rev if rev is not None else a_rev
        scope = scope if scope is not None else a_scope
        conf = conf if isinstance(conf, (int, float)) else a_conf
    if scope == "owner_reserved" or rev != "two_way":
        return False  # Escalate(§20) 경로 — 숙의 아님
    if float(conf) >= TAU_NOTIFY:
        return False
    low = (directive or "").lower()
    return any(sig in low for sig in _DELIBERATE_SIGNAL)


# ── §20.4a BLUF Escalate-brief + §24.2 rich interrupt 페이로드 ──────────────────
def render_escalate_brief(
    decision_name: str,
    bluf: str,
    options: list[str],
    rationale: str,
    risks: list[str],
    confidence: float,
    reversibility: str,
    deadline: str = "TBD",
    link: str = "",
) -> str:
    """§20.4a 🔴 Escalate 브리프(한 화면·결론/필요행동 먼저). 인지적 구두쇠 대응."""
    opt_labels = ["A(권고)", "B", "C"]
    opt_line = (
        " / ".join(f"{opt_labels[i]} {opt}" for i, opt in enumerate(options[:3]))
        or "A(권고) 진행 / B 보류"
    )
    risk_line = " / ".join(risks[:2]) or "(주요 리스크 없음)"
    link_sfx = f" [{link}]" if link else ""
    return (
        f"🔴 [{decision_name}]                         기한: ~{deadline}\n"
        f"BLUF: {bluf} 확신 {confidence:.2f}. (가역성 {reversibility})\n"
        f"옵션:  {opt_line}\n"
        f"근거:  {rationale}{link_sfx}\n"
        f"리스크: {risk_line}"
    )


def escalate_payload(
    directive: str, state: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    """Escalate interrupt 페이로드(§24.2 richness). 계산 confidence 가 하드코딩 0.55/0.5 대체."""
    artifacts = state.get("artifacts") or {}
    conf = float(inputs.get("confidence", 0.5))
    rev = str(inputs.get("reversibility", "two_way"))
    name = (directive or "결정")[:48]
    brief = render_escalate_brief(
        decision_name=name,
        bluf=f"'{name}' 승인 필요.",
        options=["진행(가역성 확인)", "보류", "수정 요청"],
        rationale=str(
            artifacts.get("design") or artifacts.get("research") or directive
        )[:160],
        risks=[
            f"되돌림비용 {inputs.get('reversal_cost', 'low')}",
            f"범위 {inputs.get('scope', 'in_scope')}",
        ],
        confidence=conf,
        reversibility=rev,
        link=str(state.get("knowledge_source") or ""),
    )
    return {
        "question": f"승인하시겠습니까? — {name}",
        "options": ["approve", "reject"],
        "confidence": conf,  # ★계산값(0.55/0.5 리터럴 대체)
        "reversibility": rev,
        "reversal_cost": inputs.get("reversal_cost", "low"),
        "scope": inputs.get("scope", "in_scope"),
        "tier": Tier.ESCALATE.value,
        "brief": brief,
        "design": artifacts.get("design", ""),
        "content": artifacts.get("content", ""),
    }
