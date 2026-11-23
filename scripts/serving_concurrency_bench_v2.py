"""서빙 동시성 실측 v2 — closed-loop 시간박스(정상상태 처리량).

v1(burst+gather, K=6)의 ramp-up/ramp-down 아티팩트를 제거한다(적대검증 지적).
각 동시성 레벨 C에서 정확히 C개의 워커가 DURATION초 동안 요청을 back-to-back로
연속 발사 → 워커가 항상 바쁜 정상상태(steady-state) depth-C 처리량을 측정.

판정: c=2 정상상태 처리량 / c=1 ≈ ? (v1의 1.35x가 아티팩트면 1.0~1.15로 내려올 것)
바운드·자원안전: 시간박스(무한루프 아님·deadline 종료)·요청별 read timeout 120s·
전체는 safe_run.sh가 외부 가드.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import get_llm_api_key, get_llm_base_url, load_env  # noqa: E402

MODEL = "gemma-4-e4b-it"
MAX_TOKENS = 256
TEMPERATURE = 0.0
ENABLE_THINKING = False
READ_TIMEOUT = 120.0
DURATION_S = 18.0  # 각 레벨 정상상태 측정창
# (level, label) — c1 기준, c2(사용자 결정), c3, c2 재현(변동 확인)
RUN_PLAN = [(1, "c1"), (2, "c2"), (3, "c3"), (2, "c2_repeat")]

PROMPTS = [
    "신작 모바일 서바이버라이크 게임의 7일차 리텐션을 올릴 구체적 방법 5가지를 한국어로 설명해줘.",
    "Explain in detail how continuous batching improves LLM serving throughput on a single GPU.",
    "스타트업 1인 기업가가 분기 KPI를 설정할 때 흔히 저지르는 실수 5가지를 설명해줘.",
    "Describe a robust at-most-once email delivery design for an idempotent notification worker.",
    "도트 그래픽 픽셀 아트 캐릭터의 8방향 워크 애니메이션 제작 워크플로를 단계별로 설명해줘.",
    "Compare vector RAG with graph RAG for small local models and recommend one with reasons.",
]


async def one_call(client, url, headers, prompt, phase_t0):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
    }
    start = time.perf_counter()
    try:
        resp = await client.post(url, headers=headers, json=body)
        end = time.perf_counter()
        resp.raise_for_status()
        data = resp.json()
        t = data.get("timings") or {}
        usage = data.get("usage") or {}
        return {
            "ok": True,
            "start_s": start - phase_t0,
            "end_s": end - phase_t0,
            "wall_ms": (end - start) * 1000.0,
            "predicted_n": t.get("predicted_n") or usage.get("completion_tokens") or 0,
            "predicted_per_second": t.get("predicted_per_second"),
            "predicted_ms": t.get("predicted_ms"),
            "prompt_ms": t.get("prompt_ms"),
        }
    except Exception as ex:  # noqa: BLE001
        return {
            "ok": False,
            "wall_ms": (time.perf_counter() - start) * 1000.0,
            "error": f"{type(ex).__name__}: {str(ex)[:160]}",
        }


async def run_level(client, url, headers, concurrency, label):
    phase_t0 = time.perf_counter()
    deadline = phase_t0 + DURATION_S
    results: list[dict] = []
    counter = {"i": 0}

    async def worker():
        while time.perf_counter() < deadline:
            idx = counter["i"]
            counter["i"] += 1
            prompt = PROMPTS[idx % len(PROMPTS)]
            results.append(await one_call(client, url, headers, prompt, phase_t0))

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    elapsed = time.perf_counter() - phase_t0

    ok = [r for r in results if r.get("ok")]
    tot = sum(r["predicted_n"] for r in ok)
    walls = sorted(r["wall_ms"] for r in ok)
    pps = [r["predicted_per_second"] for r in ok if r.get("predicted_per_second")]

    def pct(xs, q):
        if not xs:
            return 0.0
        s = sorted(xs)
        return s[max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))]

    thru = tot / elapsed if elapsed else 0
    summ = {
        "label": label,
        "concurrency": concurrency,
        "elapsed_s": round(elapsed, 2),
        "n_ok": len(ok),
        "n_fail": len(results) - len(ok),
        "total_tokens": tot,
        "steady_throughput_tok_s": round(thru, 1),
        "wall_ms_p50": round(pct(walls, 0.5), 0),
        "wall_ms_p95": round(pct(walls, 0.95), 0),
        "decode_per_req_tok_s_mean": round(statistics.mean(pps), 1) if pps else 0,
        "results": results,
    }
    print(
        f"[{label:<10}] C={concurrency} ok={len(ok)} fail={summ['n_fail']} "
        f"elapsed={elapsed:.1f}s tok={tot} steady_thru={thru:.1f}tok/s "
        f"lat_p50={summ['wall_ms_p50']:.0f}ms decode/req={summ['decode_per_req_tok_s_mean']:.0f}",
        flush=True,
    )
    return summ


async def main():
    env = load_env()
    base = get_llm_base_url(env).rstrip("/")
    key = get_llm_api_key(env)
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
    timeout = httpx.Timeout(connect=10.0, read=READ_TIMEOUT, write=10.0, pool=10.0)

    phases = []
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        print("warmup...", flush=True)
        await asyncio.gather(
            *[
                one_call(client, url, headers, "Reply: ready", time.perf_counter())
                for _ in range(2)
            ]
        )
        for c, label in RUN_PLAN:
            phases.append(await run_level(client, url, headers, c, label))
            await asyncio.sleep(1.0)

    base_thru = (
        next((p["steady_throughput_tok_s"] for p in phases if p["label"] == "c1"), 1)
        or 1
    )
    base_lat = next((p["wall_ms_p50"] for p in phases if p["label"] == "c1"), 1) or 1
    print("\n=== STEADY-STATE ANALYSIS (c=1 기준) ===", flush=True)
    analysis = []
    for p in phases:
        a = {
            "label": p["label"],
            "concurrency": p["concurrency"],
            "steady_thru": p["steady_throughput_tok_s"],
            "speedup_vs_c1": round(p["steady_throughput_tok_s"] / base_thru, 2),
            "lat_p50_ms": p["wall_ms_p50"],
            "lat_inflation_vs_c1": round(p["wall_ms_p50"] / base_lat, 2),
            "decode_per_req": p["decode_per_req_tok_s_mean"],
            "n_fail": p["n_fail"],
        }
        analysis.append(a)
        print(
            f"  C={a['concurrency']:<2}[{a['label']:<10}] steady={a['steady_thru']:>7.1f}tok/s "
            f"speedup={a['speedup_vs_c1']:>4}x lat_p50={a['lat_p50_ms']:>7.0f}ms "
            f"inflation={a['lat_inflation_vs_c1']:>4}x decode/req={a['decode_per_req']:>6.0f} fail={a['n_fail']}",
            flush=True,
        )

    out = {
        "model": MODEL,
        "method": "closed-loop time-boxed",
        "duration_s": DURATION_S,
        "max_tokens": MAX_TOKENS,
        "enable_thinking": ENABLE_THINKING,
        "phases": phases,
        "analysis": analysis,
    }
    out_dir = Path(__file__).resolve().parent.parent / "results" / "serving_concurrency"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measurement_v2.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nsaved: {out_dir / 'measurement_v2.json'}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
