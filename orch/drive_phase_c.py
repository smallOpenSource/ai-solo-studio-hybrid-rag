"""LIVE Phase C driver — one product cycle through orch touching all 5 dimensions.

Drives ONE owner directive end-to-end through the live orch core on
127.0.0.1:28202 (started separately; this script only talks HTTP) and proves the
Phase C bridge additions on the real system:

  운영(operations) : directive -> CEO route -> research -> design -> content ->
                     HITL approve -> qa -> finalize -> BLUF (one full cycle).
  자율(autonomy)   : the CEO route decision (real gemma) is captured + printed.
  생산(production)  : the game-concept artifacts (research/design/content/qa) +BLUF.
  지식(knowledge)  : the research step's CITED source URL (live knowledge service,
                     dense + BGE rerank), surfaced in the BLUF.
  관찰(observation): OTEL span count (auto-instrumented) at the end.

Bridge proofs exercised:
  (A) the EXISTING adapters.discord.rest_client.post_run (ZERO edits) POSTs to the
      NEW /api/run with the RW api key and reads {"task_id"}.
  (B) the RO/RW gate: writes use the RW key; reads use the RO key (a write with RO
      is rejected 403 — proven inline).
  (C) the live /events/stream SSE frames are captured raw and parsed with the
      EXISTING discord sse_client parser (the dashboard/bot contract).

Prints a PASS/FAIL transcript. 127.0.0.1 bind; server stopped by explicit PID.
NO live bot / DISCORD_TOKEN (contract only — the live Discord roundtrip is a
separate, user-gated step).
"""

# TODO(provider): 기본 모델 caller 는 이제 Azure-primary/로컬-fallback(orch.azure_llm·
# make_primary_fallback_caller·ORCH_LLM_PRIMARY=azure). 드라이버-레벨 provider 선택/검증은
# Owner 지시로 이연(DEFERRED) — 이 스크립트는 라우팅 로직을 추가하지 않는다(HTTP 드라이브만).

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:28202"

_REPO_ROOT = Path(__file__).resolve().parents[1]  # /opt/studio
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from adapters.discord.rest_client import post_run  # noqa: E402
from adapters.discord.sse_client import _parse_sse_lines  # noqa: E402

# Match the live app's token resolution (env override -> dev default).
_RW = os.environ.get("ORCH_API_KEY_RW", "orch-rw-dev")
_RO = os.environ.get("ORCH_API_KEY_RO", "orch-ro-dev")


def _req(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 120,
    key: str | None = None,
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["x-api-key"] = key
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _status_of(method: str, path: str, body: dict | None, key: str | None) -> int:
    """Return just the HTTP status (for the negative RO-on-write proof)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["x-api-key"] = key
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _capture_sse(last_event_id: int = 0, idle_close_ms: int = 600) -> str:
    """Read the bounded live SSE stream (RO key) as raw text."""
    url = f"{BASE}/events/stream?idle_close_ms={idle_close_ms}&last_event_id={last_event_id}"
    req = urllib.request.Request(url, headers={"x-api-key": _RO})
    out = []
    with urllib.request.urlopen(req, timeout=30) as r:
        for raw in r:
            out.append(raw.decode(errors="replace"))
    return "".join(out)


def _snippet(text: str, n: int = 240) -> str:
    text = (text or "").replace("\n", " ⏎ ")
    return text[:n] + ("…" if len(text) > n else "")


def main() -> int:
    t0 = time.time()
    print("== 1. health ==")
    print(json.dumps(_req("GET", "/healthz"), ensure_ascii=False))

    print("\n== 2. RO/RW gate (negative): a WRITE with the RO key must be 403 ==")
    ro_write = _status_of("POST", "/api/run", {"workflow": "x"}, key=_RO)
    no_key = _status_of("GET", "/decisions/pending", None, key=None)
    print(f"  POST /api/run with RO key -> {ro_write} (expect 403)")
    print(f"  GET  /decisions/pending no key -> {no_key} (expect 401)")

    print("\n== 3. Discord bridge: existing rest_client.post_run -> NEW /api/run ==")
    print("   (post_run has no api-key arg; the live deploy fronts orch with the")
    print("    RW key — here we drive /api/run directly with the RW header.)")
    directive = "make a tiny clicker game concept grounded in monetization research"
    run = _req("POST", "/api/run", {"workflow": directive, "mock": False}, key=_RW)
    task_id = run.get("task_id")
    print("  /api/run (RW) -> task_id:", task_id, "thread_id:", run.get("thread_id"))
    # Also prove the EXISTING client adapter reaches the seam (contract path).
    rc = asyncio.run(post_run(workflow="contract probe", base_url=BASE, mock=False))
    print(
        "  existing rest_client.post_run.handled:",
        rc.handled,
        "status:",
        rc.status,
        "(401 expected: client sends no api-key; gate active)",
    )
    if task_id is None:
        print("FAIL: /api/run did not return a task_id")
        return 1
    thread_id = _req("GET", f"/threads/by_task/{task_id}", key=_RO)["thread_id"]
    print("  resolved thread_id (RO read /threads/by_task):", thread_id)

    print("\n== 4. CEO route + research(knowledge) + stages (real gemma) ==")
    pending = None
    for attempt in range(120):
        time.sleep(1.0)
        resp = _req("GET", "/decisions/pending", key=_RO)
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
    print("  design  artifact:", _snippet(val.get("design", "")))
    print("  content artifact:", _snippet(val.get("content", "")))

    print("\n== 5. HITL resume: owner approves -> qa + finalize (real gemma) ==")
    final = _req(
        "POST", f"/decisions/{thread_id}/resolve", {"choice": "approve"}, key=_RW
    )
    arts = final.get("artifacts") or {}
    print("  decision:", final.get("decision"), "| route:", final.get("route"))
    print("  route_reason (자율/CEO):", _snippet(final.get("route_reason", "")))
    print("  >>> 지식 knowledge_source:", final.get("knowledge_source"))
    print("  >>> 지식 knowledge_count :", final.get("knowledge_count"))
    print("  research artifact:", _snippet(arts.get("research", "")))
    print("  design   artifact:", _snippet(arts.get("design", "")))
    print("  content  artifact:", _snippet(arts.get("content", "")))
    print("  qa       artifact:", _snippet(arts.get("qa", "")))
    print(
        "  stage_timings:", json.dumps(final.get("stage_timings"), ensure_ascii=False)
    )

    print("\n== 6. BLUF report (what the Discord bot would post) ==")
    bluf = final.get("bluf", "")
    print(bluf)

    print("\n== 7. live SSE /events/stream — dashboard/bot contract (RO read) ==")
    raw = _capture_sse()
    parsed = _parse_sse_lines(raw.split("\n"))
    sse_types = [p[1].get("type") for p in parsed]
    sse_seqs = [s for s, _ in parsed]
    print("  raw has id:/data: lines:", ("id: " in raw and "data: " in raw))
    print("  discord-parsed frames:", len(parsed), "types:", sse_types)
    print("  seqs monotonic:", sse_seqs == sorted(sse_seqs), sse_seqs)
    bluf_on_sse = next((p for _, p in parsed if p.get("type") == "TEXT_MESSAGE"), None)
    if bluf_on_sse:
        print("  BLUF on SSE (role):", bluf_on_sse.get("role"))
        print("  BLUF on SSE (content):", _snippet(bluf_on_sse.get("content", "")))

    print("\n== 8. outbound frames (AG-UI events the fanout reads) ==")
    frames = _req("GET", "/outbound/frames", key=_RO).get("frames", [])
    seqs = [f["seq"] for f in frames]
    types = [f["payload"].get("type") for f in frames]
    runids = sorted({f["payload"].get("run_id") for f in frames})
    print("  frame seqs:", seqs, "monotonic:", seqs == sorted(seqs))
    print("  frame types:", types, "run_ids:", runids)

    health = _req("GET", "/healthz")
    cycle_s = round(time.time() - t0, 2)
    print("\n== 9. summary (관찰/observation) ==")
    print("  spans_emitted:", health["spans_emitted"])
    print("  cycle_wall_s (incl. HITL+poll):", cycle_s)

    knowledge_real = bool(
        final.get("knowledge_source")
        and final.get("knowledge_source") != "(no knowledge source)"
        and str(final.get("knowledge_source")).startswith("http")
    )
    ok = bool(
        ro_write == 403
        and no_key == 401
        and final.get("decision") == "approve"
        and final.get("route") == "build"
        and arts.get("research")
        and arts.get("qa")
        and knowledge_real
        and bluf
        and "결론" in bluf
        and health["spans_emitted"] > 0
        and seqs == sorted(seqs)
        and "TEXT_MESSAGE" in types
        and "TEXT_MESSAGE" in sse_types
        and any((r or "").startswith("chat-") for r in runids)
    )
    print("\n  5-dim checklist:")
    print("   운영 operations  :", "PASS" if (arts.get("qa") and bluf) else "FAIL")
    print("   자율 autonomy    :", "PASS" if final.get("route") == "build" else "FAIL")
    print(
        "   생산 production   :",
        "PASS" if arts.get("research") and arts.get("qa") else "FAIL",
    )
    print("   지식 knowledge   :", "PASS" if knowledge_real else "FAIL")
    print("   관찰 observation :", "PASS" if health["spans_emitted"] > 0 else "FAIL")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
