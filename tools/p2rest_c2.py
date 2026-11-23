"""P2-rest-A US-A6 — C2 정식 측정: §24.5 하이브리드 회복(실 bge·라이브 178pt named).

evaluate(cases/knowledge_eval.jsonl, hybrid KnowledgeStore, 실 bge reranker) → c2_summary.
헤드라인 = keyword 앵커서 dense 단독 top_n 실패였으나 hybrid 최종 rerank top-K 회복(delivered).
헤드라인 ≥3 = C2 PASS. <3 = 정직 무신호 보고(§24.5 메커니즘 구축됨·소표본·앵커 조작 금지·Architect#4).
통계주장 0·건수+id만(_SMALL_SAMPLE_NOTE). semantic 회귀 0 동시 확인.

사용: PYTHONPATH=/opt/studio \
      /opt/conda/envs/one_man_corp-knowledge/bin/python tools/p2rest_c2.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from knowledge.eval import c2_summary, evaluate  # noqa: E402
from knowledge.store import KnowledgeStore  # noqa: E402

EVAL = _REPO / "cases" / "knowledge_eval.jsonl"
OUT = _REPO / "results" / "p2rest_c2"


def main() -> int:
    store = KnowledgeStore()  # 라이브 서버(2811) + 실 bge embedder
    res = evaluate(EVAL, store)  # reranker=None → 실 BGEClient 리랭커
    max_k = max(res["k_values"])
    c2 = c2_summary(res["per_query"], max_k)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "c2_summary.json").write_text(
        json.dumps(c2, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "per_query.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in res["per_query"]),
        encoding="utf-8",
    )

    headline = c2["headline_recovered"]
    diagnostic = c2["diagnostic_recovered"]
    print(
        "=== C2 SUMMARY (§24.5 하이브리드 회복·실 bge·n=24·max_k={}) ===".format(max_k)
    )
    print(f"headline_recovered (delivered post-rerank): {len(headline)} {headline}")
    print(f"diagnostic_recovered (fusion set):          {len(diagnostic)} {diagnostic}")
    print(f"semantic_regression:                        {c2['semantic_regression']}")
    if len(headline) >= 3:
        verdict = f"C2 PASS — 헤드라인 회복 {len(headline)} ≥ 3"
    else:
        verdict = (
            f"C2 NO-SIGNAL(정직) — 헤드라인 회복 {len(headline)} < 3. §24.5 메커니즘은 "
            "spec-faithful하게 구축됐으나 이 소표본(n=13 keyword)에서 측정가능 회복 없음 "
            "(BLOCKED≠pass·코드 100%·green 주장 금지·앵커 조작 금지)."
        )
    if c2["semantic_regression"] != 0:
        verdict += f"  ⚠ semantic 회귀 {c2['semantic_regression']}건(0이어야 함)."
    print(verdict)
    print(c2["note"])
    print(f"결과 보존: {OUT.relative_to(_REPO)}/{{c2_summary.json,per_query.jsonl}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
