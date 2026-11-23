"""Unit tests (deterministic·LLM-0·not slow) for orch.evals.judge (P4b).

Covers the §1.5 judge matrix:
  (a) ★judge_agreement *산식* 결정론(use_judge_caller 로 흔들리는 stub → ≥M/K 산출
      falsifiable·C-5 (a)). 실 qwen 수치는 OBSERVE-ONLY(slow·여기서 미호출).
  (b) judge 템플릿 렌더 결정론(고정 capture → 고정 프롬프트·LLM-0).
  (c) judge 가 라이브 그래프/Redis 미접촉(import 부재 — orch.graph/org_graph/app/
      Redis 를 judge 모듈이 끌어오지 않음·seam 만).

★stub 주입·실 qwen 미호출(LLM-0·not slow). judge seam = use_judge_caller 재사용.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from orch.evals.judge import (
    DIMENSIONS,
    judge_agreement,
    render_prompt,
    score_capture,
    score_captures,
    use_judge_caller,
)

# ── synthetic capture(고정·결정론) ────────────────────────────────────────────────
_CAPTURE = {
    "anchor_id": "j-1",
    "type": "agent_task",
    "input": "make a clicker game",
    "route": "build",
    "tool_calls": [{"name": "knowledge_search", "args": {"query": "f2p", "k": 3}}],
    "search_results": [["F2P sustained engagement", "wiki/f2p.md"]],
    "bluf": "결론: 클리커 게임 승인.\n필요행동: 없음.",
}


# ── (b) 템플릿 렌더 결정론 (고정 capture → 고정 프롬프트·LLM-0) ──────────────────────
def test_render_prompt_deterministic_all_dimensions():
    """6 차원 각각 동일 capture → byte-identical 프롬프트(난수/시각 0)."""
    for dim, _label, _crit in DIMENSIONS:
        p1 = render_prompt(dim, _CAPTURE)
        p2 = render_prompt(dim, _CAPTURE)
        assert p1 == p2
        assert isinstance(p1, str) and p1


def test_render_prompt_includes_dimension_and_capture_signal():
    """프롬프트가 차원 라벨 + capture 채점 신호(라우트/도구/BLUF)를 포함(vacuous 아님)."""
    p = render_prompt("routing", _CAPTURE)
    assert "라우팅" in p
    assert "build" in p  # capture.route
    assert "knowledge_search" in p  # capture.tool_calls
    assert "VERDICT" in p  # 단답 강제


def test_render_prompt_unknown_dimension_raises():
    """알 수 없는 차원은 ValueError(침묵 통과 금지)."""
    import pytest

    with pytest.raises(ValueError):
        render_prompt("nonexistent_dim", _CAPTURE)


def test_dimensions_cover_spec_6_9():
    """§6.9 6 채점차원이 전부 존재(도구선택/라우팅/검색관련성/환각/인젝션/완료)."""
    keys = {k for k, _, _ in DIMENSIONS}
    assert keys == {
        "tool_selection",
        "routing",
        "search_relevance",
        "hallucination",
        "injection",
        "completion",
    }


# ── (a) ★judge_agreement 산식 결정론 (흔들리는 stub → ≥M/K falsifiable·C-5 (a)) ──────
class _FlickerStub:
    """호출마다 PASS/FAIL 을 *결정론적 패턴*으로 교대하는 stub(비결정 judge 시뮬).

    pattern 인덱스를 호출 카운터로 순회 → use_judge_caller 로 주입 시 judge_agreement
    의 일치율 산식이 falsifiable(stub 결정론에서 산식 결정론이 옴·실 qwen 비결정 아님).
    """

    def __init__(self, pattern: list[str]) -> None:
        self.pattern = pattern
        self.n = 0

    def __call__(self, prompt: str, temperature: float, max_tokens: int) -> str:
        verdict = self.pattern[self.n % len(self.pattern)]
        self.n += 1
        return f"VERDICT: {verdict}\n근거: stub"


def test_agreement_unanimous_pass():
    """항상 PASS stub → 모든 차원 votes==K·agreement==1.0·agree True(≥M/K)."""
    stub = _FlickerStub(["PASS"])
    with use_judge_caller(stub):
        agr = judge_agreement(_CAPTURE, k=3, m=2, dimensions=("routing",))
    info = agr["dimensions"]["routing"]
    assert info["verdicts"] == ["PASS", "PASS", "PASS"]
    assert info["majority"] == "PASS"
    assert info["votes"] == 3
    assert info["agreement"] == 1.0
    assert info["agree"] is True


def test_agreement_split_2_of_3_signals_agree():
    """PASS,PASS,FAIL 패턴(단일차원 K=3) → votes=2·agreement=2/3·agree True(M=2)."""
    # 단일 차원만 채점하므로 호출은 차원당 1콜·K회 = pattern 그대로 소비.
    stub = _FlickerStub(["PASS", "PASS", "FAIL"])
    with use_judge_caller(stub):
        agr = judge_agreement(_CAPTURE, k=3, m=2, dimensions=("routing",))
    info = agr["dimensions"]["routing"]
    assert info["verdicts"] == ["PASS", "PASS", "FAIL"]
    assert info["majority"] == "PASS"
    assert info["votes"] == 2
    assert abs(info["agreement"] - 2 / 3) < 1e-9
    assert info["agree"] is True  # 2 >= M(2)


def test_agreement_below_m_signals_disagree():
    """PASS,FAIL,FAIL → majority=FAIL·votes=2(FAIL)·하지만 PASS 신호는 1 < M → 분열 표면화.

    majority 는 FAIL(2표)·agree 는 votes(2)>=M(2) True 지만 *방향이 FAIL* — 일치율
    산식이 다수 방향과 표수를 정확히 산출함을 falsifiable 하게 검증.
    """
    stub = _FlickerStub(["PASS", "FAIL", "FAIL"])
    with use_judge_caller(stub):
        agr = judge_agreement(_CAPTURE, k=3, m=2, dimensions=("completion",))
    info = agr["dimensions"]["completion"]
    assert info["verdicts"] == ["PASS", "FAIL", "FAIL"]
    assert info["majority"] == "FAIL"
    assert info["votes"] == 2


def test_agreement_no_majority_signal_when_below_m():
    """K=3·M=3 인데 2:1 분열 → agree False(≥M/K 미달 신호·falsifiable)."""
    stub = _FlickerStub(["PASS", "PASS", "FAIL"])
    with use_judge_caller(stub):
        agr = judge_agreement(_CAPTURE, k=3, m=3, dimensions=("routing",))
    info = agr["dimensions"]["routing"]
    assert info["votes"] == 2
    assert info["agree"] is False  # 2 < M(3)


def test_agreement_k_must_be_positive():
    import pytest

    with pytest.raises(ValueError):
        judge_agreement(_CAPTURE, k=0)


# ── score_capture / score_captures (stub·배치 경계 1회 바인딩) ──────────────────────
def test_score_capture_all_dimensions_with_stub():
    """stub 주입 시 6 차원 전부 채점(PASS/FAIL 단답 파싱)."""
    stub = _FlickerStub(["PASS"])
    with use_judge_caller(stub):
        result = score_capture(_CAPTURE)
    assert set(result.keys()) == {k for k, _, _ in DIMENSIONS}
    assert all(v in ("PASS", "FAIL") for v in result.values())


def test_score_captures_batch_binds_caller_once():
    """배치 채점이 caller 1회 바인딩(per-anchor 스왑 0)·항목 스키마 확인."""
    captures = [_CAPTURE, dict(_CAPTURE, anchor_id="j-2")]
    stub = _FlickerStub(["PASS"])
    out = score_captures(captures, caller=stub)
    assert len(out) == 2
    assert out[0]["anchor_id"] == "j-1" and out[1]["anchor_id"] == "j-2"
    assert "judge" in out[0] and isinstance(out[0]["judge"], dict)


def test_verdict_parse_conservative_fail_on_unparseable():
    """VERDICT 토큰 없고 PASS/FAIL 불명확 → FAIL(보수·침묵 PASS 금지)."""

    def garbage(prompt: str, t: float, mx: int) -> str:
        return "음... 잘 모르겠습니다 (no verdict token)"

    with use_judge_caller(garbage):
        result = score_capture(_CAPTURE, dimensions=("routing",))
    assert result["routing"] == "FAIL"


# ── (c) ★judge 격리: 라이브 그래프/Redis 미접촉 (import 부재) ───────────────────────
def test_judge_does_not_import_sideeffect_modules():
    """fresh subprocess 에서 orch.evals.judge import 후 sys.modules 에 *부수효과* 모듈
    (orch.app·orch.outbound·orch.redis_client·orch.state_store·orch.org_graph) 부재 —
    judge 배치 격리 증거(라이브 Discord emit·Redis aigs:*·OTLP·app._run sink 미접촉).

    ★orch.graph 는 *허용* — judge seam(use_judge_caller/_ACTIVE_JUDGE)이 orch.deliberation
    에 살고, deliberation 이 gemma caller ContextVar(_active_caller)를 orch.graph 에서
    *재사용*(DR-1·신규 qwen 클라 0)하기 때문에 graph *모듈*은 전이적으로 로드된다.
    하지만 graph.py 는 outbound/redis/app/discord 를 모듈탑서 import 하지 않고(확인됨),
    judge 는 그래프를 *invoke 하지 않는다*(seam 참조만) → 라이브 핫패스/부수효과 0.
    gemma-affinity 불변식은 "judge 가 라이브 그래프를 invoke 하지 않음"이지 "graph 모듈
    의존 0"이 아니다(spec §6.9 judge=qwen 정적창 배치·캡처 dict 만 입력).
    """
    import subprocess

    code = (
        "import orch.evals.judge, sys; "
        "bad=[m for m in ('orch.app','orch.outbound','orch.redis_client',"
        "'orch.state_store','orch.org_graph') if m in sys.modules]; "
        "assert not bad, f'judge pulled in side-effect modules: {bad}'; "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )
    assert proc.returncode == 0, f"judge isolation import failed: {proc.stderr}"
    assert "OK" in proc.stdout


def test_judge_module_source_no_live_graph_calls():
    """judge.py 소스(AST)에 라이브 그래프/Redis invoke 식별자 부재(seam 만)."""
    import ast

    src = (Path(__file__).resolve().parent.parent / "evals" / "judge.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    forbidden = {
        "orch.graph",
        "orch.org_graph",
        "orch.app",
        "orch.redis_client",
        "orch.outbound",
    }
    assert not (forbidden & imported), f"judge imports hotpath: {forbidden & imported}"
    # judge seam(deliberation) 만 import.
    assert "orch.deliberation" in imported


# ── 산식 OBSERVE-ONLY 정직 표기 (docstring 에 결정론 미주장 명시) ───────────────────
def test_judge_agreement_docstring_marks_qwen_nondeterminism():
    """★C-5 (b): judge_agreement docstring 이 실 qwen 수치를 OBSERVE-ONLY 로 명시."""
    doc = judge_agreement.__doc__ or ""
    assert "OBSERVE-ONLY" in doc
    assert "비결정" in doc or "결정론" in doc


def test_self_check_flicker_stub_is_deterministic():
    """메타: _FlickerStub 자체가 결정론(같은 패턴 두 인스턴스 동일 시퀀스) — 산식 결정론 토대."""
    a = _FlickerStub(["PASS", "FAIL"])
    b = _FlickerStub(["PASS", "FAIL"])
    seq_a = [a("p", 0.0, 1) for _ in range(4)]
    seq_b = [b("p", 0.0, 1) for _ in range(4)]
    assert seq_a == seq_b
    assert json.dumps(seq_a) == json.dumps(seq_b)
