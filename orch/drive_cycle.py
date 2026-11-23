"""LIVE 1-cycle driver (real gemma) — Phase B acceptance evidence.

Drives ONE owner directive end-to-end through the live orch core on
127.0.0.1:28202 (started separately; this script only talks HTTP):

  directive -> CEO route (real gemma) -> 3 real-gemma stages
  -> ONE HITL approval gate INSIDE the cycle (interrupt -> resolve -> resume,
     single process) -> BLUF report.

Two bridges are exercised:
  (A) the EXISTING repo client adapters.discord.rest_client.post_run (ZERO edits,
      imported via sys.path) POSTs to the NEW /api/run and reads {"task_id"}.
  (B) urllib drives the HITL pause/resume + fetches the final BLUF and the
      outbound frames (what the Discord bridge would post).

Prints a PASS/FAIL transcript + span count + wall time. NO live bot / DISCORD_TOKEN
(contract only). The server binds 127.0.0.1 and is stopped by explicit PID.
"""

# TODO(provider): 기본 모델 caller 는 이제 Azure-primary/로컬-fallback(orch.azure_llm·
# make_primary_fallback_caller·ORCH_LLM_PRIMARY=azure). 드라이버-레벨 provider 선택/검증은
# Owner 지시로 이연(DEFERRED) — 이 스크립트는 라우팅 로직을 추가하지 않는다(HTTP 드라이브만).

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:28202"

_REPO_ROOT = Path(__file__).resolve().parents[1]  # /opt/studio
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from adapters.discord.rest_client import post_run  # noqa: E402


def _req(method: str, path: str, body: dict | None = None, timeout: float = 90) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _snippet(text: str, n: int = 220) -> str:
    text = (text or "").replace("\n", " ⏎ ")
    return text[:n] + ("…" if len(text) > n else "")


def main() -> int:
    t0 = time.time()
    print("== 1. health ==")
    print(json.dumps(_req("GET", "/healthz"), ensure_ascii=False))

    print("\n== 2. Discord bridge: existing rest_client.post_run -> NEW /api/run ==")
    directive = "make a tiny clicker game concept"
    run_result = asyncio.run(post_run(workflow=directive, base_url=BASE, mock=False))
    print(
        "  post_run.handled:",
        run_result.handled,
        "status:",
        run_result.status,
        "task_id:",
        run_result.task_id,
    )
    if not run_result.handled or run_result.task_id is None:
        print(
            "FAIL: existing rest_client could not drive the new seam:", run_result.error
        )
        return 1
    resolved = _req("GET", f"/threads/by_task/{run_result.task_id}")
    thread_id = resolved["thread_id"]
    print("  resolved thread_id (via /threads/by_task):", thread_id)

    print("\n== 3. CEO route + stages (real gemma); poll for HITL gate ==")
    pending = None
    for attempt in range(90):
        time.sleep(1.0)
        resp = _req("GET", "/decisions/pending")
        match = [p for p in resp["pending"] if p["thread_id"] == thread_id]
        if match:
            pending = match[0]
            print(f"  (attempt {attempt}) HITL interrupt surfaced")
            break
    if not pending:
        print("FAIL: HITL gate never surfaced")
        return 1
    val = pending["value"]
    print("  question :", val.get("question"))
    print("  options  :", val.get("options"), "confidence:", val.get("confidence"))
    print("  next     :", pending["next"])
    print("  design  artifact:", _snippet(val.get("design", "")))
    print("  content artifact:", _snippet(val.get("content", "")))

    print("\n== 4. HITL resume: owner approves -> qa + finalize (real gemma) ==")
    final = _req("POST", f"/decisions/{thread_id}/resolve", {"choice": "approve"})
    print(
        "  decision:",
        final.get("decision"),
        "| route:",
        final.get("route"),
        "| model:",
        final.get("model"),
        "| run_id:",
        final.get("run_id"),
    )
    print("  route_reason:", _snippet(final.get("route_reason", "")))
    print("  qa artifact :", _snippet((final.get("artifacts") or {}).get("qa", "")))
    print(
        "  stage_timings:", json.dumps(final.get("stage_timings"), ensure_ascii=False)
    )

    print("\n== 5. BLUF report (what the Discord bot would post) ==")
    bluf = final.get("bluf", "")
    print(bluf)

    print("\n== 6. outbound frames (AG-UI events the fanout reads) ==")
    frames = _req("GET", "/outbound/frames").get("frames", [])
    seqs = [f["seq"] for f in frames]
    types = [f["payload"].get("type") for f in frames]
    runids = sorted({f["payload"].get("run_id") for f in frames})
    print("  frame seqs:", seqs, "monotonic:", seqs == sorted(seqs))
    print("  frame types:", types)
    print("  run_ids:", runids)

    health = _req("GET", "/healthz")
    cycle_s = round(time.time() - t0, 2)
    print("\n== 7. summary ==")
    print("  spans_emitted:", health["spans_emitted"])
    print("  cycle_wall_s (incl. HITL+poll):", cycle_s)

    ok = bool(
        run_result.handled
        and final.get("decision") == "approve"
        and final.get("route") == "build"
        and (final.get("artifacts") or {}).get("qa")
        and bluf
        and "결론" in bluf
        and health["spans_emitted"] > 0
        and seqs == sorted(seqs)
        and "TEXT_MESSAGE" in types
        and any((r or "").startswith("chat-") for r in runids)
    )
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
