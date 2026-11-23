"""SR/renewal P4 — Evals & 연속 품질 루프 (§6.9 + §21.4).

이 패키지는 골든셋 회귀 평가의 **결정론·핫패스-격리** 코어를 담는다(P4a):

- ``runner``  — 캡처 단계(capture-then-batch). 각 골든 앵커에 대해 그래프
  (``build_cycle_graph``/``build_org_graph``)를 **app._run 우회 직접 invoke**해
  도구호출·검색결과·route·confidence·BLUF를 *캡처 레코드*(JSONL 1줄/앵커)로
  전량 기록한다. gemma 핫패스만 사용(qwen judge는 P4b 의 별도 배치 단계).
  outbound emitter·Redis store·OTLP exporter 미주입 → 라이브 Discord·Redis
  ``aigs:*``·OTLP 오염 0(C-1 격리·[[discord-replay-demo-cleanup]] 재방송 인시던트).
- ``scorers`` — 5 결정론 scorer(LLM-0·bit-재현): tool_selection·routing·
  pass_at_k·injection·completion. 각 ``score_<dim>(anchor, capture) -> Verdict``.

★capture-then-batch (DR-1·F5a=(ii) Owner 비준): runner 가 출력을 *먼저 전부* 캡처하고
qwen judge 는 그 캡처를 별도 배치에서 1회 스왑으로 채점한다 — per-anchor 스왑 영구 금지.
"""

from __future__ import annotations

from orch.evals.scorers import (
    Verdict,
    score_completion,
    score_injection,
    score_pass_at_k,
    score_routing,
    score_tool_selection,
)

__all__ = [
    "Verdict",
    "score_completion",
    "score_injection",
    "score_pass_at_k",
    "score_routing",
    "score_tool_selection",
]
