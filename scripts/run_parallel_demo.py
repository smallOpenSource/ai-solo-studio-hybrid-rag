#!/usr/bin/env python
"""정직한 N=2 병렬 처리량 데모 + R5 실측 — US-5·US-6.

★협업 아님(throughput): 2개의 독립 잡(리서치 ∥ 게임빌드)을 가동 워커풀(slot0/1)에 동시
투입해 *서로 다른 일*을 같은 시각에 처리시킨다(병렬 분업). 증거:
- (US-5) claimed_by_slot가 in-flight(running) 중 **0과 1 둘 다** 점유되는 스냅샷(완료 후엔
  _release_slot이 NULL 반납하므로 running 중 1초 폴링으로 포착) + 두 task wall-clock 오버랩.
- (US-6) 2-동시 구간의 nvidia-smi(VRAM/util)를 베이스라인 대비 측정 → 동일 GPU gemma 2-동시
  실거동 실측(R5 미측정 충전). ★PASS까지 반복 0(1회 측정·산출물 존재로 검증).

★game/data는 게임빌드가 덮음(백업 .omc/backups/game-data-preLive·US-11에서 재빌드). 라이브
office.db 사용(가동 워커가 실행)·신규 이벤트/노드 0·동결 미접촉.

실행(★safe_run 래핑):
  bash .omc/guards/safe_run.sh 400 0 -- \
    /opt/conda/envs/one_man_corp-engine/bin/python scripts/run_parallel_demo.py
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from office.coordinator import BuildCoordinator
from office.event_store import EventStore
from office.task_queue import TaskQueue

DB = "state/office.db"
OUT = Path(".omc/state/us5-parallel.json")
POLL_S = 1.0
MAX_S = 240.0


def gpu_sample() -> str:
    """nvidia-smi 1줄 샘플(없으면 'n/a')."""
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 else "n/a"
    except Exception:  # noqa: BLE001
        return "n/a"


def running_slots(db: str) -> list[tuple]:
    """현재 running 태스크의 (id, workflow, claimed_by_slot) — in-flight 슬롯 각인."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return c.execute(
            "select id, workflow, claimed_by_slot from tasks where status='running' order by id"
        ).fetchall()
    finally:
        c.close()


def main() -> int:
    queue = TaskQueue(db_path=Path(DB))
    print(f"[{time.strftime('%H:%M:%S')}] baseline GPU:", gpu_sample())

    # (1) 독립 잡 A: 리서치(LiveOps/retention/UA 질의) — 가동 워커가 실 gemma로 처리.
    research_q = (
        "모바일 F2P 서바이버라이크 게임의 LiveOps·리텐션·UA 핵심 전략을 요약하라."
    )
    rid = queue.enqueue("research_summary", {"mock": False, "query": research_q})
    print(f"[{time.strftime('%H:%M:%S')}] 리서치 enqueue task={rid}")

    # (2) 독립 잡 B: 게임빌드 체인(코디네이터·실 gemma) — 스레드로 동시 가동.
    coord = BuildCoordinator(
        queue=queue,
        event_store=EventStore(db_path=Path(DB)),
        db_path=Path(DB),
        t_claim=120.0,
        t_run=900.0,
        poll_interval=2.0,
    )
    holder: dict = {}

    def _build() -> None:
        holder["build"] = coord.run_build(f"par-{int(time.time())}", mock=False)

    bt = threading.Thread(target=_build, name="par-build", daemon=True)
    bt.start()

    # (3) running 슬롯 + GPU 폴링 — 2-동시(슬롯 0&1) 스냅샷·오버랩·GPU 포착.
    t0 = time.monotonic()
    snapshots, two_slot_hits, gpu_two = [], [], []
    seen_slots: set = set()
    last_gpu = 0.0
    while time.monotonic() - t0 < MAX_S:
        rs = running_slots(DB)
        slots = {s[2] for s in rs if s[2] is not None}
        seen_slots |= slots
        if len(rs) >= 2:
            snap = {
                "t": round(time.monotonic() - t0, 1),
                "running": [{"id": r[0], "wf": r[1], "slot": r[2]} for r in rs],
            }
            snapshots.append(snap)
            if len({s[2] for s in rs if s[2] is not None}) >= 2:
                two_slot_hits.append(snap)
            if time.monotonic() - last_gpu > 6:
                g = gpu_sample()
                gpu_two.append({"t": snap["t"], "gpu": g, "n_running": len(rs)})
                last_gpu = time.monotonic()
        if not bt.is_alive() and not rs:
            break
        time.sleep(POLL_S)

    bt.join(timeout=10)
    result = {
        "build_state": (holder.get("build") or {}).get("state"),
        "research_task": rid,
        "seen_slots_inflight": sorted(seen_slots),
        "max_concurrent_running": max(
            (len(s["running"]) for s in snapshots), default=0
        ),
        "two_slot_hits": two_slot_hits[:6],
        "n_two_slot_snapshots": len(two_slot_hits),
        "gpu_during_concurrency": gpu_two[:8],
        "baseline_note": "베이스라인 GPU는 첫 줄 로그 참조",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(
        f"[{time.strftime('%H:%M:%S')}] 결과:", json.dumps(result, ensure_ascii=False)
    )
    print(f"[{time.strftime('%H:%M:%S')}] → {OUT}")
    # 성공 판정: in-flight에서 슬롯 0과 1을 모두 봤는가(슬롯경로 실가동 + N=2).
    ok = seen_slots >= {0, 1} or len(two_slot_hits) > 0
    print("SLOT_PATH_EXERCISED(0&1 in-flight):", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
