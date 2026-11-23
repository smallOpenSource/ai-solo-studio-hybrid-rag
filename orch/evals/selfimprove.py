"""SR/renewal P4c — 자기개선 경로 (§6.9 사람 라벨 경로 · falsifiable 부분만).

★[OBSERVE-ONLY] Phoenix 실시간 자동수집(trace → 라벨)은 이 모듈에 구현하지 않는다.
  - 이유: Phoenix query API가 외부 자원(§16)이므로 BLOCKED-pending-Owner.
  - 현황: code 100% 준비 / runtime pending: Phoenix query API.
  - green-fake 금지: 이 주석이 없는 척 자동수집 경로를 구현하거나
    "PASS"로 표기하는 것은 정직 위반이다.
  - 구현 가능 시점: Owner가 Phoenix 엔드포인트를 제공하면 별도 phoenix_ingest.py 로 추가.

구현된 경로 (사람 라벨 경로 · falsifiable):
  1. add_golden_anchor(failure_record, label, golden_path) → None
     : 운영 실패 캡처 레코드 + 사람 라벨 → golden 스키마 앵커로 변환 → golden.jsonl append.
  2. CLI: python -m orch.evals.selfimprove --from-capture <capture.jsonl>
           --label <label.json> --golden <path>
     : append 후 run_gate를 단 1회 invocation (★루프 0 · termination P0).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


# ── golden 스키마 상수 (test_evals_golden.py 와 동형) ─────────────────────────────
_VALID_TYPES = {"agent_task", "routing", "rag", "injection"}
_VALID_GRAPHS = {"cycle", "org"}
_REQUIRED_KEYS = {
    "id",
    "type",
    "graph",
    "input",
    "expected",
    "category",
    "expects_tool",
}


def _validate_anchor(anchor: dict[str, Any]) -> None:
    """golden 스키마 필수키·enum 검증. 위반 시 ValueError(침묵 통과 금지)."""
    missing = _REQUIRED_KEYS - anchor.keys()
    if missing:
        raise ValueError(f"anchor missing required keys: {missing}")
    if anchor["type"] not in _VALID_TYPES:
        raise ValueError(
            f"invalid anchor type={anchor['type']!r}; allowed={_VALID_TYPES}"
        )
    if anchor["graph"] not in _VALID_GRAPHS:
        raise ValueError(
            f"invalid anchor graph={anchor['graph']!r}; allowed={_VALID_GRAPHS}"
        )
    if not isinstance(anchor["expects_tool"], bool):
        raise ValueError(
            f"expects_tool must be bool, got {type(anchor['expects_tool']).__name__}"
        )


def _load_ids(golden_path: Path) -> set[str]:
    """현재 golden.jsonl 의 id 집합을 반환(중복 방지용)."""
    if not golden_path.exists():
        return set()
    ids: set[str] = set()
    for line in golden_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            ids.add(json.loads(line).get("id", ""))
    return ids


def add_golden_anchor(
    failure_record: dict[str, Any],
    label: dict[str, Any],
    golden_path: str | Path,
) -> None:
    """운영 실패 캡처 레코드 + 사람 라벨 → golden.jsonl 에 새 앵커를 append 한다.

    Args:
        failure_record: runner.run_anchor() 가 반환한 캡처 레코드(anchor_id·type·graph·
            route·bluf·tool_calls·search_results·final_state·decision_confidence 포함).
        label: 사람이 제공하는 라벨 dict. 필수 키:
            - "expected"  (dict)  — golden 스키마의 expected 필드 전체
            - "type"      (str)   — _VALID_TYPES 중 하나
            - "category"  (str)   — 임의 문자열 (예: "selfimprove/routing")
            선택 키:
            - "id"        (str)   — 지정 시 그대로 사용, 미지정 시 uuid4 자동 생성
            - "graph"     (str)   — 미지정 시 failure_record["graph"] 에서 추론
            - "input"     (str)   — 미지정 시 failure_record.get("input","") 에서 추론
            - "expects_tool" (bool) — 미지정 시 bool(failure_record.get("tool_calls"))
        golden_path: append 대상 JSONL 경로 (없으면 생성).

    Guarantees:
        - id 유일: 기존 golden 에 같은 id 가 있으면 uuid4 로 교체.
        - golden 스키마 검증(_validate_anchor) 통과 후에만 기록.
        - 실 vault/evals/golden.jsonl 오염 방지는 호출자(테스트)의 tmp 격리 책임.
        - ★루프 0 · 단발 append(termination P0).

    ★Phoenix 자동수집 경로: OBSERVE-ONLY · 미구현 (모듈 docstring 참조).
    """
    golden_path = Path(golden_path)

    # ── 앵커 필드 조립 ─────────────────────────────────────────────────────────
    # graph: label 우선 → failure_record 폴백
    graph = str(label.get("graph") or failure_record.get("graph") or "cycle")
    # input: label 우선 → failure_record.final_state.directive → anchor_id 폴백
    input_text = str(
        label.get("input")
        or (failure_record.get("final_state") or {}).get("directive")
        or failure_record.get("anchor_id", "")
    )
    # expects_tool: label 우선 → tool_calls 존재 여부로 추론
    if "expects_tool" in label:
        expects_tool = bool(label["expects_tool"])
    else:
        expects_tool = bool(failure_record.get("tool_calls"))

    # id 유일 보장
    existing_ids = _load_ids(golden_path)
    anchor_id = str(label.get("id") or "")
    if not anchor_id or anchor_id in existing_ids:
        anchor_id = f"si-{uuid.uuid4().hex[:12]}"
        # 충돌이 거의 없지만 안전하게 한 번 더 확인
        while anchor_id in existing_ids:
            anchor_id = f"si-{uuid.uuid4().hex[:12]}"

    anchor: dict[str, Any] = {
        "id": anchor_id,
        "type": str(label.get("type", failure_record.get("type", "agent_task"))),
        "graph": graph,
        "input": input_text,
        "expected": label["expected"],
        "category": str(label.get("category", "selfimprove/unlabeled")),
        "expects_tool": expects_tool,
    }

    # ── 스키마 검증(append 전 차단) ───────────────────────────────────────────
    _validate_anchor(anchor)

    # ── append (단발·루프 0) ───────────────────────────────────────────────────
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    with golden_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(anchor, ensure_ascii=False) + "\n")


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리.

    사용법:
        python -m orch.evals.selfimprove \\
            --from-capture <capture.jsonl> \\
            --label <label.json> \\
            --golden <golden.jsonl>

    동작:
        1. capture.jsonl 의 첫 번째 레코드를 failure_record 로 읽는다.
        2. label.json 을 사람 라벨로 읽는다.
        3. add_golden_anchor(failure_record, label, golden) 로 append 한다.
        4. ★단발 재실행: gate.main(['--captures', <capture>, '--anchors', <golden>])
           를 1회 invocation 한다(루프 0 · termination P0).

    종료코드: gate 종료코드(0=통과 / 비-제로=차단)를 그대로 반환한다.
    게이트는 단발(재귀 0 · 루프 0).

    ★Phoenix 자동수집 경로: OBSERVE-ONLY · 미구현 (모듈 docstring 참조).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="SR/renewal P4c — 자기개선: 운영 실패 → golden append → 게이트 단발 재실행"
    )
    parser.add_argument(
        "--from-capture",
        required=True,
        metavar="CAPTURE_JSONL",
        help="운영 실패 캡처 JSONL (첫 레코드를 사용)",
    )
    parser.add_argument(
        "--label",
        required=True,
        metavar="LABEL_JSON",
        help="사람 라벨 JSON 파일 (expected·type·category 포함)",
    )
    parser.add_argument(
        "--golden",
        required=True,
        metavar="GOLDEN_JSONL",
        help="append 대상 golden.jsonl 경로",
    )
    args = parser.parse_args(argv)

    # ── 1. failure_record 로드 ─────────────────────────────────────────────────
    capture_path = Path(args.from_capture)
    failure_record: dict[str, Any] = {}
    for line in capture_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            failure_record = json.loads(line)
            break  # 첫 레코드만 사용(단발)
    if not failure_record:
        print(f"[SELFIMPROVE] ERROR: no records in {capture_path}", flush=True)
        return 2

    # ── 2. label 로드 ─────────────────────────────────────────────────────────
    label: dict[str, Any] = json.loads(Path(args.label).read_text(encoding="utf-8"))

    # ── 3. golden append ──────────────────────────────────────────────────────
    add_golden_anchor(failure_record, label, args.golden)
    print(f"[SELFIMPROVE] appended anchor to {args.golden}", flush=True)

    # ── 4. 게이트 단발 재실행(루프 0 · termination P0) ─────────────────────────
    # ★루프 없음: gate.main() 은 1회 호출로 종료(재귀 0·반복 0).
    # gate 는 --captures 로 원본 capture JSONL, --anchors 로 append-후 golden 을 받는다.
    from orch.evals.gate import main as gate_main

    exit_code = gate_main(
        ["--captures", str(capture_path), "--anchors", str(args.golden)]
    )
    print(f"[SELFIMPROVE] gate exit={exit_code}", flush=True)
    return exit_code


if __name__ == "__main__":
    import sys

    sys.exit(main())
