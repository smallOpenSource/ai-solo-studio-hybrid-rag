"""P1 step0 — measure-FIRST 게이트 (인프라 코드 0줄·격리·바운드).

합의 계획(.omc/plans/improvement-plan-2026-06-16.md·ralplan)이 강제하는 선행 게이트:
동시성 인프라(claimed_by_slot·supervisor·per-slot lock)를 한 줄도 짓기 전에
"자연 워크로드가 N=2를 정당화하는가"를 실측한다. 미충족이면 Option D(미구축).

게이트 기준:
  (A) 자연 출력(natural EOS) research_summary N2 요약 턴의 동시성 c=2 처리량 ≥ 1.4x
  (B) N writer + 상시 reader(FanoutPoller 형상) 동시 event_store write에서
      database-is-locked == 0 (busy_timeout=5000 하) AND 실효 write 처리량 측정

격리·안전: temp DB(프로덕션 office.db 무접촉)·시간박스(무한루프 0)·요청 read timeout·
safe_run.sh 외부 가드·pkill 금지. (A)는 gemma 서빙, (B)는 로컬 SQLite만.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import get_llm_api_key, get_llm_base_url, load_env  # noqa: E402
from office.db import get_connection  # noqa: E402

GEMMA = "gemma-4-e4b-it"
N2_PROMPT = (ROOT / "prompts/research_summary/n2_summarize.txt").read_text(
    encoding="utf-8"
)

# 실제 research_summary N2가 받는 형상: system=n2 프롬프트, user=질문+검색결과(carry note).
RESEARCH_QUESTIONS = [
    (
        "모바일 서바이버라이크 게임의 신규 유저 온보딩 베스트 프랙티스는?",
        "[검색결과] source1: 튜토리얼은 첫 3분 내 핵심 루프 체험 필수. source2: 강제 가이드보다 점진적 능력 해금이 이탈 감소. source3: D1 리텐션은 첫 세션 보상 밀도와 강상관.",
    ),
    (
        "단일 GPU LLM 서빙에서 처리량을 올리는 방법은?",
        "[검색결과] source1: continuous batching이 동시 요청 처리량을 높임. source2: paged KV-cache로 메모리 단편화 감소. source3: 짧은 출력은 배칭 이득이 작고 긴 출력에서 큼.",
    ),
    (
        "1인 기업의 분기 KPI 설정 원칙은?",
        "[검색결과] source1: 결과지표와 선행지표를 분리. source2: 3~5개로 제한해 집중. source3: 측정 가능·통제 가능한 것만 KPI로.",
    ),
]


# ───────────────────────── (A) 서빙 동시성 (자연 EOS·real n2) ─────────────────
async def _one(client, url, headers, q, ctx, t0):
    body = {
        "model": GEMMA,
        "messages": [
            {"role": "system", "content": N2_PROMPT},
            {"role": "user", "content": f"리서치 질문: {q}\n\n{ctx}"},
        ],
        "max_tokens": 2048,  # 자연 EOS 허용(상한은 안전용·실제 predicted_n 측정)
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": True},  # N2 thinking=on
    }
    s = time.perf_counter()
    try:
        r = await client.post(url, headers=headers, json=body)
        e = time.perf_counter()
        r.raise_for_status()
        d = r.json()
        t = d.get("timings") or {}
        return {
            "ok": True,
            "wall_ms": (e - s) * 1000,
            "predicted_n": t.get("predicted_n") or 0,
            "finish": (d.get("choices") or [{}])[0].get("finish_reason"),
        }
    except Exception as ex:  # noqa: BLE001
        return {
            "ok": False,
            "wall_ms": (time.perf_counter() - s) * 1000,
            "error": f"{type(ex).__name__}: {str(ex)[:140]}",
        }


async def _level(client, url, headers, c, dur):
    t0 = time.perf_counter()
    deadline = t0 + dur
    res = []
    ctr = {"i": 0}

    async def worker():
        while time.perf_counter() < deadline:
            i = ctr["i"]
            ctr["i"] += 1
            q, ctx = RESEARCH_QUESTIONS[i % len(RESEARCH_QUESTIONS)]
            res.append(await _one(client, url, headers, q, ctx, t0))

    await asyncio.gather(*[worker() for _ in range(c)])
    el = time.perf_counter() - t0
    ok = [r for r in res if r.get("ok")]
    tot = sum(r["predicted_n"] for r in ok)
    walls = sorted(r["wall_ms"] for r in ok)
    preds = [r["predicted_n"] for r in ok]
    eos = sum(1 for r in ok if r.get("finish") == "stop")
    return {
        "c": c,
        "elapsed_s": round(el, 1),
        "n_ok": len(ok),
        "n_fail": len(res) - len(ok),
        "tot_tok": tot,
        "thru": round(tot / el, 1) if el else 0,
        "lat_p50_ms": round(walls[len(walls) // 2], 0) if walls else 0,
        "pred_n_mean": round(statistics.mean(preds), 0) if preds else 0,
        "pred_n_max": max(preds) if preds else 0,
        "natural_eos": eos,
    }


async def part_a(dur):
    env = load_env()
    base = get_llm_base_url(env).rstrip("/")
    key = get_llm_api_key(env)
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
    out = []
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        print("  warmup...", flush=True)
        await _one(
            client, url, headers, "준비?", "[검색결과] source1: ok", time.perf_counter()
        )
        for c in (1, 2, 3):
            r = await _level(client, url, headers, c, dur)
            out.append(r)
            print(
                f"  c={c}: thru={r['thru']}tok/s n_ok={r['n_ok']} lat_p50={r['lat_p50_ms']:.0f}ms "
                f"pred_n_mean={r['pred_n_mean']:.0f}(max{r['pred_n_max']}) natural_eos={r['natural_eos']}/{r['n_ok']}",
                flush=True,
            )
            await asyncio.sleep(1.0)
    base_thru = out[0]["thru"] or 1
    for r in out:
        r["speedup"] = round(r["thru"] / base_thru, 2)
    return out


# ───────────────────────── (B) DB 동시쓰기 db-is-locked (temp·격리) ───────────
_INSERT = (
    "INSERT INTO events (run_id, family, type, payload_json, ts, schema_v) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_DDL = (
    "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "run_id TEXT NOT NULL, family TEXT NOT NULL, type TEXT NOT NULL, "
    "payload_json TEXT NOT NULL, ts TEXT NOT NULL, schema_v INTEGER NOT NULL DEFAULT 1)"
)


def _writer(db_path, wid, n_events, ret):
    """실 append 경로와 동일: get_connection(WAL+busy_timeout5000)+INSERT+commit."""
    locked = 0
    ok = 0
    other = 0
    payload = json.dumps(
        {"family": "Text", "type": "TextMessage", "delta": "x" * 200},
        ensure_ascii=False,
    )
    t0 = time.perf_counter()
    for k in range(n_events):
        try:
            with get_connection(
                db_path
            ) as conn:  # 매 append 신규연결(실 event_store 동일)
                conn.execute(
                    _INSERT,
                    (
                        f"w{wid}-{k}",
                        "Text",
                        "TextMessage",
                        payload,
                        "2026-06-16T00:00:00",
                        1,
                    ),
                )
                conn.commit()
            ok += 1
        except Exception as ex:  # noqa: BLE001
            if "database is locked" in str(ex).lower():
                locked += 1
            else:
                other += 1
    ret[wid] = {
        "ok": ok,
        "locked": locked,
        "other": other,
        "elapsed_s": round(time.perf_counter() - t0, 2),
    }


def _reader(db_path, stop_evt, ret):
    """FanoutPoller 형상: read_since(seq>?) 0.5s 폴링 상시 리더."""
    last = 0
    reads = 0
    locked = 0
    while not stop_evt.is_set():
        try:
            with get_connection(db_path) as conn:
                rows = conn.execute(
                    "SELECT seq FROM events WHERE seq>? ORDER BY seq", (last,)
                ).fetchall()
                if rows:
                    last = rows[-1]["seq"]
                reads += 1
        except Exception as ex:  # noqa: BLE001
            if "database is locked" in str(ex).lower():
                locked += 1
        time.sleep(0.05)
    ret["reader"] = {"reads": reads, "locked": locked, "last_seq": last}


def part_b(n_writers, n_events):
    import tempfile, os

    tmpdir = tempfile.mkdtemp(prefix="p1_step0_db_")
    db_path = os.path.join(tmpdir, "office_test.db")
    with get_connection(db_path) as conn:
        conn.execute(_DDL)
        conn.commit()
    mgr = mp.Manager()
    ret = mgr.dict()
    stop = mp.Event()
    reader = mp.Process(target=_reader, args=(db_path, stop, ret))
    reader.start()
    t0 = time.perf_counter()
    writers = [
        mp.Process(target=_writer, args=(db_path, w, n_events, ret))
        for w in range(n_writers)
    ]
    for p in writers:
        p.start()
    for p in writers:
        p.join()
    wall = time.perf_counter() - t0
    stop.set()
    reader.join(timeout=5)
    # WAL 파일 크기
    wal = db_path + "-wal"
    wal_kb = round(os.path.getsize(wal) / 1024, 1) if os.path.exists(wal) else 0
    res = dict(ret)
    tot_ok = sum(v["ok"] for k, v in res.items() if k != "reader")
    tot_locked = sum(v["locked"] for k, v in res.items() if k != "reader") + res.get(
        "reader", {}
    ).get("locked", 0)
    # 정리
    for f in (db_path, db_path + "-wal", db_path + "-shm"):
        try:
            os.remove(f)
        except OSError:
            pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass
    return {
        "n_writers": n_writers,
        "n_events_each": n_events,
        "total_writes_ok": tot_ok,
        "database_is_locked_total": tot_locked,
        "wall_s": round(wall, 2),
        "write_throughput_per_s": round(tot_ok / wall, 1) if wall else 0,
        "wal_peak_kb": wal_kb,
        "per_proc": res,
    }


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dur", type=float, default=18.0, help="(A) 레벨별 시간박스 초")
    ap.add_argument("--writers", type=int, default=3)
    ap.add_argument("--events", type=int, default=150)
    args = ap.parse_args()

    print(
        "=== (B) DB 동시쓰기 db-is-locked (temp·격리·FanoutPoller 리더 동반) ===",
        flush=True,
    )
    b = part_b(args.writers, args.events)
    print(
        f"  writers={b['n_writers']}×{b['n_events_each']} → ok={b['total_writes_ok']} "
        f"★database_is_locked={b['database_is_locked_total']} "
        f"write_thru={b['write_throughput_per_s']}/s wal_peak={b['wal_peak_kb']}KB wall={b['wall_s']}s",
        flush=True,
    )

    print(
        "\n=== (A) 서빙 동시성 — real n2_summarize·자연 EOS·thinking-ON ===", flush=True
    )
    a = asyncio.run(part_a(args.dur))

    c2 = next((r for r in a if r["c"] == 2), {})
    gate_a = c2.get("speedup", 0) >= 1.4
    gate_b = b["database_is_locked_total"] == 0
    print("\n=== GATE 판정 ===", flush=True)
    print(
        f"  (A) c=2 speedup = {c2.get('speedup')}x  → ≥1.4x? {'PASS' if gate_a else 'FAIL'}",
        flush=True,
    )
    print(
        f"  (B) database-is-locked = {b['database_is_locked_total']}  → ==0? {'PASS' if gate_b else 'FAIL'}",
        flush=True,
    )
    print(
        f"  ★GATE = {'PASS (인프라 착수 정당)' if (gate_a and gate_b) else 'FAIL (Option D 검토 — 인프라 보류)'}",
        flush=True,
    )

    out = {
        "part_a_serving": a,
        "part_b_db_contention": b,
        "gate": {
            "a_speedup_c2": c2.get("speedup"),
            "a_pass": gate_a,
            "b_locked": b["database_is_locked_total"],
            "b_pass": gate_b,
            "overall_pass": gate_a and gate_b,
        },
    }
    outd = ROOT / "results" / "serving_concurrency"
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "p1_step0_measure_first.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nsaved: {outd / 'p1_step0_measure_first.json'}", flush=True)


if __name__ == "__main__":
    main()
