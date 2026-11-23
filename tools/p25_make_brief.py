"""P2.5 T2 — DEV 핸드오프 브리프 생성 (orch/handoff.py write_brief 경유).

자유형식 직접 작성 금지(provenance): 브리프는 반드시 ``write_brief`` 로 생성한다.
``acceptance_criteria`` 는 QA(web/scripts/play_clicker_check.mjs)가 파싱해 단언을
파생하는 **기계 파싱가능 계약**(결정론 + 결합 불변식·gdd.md 와 1:1).

사용: /opt/conda/envs/orch/bin/python tools/p25_make_brief.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orch.handoff import HandoffBrief, parse_brief, write_brief  # noqa: E402

TASK_ID = "p25-clicker-mvp"


def build_brief() -> HandoffBrief:
    """gdd.md 결정론 계약을 §27.1 핸드오프 브리프로 인코딩."""
    return HandoffBrief(
        task_id=TASK_ID,
        from_="DEV",
        to="Claude Code",
        objective="단일화면 HTML5 클리커(업그레이드 결합 메커닉) 구현 — games/clicker-mvp/",
        context_refs=[
            "vault/game-design/concept.md",
            "vault/game-design/gdd.md",
            "vault/research/clicker-mvp-notes.md",
            "vault/decisions/ADR-0001-clicker-genre.md",
        ],
        # ── 기계 파싱가능 계약(QA가 파싱→단언 파생·하드코딩 금지) ──
        acceptance_criteria=[
            "state_shape: score,clicks,clickPower,upgrades,stage",
            "init: score=0 clicks=0 clickPower=1 upgrades=0 stage=0",
            "click: clicks+=1 score+=clickPower",
            "upgrade: cost=10 require=score>=10 effect=score-=10,clickPower+=1,upgrades+=1",
            "stage: stage=1 when score>=50 else stage=0",
            "coupling: after_upgrade score==old_score-10 AND clickPower==old_power+1 AND next_click adds clickPower_new",
        ],
        constraints=[
            "단일 화면 HTML5 Canvas + vanilla JS",
            "로컬 점수·외부 의존 0(오프라인 로드)",
            "RNG 없음(결정론)",
            "window.__gameState 전역 노출",
            "동결파일 0수정·games/ 신규 디렉토리",
        ],
        reversibility="two-way",
        confidence=0.82,
        priority="high",
        artifacts_expected=[
            "games/clicker-mvp/index.html",
            "games/clicker-mvp/PROVENANCE",
        ],
    )


def main() -> int:
    brief = build_brief()
    out = write_brief(brief, _REPO / "vault")
    # 라운드트립 자가검증(생성 직후 디스크에서 복원 == 원본)
    restored = parse_brief(out.read_text(encoding="utf-8"))
    if restored != brief:
        print("FAIL: round-trip 불일치", file=sys.stderr)
        return 1
    print(f"OK 브리프 작성: {out.relative_to(_REPO)}")
    print(f"OK round-trip 동등 (task_id={brief.task_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
