"""리포트 공통 집계 유틸 — bench·office 공용(단일 진실).

JSONL 로드·지연 p50/p95·백분율·평균 같은 범용 표 집계 헬퍼만 모은다.
스위트별(toolcall/carryforward) 표 생성 로직은 각자(bench/report.py 등)에
남기고, 여기엔 명백히 범용인 것만 둔다.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 dict 목록으로 읽는다(빈 줄 무시)."""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def pct(n: int, total: int) -> str:
    """n/total을 백분율 문자열로(분모 0이면 N/A)."""
    if total == 0:
        return "N/A"
    return f"{100 * n / total:.1f}%"


def p50_p95(values: list[float]) -> tuple[str, str]:
    """지연 값 목록에서 p50(중앙값)·p95 문자열을 만든다."""
    if not values:
        return ("N/A", "N/A")
    s = sorted(values)
    p50 = statistics.median(s)
    idx95 = max(0, int(len(s) * 0.95) - 1)
    p95 = s[idx95]
    return (f"{p50:.0f}ms", f"{p95:.0f}ms")


def avg(values: list[float]) -> str:
    """평균 문자열(빈 목록이면 N/A)."""
    if not values:
        return "N/A"
    return f"{statistics.mean(values):.1f}"
