"""orch/drive_org.py — 실 모델 직접 드라이브 (조직그래프 검증·미승격).

CEO = qwen(BENCH_PRIMARY_MODEL·전략모델), 부서 = gemma. ★전용 ckpt(`orch_org_ckpt.db`)·라이브
`orch_ckpt.db` 미접근(WAL contention 방지). 지시를 ainvoke → CEO 라우팅 → 부서(다부서 fan-out
가능) → finalize BLUF + routing_trace + departments + 모델/시간 출력. escalate 경로면 Command(resume)
자동 승인.

실행: PYTHONPATH=/opt/studio /opt/conda/envs/orch/bin/python orch/drive_org.py
"""

# TODO(provider): 기본 모델 caller(부서=gemma·CEO=qwen)는 이제 Azure-primary/로컬-fallback
# (orch.azure_llm·make_primary_fallback_caller·ORCH_LLM_PRIMARY=azure). 드라이버-레벨 provider
# 선택/검증은 Owner 지시로 이연(DEFERRED) — 이 스크립트는 라우팅 로직을 추가하지 않는다.

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from orch.org_graph import _STRATEGY_MODEL, build_org_graph

# 전용 체크포인트(orch/*.db gitignore 적용) — 라이브 orch_ckpt.db 와 물리 분리.
_DB = str(Path(__file__).resolve().parent / "orch_org_ckpt.db")

_DIRECTIVES = [
    ("org-tech", "작은 클리커 게임의 빌드 스크립트를 점검해줘"),  # -> dev_lead
    ("org-research", "신규 캐주얼 게임 시장 트렌드를 조사해줘"),  # -> coo
    (
        "org-multi",
        "신규 캐주얼 게임을 시장조사하고 동시에 에셋 컨셉도 잡아줘",
    ),  # -> coo 다부서
]


async def _drive(graph, thread: str, directive: str) -> tuple[dict, float]:
    cfg = {"configurable": {"thread_id": thread}}
    t0 = time.time()
    out = await graph.ainvoke({"directive": directive}, cfg)
    snap = await graph.aget_state(cfg)
    if snap.interrupts:  # escalate 경로 -> 자동 approve
        out = await graph.ainvoke(Command(resume="approve"), cfg)
    return out, round(time.time() - t0, 1)


async def main() -> int:
    print(f"== CEO 전략모델(qwen) = {_STRATEGY_MODEL} · 부서 = gemma ==")
    all_ok = True
    async with AsyncSqliteSaver.from_conn_string(_DB) as saver:
        graph = build_org_graph(saver)
        for thread, directive in _DIRECTIVES:
            print(f"\n== 지시: {directive} ==")
            out, secs = await _drive(graph, thread, directive)
            trace = out.get("routing_trace", [])
            depts = out.get("departments", [])
            arts = out.get("artifacts", {})
            print("  route        :", out.get("route"), f"({secs}s)")
            print("  routing_trace:", " -> ".join(trace))
            print("  departments  :", depts)
            for key, val in arts.items():
                print(f"  [{key}] {str(val)[:100]}")
            print("  BLUF:\n", (out.get("bluf") or "")[:300])
            ok = bool(
                trace
                and trace[0] == "ceo_route"
                and trace[-1] == "finalize"
                and out.get("bluf")
                and arts
            )
            print("  ->", "PASS" if ok else "FAIL")
            all_ok = all_ok and ok
    print("\nRESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
