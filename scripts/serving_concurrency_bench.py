"""서빙 동시성 실측 — 단일 GPU에서 N 에이전트 동시 서빙의 지연/처리량.

계획서 v5 §7 R5(미해결: '동시 직원의 현실적 상한')의 Phase 0 미측정 질문을 실측한다.

설계(바운드·자원안전):
- 동일 프롬프트 K개를 동시성 C ∈ {1,2,4}로 각각 수행 + 재현용 c=1 재실행.
- httpx.AsyncClient + Semaphore(C)로 in-flight를 정확히 C로 제한(무한루프 0).
- 서버 timings(predicted_n·predicted_per_second·prompt_ms — 순수 GPU decode)와
  wall latency(에이전트 체감, 큐 대기 포함)를 둘 다 캡처해 분리.
- warmup 선행으로 llama-swap cold-load(~20s) 교란 제거.
- 각 요청 read timeout=120s, 전체는 safe_run.sh timeout으로 외부 가드.

판정 논리:
- 처리량(C)/처리량(1) ≈ C  → 연속배칭 작동, 동시 직원 가능.
- 처리량(C)/처리량(1) ≈ 1 + 지연 ≈ C배  → 직렬화(배칭 없음), '동시 직원'은 서빙층 환상.
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
ENABLE_THINKING = False  # 결정적·바운드 출력(도구/라우팅 모드). thinking ON은 변동 큼.
READ_TIMEOUT = 120.0
CONCURRENCY_LEVELS = [1, 2, 4]

# 직원 업무를 모사한 distinct 프롬프트 K개(프롬프트 캐시 교란 회피·조건 간 동일 집합 사용).
PROMPTS = [
    "신작 모바일 서바이버라이크 게임의 7일차 리텐션을 올릴 구체적 방법 5가지를 한국어로 설명해줘.",
    "Explain in detail how continuous batching improves LLM serving throughput on a single GPU.",
    "스타트업 1인 기업가가 분기 KPI를 설정할 때 흔히 저지르는 실수 5가지를 설명해줘.",
    "Describe a robust at-most-once email delivery design for an idempotent notification worker.",
    "도트 그래픽 픽셀 아트 캐릭터의 8방향 워크 애니메이션 제작 워크플로를 단계별로 설명해줘.",
    "Compare vector RAG with graph RAG for small local models and recommend one with reasons.",
]


async def one_call(
    client: httpx.AsyncClient, url: str, headers: dict, prompt: str, idx: int
) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, headers=headers, json=body)
        wall_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        data = resp.json()
        t = data.get("timings") or {}
        usage = data.get("usage") or {}
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        return {
            "idx": idx,
            "ok": True,
            "wall_ms": wall_ms,
            "prompt_n": t.get("prompt_n"),
            "prompt_ms": t.get("prompt_ms"),
            "predicted_n": t.get("predicted_n") or usage.get("completion_tokens"),
            "predicted_ms": t.get("predicted_ms"),
            "predicted_per_second": t.get("predicted_per_second"),
            "server_compute_ms": (t.get("prompt_ms") or 0)
            + (t.get("predicted_ms") or 0),
            "content_len": len(content),
        }
    except Exception as ex:  # noqa: BLE001 — 측정 스크립트, 실패도 데이터
        return {
            "idx": idx,
            "ok": False,
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
            "error": f"{type(ex).__name__}: {str(ex)[:200]}",
        }


async def run_phase(
    client: httpx.AsyncClient, url: str, headers: dict, concurrency: int, label: str
) -> dict:
    sem = asyncio.Semaphore(concurrency)

    async def guarded(p: str, i: int) -> dict:
        async with sem:
            return await one_call(client, url, headers, p, i)

    phase_t0 = time.perf_counter()
    results = await asyncio.gather(*[guarded(p, i) for i, p in enumerate(PROMPTS)])
    phase_wall_s = time.perf_counter() - phase_t0

    ok = [r for r in results if r.get("ok")]
    walls = [r["wall_ms"] for r in ok]
    pred = [r["predicted_n"] or 0 for r in ok]
    pps = [r["predicted_per_second"] or 0 for r in ok if r.get("predicted_per_second")]
    total_pred = sum(pred)

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
        return s[k]

    summary = {
        "label": label,
        "concurrency": concurrency,
        "n_requests": len(results),
        "n_ok": len(ok),
        "n_fail": len(results) - len(ok),
        "phase_wall_s": round(phase_wall_s, 3),
        "total_output_tokens": total_pred,
        # 시스템 처리량: 전체 출력토큰 / 페이즈 벽시계 — 동시성의 진짜 이득 지표.
        "system_throughput_tok_s": round(total_pred / phase_wall_s, 2)
        if phase_wall_s
        else 0,
        "wall_ms_mean": round(statistics.mean(walls), 1) if walls else 0,
        "wall_ms_p50": round(pct(walls, 0.5), 1),
        "wall_ms_p95": round(pct(walls, 0.95), 1),
        "server_decode_tok_s_mean": round(statistics.mean(pps), 1) if pps else 0,
        "results": results,
    }
    print(
        f"[{label}] C={concurrency} ok={len(ok)}/{len(results)} "
        f"wall={phase_wall_s:.1f}s out_tok={total_pred} "
        f"sys_thru={summary['system_throughput_tok_s']}tok/s "
        f"lat_p50={summary['wall_ms_p50']:.0f}ms "
        f"decode/req={summary['server_decode_tok_s_mean']:.0f}tok/s",
        flush=True,
    )
    return summary


async def main() -> None:
    env = load_env()
    base = get_llm_base_url(env).rstrip("/")
    key = get_llm_api_key(env)
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
    timeout = httpx.Timeout(connect=10.0, read=READ_TIMEOUT, write=10.0, pool=10.0)

    phases = []
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        # warmup 2회(모델 hot 유지·cold-swap 교란 제거)
        print("warmup...", flush=True)
        await asyncio.gather(
            one_call(client, url, headers, "Reply with: ready", 901),
            one_call(client, url, headers, "Reply with: ready", 902),
        )
        # c=1 → c=2 → c=4 → c=1(재현) 연속 실행(모델 hot 유지)
        run_plan = [(c, f"c{c}") for c in CONCURRENCY_LEVELS] + [(1, "c1_repeat")]
        for c, label in run_plan:
            phases.append(await run_phase(client, url, headers, c, label))
            await asyncio.sleep(1.0)  # 페이즈 간 서버 settle

    # 분석: c=1 기준 대비 배율
    base_phase = next((p for p in phases if p["label"] == "c1"), phases[0])
    base_thru = base_phase["system_throughput_tok_s"] or 1
    base_lat = base_phase["wall_ms_p50"] or 1
    analysis = []
    for p in phases:
        analysis.append(
            {
                "label": p["label"],
                "concurrency": p["concurrency"],
                "sys_throughput_tok_s": p["system_throughput_tok_s"],
                "throughput_speedup_vs_c1": round(
                    p["system_throughput_tok_s"] / base_thru, 2
                ),
                "wall_p50_ms": p["wall_ms_p50"],
                "latency_inflation_vs_c1": round(p["wall_ms_p50"] / base_lat, 2),
                "decode_per_req_tok_s": p["server_decode_tok_s_mean"],
                "n_fail": p["n_fail"],
            }
        )

    out = {
        "model": MODEL,
        "base_url": base,
        "max_tokens": MAX_TOKENS,
        "enable_thinking": ENABLE_THINKING,
        "k_prompts": len(PROMPTS),
        "concurrency_levels": CONCURRENCY_LEVELS,
        "phases": phases,
        "analysis": analysis,
    }
    out_dir = Path(__file__).resolve().parent.parent / "results" / "serving_concurrency"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "measurement.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== ANALYSIS (c=1 기준) ===", flush=True)
    for a in analysis:
        print(
            f"  C={a['concurrency']:<2} [{a['label']:<10}] "
            f"thru={a['sys_throughput_tok_s']:>7.1f}tok/s "
            f"speedup={a['throughput_speedup_vs_c1']:>4}x "
            f"lat_p50={a['wall_p50_ms']:>8.0f}ms "
            f"inflation={a['latency_inflation_vs_c1']:>4}x "
            f"decode/req={a['decode_per_req_tok_s']:>6.0f}tok/s "
            f"fail={a['n_fail']}",
            flush=True,
        )
    print(f"\nsaved: {out_path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
