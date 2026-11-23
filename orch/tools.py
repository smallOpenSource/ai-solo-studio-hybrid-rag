"""orch.tools — REUSE the proven repo tools as graph-callable nodes.

The originals are UNCHANGED: we only inject the repo root on sys.path and import
them. ``office.business`` is a pure synchronous SQLite store (the baseline spike
ran ``BusinessStore.get_customer`` with ZERO edits); ``office.knowledge_client``
is the cross-env HTTP search client (httpx-only, delegates to the knowledge
service). Both are wrapped here as thin functions a LangGraph node can call.

Per Phase B: "wrap REUSE tools as nodes ... Originals untouched." So this module
does NOT modify office/; it adapts to its existing constructor/signature:
  * BusinessStore(db_path=...).get_customer(customer_id) -> dict | None
  * knowledge_client.search(query, k=...) -> {"results": [...], "count": int}
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Inject the repo root so the UNCHANGED office.* modules import cleanly from the
# isolated orch env (sys.path insert only — no edits to office/).
_REPO_ROOT = Path(__file__).resolve().parents[1]  # /opt/studio
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def business_get_customer(
    customer_id: str, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    """REUSE office.business.BusinessStore.get_customer (pure, unchanged).

    Args:
        customer_id: CRM customer id to look up.
        db_path:     SQLite path (tests inject an isolated tmp db). None => the
                     store's default state/business.db.

    Returns:
        The customer row as a dict, or None if not found. (No new behavior — this
        is a direct pass-through to the unchanged store method.)
    """
    from office.business import BusinessStore  # imported lazily; original untouched

    store = BusinessStore(db_path=db_path)
    return store.get_customer(customer_id)


def knowledge_search(
    query: str, k: int = 5, transport: Any | None = None
) -> dict[str, Any]:
    """REUSE office.knowledge_client.search (cross-env HTTP client, unchanged).

    Args:
        query:     search query.
        k:         max results (default 5).
        transport: optional httpx transport for tests (in-process / mock). None
                   hits the live knowledge service.

    Returns:
        {"results": [{"text", "source", "score"}, ...], "count": int}. Raises
        httpx.HTTPError on a down/unreachable service (connect=5s fast-fail), per
        the original client contract.
    """
    from office.knowledge_client import search as _search  # lazy; original untouched

    return _search(query, k=k, transport=transport)
