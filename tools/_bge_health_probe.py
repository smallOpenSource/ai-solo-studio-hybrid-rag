"""1-shot bge health probe (harness·미커밋 후보). 단일 search_dense 왕복 시간 측정.

healthy(~12s) → 형식 C2 풀런 가치 / degraded(>30s·timeout) → 정직 이연 유지.
사용: timeout -k 5 45 /opt/conda/envs/one_man_corp-knowledge/bin/python tools/_bge_health_probe.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from knowledge.store import KnowledgeStore  # noqa: E402


def main() -> int:
    t0 = time.time()
    store = KnowledgeStore()
    hits = store.search_dense("game retention", top_n=1)
    dt = time.time() - t0
    verdict = "BGE_HEALTHY" if dt <= 18.0 else "BGE_DEGRADED"
    print(f"{verdict} elapsed={dt:.1f}s hits={len(hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
