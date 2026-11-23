"""raw.jsonl → report.md 생성기.

범용 집계 헬퍼(JSONL 로드·p50/p95·백분율·평균)는 core.report_common으로
승격되어 bench·office가 공유한다. 여기선 그것을 기존 사설 이름으로 재노출해
이 모듈 내부 호출·bench.run의 import가 무변경으로 동작한다.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.scoring import score
from core.report_common import avg as _avg
from core.report_common import load_jsonl as _load_jsonl
from core.report_common import p50_p95 as _p50_p95
from core.report_common import pct as _pct

_PROJECT_ROOT = Path(__file__).parent.parent


def generate_report(rows: list[dict[str, Any]], out_path: Path) -> None:
    """rows(raw.jsonl 내용)로부터 report.md를 생성한다."""
    toolcall_rows = [r for r in rows if r.get("suite") == "toolcall"]
    carry_rows = [r for r in rows if r.get("suite") == "carryforward"]

    lines: list[str] = ["# 벤치 결과 리포트\n"]

    # ── 도구 호출 스위트 ──────────────────────────────────────────
    if toolcall_rows:
        lines.append("## 도구 호출 시험 (toolcall)\n")

        # 모드×언어 정확도 표
        lines.append("### 모드×언어 정확도\n")
        lines.append("| 모드 | 언어 | 케이스 수 | correct | parse_fail | 정확도 |")
        lines.append("|---|---|---|---|---|---|")

        mode_lang: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in toolcall_rows:
            mode_lang[(r.get("mode", ""), r.get("lang", ""))].append(r)

        for (mode, lang), rlist in sorted(mode_lang.items()):
            total = len(rlist)
            correct = sum(1 for r in rlist if r.get("verdict") == "correct")
            parse_fail = sum(1 for r in rlist if r.get("verdict") == "parse_fail")
            lines.append(
                f"| {mode} | {lang} | {total} | {correct} | {parse_fail} | {_pct(correct, total)} |"
            )
        lines.append("")

        # verdict 분포
        lines.append("### verdict 분포 (파싱실패 분리)\n")
        lines.append("| verdict | 건수 |")
        lines.append("|---|---|")
        verdict_counts: dict[str, int] = defaultdict(int)
        for r in toolcall_rows:
            verdict_counts[r.get("verdict", "")] += 1
        for verdict, cnt in sorted(verdict_counts.items()):
            lines.append(f"| {verdict} | {cnt} |")
        lines.append("")

        # reasoning/content 문자수 및 completion_tokens 평균
        lines.append("### 토큰 및 문자수 평균\n")
        lines.append(
            "| 모드 | reasoning_chars 평균 | content_chars 평균 | completion_tokens 평균 |"
        )
        lines.append("|---|---|---|---|")
        modes = sorted({r.get("mode", "") for r in toolcall_rows})
        for mode in modes:
            mrows = [r for r in toolcall_rows if r.get("mode") == mode]
            r_chars = [r.get("reasoning_chars", 0) for r in mrows]
            c_chars = [r.get("content_chars", 0) for r in mrows]
            comp_tokens = [
                r.get("usage", {}).get("completion_tokens", 0)
                for r in mrows
                if r.get("usage", {}).get("completion_tokens") is not None
            ]
            lines.append(
                f"| {mode} | {_avg(r_chars)} | {_avg(c_chars)} | {_avg(comp_tokens)} |"
            )
        lines.append("")

        # 지연 p50/p95
        lines.append("### 지연 p50/p95\n")
        lines.append("| 모드 | p50 | p95 |")
        lines.append("|---|---|---|")
        for mode in modes:
            mrows = [r for r in toolcall_rows if r.get("mode") == mode]
            latencies = [r.get("latency_ms", 0.0) for r in mrows]
            p50, p95 = _p50_p95(latencies)
            lines.append(f"| {mode} | {p50} | {p95} |")
        lines.append("")

    # ── carryforward 스위트 ──────────────────────────────────────
    if carry_rows:
        lines.append("## 기억 전달 시험 (carryforward)\n")
        lines.append("> ⚠️ 소표본·신호용 — 통계적 유의미성 미보장\n")

        # ON/OFF 성공률 표
        lines.append("### carry_on / carry_off 체인 성공률\n")
        lines.append("| 체인 ID | 언어 | 변형 | 시도 횟수 | 전 스텝 correct 비율 |")
        lines.append("|---|---|---|---|---|")

        chain_variant: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        for r in carry_rows:
            key = (r.get("chain_id", ""), r.get("lang", ""), r.get("variant", ""))
            chain_variant[key].append(r)

        # rep별 체인 성공 여부 집계
        rep_key: dict[tuple[str, str, str, int], list[str]] = defaultdict(list)
        for r in carry_rows:
            k = (
                r.get("chain_id", ""),
                r.get("lang", ""),
                r.get("variant", ""),
                r.get("rep", 0),
            )
            rep_key[k].append(r.get("verdict", ""))

        chain_variant_reps: dict[tuple[str, str, str], list[bool]] = defaultdict(list)
        for (cid, lang, variant, rep), verdicts in rep_key.items():
            all_correct = all(v == "correct" for v in verdicts)
            chain_variant_reps[(cid, lang, variant)].append(all_correct)

        for (cid, lang, variant), successes in sorted(chain_variant_reps.items()):
            total = len(successes)
            ok = sum(successes)
            lines.append(
                f"| {cid} | {lang} | {variant} | {total} | {_pct(ok, total)} |"
            )
        lines.append("")

    lines.append(
        "---\n*report.py로 자동 생성. `python -m bench.report --rescore <dir>` 로 재채점 가능.*\n"
    )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"리포트 저장: {out_path}")


def rescore_and_report(results_dir: Path) -> None:
    """raw.jsonl의 expected/got으로 점수를 재계산하고 report.md를 재생성한다."""
    raw_path = results_dir / "raw.jsonl"
    if not raw_path.exists():
        print(f"raw.jsonl 없음: {raw_path}")
        return

    rows = _load_jsonl(raw_path)
    rescored: list[dict[str, Any]] = []
    for r in rows:
        expected = r.get("expected") or {}
        got = r.get("got")
        if expected and r.get("suite") == "toolcall":
            result = score(expected, got)
            r = dict(r)
            r["verdict"] = result["verdict"]
            r["detail"] = result["detail"]
        rescored.append(r)

    report_path = results_dir / "report.md"
    generate_report(rescored, report_path)


def _find_latest_results() -> Path | None:
    results_root = _PROJECT_ROOT / "results"
    if not results_root.exists():
        return None
    dirs = sorted(results_root.iterdir(), reverse=True)
    for d in dirs:
        if d.is_dir() and (d / "raw.jsonl").exists():
            return d
    return None


def _load_meta(results_dir: Path) -> dict[str, Any]:
    """meta.json을 읽거나, 없으면 raw.jsonl에서 model/thinking/suite를 추론한다."""
    meta_path = results_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))

    # raw.jsonl에서 추론
    raw_path = results_dir / "raw.jsonl"
    if not raw_path.exists():
        return {}
    rows = _load_jsonl(raw_path)
    if not rows:
        return {}
    first = rows[0]
    suite = first.get("suite", "unknown")
    thinking = first.get("thinking", "on")
    return {
        "suite": suite,
        "model": "unknown",
        "thinking": thinking,
        "ts": results_dir.name,
        "cases_paths": [],
    }


def _make_label(meta: dict[str, Any]) -> str:
    """meta에서 표 라벨 문자열을 생성한다."""
    model = meta.get("model", "unknown")
    thinking = meta.get("thinking", "on")
    return f"{model}/thinking-{thinking}"


def generate_compare_report(dirs: list[Path]) -> str:
    """여러 results 디렉토리를 비교하는 마크다운 리포트를 생성한다.

    Returns:
        마크다운 문자열
    """
    # 각 dir의 데이터 로드
    entries: list[dict[str, Any]] = []
    for d in dirs:
        meta = _load_meta(d)
        raw_path = d / "raw.jsonl"
        if not raw_path.exists():
            rows: list[dict[str, Any]] = []
        else:
            rows = _load_jsonl(raw_path)
        label = _make_label(meta)
        entries.append(
            {
                "dir": d,
                "meta": meta,
                "label": label,
                "rows": rows,
                "suite": meta.get("suite", "unknown"),
            }
        )

    lines: list[str] = ["# 교차 런 비교 리포트\n"]

    # ── toolcall 비교 ──────────────────────────────────────────────
    tc_entries = [e for e in entries if e["suite"] == "toolcall"]
    if tc_entries:
        lines.append("## 도구 호출 비교 (toolcall)\n")

        # 헤더: 라벨 × 모드 → ko정확도 / en정확도 / parse_fail율 / p50지연 / completion_tokens평균 / 에러건수
        # 먼저 존재하는 모드 집합 수집
        all_modes: list[str] = []
        seen_modes: set[str] = set()
        for e in tc_entries:
            for r in e["rows"]:
                m = r.get("mode", "")
                if m and m not in seen_modes:
                    seen_modes.add(m)
                    all_modes.append(m)

        lines.append(
            "| 라벨 | 모드 | ko 정확도 | en 정확도 | parse_fail율 | p50 지연 | completion_tokens 평균 | 에러 건수 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")

        for e in tc_entries:
            label = e["label"]
            tc_rows = [r for r in e["rows"] if r.get("suite") == "toolcall"]
            for mode in all_modes:
                mrows = [r for r in tc_rows if r.get("mode") == mode]
                if not mrows:
                    continue

                error_rows = [r for r in mrows if r.get("verdict") == "error"]
                non_error = [r for r in mrows if r.get("verdict") != "error"]

                ko_rows = [r for r in non_error if r.get("lang") == "ko"]
                en_rows = [r for r in non_error if r.get("lang") == "en"]

                ko_correct = sum(1 for r in ko_rows if r.get("verdict") == "correct")
                en_correct = sum(1 for r in en_rows if r.get("verdict") == "correct")
                pf_count = sum(1 for r in non_error if r.get("verdict") == "parse_fail")

                latencies = [r.get("latency_ms", 0.0) for r in non_error]
                p50, _ = _p50_p95(latencies)

                comp_tokens = [
                    r.get("usage", {}).get("completion_tokens", 0)
                    for r in non_error
                    if r.get("usage", {}).get("completion_tokens") is not None
                ]

                ko_acc = _pct(ko_correct, len(ko_rows))
                en_acc = _pct(en_correct, len(en_rows))
                pf_rate = _pct(pf_count, len(non_error))
                ct_avg = _avg([float(t) for t in comp_tokens])
                err_cnt = len(error_rows)

                lines.append(
                    f"| {label} | {mode} | {ko_acc} | {en_acc} | {pf_rate} | {p50} | {ct_avg} | {err_cnt} |"
                )

        lines.append("")

    # ── carryforward 비교 ──────────────────────────────────────────
    cf_entries = [e for e in entries if e["suite"] == "carryforward"]
    if cf_entries:
        lines.append("## 기억 전달 비교 (carryforward)\n")
        lines.append("> ⚠️ 소표본·신호용 — 통계적 유의미성 미보장\n")

        # 변형 집합 수집
        all_variants: list[str] = []
        seen_variants: set[str] = set()
        for e in cf_entries:
            for r in e["rows"]:
                v = r.get("variant", "")
                if v and v not in seen_variants:
                    seen_variants.add(v)
                    all_variants.append(v)

        lines.append("| 라벨 | variant | 체인 성공률 | 에러 건수 |")
        lines.append("|---|---|---|---|")

        for e in cf_entries:
            label = e["label"]
            cf_rows = [r for r in e["rows"] if r.get("suite") == "carryforward"]
            for variant in all_variants:
                vrows = [r for r in cf_rows if r.get("variant") == variant]
                if not vrows:
                    continue

                error_rows = [r for r in vrows if r.get("verdict") == "error"]
                non_error = [r for r in vrows if r.get("verdict") != "error"]

                # rep별 체인 성공 여부 집계
                rep_key: dict[tuple[str, int], list[str]] = defaultdict(list)
                for r in non_error:
                    k = (r.get("chain_id", ""), r.get("rep", 0))
                    rep_key[k].append(r.get("verdict", ""))

                successes = [
                    all(v == "correct" for v in verdicts)
                    for verdicts in rep_key.values()
                ]
                total_chains = len(successes)
                ok_chains = sum(successes)

                chain_rate = _pct(ok_chains, total_chains)
                err_cnt = len(error_rows)

                lines.append(f"| {label} | {variant} | {chain_rate} | {err_cnt} |")

        lines.append("")

    lines.append("---\n*report.py --compare로 자동 생성.*\n")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="raw.jsonl → report.md 생성")
    parser.add_argument(
        "--rescore",
        metavar="DIR",
        help="지정 results 디렉토리의 raw.jsonl을 재채점하고 report.md 재생성",
    )
    parser.add_argument(
        "--compare",
        metavar="DIR",
        nargs="+",
        help="여러 results 디렉토리를 교차 비교하는 리포트 생성",
    )
    args = parser.parse_args()

    if args.rescore:
        rescore_and_report(Path(args.rescore))
    elif args.compare:
        compare_dirs = [Path(d) for d in args.compare]
        report_text = generate_compare_report(compare_dirs)
        print(report_text)

        # 첫 번째 dir의 부모(results/)에 compare_<ts>.md 저장
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        save_dir = compare_dirs[0].parent
        save_path = save_dir / f"compare_{ts}.md"
        save_path.write_text(report_text, encoding="utf-8")
        print(f"\n비교 리포트 저장: {save_path}", flush=True)
    else:
        results_dir = _find_latest_results()
        if results_dir is None:
            print("results/ 디렉토리에 raw.jsonl이 없습니다.")
            return
        rows = _load_jsonl(results_dir / "raw.jsonl")
        generate_report(rows, results_dir / "report.md")


if __name__ == "__main__":
    main()
