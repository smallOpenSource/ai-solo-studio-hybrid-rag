"""한국어 검색 평가 하니스 — dense vs 리랭커 비교 + 실패 분리집계 (Phase 2 K6).

자체 평가셋(질의-정답 쌍)으로 두 검색 구성을 채점·비교한다(계획 §6.9):
  ① dense-only      : store.search_dense(query, top_n)  — 리랭커 없이 dense 순위.
  ② dense+리랭커     : search(query, k) — dense top_n 후보를 리랭커가 다시 줄 세움.

두 구성의 Hit@k(k=1,3,5)·MRR을 비교표로 내고, **실패를 두 종류로 분리집계**한다
(★이 분리집계가 측정의 핵심 — 계획 progress "Phase 2 핵심 컨벤션"):

  - **dense 회수실패**: 정답이 dense top_n 후보에 *아예 없음*. 의미(dense)로는
    못 잡았다는 뜻이라, 단어 일치(sparse/BM25)로 보강하면 잡힐 수 있는 신호다.
    → **키워드 질의의 dense 회수실패율이 sparse 도입 트리거**(§2 결정2).
  - **리랭크실패**: 정답이 dense top_n 후보엔 *있으나*, 리랭커가 줄 세운 뒤
    top-k(최종 반환) *밖*으로 밀렸다. dense 회수는 됐는데 리랭커가 못 살린 것.
  - **성공**: 정답이 리랭커 top-k 안에 들었다.

판정 방식(부분 일치 허용 — 계획 산출물): 한 검색 결과 항목이 '정답'인지는
answer_doc이 결과 source에 포함되거나 answer_snippet이 결과 text에 포함되면
맞다고 본다(_is_answer). dense 후보의 source/text는 store가 payload로 보존한다.

리포트(report.md)에 비교표 + 분리집계 + 카테고리별(semantic/keyword) 집계를
쓰고, **원본 JSONL을 raw.jsonl로 보존**해 재채점(rescore)이 가능하다. n이 작아
(≥20 소표본) **"통계 유의 주장 금지, 방향·사례 관찰만"**을 리포트에 명기한다.

CLI:
    python -m knowledge.eval <eval.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.report_common import load_jsonl, pct

from knowledge.search import _default_top_n, _normalize_rerank

if TYPE_CHECKING:
    from knowledge.chunk import Chunk
    from knowledge.search import DenseStore, Reranker

_PROJECT_ROOT = Path(__file__).parent.parent

# 합격선/리포트 상수.
_K_VALUES = (1, 3, 5)
_DEFAULT_TOP_N = 20

# 소표본 경고 — 리포트·비교표에 항상 박는다(vanity 방지, 계획 수용6·7).
_SMALL_SAMPLE_NOTE = (
    "> ⚠️ 소표본·신호용 — 통계적 유의미성 미보장(통계 유의 주장 금지, 방향·사례 관찰만)."
)

# 실패 분류 라벨(분리집계 키).
_RETRIEVAL_FAIL = "dense_retrieval_fail"  # dense 회수실패(top_n에도 없음 = sparse 신호)
_RERANK_FAIL = "rerank_fail"  # 리랭크실패(top_n엔 있으나 리랭크 후 top-k 밖)
_SUCCESS = "success"  # 리랭커 top-k 안에 정답


def _is_answer(text: str, source: str, item: dict[str, Any]) -> bool:
    """검색 결과 항목(text·source)이 평가 항목의 정답인지 본다(부분 일치 허용).

    answer_doc이 결과 source에 포함되거나(파일 경로 부분 일치),
    answer_snippet이 결과 text에 포함되면(본문 부분 일치) 정답으로 본다.
    둘 중 하나라도 맞으면 True(둘 다 비면 False).
    """
    answer_doc = (item.get("answer_doc") or "").strip()
    answer_snippet = (item.get("answer_snippet") or "").strip()
    if answer_doc and answer_doc in source:
        return True
    if answer_snippet and answer_snippet in text:
        return True
    return False


def _first_hit_rank(ranked: list[tuple[str, str]], item: dict[str, Any]) -> int | None:
    """정답이 처음 나타나는 순위(1부터)를 돌려준다. 없으면 None.

    ranked는 (text, source)를 순위 순으로 담은 목록(0번이 1등). 정답을 위에서부터
    찾아 1-기반 순위로 돌려준다 — Hit@k·MRR·실패분류에 공통으로 쓴다.
    """
    for position, (text, source) in enumerate(ranked):
        if _is_answer(text, source, item):
            return position + 1
    return None


def _rerank_candidates(
    candidates: list[tuple[Chunk, float]],
    query: str,
    reranker: Reranker,
) -> list[tuple[str, str]]:
    """dense 후보를 리랭커로 다시 줄 세워 (text, source) 순위 목록으로 만든다.

    search()와 같은 정규화(_normalize_rerank)를 써 dense 후보 전체의 리랭크 순서를
    얻는다. top-k 자르기는 하지 않는다(분리집계가 top-k *밖* 순위도 봐야 하므로).
    """
    documents = [chunk.text for chunk, _ in candidates]
    raw = reranker.rerank(query, documents)
    ranked_idx = _normalize_rerank(raw, n_docs=len(documents))
    out: list[tuple[str, str]] = []
    for idx, _score in ranked_idx:
        chunk = candidates[idx][0]
        out.append((chunk.text, chunk.source))
    return out


def _eval_one(
    item: dict[str, Any],
    store: DenseStore,
    reranker: Reranker,
    k_values: tuple[int, ...],
    top_n: int,
) -> dict[str, Any]:
    """한 질의를 두 구성으로 채점하고 실패 분류까지 매긴다.

    Returns:
        {
          "id", "query", "category", "difficulty",
          "dense_rank": int|None,        # dense-only 순위(top_n 안, 없으면 None)
          "rerank_rank": int|None,       # 리랭커 재정렬 후 순위(후보 안, 없으면 None)
          "dense_hit@k": {k: bool}, "rerank_hit@k": {k: bool},
          "classification": _RETRIEVAL_FAIL | _RERANK_FAIL | _SUCCESS,
          "rank_changed": bool,          # 리랭커가 정답 순위를 바꿨는지(관측용)
        }
    """
    query = item.get("query", "")
    max_k = max(k_values)

    # ① dense-only: top_n 후보를 dense 순서 그대로(리랭커 없이).
    candidates = store.search_dense(query, top_n=top_n)
    dense_ranked = [(c.text, c.source) for c, _ in candidates]
    dense_rank = _first_hit_rank(dense_ranked, item)

    # ② dense+리랭커: 같은 후보를 리랭커로 다시 줄 세운 순위.
    if candidates:
        rerank_ranked = _rerank_candidates(candidates, query, reranker)
    else:
        rerank_ranked = []
    rerank_rank = _first_hit_rank(rerank_ranked, item)

    # ③ hybrid(§24.5): dense+sparse RRF 융합 후보 → (진단) 융합셋 회수 + (헤드라인) rerank top-K.
    #    store가 search_hybrid를 지원할 때만 — 기존 dense+rerank 구성은 무영향(하위호환).
    hybrid_fused_rank: int | None = None
    hybrid_rerank_rank: int | None = None
    if hasattr(store, "search_hybrid"):
        hybrid_candidates = store.search_hybrid(query, top_n=top_n)
        hybrid_fused = [(c.text, c.source) for c, _ in hybrid_candidates]
        hybrid_fused_rank = _first_hit_rank(hybrid_fused, item)  # 진단(융합 후보셋)
        hybrid_rr = (
            _rerank_candidates(hybrid_candidates, query, reranker)
            if hybrid_candidates
            else []
        )
        hybrid_rerank_rank = _first_hit_rank(
            hybrid_rr, item
        )  # 헤드라인(post-rerank·delivered)

    dense_hit = {k: (dense_rank is not None and dense_rank <= k) for k in k_values}
    rerank_hit = {k: (rerank_rank is not None and rerank_rank <= k) for k in k_values}

    # ★실패 분리집계 판정 — 측정의 핵심.
    #   dense 회수실패: 정답이 dense top_n 후보에도 없음(dense_rank is None).
    #     → 의미검색으로 못 잡음 = sparse(단어일치) 보강 신호.
    #   리랭크실패: dense 후보엔 있으나(dense_rank not None) 리랭크 top-k 밖
    #     (rerank_rank None 또는 > max_k). dense 회수는 됐는데 리랭커가 못 살림.
    #   성공: 리랭크 top-k 안(rerank_rank ≤ max_k).
    if dense_rank is None:
        classification = _RETRIEVAL_FAIL
    elif rerank_rank is not None and rerank_rank <= max_k:
        classification = _SUCCESS
    else:
        classification = _RERANK_FAIL

    rank_changed = (
        dense_rank is not None and rerank_rank is not None and dense_rank != rerank_rank
    )

    return {
        "id": item.get("id", ""),
        "query": query,
        "category": item.get("category", ""),
        "difficulty": item.get("difficulty", ""),
        "answer_doc": item.get("answer_doc", ""),
        "dense_rank": dense_rank,
        "rerank_rank": rerank_rank,
        "dense_hit@k": dense_hit,
        "rerank_hit@k": rerank_hit,
        "hybrid_fused_rank": hybrid_fused_rank,
        "hybrid_rerank_rank": hybrid_rerank_rank,
        "hybrid_hit@k": {
            k: (hybrid_rerank_rank is not None and hybrid_rerank_rank <= k)
            for k in k_values
        },
        "classification": classification,
        "rank_changed": rank_changed,
    }


def c2_summary(per_query: list[dict[str, Any]], max_k: int) -> dict[str, Any]:
    """C2(§24.5 하이브리드 이득) 분리집계 — 헤드라인(delivered post-rerank) + 진단(융합셋).

    - 헤드라인: dense 단독 top_n 실패(dense_rank None)였으나 **hybrid 최종 rerank top-K**
      (hybrid_rerank_rank ≤ max_k)가 회복한 **keyword** 앵커(= 사용자가 실제 받는 회복).
    - 진단: dense 실패였으나 **융합 후보셋**(hybrid_fused_rank)이 회복한 keyword 앵커.
      헤드라인 < 진단이면 rerank가 회수를 떨어뜨리는 신호.
    - semantic 회귀: semantic 앵커 중 dense+rerank 성공이 hybrid rerank에서 깨진 수(비-증가 확인).
    ★건수+id만(통계주장 금지·_SMALL_SAMPLE_NOTE). 두 경로 무신호면 헤드라인 [] → 호출자가 정직 보고.
    """
    headline: list[str] = []
    diagnostic: list[str] = []
    semantic_regression = 0
    for r in per_query:
        cat = r.get("category", "")
        dense_miss = r.get("dense_rank") is None
        hr = r.get("hybrid_rerank_rank")
        hf = r.get("hybrid_fused_rank")
        if cat == "keyword":
            if dense_miss and hr is not None and hr <= max_k:
                headline.append(r.get("id", ""))
            if dense_miss and hf is not None:
                diagnostic.append(r.get("id", ""))
        elif cat == "semantic":
            base_ok = r.get("rerank_rank") is not None and r["rerank_rank"] <= max_k
            hyb_ok = hr is not None and hr <= max_k
            if base_ok and not hyb_ok:
                semantic_regression += 1
    return {
        "headline_recovered": headline,
        "diagnostic_recovered": diagnostic,
        "semantic_regression": semantic_regression,
        "max_k": max_k,
        "note": _SMALL_SAMPLE_NOTE,
    }


def evaluate(
    eval_jsonl: str | Path,
    store: DenseStore,
    k_values: list[int] | tuple[int, ...] = _K_VALUES,
    top_n: int = _DEFAULT_TOP_N,
    reranker: Reranker | None = None,
) -> dict[str, Any]:
    """평가셋 전체를 채점해 비교표·실패 분리집계 데이터를 만든다.

    각 질의를 ① dense-only ② dense+리랭커 두 구성으로 채점하고, 실패를 dense
    회수실패 / 리랭크실패로 분리집계한다(_eval_one). 카테고리별(semantic/keyword)
    집계도 함께 낸다 — 키워드 질의의 dense 회수실패율이 sparse 도입 트리거다.

    Args:
        eval_jsonl: 평가셋 JSONL 경로(각 줄 = id·query·answer_doc·answer_snippet
            ·category·difficulty). 상대경로면 프로젝트 루트 기준.
        store: dense 검색 제공자(KnowledgeStore 또는 mock).
        k_values: Hit@k의 k 목록(기본 1,3,5).
        top_n: dense 1차 회수 폭(기본 20). dense 회수실패 = 정답이 이 안에 없음.
        reranker: 리랭커 제공자. None이면 BGEClient 기본(실서버) — 테스트는 mock 주입.

    Returns:
        {
          "n": int, "k_values": [...], "top_n": int,
          "per_query": [ _eval_one 결과 ... ],
          "metrics": {"dense": {"hit@k", "mrr"}, "rerank": {"hit@k", "mrr"}},
          "failure_breakdown": {분류: 건수},
          "by_category": {카테고리: {n, dense_retrieval_fail, rerank_fail, success,
                                     dense_hit@k, rerank_hit@k}},
          "rank_changed_examples": [순위 바뀐 사례 ...],
          "items": [원본 평가 항목 ...],   # 재채점용 보존
        }
    """
    path = Path(eval_jsonl)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    items = load_jsonl(path)

    k_tuple = tuple(k_values)
    if reranker is None:
        from core.bge_client import BGEClient

        reranker = BGEClient()

    # 진행 로그(H-14): 질의당 dense+rerank BGE 콜이라 무타임아웃 누적 시 어디서
    # 멈췄는지 가시화한다. 결과(stdout) 오염을 피해 stderr로 1줄씩 낸다.
    # 동작 무변(채점 로직 동일, 로그만 추가).
    total = len(items)
    per_query: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        per_query.append(_eval_one(it, store, reranker, k_tuple, top_n))
        print(f"[eval] 평가 진행 {i + 1}/{total}", file=sys.stderr)

    metrics = {
        "dense": _aggregate_metrics(per_query, "dense", k_tuple),
        "rerank": _aggregate_metrics(per_query, "rerank", k_tuple),
    }
    failure_breakdown = _failure_breakdown(per_query)
    by_category = _by_category(per_query, k_tuple)
    rank_changed_examples = [
        {
            "id": r["id"],
            "query": r["query"],
            "dense_rank": r["dense_rank"],
            "rerank_rank": r["rerank_rank"],
        }
        for r in per_query
        if r["rank_changed"]
    ]

    return {
        "n": len(per_query),
        "k_values": list(k_tuple),
        "top_n": top_n,
        "per_query": per_query,
        "metrics": metrics,
        "failure_breakdown": failure_breakdown,
        "by_category": by_category,
        "rank_changed_examples": rank_changed_examples,
        "items": items,
    }


def _aggregate_metrics(
    per_query: list[dict[str, Any]], which: str, k_values: tuple[int, ...]
) -> dict[str, Any]:
    """한 구성(dense|rerank)의 Hit@k·MRR을 집계한다.

    Hit@k = 정답이 상위 k 안에 든 질의 비율. MRR = 정답 순위의 역수 평균
    (정답이 없으면 0). which="dense"면 dense_rank/dense_hit@k를, "rerank"면
    rerank_rank/rerank_hit@k를 본다.
    """
    n = len(per_query)
    rank_key = f"{which}_rank"
    hit_key = f"{which}_hit@k"

    hit_at_k: dict[int, int] = {k: 0 for k in k_values}
    rr_sum = 0.0
    for r in per_query:
        for k in k_values:
            if r[hit_key].get(k):
                hit_at_k[k] += 1
        rank = r[rank_key]
        if rank is not None and rank > 0:
            rr_sum += 1.0 / rank

    return {
        "n": n,
        "hit@k": {k: {"count": hit_at_k[k], "total": n} for k in k_values},
        "mrr": (rr_sum / n) if n else 0.0,
    }


def _failure_breakdown(per_query: list[dict[str, Any]]) -> dict[str, int]:
    """전체 실패 분리집계 — 분류별 건수(dense 회수실패 / 리랭크실패 / 성공)."""
    counts = {_RETRIEVAL_FAIL: 0, _RERANK_FAIL: 0, _SUCCESS: 0}
    for r in per_query:
        counts[r["classification"]] += 1
    return counts


def _by_category(
    per_query: list[dict[str, Any]], k_values: tuple[int, ...]
) -> dict[str, dict[str, Any]]:
    """카테고리별(semantic/keyword 등) 분리집계 + Hit@k.

    키워드 질의의 dense 회수실패율이 sparse 도입 트리거이므로 카테고리별
    회수실패/리랭크실패/성공 건수와 두 구성 Hit@k를 함께 낸다.
    """
    cats: dict[str, list[dict[str, Any]]] = {}
    for r in per_query:
        cats.setdefault(r["category"] or "(없음)", []).append(r)

    out: dict[str, dict[str, Any]] = {}
    for cat, rows in sorted(cats.items()):
        n = len(rows)
        out[cat] = {
            "n": n,
            _RETRIEVAL_FAIL: sum(
                1 for r in rows if r["classification"] == _RETRIEVAL_FAIL
            ),
            _RERANK_FAIL: sum(1 for r in rows if r["classification"] == _RERANK_FAIL),
            _SUCCESS: sum(1 for r in rows if r["classification"] == _SUCCESS),
            "dense_hit@k": {
                k: sum(1 for r in rows if r["dense_hit@k"].get(k)) for k in k_values
            },
            "rerank_hit@k": {
                k: sum(1 for r in rows if r["rerank_hit@k"].get(k)) for k in k_values
            },
        }
    return out


def generate_report(result: dict[str, Any]) -> str:
    """evaluate 결과로 report.md 마크다운 문자열을 만든다.

    비교표(dense vs 리랭커 Hit@k·MRR) + 실패 분리집계(전체·카테고리별) +
    리랭커가 top-k 순위를 바꾼 사례를 담는다. 소표본 경고를 항상 박는다.
    """
    k_values = result["k_values"]
    n = result["n"]
    lines: list[str] = ["# 한국어 검색 평가 리포트 (dense vs 리랭커)\n"]
    lines.append(_SMALL_SAMPLE_NOTE + "\n")
    lines.append(f"- 평가셋 크기 n = {n}")
    lines.append(f"- dense 1차 회수 폭 top_n = {result['top_n']}")
    lines.append(f"- Hit@k의 k = {k_values}\n")

    # ── 비교표: dense vs 리랭커 ───────────────────────────────────
    lines.append("## 비교표 — dense-only vs dense+리랭커\n")
    header = "| 구성 | " + " | ".join(f"Hit@{k}" for k in k_values) + " | MRR |"
    sep = "|---|" + "---|" * (len(k_values) + 1)
    lines.append(header)
    lines.append(sep)
    for label, key in (("dense-only", "dense"), ("dense+리랭커", "rerank")):
        m = result["metrics"][key]
        cells = [pct(m["hit@k"][k]["count"], m["hit@k"][k]["total"]) for k in k_values]
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {m['mrr']:.3f} |")
    lines.append("")

    # ── 실패 분리집계(전체) ───────────────────────────────────────
    lines.append("## 실패 분리집계 (★측정의 핵심)\n")
    lines.append(
        "- **dense 회수실패**: 정답이 dense top_n 후보에도 없음 = 의미검색이 "
        "못 잡음 → **단어 일치(sparse/BM25) 도입 신호**."
    )
    lines.append(
        "- **리랭크실패**: 정답이 dense 후보엔 있으나 리랭커 top-k 밖 = 리랭커 한계."
    )
    lines.append("- **성공**: 정답이 리랭커 top-k 안.\n")
    fb = result["failure_breakdown"]
    lines.append("| 분류 | 건수 | 비율 |")
    lines.append("|---|---|---|")
    for label, key in (
        ("dense 회수실패", _RETRIEVAL_FAIL),
        ("리랭크실패", _RERANK_FAIL),
        ("성공", _SUCCESS),
    ):
        lines.append(f"| {label} | {fb[key]} | {pct(fb[key], n)} |")
    lines.append("")

    # ── 카테고리별 분리집계(키워드 회수실패 = sparse 트리거) ───────
    lines.append("## 카테고리별 분리집계 (키워드 회수실패 = sparse 트리거)\n")
    lines.append(
        "| 카테고리 | n | dense 회수실패 | 리랭크실패 | 성공 | "
        "dense Hit@" + str(max(k_values)) + " | 리랭커 Hit@" + str(max(k_values)) + " |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    max_k = max(k_values)
    for cat, row in result["by_category"].items():
        cn = row["n"]
        lines.append(
            f"| {cat} | {cn} | "
            f"{row[_RETRIEVAL_FAIL]} ({pct(row[_RETRIEVAL_FAIL], cn)}) | "
            f"{row[_RERANK_FAIL]} | {row[_SUCCESS]} | "
            f"{pct(row['dense_hit@k'][max_k], cn)} | "
            f"{pct(row['rerank_hit@k'][max_k], cn)} |"
        )
    lines.append("")

    # ── 리랭커 top-k 순위변경 관측(합격선 — 최소 1사례) ───────────
    lines.append("## 리랭커 top-k 순위변경 관측 사례 (합격선)\n")
    examples = result["rank_changed_examples"]
    if examples:
        lines.append(
            f"리랭커가 정답 순위를 바꾼 사례 {len(examples)}건 관측"
            " (dense 순위 → 리랭커 순위):\n"
        )
        lines.append("| id | dense 순위 | 리랭커 순위 | query |")
        lines.append("|---|---|---|---|")
        for ex in examples:
            lines.append(
                f"| {ex['id']} | {ex['dense_rank']} | {ex['rerank_rank']} | "
                f"{ex['query']} |"
            )
    else:
        lines.append(
            "리랭커가 정답 순위를 바꾼 사례가 관측되지 않았다"
            "(dense 순위가 이미 리랭커와 같음 — 그 사실 자체가 결론)."
        )
    lines.append("")

    lines.append(
        "---\n*knowledge/eval.py로 자동 생성. 원본 raw.jsonl 보존 — "
        "`python -m knowledge.eval --rescore <dir>` 로 재채점 가능.*\n"
    )
    return "\n".join(lines)


def write_report(result: dict[str, Any], out_dir: str | Path) -> Path:
    """report.md + raw.jsonl(원본 평가셋 보존) + per_query.jsonl을 디렉토리에 쓴다.

    재채점(rescore) 가능하게 원본 평가 항목을 raw.jsonl로, 질의별 채점 결과를
    per_query.jsonl로 보존한다. report.md는 사람이 읽는 비교표·분리집계.

    Returns:
        쓴 report.md 경로.
    """
    out = Path(out_dir)
    if not out.is_absolute():
        out = _PROJECT_ROOT / out
    out.mkdir(parents=True, exist_ok=True)

    # 원본 평가셋 보존(재채점용).
    raw_path = out / "raw.jsonl"
    with open(raw_path, "w", encoding="utf-8") as f:
        for item in result["items"]:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # 질의별 채점 결과 보존.
    pq_path = out / "per_query.jsonl"
    with open(pq_path, "w", encoding="utf-8") as f:
        for row in result["per_query"]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_path = out / "report.md"
    report_path.write_text(generate_report(result), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점 — `python -m knowledge.eval <eval.jsonl>` (또는 --rescore <dir>).

    평가셋을 실서버(bge) + 실 store로 채점해 results/phase2/<ts>/에 report.md와
    원본 raw.jsonl을 쓴다. --rescore는 보존된 raw.jsonl을 다시 채점한다.
    오프라인 테스트는 evaluate(...)에 mock store/reranker를 주입해 검증한다.
    """
    parser = argparse.ArgumentParser(
        description="한국어 검색 평가 — dense vs 리랭커 비교 + 실패 분리집계"
    )
    parser.add_argument(
        "eval_jsonl",
        nargs="?",
        help="평가셋 JSONL 경로(예: cases/knowledge_eval.jsonl)",
    )
    parser.add_argument(
        "--rescore",
        metavar="DIR",
        help="보존된 results 디렉토리(raw.jsonl)를 재채점하고 report.md 재생성",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        default=None,
        help="리포트 출력 디렉토리(기본 results/phase2/<타임스탬프>)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    from knowledge.store import KnowledgeStore

    if args.rescore:
        eval_path = Path(args.rescore) / "raw.jsonl"
        out_dir = Path(args.rescore)
    elif args.eval_jsonl:
        eval_path = Path(args.eval_jsonl)
        out_dir = Path(args.out) if args.out else _default_out_dir()
    else:
        parser.error("eval.jsonl 경로 또는 --rescore <dir>가 필요합니다.")
        return 2

    store = KnowledgeStore()
    result = evaluate(eval_path, store, top_n=_default_top_n())
    report_path = write_report(result, out_dir)
    print(f"평가 완료: n={result['n']} → {report_path}")
    print(f"실패 분리집계: {result['failure_breakdown']}")
    return 0


def _default_out_dir() -> Path:
    """results/phase2/<UTC 타임스탬프> 기본 출력 디렉토리."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _PROJECT_ROOT / "results" / "phase2" / ts


if __name__ == "__main__":
    raise SystemExit(main())
