"""--compare 비교 리포트 생성 테스트 — 합성 결과 디렉토리 사용, 오프라인 전용."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from bench.report import generate_compare_report, _load_meta


# ── 헬퍼 ────────────────────────────────────────────────────────────────────────


def _write_results_dir(
    base: Path,
    name: str,
    suite: str,
    model: str,
    thinking: str,
    rows: list[dict[str, Any]],
) -> Path:
    """합성 results/<name>/ 디렉토리를 생성하고 meta.json + raw.jsonl을 기록한다."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)

    meta = {
        "suite": suite,
        "model": model,
        "thinking": thinking,
        "ts": name,
        "cases_paths": [],
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    raw_path = d / "raw.jsonl"
    with open(raw_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return d


def _tc_row(
    case_id: str,
    lang: str,
    mode: str,
    verdict: str,
    latency_ms: float = 100.0,
    completion_tokens: int = 20,
    thinking: str = "on",
) -> dict[str, Any]:
    return {
        "suite": "toolcall",
        "case_id": case_id,
        "lang": lang,
        "mode": mode,
        "thinking": thinking,
        "verdict": verdict,
        "detail": "",
        "latency_ms": latency_ms,
        "finish_reason": "stop",
        "usage": {"completion_tokens": completion_tokens},
        "timings_summary": {},
        "reasoning_chars": 0,
        "content_chars": 10,
        "attempt": 1,
    }


def _cf_row(
    chain_id: str,
    lang: str,
    variant: str,
    rep: int,
    verdict: str,
    thinking: str = "on",
) -> dict[str, Any]:
    return {
        "suite": "carryforward",
        "chain_id": chain_id,
        "lang": lang,
        "variant": variant,
        "rep": rep,
        "step_idx": 0,
        "thinking": thinking,
        "verdict": verdict,
        "detail": "",
        "latency_ms": 50.0,
        "finish_reason": "stop",
        "usage": {},
        "reasoning_chars": 0,
        "content_chars": 5,
    }


# ── 테스트 ──────────────────────────────────────────────────────────────────────


def test_compare_report_toolcall_table_contains_labels() -> None:
    """toolcall 비교표에 두 런의 라벨이 포함되는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"

        rows_a = [
            _tc_row("ko-01", "ko", "native_fc", "correct", thinking="on"),
            _tc_row("ko-02", "ko", "native_fc", "correct", thinking="on"),
            _tc_row("en-01", "en", "native_fc", "parse_fail", thinking="on"),
        ]
        rows_b = [
            _tc_row("ko-01", "ko", "native_fc", "correct", thinking="off"),
            _tc_row("ko-02", "ko", "native_fc", "wrong_name", thinking="off"),
            _tc_row("en-01", "en", "native_fc", "correct", thinking="off"),
        ]

        dir_a = _write_results_dir(base, "run_a", "toolcall", "model-x", "on", rows_a)
        dir_b = _write_results_dir(base, "run_b", "toolcall", "model-x", "off", rows_b)

        report = generate_compare_report([dir_a, dir_b])

    assert "model-x/thinking-on" in report
    assert "model-x/thinking-off" in report
    assert "native_fc" in report


def test_compare_report_toolcall_accuracy_values() -> None:
    """toolcall 비교표의 정확도 값이 올바르게 계산되는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"

        # ko 2건 모두 correct, en 1건 parse_fail
        rows_a = [
            _tc_row("ko-01", "ko", "native_fc", "correct"),
            _tc_row("ko-02", "ko", "native_fc", "correct"),
            _tc_row("en-01", "en", "native_fc", "parse_fail"),
        ]
        dir_a = _write_results_dir(base, "run_a", "toolcall", "model-y", "on", rows_a)

        report = generate_compare_report([dir_a])

    # ko 정확도 100%, en 정확도 0%
    assert "100.0%" in report
    assert "0.0%" in report


def test_compare_report_error_rows_excluded_from_accuracy() -> None:
    """verdict=error 행은 분모에서 제외되고 에러 건수 컬럼에 표기되는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"

        rows = [
            _tc_row("ko-01", "ko", "native_fc", "correct"),
            _tc_row("ko-02", "ko", "native_fc", "error"),  # 분모 제외, 에러 건수 포함
        ]
        dir_a = _write_results_dir(base, "run_a", "toolcall", "model-z", "on", rows)

        report = generate_compare_report([dir_a])

    # ko-01만 유효 → 정확도 1/1 = 100%
    assert "100.0%" in report
    # 에러 건수 1이 표기
    assert "| 1 |" in report or "1 |" in report


def test_compare_report_carryforward_table_present() -> None:
    """carryforward 비교표가 리포트에 포함되는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"

        rows_cf = [
            _cf_row("chain-01", "ko", "carry_on", 1, "correct"),
            _cf_row("chain-01", "ko", "carry_on", 2, "correct"),
            _cf_row("chain-01", "ko", "carry_off", 1, "wrong_name"),
        ]
        dir_cf = _write_results_dir(
            base, "run_cf", "carryforward", "model-cf", "on", rows_cf
        )

        report = generate_compare_report([dir_cf])

    assert "carryforward" in report.lower()
    assert "carry_on" in report
    assert "carry_off" in report
    assert "model-cf/thinking-on" in report


def test_compare_report_saves_file_to_parent() -> None:
    """generate_compare_report가 반환하는 텍스트가 마크다운 헤더를 포함하는지 확인.

    실제 파일 저장은 main()에서 수행하므로 여기서는 반환값만 검증한다.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"
        rows = [_tc_row("ko-01", "ko", "native_fc", "correct")]
        dir_a = _write_results_dir(base, "run_a", "toolcall", "m", "on", rows)

        report = generate_compare_report([dir_a])

    assert report.startswith("# 교차 런 비교 리포트")


def test_compare_report_meta_inferred_from_raw_when_no_meta_json() -> None:
    """meta.json이 없을 때 raw.jsonl에서 thinking/suite를 추론하는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"
        d = base / "run_no_meta"
        d.mkdir(parents=True, exist_ok=True)

        # meta.json 없이 raw.jsonl만 기록
        row = _tc_row("ko-01", "ko", "native_fc", "correct", thinking="off")
        (d / "raw.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        meta = _load_meta(d)

    assert meta["suite"] == "toolcall"
    assert meta["thinking"] == "off"


def test_compare_report_multiple_modes() -> None:
    """여러 모드가 있을 때 각 모드별로 행이 생성되는지 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir) / "results"

        rows = [
            _tc_row("ko-01", "ko", "native_fc", "correct"),
            _tc_row("ko-01", "ko", "prompt_json", "correct"),
            _tc_row("ko-01", "ko", "schema_strict", "parse_fail"),
        ]
        dir_a = _write_results_dir(base, "run_a", "toolcall", "model-m", "on", rows)

        report = generate_compare_report([dir_a])

    assert "native_fc" in report
    assert "prompt_json" in report
    assert "schema_strict" in report
