"""SR/renewal P4b — 회귀 게이트 (★MAJOR-1 종료코드 우선순위·결정론=유일권한·judge=warn-only).

§6.9 "회귀 게이트: 임계 미달이면 '완료'/머지 차단"을 falsifiable 하게 구현(DR-2).
결정론 프로그램적 scorer(scorers.py) + qwen judge(judge.py·warn-only)를 종합한다.

★[MAJOR-1 종료코드 우선순위 — green-theater 최고레버리지 구멍 봉합]
  - **deterministic scorer verdict = 종료코드의 *유일* 권한.** 결정론 scorer 임계
    미달 시 ``sys.exit(비-제로)``(merge 차단).
  - **qwen judge 다수결 = warn-only(비-블로킹).** judge 임계 미달이어도 **exit code
    변이 0** — ``sys.stderr`` 에 ``[JUDGE-WARN] ...`` + 리포트 기록만. **judge 경로는
    ``sys.exit`` 를 절대 호출하지 않고 exit code 를 절대 변이하지 않는다**(게이트 끝에
    ``if judge_below: sys.exit(0)`` 로 직전 비-제로를 리셋해 실 회귀를 가리는 안티패턴
    금지). §6.9 line325 "평가 점수를 게이트에 포함"을 *비-블로킹 포함*으로 리터럴 충족.
  - **접합: det-FAIL ∧ judge-WARN 동시 → exit≠0** (deterministic block 우선·judge warn
    이 block 을 가리지 않음). 즉 종료코드는 *오직* deterministic verdict 로 정해지고,
    judge 경로는 그 위에 stderr 경고만 얹는다.

임계는 결정론 scorer 합산(judge 단독 hard-fail 영구 금지). 게이트는 단발(재귀 0·
termination P0·루프는 캡처/앵커 유한 순회만).

CLI:
    python -m orch.evals.gate --captures <captures.jsonl> --anchors <golden.jsonl> \
        [--judge] [--threshold 1.0] [--judge-threshold 0.5]
  종료코드: deterministic 전부 PASS(임계 충족) → 0 / 미달 → 1(머지 차단).
  judge 미달은 stderr [JUDGE-WARN] 만(exit code 무변이).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from orch.evals.scorers import (
    Verdict,
    score_completion,
    score_injection,
    score_pass_at_k,
    score_routing,
    score_tool_selection,
)

# anchor.type → 적용할 결정론 scorer(타입별 1 차원·LLM-0·bit-재현).
_SCORER_BY_TYPE: dict[str, Callable[[dict, dict], Verdict]] = {
    "routing": score_routing,
    "agent_task": score_completion,
    "rag": score_pass_at_k,
    "injection": score_injection,
}
# tool-bearing 앵커(expects_tool:true)는 tool_selection 도 추가 채점(타입 무관).


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """JSONL 파일을 dict 목록으로(빈 줄 skip·유한 순회·루프 0)."""
    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _index_by_id(anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {a.get("id", ""): a for a in anchors}


def score_deterministic(
    captures: list[dict[str, Any]], anchors: list[dict[str, Any]]
) -> dict[str, Any]:
    """결정론 scorer 종합(LLM-0·bit-재현) → 집계 dict.

    각 capture 를 그 앵커 타입 scorer + (expects_tool 면) tool_selection 으로 채점한다.
    NA 는 분모에서 제외(no-tool 앵커의 합법 비대상). 결과:
        {"total": int, "passed": int, "failed": int, "na": int,
         "pass_rate": float, "failures": [{anchor_id, dim, verdict}...]}
    """
    by_id = _index_by_id(anchors)
    total = passed = failed = na = 0
    failures: list[dict[str, str]] = []

    for cap in captures:
        anchor = by_id.get(cap.get("anchor_id", ""))
        if anchor is None:
            continue  # 캡처에 대응 앵커 없음 → 채점 불가(스키마 정합은 별도 게이트)
        scorers: list[tuple[str, Callable[[dict, dict], Verdict]]] = []
        primary = _SCORER_BY_TYPE.get(anchor.get("type", ""))
        if primary is not None:
            scorers.append((anchor.get("type", "dim"), primary))
        if anchor.get("expects_tool"):
            scorers.append(("tool_selection", score_tool_selection))

        for dim, fn in scorers:
            verdict = fn(anchor, cap)
            if verdict is Verdict.NA:
                na += 1
                continue
            total += 1
            if verdict is Verdict.PASS:
                passed += 1
            else:
                failed += 1
                failures.append(
                    {
                        "anchor_id": cap.get("anchor_id", ""),
                        "dim": dim,
                        "verdict": verdict.value,
                    }
                )

    pass_rate = (passed / total) if total else 0.0
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "na": na,
        "pass_rate": pass_rate,
        "failures": failures,
    }


def score_judge_majority(
    captures: list[dict[str, Any]],
    *,
    k: int = 3,
    m: int = 2,
    caller: Callable[[str, float, int], str] | None = None,
) -> dict[str, Any]:
    """qwen judge 다수결 집계(warn-only·비-블로킹·게이트 종료코드에 *절대* 영향 0).

    각 capture 를 judge_agreement(K회)로 채점하고 차원별 다수표 PASS 비율을 낸다.
    ★이 함수는 *측정·보고*만 한다 — sys.exit 호출 0·exit code 변이 0(MAJOR-1).

    ★[C1 eval 격리] caller 미주입(None)이면 **로컬 qwen judge**(deliberation._judge_caller)를
    명시 바인딩한다 — ORCH_LLM_PRIMARY=azure 라도 use_judge_caller override 가 Azure-wrapped
    기본(deliberation._ACTIVE_JUDGE)에 *도달하지 못하게* 한다. judge 는 항상 로컬 qwen 으로
    채점(회귀 게이트가 Azure judge 로 침묵 전환되는 것 차단). 기존 호출자는 명시 caller=stub
    주입(이 분기 미사용) → 동작 불변(동일 _judge_caller 객체).
    """
    from orch.deliberation import _judge_caller
    from orch.evals.judge import judge_agreement, use_judge_caller

    import contextlib

    eff_caller = caller if caller is not None else _judge_caller
    with contextlib.ExitStack() as stack:
        stack.enter_context(use_judge_caller(eff_caller))  # 배치 1회 바인딩(로컬 강제)
        total = pass_maj = 0
        weak: list[dict[str, Any]] = []
        for cap in captures:
            agr = judge_agreement(cap, k=k, m=m)
            for dim, info in agr["dimensions"].items():
                total += 1
                if info["majority"] == "PASS":
                    pass_maj += 1
                else:
                    weak.append(
                        {
                            "anchor_id": cap.get("anchor_id", ""),
                            "dim": dim,
                            "majority": info["majority"],
                            "agreement": info["agreement"],
                        }
                    )

    maj_rate = (pass_maj / total) if total else 0.0
    return {
        "total": total,
        "pass_majority": pass_maj,
        "majority_rate": maj_rate,
        "weak": weak,
    }


def run_gate(
    captures: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    *,
    judge_enabled: bool = False,
    threshold: float = 1.0,
    judge_threshold: float = 0.5,
    judge_k: int = 3,
    judge_m: int = 2,
    judge_caller: Callable[[str, float, int], str] | None = None,
    report_out: Callable[[str], None] | None = None,
    warn_out: Callable[[str], None] | None = None,
) -> int:
    """게이트 실행 → 종료코드(0=통과/비-제로=머지 차단).

    ★종료코드 우선순위(MAJOR-1):
      - 종료코드는 **오직 deterministic scorer** 로 정해진다(pass_rate < threshold → 1).
      - judge 다수결 미달은 **warn-only** — ``warn_out``(기본 stderr)에 ``[JUDGE-WARN]``
        + 리포트 기록만·**exit code 변이 0**(이 함수는 judge 결과로 반환값을 바꾸지 않음).
      - 접합(det-FAIL ∧ judge-WARN): deterministic 이 이미 1 을 반환하므로 judge warn
        은 그것을 *가리지 못한다*(judge 경로에 종료코드 리셋 로직 자체가 없음).

    report_out/warn_out 미지정 시 각각 stdout/stderr 로 출력(테스트는 주입 가능).
    """
    emit = report_out or (lambda s: print(s))
    warn = warn_out or (lambda s: print(s, file=sys.stderr))

    det = score_deterministic(captures, anchors)
    emit(
        f"[GATE] deterministic: {det['passed']}/{det['total']} passed "
        f"(rate={det['pass_rate']:.3f}, na={det['na']}, fail={det['failed']})"
    )
    for f in det["failures"]:
        emit(f"[GATE-FAIL] {f['anchor_id']} · {f['dim']} → {f['verdict']}")

    # ── 종료코드 결정 = deterministic 단독 권한(judge 이전에 확정) ──────────────────
    det_blocked = det["pass_rate"] < threshold
    exit_code = 1 if det_blocked else 0

    # ── judge = warn-only(exit_code 를 *읽지도 쓰지도* 않음·MAJOR-1) ────────────────
    # ★MINOR-1 의도적 선택: deterministic 차단이 확정된 뒤에도 judge 배치를 *일부러*
    # 돌린다 — (1) 차단 런에도 judge 진단을 리포트에 남겨 가시성 확보, (2) 접합
    # (det-FAIL ∧ judge-WARN) 케이스에서 [JUDGE-WARN] 가 stderr 에 *나타나되 종료코드는
    # deterministic 이 지배*함을 실증(test_gate_detfail_and_judgewarn_conjunction_blocks).
    # 비용은 F5a=(ii) 오프라인 정적창 배치라 수용(핫패스 미접촉). 단락(not det_blocked)은
    # 그 실증을 깨므로 채택하지 않는다.
    if judge_enabled:
        jm = score_judge_majority(captures, k=judge_k, m=judge_m, caller=judge_caller)
        emit(
            f"[GATE] judge(majority·warn-only): {jm['pass_majority']}/{jm['total']} "
            f"(rate={jm['majority_rate']:.3f}, k={judge_k}, m={judge_m})"
        )
        if jm["majority_rate"] < judge_threshold:
            # ★warn-only: stderr 경고 + 리포트만. sys.exit 호출 0·exit_code 변이 0.
            warn(
                f"[JUDGE-WARN] judge majority rate {jm['majority_rate']:.3f} "
                f"< threshold {judge_threshold:.3f} (non-blocking·deterministic gate unchanged)"
            )
            for w in jm["weak"][:20]:
                warn(
                    f"[JUDGE-WARN] {w['anchor_id']} · {w['dim']} → "
                    f"majority={w['majority']} (agreement={w['agreement']:.2f})"
                )

    emit(
        f"[GATE] exit={exit_code} (deterministic={'BLOCK' if det_blocked else 'PASS'})"
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리 — 캡처/앵커 JSONL 로드 후 run_gate. 종료코드를 *그대로* 반환.

    ★judge 경로는 exit code 를 변이하지 않으므로, main 은 run_gate 반환값을 그대로
    sys.exit 한다(deterministic=유일권한). 게이트는 단발(재귀 0).
    """
    import argparse

    parser = argparse.ArgumentParser(description="SR/renewal P4 회귀 게이트")
    parser.add_argument("--captures", required=True, help="capture record JSONL")
    parser.add_argument("--anchors", required=True, help="golden anchor JSONL")
    parser.add_argument(
        "--judge", action="store_true", help="qwen judge warn-only 포함"
    )
    parser.add_argument(
        "--threshold", type=float, default=1.0, help="deterministic pass-rate 임계"
    )
    parser.add_argument(
        "--judge-threshold",
        type=float,
        default=0.5,
        help="judge majority-rate warn 임계",
    )
    parser.add_argument("--judge-k", type=int, default=3, help="judge 일치율 K(반복)")
    parser.add_argument(
        "--judge-m", type=int, default=2, help="judge 일치 신호 M(≥M/K)"
    )
    args = parser.parse_args(argv)

    captures = _load_jsonl(args.captures)
    anchors = _load_jsonl(args.anchors)
    return run_gate(
        captures,
        anchors,
        judge_enabled=args.judge,
        threshold=args.threshold,
        judge_threshold=args.judge_threshold,
        judge_k=args.judge_k,
        judge_m=args.judge_m,
    )


if __name__ == "__main__":
    sys.exit(main())
