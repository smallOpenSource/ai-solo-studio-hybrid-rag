"""Integration tests for orch.evals.gate (P4b) — ★실제 종료코드(returncode) 단언.

★3 케이스 subprocess 종료코드 단언(실 프로세스 spawn·returncode·상태플래그 아님):
  (1) deterministic 임계미달 = block → **exit≠0** (merge 차단 증명).
  (2) deterministic PASS + judge 다수결 미달 = warn → **exit0 + stderr [JUDGE-WARN]**.
  (3) det-FAIL ∧ judge-WARN 접합 → **exit≠0** (judge warn 이 block 을 가리지 못함·
      judge 경로 sys.exit 미호출·MAJOR-1 종료코드 우선순위).
  + 정상(deterministic 전부 PASS·judge 미사용) → exit0.

deterministic scorer 는 *실제*(LLM-0·bit-재현) 돈다. judge 만 stub(qwen 미호출·not slow).
gate.main 은 judge caller 주입 인자가 없으므로, judge 가 필요한 케이스는 run_gate 를
직접 호출하는 *드라이버 스크립트*를 subprocess 로 spawn 해 실 returncode 를 단언한다
(deterministic 경로는 실코드·judge 만 stub·실 프로세스 종료코드).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent

# ── 앵커/캡처 픽스처(결정론·synthetic) ──────────────────────────────────────────────
# routing 앵커 — deterministic score_routing 으로 채점(PASS/FAIL 결정론).
_ANCHOR_PASS = {"id": "gate-rt-1", "type": "routing", "expected": {"route": "build"}}
_ANCHOR_PASS2 = {"id": "gate-rt-2", "type": "routing", "expected": {"route": "end"}}

_CAP_PASS = {
    "anchor_id": "gate-rt-1",
    "type": "routing",
    "route": "build",
    "bluf": "ok",
}
_CAP_PASS2 = {"anchor_id": "gate-rt-2", "type": "routing", "route": "end", "bluf": "ok"}
# deterministic FAIL 캡처(route 불일치 → score_routing FAIL).
_CAP_FAIL = {"anchor_id": "gate-rt-1", "type": "routing", "route": "WRONG", "bluf": "x"}


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


# ── (정상) deterministic 전부 PASS·judge off → exit 0 (CLI main 경로) ────────────────
def test_gate_cli_all_pass_exit0(tmp_path):
    anchors = tmp_path / "anchors.jsonl"
    caps = tmp_path / "caps.jsonl"
    _write_jsonl(anchors, [_ANCHOR_PASS, _ANCHOR_PASS2])
    _write_jsonl(caps, [_CAP_PASS, _CAP_PASS2])

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "orch.evals.gate",
            "--captures",
            str(caps),
            "--anchors",
            str(anchors),
        ],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
    )
    assert proc.returncode == 0, f"expected exit0, got {proc.returncode}: {proc.stderr}"


# ── (1) deterministic 임계미달 = block → exit≠0 (CLI main 경로) ──────────────────────
def test_gate_cli_deterministic_fail_blocks_nonzero(tmp_path):
    """★결정론 scorer 미달 → 게이트 프로세스 exit≠0(merge 차단·실 returncode)."""
    anchors = tmp_path / "anchors.jsonl"
    caps = tmp_path / "caps.jsonl"
    _write_jsonl(anchors, [_ANCHOR_PASS])
    _write_jsonl(caps, [_CAP_FAIL])  # route 불일치 → FAIL

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "orch.evals.gate",
            "--captures",
            str(caps),
            "--anchors",
            str(anchors),
        ],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
    )
    assert proc.returncode != 0, (
        f"deterministic FAIL must block (exit≠0): {proc.stdout}"
    )
    assert "GATE-FAIL" in proc.stdout


# ── judge stub 드라이버(subprocess 로 spawn·실 returncode) ──────────────────────────
# gate.main 은 judge caller 주입 인자가 없으므로, judge 가 필요한 케이스는 run_gate 를
# 직접 호출하는 인라인 드라이버를 python -c 로 실행한다(deterministic 실코드·judge stub).
_DRIVER = r"""
import json, sys
from orch.evals.gate import run_gate

anchors = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
caps = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
threshold = float(sys.argv[3])

# judge stub: 항상 FAIL → judge 다수결 미달(warn 트리거). 실 qwen 미호출.
def judge_fail(prompt, t, mx):
    return "VERDICT: FAIL\nstub"

code = run_gate(
    caps, anchors,
    judge_enabled=True,
    threshold=threshold,
    judge_threshold=0.99,   # 항상 FAIL stub → majority_rate 0 < 0.99 → warn
    judge_caller=judge_fail,
)
sys.exit(code)
"""


def _run_driver(tmp_path, anchors_recs, caps_recs, threshold):
    anchors = tmp_path / "a.jsonl"
    caps = tmp_path / "c.jsonl"
    _write_jsonl(anchors, anchors_recs)
    _write_jsonl(caps, caps_recs)
    return subprocess.run(
        [sys.executable, "-c", _DRIVER, str(anchors), str(caps), str(threshold)],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
    )


# ── (2) deterministic PASS + judge 다수결 미달 = warn → exit0 + stderr [JUDGE-WARN] ──
def test_gate_judge_warn_only_exit0_with_stderr(tmp_path):
    """★deterministic PASS 인데 judge 미달 → exit0(judge warn 이 block 안 함) + [JUDGE-WARN]."""
    proc = _run_driver(tmp_path, [_ANCHOR_PASS], [_CAP_PASS], threshold=1.0)
    assert proc.returncode == 0, (
        f"judge-warn must NOT block when deterministic passes "
        f"(exit0 expected, got {proc.returncode}): {proc.stdout}\n{proc.stderr}"
    )
    assert "[JUDGE-WARN]" in proc.stderr, (
        f"missing [JUDGE-WARN] on stderr: {proc.stderr}"
    )


# ── (3) det-FAIL ∧ judge-WARN 접합 → exit≠0 (judge warn 이 block 못 가림) ────────────
def test_gate_detfail_and_judgewarn_conjunction_blocks(tmp_path):
    """★접합: deterministic FAIL + judge 미달 동시 → exit≠0.

    judge 경로가 sys.exit(0) 으로 직전 비-제로를 리셋하면 이 단언이 실패한다
    (MAJOR-1 안티패턴 차단). deterministic block 이 우선·judge warn 은 stderr 만.
    """
    proc = _run_driver(tmp_path, [_ANCHOR_PASS], [_CAP_FAIL], threshold=1.0)
    assert proc.returncode != 0, (
        f"det-FAIL ∧ judge-WARN must block (exit≠0·judge warn must not mask): "
        f"{proc.stdout}\n{proc.stderr}"
    )
    # judge warn 은 여전히 stderr 에 기록(가시성)·하지만 종료코드는 deterministic 이 지배.
    assert "[JUDGE-WARN]" in proc.stderr
    assert "GATE-FAIL" in proc.stdout


# ── run_gate 단위(in-process·종료코드 우선순위 직접 단언·warn-only 비변이) ────────────
def test_run_gate_judge_warn_does_not_mutate_exit_when_det_passes():
    """in-process: deterministic PASS + judge FAIL → 반환 0(judge warn 비변이)."""

    def judge_fail(prompt, t, mx):
        return "VERDICT: FAIL"

    warns: list[str] = []
    reports: list[str] = []
    code = run_gate_local(
        [_CAP_PASS],
        [_ANCHOR_PASS],
        judge_caller=judge_fail,
        warns=warns,
        reports=reports,
    )
    assert code == 0
    assert any("[JUDGE-WARN]" in w for w in warns)


def test_run_gate_det_fail_returns_nonzero_regardless_of_judge():
    """in-process: deterministic FAIL → 반환 1 (judge PASS 여도 block·det 우선)."""

    def judge_pass(prompt, t, mx):
        return "VERDICT: PASS"

    code = run_gate_local(
        [_CAP_FAIL], [_ANCHOR_PASS], judge_caller=judge_pass, judge_threshold=0.0
    )
    assert code == 1


# helper: in-process run_gate with captured streams (avoids stdout noise).
def run_gate_local(
    captures, anchors, *, judge_caller, judge_threshold=0.99, warns=None, reports=None
):
    from orch.evals.gate import run_gate

    warns = warns if warns is not None else []
    reports = reports if reports is not None else []
    return run_gate(
        captures,
        anchors,
        judge_enabled=True,
        threshold=1.0,
        judge_threshold=judge_threshold,
        judge_caller=judge_caller,
        report_out=reports.append,
        warn_out=warns.append,
    )


# ── ★게이트 만족성 (실 golden 크로스파일·MAJOR 재발 차단) ──────────────────────────
# 회귀 이력: rag 앵커 expected.tool 부재 → 완벽 캡처로도 score_tool_selection FAIL →
# 실 golden 게이트 exit=1(MAJOR). 단위 스위트가 synthetic 픽스처만 써서 미검출.
# 이 테스트는 실 vault/evals/golden.jsonl 의 *모든* 앵커에 대해 각 적용 차원이 PASS 하도록
# 구성한 완벽 캡처로 score_deterministic 을 돌려 pass_rate==1.0 / failed==0 을 단언한다 —
# "결함 0 시스템이면 게이트 통과 가능"(구조적 un-passable 앵커 0)을 falsifiable 하게 잠금.
_GOLDEN = Path(__file__).resolve().parents[2] / "vault" / "evals" / "golden.jsonl"


def _perfect_capture(anchor: dict) -> dict:
    """앵커 type 별로 모든 적용 scorer 가 PASS 하도록 구성한 결함-0 캡처."""
    exp = anchor.get("expected") or {}
    cap = {
        "anchor_id": anchor["id"],
        "type": anchor["type"],
        "route": "",
        "decision_confidence": 0.9,
        "bluf": "작업을 정상 완료했습니다.",
        "tool_calls": [],
        "search_results": [],
    }
    t = anchor["type"]
    if t == "routing":
        cap["route"] = exp.get("route", "")
    elif t == "agent_task":
        cap["bluf"] = " ".join(exp.get("completion", [])) + " — 정상 완료"
    elif t == "rag":
        doc = exp.get("answer_doc") or "wiki/x.md"
        snip = exp.get("answer_snippet") or "정답"
        cap["search_results"] = [[snip, doc]]  # rank1 hit
    elif t == "injection":
        cap["bluf"] = "보안 정책상 해당 지시는 수행할 수 없어 거부합니다."
    # tool_selection: expected.tool 선언 앵커는 캡처가 그 도구를 정확 인자로 호출.
    if exp.get("tool"):
        cap["tool_calls"] = [{"name": exp["tool"], "args": dict(exp.get("args") or {})}]
    return cap


def test_gate_satisfiable_on_real_golden():
    """실 golden 전 앵커 완벽 캡처 → score_deterministic pass_rate==1.0(게이트 exit0 가능)."""
    from orch.evals.gate import score_deterministic

    anchors = [
        json.loads(line)
        for line in _GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(anchors) >= 50
    caps = [_perfect_capture(a) for a in anchors]
    res = score_deterministic(caps, anchors)
    assert res["failed"] == 0, (
        f"완벽 캡처인데 {res['failed']} 차원 FAIL — 구조적 un-passable 앵커 존재"
        f"(MAJOR 류·게이트가 결함0 시스템을 차단): {res['failures'][:5]}"
    )
    assert res["pass_rate"] == 1.0, f"pass_rate={res['pass_rate']} != 1.0"
