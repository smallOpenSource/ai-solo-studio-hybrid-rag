"""워크플로 실행 리포트 생성기 — US-009 (T8), 수용 기준 14.

report.md 항목:
  - 워크플로명 · run_id · 실행 시각
  - 노드별: latency_ms · finish_reason · thinking 토큰(reasoning_chars)
  - 게이팅 결정(INTERRUPT_REQUESTED/RESOLVED 이벤트)
  - 총 소요(ms)

범용 집계 헬퍼는 core.report_common을 사용 (bench/report.py와 이중화 금지).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from office.db import DEFAULT_DB_PATH
from office.event_store import EventStore
from office.events import (
    InterruptRequested,
    InterruptResolved,
    NodeFailed,
    RunFinished,
    RunStarted,
)

_PROJECT_ROOT = Path(__file__).parent.parent
_REPORTS_DIR = _PROJECT_ROOT / "results" / "reports"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_run_report(
    run_id: str,
    state: dict[str, Any],
    db_path: str | Path | None = None,
) -> str:
    """run_id에 해당하는 이벤트를 DB에서 읽어 report.md 마크다운 문자열을 반환한다.

    Args:
        run_id  : 이번 실행 식별자.
        state   : engine.run()이 반환한 최종 상태 dict.
        db_path : EventStore DB 경로. None이면 기본값.

    Returns:
        report.md 마크다운 문자열.
    """
    store = EventStore(db_path or DEFAULT_DB_PATH)
    events = store.read(run_id)

    workflow_name: str = state.get("_workflow", "unknown")
    status: str = state.get("_workflow_status", "unknown")

    # 실행 시작/종료 시각
    run_started_ts = ""
    run_finished_ts = ""
    for ev in events:
        if isinstance(ev, RunStarted) and not run_started_ts:
            run_started_ts = ev.ts
        if isinstance(ev, RunFinished) and not run_finished_ts:
            run_finished_ts = ev.ts

    # state에서 노드별 메트릭 추출 (엔진이 _node_metrics에 누적, 수용 12)
    node_meta: dict[str, dict[str, Any]] = state.get("_node_metrics", {})

    # 게이팅 결정 이벤트
    interrupt_events: list[InterruptRequested] = [
        e for e in events if isinstance(e, InterruptRequested)
    ]
    resolved_events: list[InterruptResolved] = [
        e for e in events if isinstance(e, InterruptResolved)
    ]
    failed_events: list[NodeFailed] = [e for e in events if isinstance(e, NodeFailed)]

    # 총 소요 계산
    total_latency_ms: float = sum(m.get("latency_ms", 0.0) for m in node_meta.values())

    # ── 마크다운 생성 ──────────────────────────────────────────────────────────
    lines: list[str] = []
    lines.append(f"# 워크플로 실행 리포트: {workflow_name}")
    lines.append("")
    lines.append(f"- **run_id**: `{run_id}`")
    lines.append(f"- **status**: `{status}`")
    lines.append(f"- **시작**: {run_started_ts or 'N/A'}")
    lines.append(f"- **종료**: {run_finished_ts or 'N/A'}")
    lines.append(f"- **총 소요**: {total_latency_ms:.1f} ms")
    lines.append("")

    # 노드별 상세
    lines.append("## 노드별 실행 결과")
    lines.append("")
    lines.append("| 노드 | latency_ms | finish_reason | reasoning_chars | 비고 |")
    lines.append("|------|-----------|--------------|-----------------|------|")

    for node_id, meta in node_meta.items():
        lat = meta.get("latency_ms", 0.0)
        fr = meta.get("finish_reason", "N/A")
        rc = meta.get("reasoning_chars", 0)
        note = meta.get("note", "")
        lines.append(f"| {node_id} | {lat:.1f} | {fr} | {rc} | {note} |")

    if not node_meta:
        lines.append("| (노드 메타 없음) | - | - | - | - |")

    lines.append("")

    # 게이팅 결정
    if interrupt_events or resolved_events:
        lines.append("## 게이팅 결정")
        lines.append("")
        for ev in interrupt_events:
            lines.append(
                f"- **INTERRUPT_REQUESTED**: node=`{ev.node_id}`, tool=`{ev.tool}`, risk=`{ev.risk}`"
            )
        for ev in resolved_events:
            lines.append(
                f"- **INTERRUPT_RESOLVED**: node=`{ev.node_id}`, decision=`{ev.decision}`"
            )
        lines.append("")

    # 노드 실패
    if failed_events:
        lines.append("## 노드 실패")
        lines.append("")
        for ev in failed_events:
            lines.append(
                f"- node=`{ev.node_id}`, reason=`{ev.reason.value}`, detail={ev.detail}"
            )
        lines.append("")

    # thinking 양방향 요약 (수용 12)
    lines.append("## thinking 양방향 검증")
    lines.append("")
    for node_id, meta in node_meta.items():
        rc = meta.get("reasoning_chars", 0)
        thinking = meta.get("thinking", "off")
        expected = "reasoning_chars>0" if thinking == "on" else "reasoning_chars=0"
        ok = (
            "✓"
            if (thinking == "on" and rc > 0) or (thinking == "off" and rc == 0)
            else "✗"
        )
        lines.append(
            f"- {node_id}: thinking={thinking}, reasoning_chars={rc} → {expected} {ok}"
        )
    lines.append("")

    return "\n".join(lines)


def write_run_report(
    run_id: str,
    state: dict[str, Any],
    db_path: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> Path:
    """generate_run_report()를 파일로 저장하고 경로를 반환한다."""
    content = generate_run_report(run_id, state, db_path)
    out = Path(out_dir) if out_dir else _REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = out / f"{ts}_{run_id[:8]}_report.md"
    report_path.write_text(content, encoding="utf-8")
    return report_path
