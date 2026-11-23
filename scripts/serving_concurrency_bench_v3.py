"""서빙 동시성 실측 v3 — 실 워크로드(thinking-ON·자연EOS) + 다중trial CI + 스왑비용.

v2(thinking-OFF·256 cap·단일trial)의 한계를 보강(적대검증 권고):
- thinking ON(직원 대화/추론 실모드) + max_tokens=1024(자연 EOS, predicted_n 실측)
- 각 동시성 레벨 TRIALS회 반복 → 평균±표준편차(CI) — GPU 경합 변동 정량화
- c sweep [1,2,3] — c=2 미포화 천장 확인
- gemma↔qwen cross-model 스왑 비용 micro-bench(직원=gemma·코딩=qwen 동시 가능성 판정)

바운드·자원안전: closed-loop 시간박스(무한루프 아님)·요청별 read timeout 180s·
safe_run.sh 외부 가드. 스왑테스트는 N회 교대(바운드).
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

GEMMA = "gemma-4-e4b-it"
QWEN = "qwen3.6-35b-a3b"
MAX_TOKENS = 1024  # 자연 EOS 허용(실측 predicted_n)
TEMPERATURE = 0.0
READ_TIMEOUT = 180.0
DURATION_S = 20.0
LEVELS = [1, 2, 3]
TRIALS = 3
SWAP_ALTERNATIONS = 4  # gemma→qwen→gemma→qwen ... (각 전환의 콜드 스왑 비용)

# 실 직원 워크로드 모사 — 추론/설계가 필요한 긴 답변 유도(thinking ON)
PROMPTS = [
    "신작 모바일 서바이버라이크 게임의 7일차 리텐션을 30% 올릴 라이브옵스 전략을 단계별 근거와 함께 자세히 설계해줘.",
    "Design in depth a fault-tolerant at-most-once email delivery pipeline for an idempotent notification worker; cover failure modes, retries, and dedup.",
    "1인 AI 기업의 분기 OKR 체계를 직무별(기획·개발·마케팅·CS)로 구체적 지표와 함께 설계하고 흔한 실패모드를 분석해줘.",
    "Explain step by step how continuous batching and paged KV-cache interact on a single GPU, and when adding concurrency stops helping throughput.",
    "도트 픽셀 아트 캐릭터 8방향 워크 애니메이션을 LPC 스프라이트시트로 만드는 전체 파이프라인을 도구·해상도·프레임수까지 구체적으로 설명해줘.",
    "Compare vector RAG, hybrid RAG, and graph RAG for a small local 4B model on Korean corpora; recommend an architecture with detailed reasoning.",
]


async def one_call(client, url, headers, model, prompt, phase_t0, thinking):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "chat_template_kwargs": {"enable_thinking": thinking},
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
            "model": model,
            "start_s": start - phase_t0,
            "end_s": end - phase_t0,
            "wall_ms": (end - start) * 1000.0,
            "predicted_n": t.get("predicted_n") or usage.get("completion_tokens") or 0,
            "predicted_ms": t.get("predicted_ms"),
            "predicted_per_second": t.get("predicted_per_second"),
            "prompt_ms": t.get("prompt_ms"),
            "server_compute_ms": (t.get("prompt_ms") or 0)
            + (t.get("predicted_ms") or 0),
        }
    except Exception as ex:  # noqa: BLE001
        return {
            "ok": False,
            "model": model,
            "wall_ms": (time.perf_counter() - start) * 1000.0,
            "error": f"{type(ex).__name__}: {str(ex)[:160]}",
        }


async def run_level(client, url, headers, concurrency, label):
    phase_t0 = time.perf_counter()
    deadline = phase_t0 + DURATION_S
    results: list[dict] = []
    ctr = {"i": 0}

    async def worker():
        while time.perf_counter() < deadline:
            i = ctr["i"]
            ctr["i"] += 1
            results.append(
                await one_call(
                    client,
                    url,
                    headers,
                    GEMMA,
                    PROMPTS[i % len(PROMPTS)],
                    phase_t0,
                    True,
                )
            )

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    elapsed = time.perf_counter() - phase_t0
    ok = [r for r in results if r.get("ok")]
    tot = sum(r["predicted_n"] for r in ok)
    walls = [r["wall_ms"] for r in ok]
    preds = [r["predicted_n"] for r in ok]
    thru = tot / elapsed if elapsed else 0

    def p50(xs):
        s = sorted(xs)
        return s[len(s) // 2] if s else 0

    summ = {
        "label": label,
        "concurrency": concurrency,
        "elapsed_s": round(elapsed, 2),
        "n_ok": len(ok),
        "n_fail": len(results) - len(ok),
        "total_tokens": tot,
        "steady_throughput_tok_s": round(thru, 1),
        "wall_ms_p50": round(p50(walls), 0),
        "pred_n_mean": round(statistics.mean(preds), 0) if preds else 0,
        "pred_n_min": min(preds) if preds else 0,
        "pred_n_max": max(preds) if preds else 0,
        "results": results,
    }
    print(
        f"  [{label:<8}] C={concurrency} ok={len(ok)} fail={summ['n_fail']} "
        f"thru={thru:.1f}tok/s lat_p50={summ['wall_ms_p50']:.0f}ms "
        f"out_tok_mean={summ['pred_n_mean']:.0f}(min{summ['pred_n_min']}-max{summ['pred_n_max']})",
        flush=True,
    )
    return summ


async def swap_test(client, url, headers):
    """gemma↔qwen 교대 호출 — 각 전환의 wall vs server_compute 갭 = 콜드 스왑 비용."""
    print("\n=== cross-model swap cost (gemma <-> qwen) ===", flush=True)
    seq = []
    for k in range(SWAP_ALTERNATIONS):
        model = GEMMA if k % 2 == 0 else QWEN
        r = await one_call(
            client,
            url,
            headers,
            model,
            "Reply with one short sentence about teamwork.",
            time.perf_counter(),
            True,
        )
        gap = (
            (r.get("wall_ms", 0) - (r.get("server_compute_ms") or 0))
            if r.get("ok")
            else None
        )
        seq.append(
            {
                "k": k,
                "model": model,
                "ok": r.get("ok"),
                "wall_ms": round(r.get("wall_ms", 0), 0),
                "server_compute_ms": round(r.get("server_compute_ms") or 0, 0),
                "swap_overhead_ms": round(gap, 0) if gap is not None else None,
                "error": r.get("error"),
            }
        )
        tag = "SWAP" if k > 0 else "first"
        print(
            f"  [{tag}] -> {model:<18} wall={seq[-1]['wall_ms']:.0f}ms "
            f"compute={seq[-1]['server_compute_ms']:.0f}ms overhead={seq[-1]['swap_overhead_ms']}ms "
            f"{'ERR:' + r.get('error', '') if not r.get('ok') else ''}",
            flush=True,
        )
    return seq


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
        print("warmup (thinking ON, cold load 흡수)...", flush=True)
        await one_call(
            client, url, headers, GEMMA, "Reply: ready", time.perf_counter(), True
        )
        print(
            f"thinking=ON max_tokens={MAX_TOKENS} dur={DURATION_S}s trials={TRIALS} levels={LEVELS}\n",
            flush=True,
        )
        for c in LEVELS:
            for tr in range(TRIALS):
                phases.append(
                    await run_level(client, url, headers, c, f"c{c}_t{tr + 1}")
                )
                await asyncio.sleep(1.0)
        swap = await swap_test(client, url, headers)

    # 레벨별 trial 집계 (평균±표준편차)
    print("\n=== AGGREGATE (thinking ON, mean ± stdev over trials) ===", flush=True)
    agg = []
    base_thru = None
    for c in LEVELS:
        ph = [p for p in phases if p["concurrency"] == c]
        thrus = [p["steady_throughput_tok_s"] for p in ph]
        lats = [p["wall_ms_p50"] for p in ph]
        mean_thru = statistics.mean(thrus)
        sd_thru = statistics.pstdev(thrus) if len(thrus) > 1 else 0.0
        if c == LEVELS[0]:
            base_thru = mean_thru or 1
        a = {
            "concurrency": c,
            "n_trials": len(ph),
            "throughput_mean": round(mean_thru, 1),
            "throughput_stdev": round(sd_thru, 1),
            "throughput_trials": thrus,
            "speedup_vs_c1": round(mean_thru / base_thru, 2),
            "lat_p50_mean_ms": round(statistics.mean(lats), 0),
            "lat_p50_trials_ms": lats,
        }
        agg.append(a)
        print(
            f"  C={c}: thru={a['throughput_mean']}±{a['throughput_stdev']} tok/s "
            f"(trials {thrus}) speedup={a['speedup_vs_c1']}x lat_p50_mean={a['lat_p50_mean_ms']:.0f}ms",
            flush=True,
        )

    swap_overheads = [
        s["swap_overhead_ms"]
        for s in swap
        if s["k"] > 0 and s.get("swap_overhead_ms") is not None
    ]
    print(f"\nswap overhead (전환 시): {swap_overheads} ms", flush=True)

    out = {
        "method": "v3 closed-loop thinking-ON multi-trial + swap",
        "models": {"gemma": GEMMA, "qwen": QWEN},
        "max_tokens": MAX_TOKENS,
        "duration_s": DURATION_S,
        "trials": TRIALS,
        "levels": LEVELS,
        "phases": phases,
        "aggregate": agg,
        "swap_test": swap,
        "swap_overheads_ms": swap_overheads,
    }
    out_dir = Path(__file__).resolve().parent.parent / "results" / "serving_concurrency"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measurement_v3.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nsaved: {out_dir / 'measurement_v3.json'}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
