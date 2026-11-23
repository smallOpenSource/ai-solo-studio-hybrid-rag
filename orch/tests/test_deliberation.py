"""orch/deliberation.py 결정론 단위 (§19 숙의 서브그래프·LLM 0·not slow·stub 주입).

★종료성 P0 불변식이 최우선(프로젝트 다운 2회 이력): stub 가 *항상 미합의/이의/저confidence*
반환해도 서브그래프가 CAP 내 강제 synthesize 종료(무한루프 0). 합의 무관 종료(카운터 구동·
Tension B). + 독립제안 격리(MED-3·proposals 키 부재) + needs_deliberation 진리표(≥6) +
classify 회귀(byte-identical) + render_adr 합집합 + ADR 번호.

orch env 은 async 를 ``asyncio.run`` 으로 구동(test_state_store 관례·pytest-asyncio 비의존).
"""

from __future__ import annotations

import asyncio

from langgraph.types import Command, Send

from orch.authority import TAU_NOTIFY, Tier, classify, needs_deliberation
from orch.deliberation import (
    _ADR_NUM_RE,
    _CAP,
    _N,
    _dispatch,
    _premortem_dispatch,
    _synthesize,
    adr_is_substantive,
    build_deliberation_graph,
    next_adr_number,
    render_adr,
    use_judge_caller,
)
from orch.graph import use_caller


# ── stub callers (항상 이의/미합의/저confidence — 종료성 falsify 용) ─────────────
def disagree_stub(prompt: str, temperature: float, max_tokens: int) -> str:
    """절대 합의 안 함·항상 이의·저confidence 반환 → 종료가 *합의*에 의존하면 무한루프."""
    if "Synthesizer" in prompt:
        return "DECISION: 옵션 A 진행 | conf 0.40 | MINORITY: 반대 다수 잔존"
    if "Challenger" in prompt:
        return "반론: 근거 부족·리스크 큼(이의)."
    if "Proponent" in prompt:
        return "찬성: 그래도 진행."
    if "Premortem" in prompt:
        return "실패원인: 시장 외면."
    if "독립 제안" in prompt:
        return "제안: 구체안."
    return "stub"


def _invoke(directive: str = "전략 방향 결정", stub=disagree_stub):
    g = build_deliberation_graph()
    with use_caller(stub), use_judge_caller(stub):
        return g.invoke(
            {"directive": directive, "round": 0}, config={"recursion_limit": 30}
        )


# ── 종료성 불변식 (P0·★시스템 다운 3회째 차단) ──────────────────────────────────
def test_terminates_even_when_never_agree():
    """stub 가 항상 이의/미합의/저conf → CAP 내 synthesize 도달·END 종료·예외 0.

    ★Tension B: 종료는 카운터(round=CAP) 구동이지 합의 감지 아님(Challenger 필수 이의라
    합의는 구조적 미달). recursion_limit 도달 *전* 자연 종료.
    """
    out = _invoke()
    assert out["deliberated"] is True
    assert out["round"] == _CAP  # 라운드 카운터가 정확히 CAP 에서 멈춤(오프바이원 0)
    assert out["adr_payload"]  # 강제 합성(빈 폴백 아님)
    assert out["deliberation_path"] is True


def test_synthesize_records_both_confidence_keys():
    """synthesize 가 갱신 conf 를 decision_confidence(cycle)+confidence(org) 둘 다 기록(HIGH-2)."""
    out = _invoke()
    assert out["confidence"] == 0.40
    assert out["decision_confidence"] == 0.40  # 부모 cycle assess 입력 키


def test_no_consensus_shortcut_branch():
    """★Tension B 불변식: 합의 시 조기종료 분기 없음(종료는 항상 round==CAP)."""

    # 합의하는 stub 라도 round 는 여전히 CAP(조기종료 안 함·카운터 구동).
    def agree_stub(prompt, t, m):
        if "Synthesizer" in prompt:
            return "DECISION: 만장일치 A | conf 0.95 | MINORITY: 없음"
        if "Challenger" in prompt:
            return "반론: (형식상)."
        return disagree_stub(prompt, t, m)

    out = _invoke(stub=agree_stub)
    assert out["round"] == _CAP  # 고confidence 합의여도 CAP 까지 — 조기종료 분기 부재


# ── 독립 제안 격리 (MED-3·falsifiable) ───────────────────────────────────────────
def test_propose_payloads_have_no_proposals_key():
    """dispatch Send payload 키 집합에 'proposals' 부재(herding/동조 차단)·{directive,role_prompt,idx}만."""
    cmd = _dispatch({"directive": "d"})
    assert isinstance(cmd, Command)
    sends = cmd.goto
    assert all(isinstance(s, Send) and s.node == "propose" for s in sends)
    for s in sends:
        keys = set(s.arg.keys())
        assert "proposals" not in keys  # ★상호 비가시(proposals-so-far 금지)
        assert keys == {"directive", "role_prompt", "idx"}


def test_dispatch_fanout_bounded_to_n():
    """propose fan-out len == N(기본 3·단일 wave)."""
    assert len(_dispatch({"directive": "d"}).goto) == _N


def test_premortem_dispatch_fanout_bounded_and_isolated():
    """premortem fan-out len == N·payload 상호 비가시(타 premortem/proposals 없음)."""
    cmd = _premortem_dispatch({"directive": "d"})
    sends = cmd.goto
    assert len(sends) == _N
    for s in sends:
        keys = set(s.arg.keys())
        assert "premortem_risks" not in keys and "proposals" not in keys
        assert keys == {"directive", "angle", "idx"}


def test_fanout_single_wave_even_when_n_raised():
    """N 상향(소스 상수)해도 단일 wave(재귀 0)·종료성 불변 — 실 invoke 가 종료함으로 입증."""
    out = _invoke()  # 기본 N 으로도 propose/premortem 각 N 개 누적·종료
    assert len(out["proposals"]) == _N
    assert len(out["premortem_risks"]) == _N


# ── needs_deliberation 진리표 (≥6) ──────────────────────────────────────────────
def test_needs_deliberation_truth_table():
    # (1) 가역 + conf<0.60 + 고위험신호("전략") → True
    assert needs_deliberation("전략 방향 재설정", {"decision_confidence": 0.50}) is True
    # (2) 가역 + conf<0.60 + 신호 없음 → False (저확신 단독으론 부족)
    assert (
        needs_deliberation("평범한 카드 디자인", {"decision_confidence": 0.50}) is False
    )
    # (3) 가역 + conf≥0.60 → False
    assert needs_deliberation("전략 방향", {"decision_confidence": 0.60}) is False
    # (4) owner_reserved(키워드 '출시') → False (Escalate 경로)
    assert needs_deliberation("스팀 출시 전략", {"decision_confidence": 0.40}) is False
    # (5) 비가역(키워드 '삭제') → False (Escalate 경로)
    assert (
        needs_deliberation("전략 데이터 삭제", {"decision_confidence": 0.40}) is False
    )
    # (6) canonical 일상 가역(clicker·assess 기반·inputs None) → False
    assert needs_deliberation("make a clicker game", {}, None) is False
    # (7) one-shot(HIGH-2): deliberated=True → False(게이트 방문당 1회)
    assert (
        needs_deliberation(
            "전략 방향", {"decision_confidence": 0.50, "deliberated": True}
        )
        is False
    )


def test_needs_deliberation_uses_inputs_when_given():
    """inputs 의 reversibility/scope/confidence 가 주어지면 assess 재호출 안 함(재사용·LLM 0)."""
    inp = {"reversibility": "two_way", "scope": "in_scope", "confidence": 0.5}
    assert needs_deliberation("전략 방향", {}, inp) is True
    inp_hi = {"reversibility": "two_way", "scope": "in_scope", "confidence": 0.8}
    assert needs_deliberation("전략 방향", {}, inp_hi) is False


# ── classify 진리표 회귀 (1B: byte-identical·classify 수정 0 증명) ────────────────
def test_classify_truth_table_unchanged():
    """P3 8케이스 동일 단언 — needs_deliberation 추가가 classify 를 안 건드렸음을 증명."""
    assert classify("two_way", "low", 0.80, "in_scope") is Tier.AUTONOMOUS
    assert classify("two_way", "low", 0.70, "in_scope") is Tier.NOTIFY
    assert classify("two_way", "low", 0.50, "in_scope") is Tier.NOTIFY  # 가역→진행
    assert classify("one_way", "low", 0.95, "in_scope") is Tier.ESCALATE
    assert classify("one_way", "low", 0.50, "in_scope") is Tier.ESCALATE
    assert classify("two_way", "high", 0.90, "in_scope") is Tier.ESCALATE  # co-equal
    assert classify("two_way", "low", 0.99, "owner_reserved") is Tier.ESCALATE
    assert classify("two_way", "low", TAU_NOTIFY, "in_scope") is Tier.NOTIFY  # tie >=


# ── render_adr 라운드트립 (§27.2 + §19.2⑤ 합집합) ───────────────────────────────
def _adr_payload():
    return {
        "title": "첫 게임 장르",
        "context": "장르 선택 결정",
        "options": ["클리커 — 최소 구현", "퍼즐 — 리텐션 유리"],
        "decision": "퍼즐 선택",
        "pro": ["퍼즐이 리텐션 유리"],
        "con": ["콘텐츠 제작 부담(이의)"],
        "premortem_risks": ["콘텐츠 고갈로 6개월 뒤 이탈"],
        "minority": "클리커가 더 빠른 출시(disagree & commit)",
        "confidence": 0.72,
        "reversibility": "two_way",
        "deciders": "CEO, RESEARCH",
        "date": "2026-06-22",
    }


def test_render_adr_union_sections():
    adr = render_adr(_adr_payload(), number=13, slug="genre")
    assert adr.startswith("# ADR-0013: 첫 게임 장르")  # zero-pad 4
    for section in (
        "## Considered Options",
        "## Decision",
        "## Consequences",
        "## 찬반 (Proponent / Challenger)",
        "## 소수의견 (disagree & commit)",
        "## Premortem (§19)",
        "Confidence: 0.72",
        "Reversibility: two_way",
    ):
        assert section in adr, section
    # ≥2 옵션 + 두 추가 섹션 non-empty(부정변이 게이트 대상)
    assert "1. 클리커" in adr and "2. 퍼즐" in adr
    assert "클리커가 더 빠른 출시" in adr  # 소수의견 non-empty
    assert "(반론) 콘텐츠 제작 부담(이의)" in adr  # 찬반 non-empty
    assert "콘텐츠 고갈" in adr  # premortem non-empty


def test_render_adr_pads_options_to_two():
    """옵션 1개여도 §27.2 Considered Options ≥2 보강(빈 옵션 게이트 대상이나 렌더는 방어)."""
    p = _adr_payload()
    p["options"] = ["단일 옵션"]
    adr = render_adr(p, number=1, slug="x")
    assert "1. 단일 옵션" in adr and "2. " in adr


def test_render_adr_date_from_payload_not_argless():
    """date 는 payload 에서 — argless datetime.now() 금지(결정론)."""
    adr = render_adr(_adr_payload(), number=1, slug="x")
    assert "- Date: 2026-06-22" in adr


# ── ADR 번호 부여 (incr 우선·glob 폴백·anchored regex) ──────────────────────────
class _MonotoneStore:
    def __init__(self) -> None:
        self.n = 0

    async def incr(self, key: str) -> int:
        self.n += 1
        return self.n


def test_next_adr_number_store_incr_monotone():
    """store.incr 우선·단조·suffix=None(신뢰 카운터)."""
    s = _MonotoneStore()
    assert asyncio.run(next_adr_number(s)) == (1, None)
    assert asyncio.run(next_adr_number(s)) == (2, None)


def test_next_adr_number_fallback_glob(tmp_path):
    """store=None → vault/decisions glob max+1 + uuid 접미."""
    dec = tmp_path / "decisions"
    dec.mkdir()
    (dec / "ADR-0001-clicker.md").write_text("x", encoding="utf-8")
    num, suffix = asyncio.run(next_adr_number(None, vault_dir=tmp_path))
    assert num == 2
    assert suffix is not None and len(suffix) == 8  # uuid 접미(동시쓰기 충돌 회피)


def test_next_adr_number_anchored_regex_ignores_uuid_suffix(tmp_path):
    """anchored regex ADR-(\\d{4})- → uuid 접미 있어도 NNNN 만 추출(ADR-0007-foo-ab12cd34→7)."""
    dec = tmp_path / "decisions"
    dec.mkdir()
    (dec / "ADR-0007-foo-ab12cd34.md").write_text("x", encoding="utf-8")
    num, _suffix = asyncio.run(next_adr_number(None, vault_dir=tmp_path))
    assert num == 8
    # regex 직접 단언
    assert _ADR_NUM_RE.search("ADR-0007-foo-ab12cd34.md").group(1) == "0007"


def test_next_adr_number_fallback_empty_dir(tmp_path):
    """ADR 0건 → 1 시작."""
    num, _suffix = asyncio.run(next_adr_number(None, vault_dir=tmp_path))
    assert num == 1


# ── ★부정변이: adr_is_substantive 게이트 판별력(green-theater 차단·verify [5]) ──────
def test_adr_substantive_gate_rejects_degenerate():
    """빈 proposals/premortem → _synthesize 가 패딩으로 채운 껍데기 ADR → 게이트 False(FAIL).

    ★이 게이트가 항상 True 면 '숙의했다' 주장이 연극 — degenerate→False 가 판별력을 falsify.
    실데이터(≥2 비-패딩 옵션 + ≥1 premortem)만 True.
    """
    with use_judge_caller(disagree_stub):
        degenerate = _synthesize(
            {"directive": "x", "proposals": [], "premortem_risks": [], "debate": {}}
        )
    # 껍데기(패딩 옵션·premortem 0) → 게이트 FAIL(부정변이 실증).
    assert adr_is_substantive(degenerate["adr_payload"]) is False
    # 실데이터(≥2 비-패딩 옵션 + premortem) → substantive.
    assert (
        adr_is_substantive(
            {
                "options": ["A안 상세 근거", "B안 상세 근거"],
                "premortem_risks": ["출시 지연"],
            }
        )
        is True
    )
    # 경계: 1 옵션만 → False · premortem 0 → False(각 조건 독립).
    assert adr_is_substantive({"options": ["A안만"], "premortem_risks": ["r"]}) is False
    assert (
        adr_is_substantive({"options": ["A안", "B안"], "premortem_risks": []}) is False
    )
