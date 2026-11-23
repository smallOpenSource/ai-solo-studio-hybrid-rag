"""SR/renewal P5c C1 — §0.5 4차원 라이브 완주 + 관찰 ◐ 부분충족 데모(falsifiable).

스펙 권위(renewal-spec-is-authority · SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md):
  - §0.5(26-33) 성공상 5차원: 운영·자율·생산·지식·관찰.
  - §6.7 관찰 = 모든 에이전트 행위가 Phoenix 트레이스로 가시화.

★M3(계획 §1.3-T3·긴장점3): P5 = **4차원 라이브 완주(생산·운영·자율·지식·✔) + 관찰차원
  부분충족(◐·orch 트레이스+SSE)**. 풀 관찰(WA 패널 8-상태 라이브 반영) = P6. 미실재를
  ✔ 로 분식 금지(Principle 4·green-theater 차단).

각 차원 PASS 술어는 **falsifiable**(상태플래그 아닌 실 실행/파일/프레임 레벨):
  - 운영: ``run_anchor``(가역 directive·stub caller·모델스왑 0·바운드) → ceo_route route
    산출 + BLUF(state.bluf 또는 outbound TEXT_MESSAGE).
  - 자율: 가역 결정이 route≠escalate AND ``__interrupt__`` 부재(runner.py 계약·P3).
  - 생산: ``games/slide-puzzle/`` 아티팩트 존재 + C4 harness 존재(상태전이 단언=verify 게이트3).
  - 지식: 사이클 산출이 ``vault/`` 에 ≥1 + provenance frontmatter(provenance: 키).
  - 관찰 ◐: orch ``/init_tracing`` span export 경로 존재(span_count 카운터) + SSE 프레임 경로
    (``/events/stream``). 풀 관찰=P6 → ◐ 정직 표기(✔ 아님).

모델스왑 회피: stub caller(conftest)로 바운드 invoke — 실 gemma 미접촉(런타임 bounded·
스왑 스톨 0). 이 도구는 orch 코어 0수정(read-only 검증 표면).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]


@dataclass
class DimensionResult:
    """차원 1개 판정. ``mark`` ∈ {"✔","◐"}·``ok``=PASS 술어 충족·``evidence``=근거."""

    name: str
    mark: str
    ok: bool
    evidence: dict[str, Any] = field(default_factory=dict)


# ── stub caller/searcher (모델스왑 0·결정론·바운드) ────────────────────────────
def _stub_caller(prompt: str, temperature: float, max_tokens: int) -> str:
    """conftest.stub_caller 동형(가역 directive → BUILD route·BLUF '결론' 시작).

    실 gemma 미접촉(런타임 bounded·스왑 스톨 0). ceo_route 가 BUILD 로 가도록 키잉.
    """
    p = prompt.lower()
    if "ceo router" in p:
        return "ROUTE: build | reversible game request"
    if "design stage" in p:
        return "core loop: slide tiles. win: sorted. reversible: yes."
    if "content stage" in p:
        return "Name: Slide Puzzle. tiles: 3x3."
    if "qa stage" in p:
        return "check1: loop ok. check2: solvable. VERDICT: PASS"
    if "bluf" in p:
        return (
            "결론: 슬라이딩 퍼즐 가역 작업 — 자율 진행 가능.\n"
            "필요행동: 없음.\n근거:\n- design: 코어 루프 명확\n- qa: PASS"
        )
    return "stub-artifact"


def _stub_searcher(query: str, k: int) -> dict[str, Any]:
    """no-network knowledge 검색 stub(결정론·실 bge 미접촉)."""
    return {
        "results": [
            {
                "text": "퍼즐 코어 루프 설계 근거",
                "source": "https://example.test/wiki/Puzzle",
            }
        ]
    }


# ── 차원 1: 운영(가역 directive → ceo_route route + BLUF) ──────────────────────
def check_operations() -> DimensionResult:
    """운영 ✔: 지시 1건 → ceo_route → route 산출 + BLUF(run_anchor·stub·모델스왑 0)."""
    from orch.evals.runner import run_anchor

    anchor = {
        "id": "p5-ops-demo",
        "type": "routing",
        "graph": "cycle",
        "input": "슬라이딩 퍼즐 게임을 만들어줘",
    }
    rec = run_anchor(anchor, caller=_stub_caller, searcher=_stub_searcher)
    route = rec.get("route", "")
    bluf = rec.get("bluf", "")
    # PASS 술어: route 산출(비어있지 않음) AND BLUF 존재(결론 보고까지).
    ok = bool(route) and route != "escalate" and bool(bluf) and "결론" in bluf
    return DimensionResult(
        name="운영",
        mark="✔",
        ok=ok,
        evidence={"route": route, "bluf_head": bluf.splitlines()[0] if bluf else ""},
    )


# ── 차원 2: 자율(가역 결정 route≠escalate · __interrupt__ 부재) ─────────────────
def check_autonomy() -> DimensionResult:
    """자율 ✔: 가역 결정이 interrupt 없이 완주(route≠escalate·runner 계약·P3)."""
    from orch.evals.runner import run_anchor

    anchor = {
        "id": "p5-auto-demo",
        "type": "routing",
        "graph": "cycle",
        "input": "슬라이딩 퍼즐 타일 색상을 조정해줘",  # 가역·저비용
    }
    rec = run_anchor(anchor, caller=_stub_caller, searcher=_stub_searcher)
    route = rec.get("route", "")
    # 가역 결정 → escalate(interrupt) 아님. runner 는 __interrupt__ 도달 시 route="escalate".
    ok = route != "escalate" and bool(route)
    return DimensionResult(
        name="자율",
        mark="✔",
        ok=ok,
        evidence={"route": route, "interrupt_free": route != "escalate"},
    )


# ── 차원 3: 생산(게임 아티팩트 + C4 harness 존재·전이단언=verify 게이트3) ────────
def check_production() -> DimensionResult:
    """생산 ✔: games/slide-puzzle/ 아티팩트 + C4 harness 존재(상태전이 단언=verify 게이트3)."""
    game = _REPO / "games" / "slide-puzzle" / "index.html"
    prov = _REPO / "games" / "slide-puzzle" / "PROVENANCE"
    harness = _REPO / "web" / "scripts" / "play_puzzle_check.mjs"
    ok = game.is_file() and prov.is_file() and harness.is_file()
    return DimensionResult(
        name="생산",
        mark="✔",
        ok=ok,
        evidence={
            "game": str(game.relative_to(_REPO)) if game.is_file() else "(missing)",
            "harness": str(harness.relative_to(_REPO))
            if harness.is_file()
            else "(missing)",
            "note": "상태전이 PASS 는 verify_phase5.sh 게이트3(C4 e2e)에서 실증",
        },
    )


# ── 차원 4: 지식(사이클 산출 vault/ ≥1 + provenance frontmatter) ───────────────
def check_knowledge() -> DimensionResult:
    """지식 ✔: 사이클 산출이 vault/ 에 ≥1 + provenance frontmatter(provenance: 키)."""
    vault = _REPO / "vault"
    # 산출물 후보(P5 사이클 산출). provenance frontmatter 보유 ≥1 입증.
    candidates = [
        vault / "quality" / "slide-puzzle-qa.md",
        vault / "quality" / "worklog-p5-slide-puzzle.md",
        vault / "research" / "p5-slide-puzzle-notes.md",
        vault / "decisions" / "ADR-0002-puzzle-genre.md",
        vault / "game-design" / "gdd-slide-puzzle.md",
    ]
    present = [c for c in candidates if c.is_file()]
    with_provenance = [
        c
        for c in present
        if "provenance:" in c.read_text(encoding="utf-8")[:400].lower()
    ]
    ok = len(present) >= 1 and len(with_provenance) >= 1
    return DimensionResult(
        name="지식",
        mark="✔",
        ok=ok,
        evidence={
            "vault_artifacts": [str(c.relative_to(_REPO)) for c in present],
            "with_provenance": [str(c.relative_to(_REPO)) for c in with_provenance],
        },
    )


# ── 차원 5: 관찰 ◐(orch span export 경로 + SSE 프레임 경로·풀 관찰=P6) ──────────
def check_observability() -> DimensionResult:
    """관찰 ◐ 부분충족: orch span export 경로(span_count 카운터) + SSE 프레임 경로 존재.

    풀 관찰(WA 패널 8-상태 라이브 반영) = P6 → ◐(✔ 아님·정직 표기·Principle 4).
    라이브 서버 미기동에서도 *경로 존재*(코드 표면)를 입증(스왑/네트워크 0). 라이브 span_count>0
    는 verify_phase5.sh 가 가동 중 서버에 prober(있으면) 또는 정적 경로로 확인.
    """
    app_src = (_REPO / "orch" / "app.py").read_text(encoding="utf-8")
    # span export 경로: _init_tracing + span 카운터 + /healthz spans_emitted.
    span_path = (
        "_init_tracing" in app_src
        and "spans_emitted" in app_src
        and "_SPAN_COUNT" in app_src
    )
    # SSE 프레임 경로: /events/stream(AG-UI SSE·대시보드/봇 브리지).
    sse_path = "/events/stream" in app_src
    ok = span_path and sse_path
    return DimensionResult(
        name="관찰",
        mark="◐",
        ok=ok,
        evidence={
            "span_export_path": span_path,
            "sse_frame_path": sse_path,
            "deferral": "풀 관찰(WA 패널 8-상태 라이브 반영) = P6 · 본 P5 = ◐ 부분충족(orch 트레이스+SSE)",
        },
    )


def run_all() -> dict[str, Any]:
    """4차원 ✔ + 관찰 ◐ 데모를 실행해 결과 dict 반환(verify/CLI 공유).

    날조 0: 각 차원 PASS 술어가 falsifiable(실 invoke·파일·코드 경로). 관찰은 ◐ 정직 표기.
    """
    dims = [
        check_operations(),
        check_autonomy(),
        check_production(),
        check_knowledge(),
        check_observability(),
    ]
    full_dims = [d for d in dims if d.mark == "✔"]
    full_ok = all(d.ok for d in full_dims)
    obs = next(d for d in dims if d.name == "관찰")
    return {
        "four_dimensions_pass": full_ok,
        "four_dimensions": {d.name: d.ok for d in full_dims},
        "observability_partial": {"mark": obs.mark, "ok": obs.ok},
        "results": [
            {"name": d.name, "mark": d.mark, "ok": d.ok, "evidence": d.evidence}
            for d in dims
        ],
    }


if __name__ == "__main__":
    out = run_all()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    # exit 0 = 4차원 ✔ 완주(관찰 ◐ 는 부분충족이라 종료코드에 미반영·정직).
    raise SystemExit(0 if out["four_dimensions_pass"] else 1)
