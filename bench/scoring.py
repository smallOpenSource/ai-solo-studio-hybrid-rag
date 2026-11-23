"""채점기 — core.scoring으로 승격됨(재export, 무중단).

실제 구현은 core/scoring.py에 있다. 기존 공개 이름은 전부 여기서 그대로
재노출하여 bench.scoring을 import 하던 코드·테스트가 무변경으로 동작한다.
"""

from __future__ import annotations

from core.scoring import (
    _values_equal,
    lenient_extract_json,
    score,
)

__all__ = [
    "_values_equal",
    "lenient_extract_json",
    "score",
]
