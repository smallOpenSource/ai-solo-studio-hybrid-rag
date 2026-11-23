"""P5 B3 — 새 게임(슬라이딩 타일 퍼즐) DEV→Claude Code 핸드오프 브리프 + 명시 enqueue.

P2.5 ``tools/p25_make_brief.py`` 선례 계승(자유형식 금지·write_brief 경유·라운드트립
자가검증). ★C1: 이 DEV 도구가 ``enqueue_spec(trigger="impl")`` 을 **명시적으로**
호출한다 — graph 자동 sink 아님(cycle/org graph 에 DEV-핸드오프 산출 노드 없음).

``acceptance_criteria`` 는 QA(web/scripts/play_puzzle_check.mjs)가 파싱해 단언을
파생하는 **기계 파싱가능 계약**(결정론·결합 불변식·gdd.md 와 1:1). 슬라이딩 타일
퍼즐 = clicker 재탕 금지(§21 line 791 장르 택1·아케이드 대비 입력/물리 단순·결정론).

사용: /opt/conda/envs/orch/bin/python tools/p5_make_brief.py
  REDIS_ENABLED 미설정 시 vault/specs 기록은 되고 큐는 graceful no-op(§7.5 무손실).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orch.handoff import HandoffBrief, parse_brief, render_brief  # noqa: E402
from orch.spec_queue import enqueue_spec  # noqa: E402
from orch.state_store import make_state_store  # noqa: E402

TASK_ID = "p5-slide-puzzle"


def build_brief() -> HandoffBrief:
    """gdd 결정론 계약을 §27.1 핸드오프 브리프로 인코딩(슬라이딩 타일 퍼즐)."""
    return HandoffBrief(
        task_id=TASK_ID,
        from_="DEV",
        to="Claude Code",
        objective="단일화면 HTML5 슬라이딩 타일 퍼즐(3×3·결정론 셔플·이동수 카운터) 구현 — games/slide-puzzle/",
        context_refs=[
            "vault/game-design/concept-slide-puzzle.md",
            "vault/game-design/gdd-slide-puzzle.md",
            "vault/research/p5-slide-puzzle-notes.md",
            "vault/decisions/ADR-0002-puzzle-genre.md",
        ],
        # ── 기계 파싱가능 계약(QA가 파싱→단언 파생·하드코딩 금지) ──
        acceptance_criteria=[
            "state_shape: tiles,moves,solved,size",
            "init: size=3 moves=0 solved=false tiles=scrambled_solvable",
            "move: adjacent_to_blank swaps with blank AND moves+=1",
            "win: solved=true when tiles==goal_order (1..8,blank_last)",
            "stage: solved transitions false->true exactly once on win",
            "coupling: each valid move increments moves AND changes tiles array AND only blank-adjacent tile moves",
        ],
        constraints=[
            "단일 화면 HTML5 Canvas + vanilla JS",
            "로컬 점수·외부 의존 0(오프라인 로드)",
            "RNG=시드고정(결정론 셔플·재현·solvable 보장)",
            "window.__gameState 전역 노출",
            "동결파일 0수정·games/ 신규 디렉토리",
        ],
        reversibility="two-way",
        confidence=0.8,
        priority="high",
        artifacts_expected=[
            "games/slide-puzzle/index.html",
            "games/slide-puzzle/PROVENANCE",
        ],
    )


async def _enqueue(brief: HandoffBrief) -> tuple[str, bool]:
    """spec-queue 에 명시 적재(REDIS 미설정 시 vault 기록만·큐 graceful no-op)."""
    store, ok, _err = await make_state_store()
    try:
        task_id = await enqueue_spec(store, brief, trigger="impl")
    finally:
        if store is not None:
            await store.aclose()
    return task_id, ok


def main() -> int:
    brief = build_brief()
    # 라운드트립 자가검증(render→parse 동등·디스크 기록 전 계약 무결).
    if parse_brief(render_brief(brief)) != brief:
        print("FAIL: round-trip 불일치", file=sys.stderr)
        return 1
    task_id, queued = asyncio.run(_enqueue(brief))
    spec = _REPO / "vault" / "specs" / f"{task_id}.md"
    print(f"OK 브리프 작성: {spec.relative_to(_REPO)}")
    print(f"OK round-trip 동등 (task_id={task_id})")
    if queued:
        print(
            f"OK spec-queue 적재: aigs:queue:claude_code (trigger=impl, task_id={task_id})"
        )
    else:
        print("INFO REDIS 미설정 — vault 기록만(큐 graceful no-op·§7.5 무손실)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
