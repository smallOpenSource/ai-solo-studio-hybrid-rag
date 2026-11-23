#!/usr/bin/env python
"""라이브 협업 게임빌드 러너 — BuildCoordinator를 라이브 office.db에 구동(web 재시작 불요).

US-4(라이브 end-to-end 직렬 협업)용. web_server/coop_router는 가동 중 web 서비스에 아직
없으므로(추가 후 미재시작), 이 스크립트가 `office.coordinator.BuildCoordinator`를 **라이브
state/office.db에 직접** 구동한다 → 가동 중 supervisor 워커풀(slot0/1)이 큐를 드레인해 실제로
game_design→balance→content→assemble→qa 5단계를 실행한다(직렬 협업 = 배턴·②교차영향).

★안전:
- 라이브 office.db에 enqueue → 가동 워커가 실 gemma로 실행(실 game/data 기록). 실행 전 game/data
  백업은 호출측 책임(이 스크립트는 굽지 않는 한 game/data 미접촉).
- 코디네이터 폴링은 2축 바운드(t_claim/t_run)라 무한대기 0. 이 스크립트도 전체를
  `.omc/guards/safe_run.sh <총상한>`으로 감싸 실행할 것(워치독 이중화).
- 신규 이벤트/노드/워크플로 0·동결 7파일 미접촉(enqueue=task_queue·관측=EventStore.read).

사용:
  bash .omc/guards/safe_run.sh 1800 0 -- \
    /opt/conda/envs/one_man_corp-engine/bin/python scripts/run_coop_build_live.py \
    --query "..." [--mock] [--db state/office.db]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from office.coordinator import BuildCoordinator
from office.event_store import EventStore
from office.task_queue import TaskQueue


def _now() -> str:
    """로컬 시각 문자열(로그 타임스탬프·관측 타이밍 보조)."""
    return time.strftime("%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser(description="라이브 협업 게임빌드 러너")
    ap.add_argument("--db", default="state/office.db", help="라이브 office.db 경로")
    ap.add_argument("--query", default=None, help="payload.query(워커 messages 시딩)")
    ap.add_argument("--mock", action="store_true", help="mock=true(결정론 fake LLM)")
    ap.add_argument(
        "--t-claim", type=float, default=120.0, help="단계 claim 타임아웃(초)"
    )
    ap.add_argument(
        "--t-run", type=float, default=900.0, help="단계 RUN_FINISHED 타임아웃(초)"
    )
    ap.add_argument("--poll", type=float, default=2.0, help="폴 간격(초)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] db 부재: {db_path}", file=sys.stderr)
        return 2

    coordinator = BuildCoordinator(
        queue=TaskQueue(db_path=db_path),
        event_store=EventStore(db_path=db_path),
        db_path=db_path,
        t_claim=args.t_claim,
        t_run=args.t_run,
        poll_interval=args.poll,
    )
    build_id = f"coop-live-{int(time.time())}"
    print(f"[{_now()}] 빌드 시작 build_id={build_id} mock={args.mock} db={db_path}")

    # run_build는 블로킹(5단계 직렬 폴링) → 스레드로 띄우고 get_status를 폴링해 단계 전이를 로깅.
    holder: dict[str, object] = {}

    def _run() -> None:
        holder["result"] = coordinator.run_build(
            build_id, mock=args.mock, query=args.query
        )

    bt = threading.Thread(target=_run, name=build_id, daemon=True)
    bt.start()

    # 단계 전이 관측(관측창·증거 타이밍용). 총 상한 = 5단계×(t_claim+t_run) 여유.
    total_cap = 5 * (args.t_claim + args.t_run) + 60
    deadline = time.monotonic() + total_cap
    last_stage = None
    while bt.is_alive() and time.monotonic() < deadline:
        st = coordinator.get_status(build_id) or {}
        stage = st.get("stage")
        if stage != last_stage:
            print(f"[{_now()}] 단계 진입: {stage}  state={st.get('state')}")
            last_stage = stage
        time.sleep(args.poll)
    bt.join(timeout=10)

    result = holder.get("result") or coordinator.get_status(build_id) or {}
    print(f"[{_now()}] 최종: {json.dumps(result, ensure_ascii=False)}")

    # 산출물 종료신호 확인(라이브 game/data 기록 — mock=false면 실 직원 산출).
    data_dir = Path("game/data")
    for fname in ("assembly.json", "qa_report.json"):
        p = data_dir / fname
        if p.exists():
            try:
                obj = json.loads(p.read_text("utf-8"))
                key = "ready" if fname == "assembly.json" else "pass"
                print(f"[{_now()}] {fname}: {key}={obj.get(key)}")
            except Exception as exc:  # noqa: BLE001 — 보고용
                print(f"[{_now()}] {fname} 파싱 실패: {exc}")

    state = result.get("state") if isinstance(result, dict) else None
    return 0 if state == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
