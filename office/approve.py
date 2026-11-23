"""CLI 승인 흐름 — US-007 (T6).

LLM 호출 없음. DB/파일에 결정을 기록하는 얇은 writer.

사용법:
    python -m office.approve list [--db <path>]
    python -m office.approve approve <interrupt_id> [--db <path>]
    python -m office.approve reject  <interrupt_id> [--db <path>]

설계 (계획 §4.4):
- 2-프로세스 모델: 디스패처(폴링)와 이 CLI는 각자 별도 프로세스.
  둘 다 같은 SQLite 파일에 WAL 모드로 접근 — database is locked 없음.
- CLI는 결정을 ① events 테이블에 INTERRUPT_RESOLVED 추가,
                ② state/approvals/<interrupt_id>.json 파일 기록(이중 폴백),
                ③ TaskQueue.resume(task_id)로 작업을 queued로 되돌림.
- 파일 폴백 헬퍼 poll_file_decisions: state/approvals/*.json을 읽어 아직
  INTERRUPT_RESOLVED 없는 항목을 처리 후 .consumed로 rename.
  디스패처/엔진이 주기적으로 호출하는 순수 함수(US-008에서 배선).

interrupt ↔ task 연결 계약 (US-006 게이팅이 지켜야 할 것):
    INTERRUPT_REQUESTED 이벤트의 payload dict에 반드시
        "task_id": int  — 해당 작업의 TaskQueue row id
    를 포함해야 한다. 이 값이 없으면 resume이 불가하므로
    US-006(office/gating.py)은 INTERRUPT_REQUESTED 발행 시
    payload["task_id"]를 반드시 채울 계약을 진다.
    (CLI는 payload["task_id"]를 읽어 TaskQueue.resume을 호출한다.)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from office.db import DEFAULT_DB_PATH, get_connection
from office.event_store import EventStore
from office.task_queue import TaskQueue


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """현재 시각 ISO-8601 문자열 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _elapsed(ts_str: str) -> str:
    """ISO-8601 타임스탬프로부터 경과 시간을 사람이 읽기 쉬운 문자열로 반환한다.

    계획 §4.4 M10a: 무기한 대기, 표시만 함.
    반환 형식: "3일 2시간 15분", "45분", "30초" 등.
    """
    try:
        then = datetime.fromisoformat(ts_str)
        # 타임존 없는 경우 UTC로 간주
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta_secs = int((now - then).total_seconds())
        if delta_secs < 0:
            delta_secs = 0

        days = delta_secs // 86400
        hours = (delta_secs % 86400) // 3600
        minutes = (delta_secs % 3600) // 60
        seconds = delta_secs % 60

        parts: list[str] = []
        if days > 0:
            parts.append(f"{days}일")
        if hours > 0:
            parts.append(f"{hours}시간")
        if minutes > 0:
            parts.append(f"{minutes}분")
        if not parts:
            parts.append(f"{seconds}초")
        return " ".join(parts)
    except (ValueError, TypeError):
        return "알 수 없음"


def _all_pending_interrupts(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """전체 DB에서 아직 INTERRUPT_RESOLVED 없는 INTERRUPT_REQUESTED 행 목록 반환.

    EventStore.pending_interrupts는 run_id 기준이라 여기서 전체 범위로 질의한다.
    반환 값은 각 항목이 {seq, interrupt_id, run_id, node_id, tool, risk,
                        payload, ts, elapsed} 형태의 dict.
    interrupt_id = seq (DB 기본키, AUTOINCREMENT)를 식별자로 사용한다.
    """
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    with get_connection(path) as conn:
        # 전체 INTERRUPT_REQUESTED 조회
        requested_rows = conn.execute(
            """
            SELECT seq, run_id, payload_json, ts
            FROM events
            WHERE type = 'INTERRUPT_REQUESTED'
            ORDER BY seq ASC
            """
        ).fetchall()

        # INTERRUPT_RESOLVED의 (run_id, node_id) 집합 — 이미 처리된 것
        resolved_rows = conn.execute(
            """
            SELECT payload_json FROM events
            WHERE type = 'INTERRUPT_RESOLVED'
            """
        ).fetchall()

    resolved_keys: set[tuple[str, str]] = set()
    for row in resolved_rows:
        data = json.loads(row["payload_json"])
        run_id = data.get("run_id", "")
        node_id = data.get("node_id", "")
        if run_id and node_id:
            resolved_keys.add((run_id, node_id))

    pending: list[dict[str, Any]] = []
    for row in requested_rows:
        data = json.loads(row["payload_json"])
        run_id = row["run_id"]
        node_id = data.get("node_id", "")
        if (run_id, node_id) not in resolved_keys:
            pending.append(
                {
                    "interrupt_id": row["seq"],  # seq를 interrupt_id로 사용
                    "run_id": run_id,
                    "node_id": node_id,
                    "tool": data.get("tool", ""),
                    "risk": data.get("risk", "needs-approval"),
                    "payload": data.get("payload", {}),
                    "ts": row["ts"],
                    "elapsed": _elapsed(row["ts"]),
                }
            )

    return pending


def _record_decision(
    interrupt_id: int,
    decision: str,
    db_path: str | Path | None = None,
    approvals_dir: str | Path | None = None,
    _write_file: bool = True,
    resume: bool = True,
) -> dict[str, Any]:
    """승인/거부 결정을 DB와 파일에 기록하고 (선택적으로) 작업을 resume한다.

    Args:
        interrupt_id: INTERRUPT_REQUESTED 이벤트의 seq (= DB interrupt_id).
        decision:     "approve" 또는 "reject".
        db_path:      SQLite 파일 경로. None이면 state/office.db.
        approvals_dir: 결정 파일 저장 디렉토리. None이면 state/approvals/.
        _write_file:  False이면 파일 기록을 건너뜀 (파일 폴백 경로에서 호출 시).
        resume:       True(기본 — CLI 경로)면 종전대로 ① INTERRUPT_RESOLVED 발행
                      ② 파일 기록 ③ TaskQueue.resume(task 상태 queued로 전환)까지
                      한다. False(웹 Inbox 버튼 경로 — 계획 §4.6 M3)면 ①②만 하고
                      ③ TaskQueue.resume를 건너뛴다(task 상태 불변). 재개는 워크플로
                      전용 스레드의 폴링 루프(resume_after_approval)가 단일 담당해
                      resume 이중 트리거(상태 천이 경합)를 막는다.

    Returns:
        처리 결과 dict {interrupt_id, decision, run_id, task_id, ...}.

    Raises:
        ValueError: interrupt_id에 해당하는 pending 인터럽트가 없는 경우.
    """
    path = db_path if db_path is not None else DEFAULT_DB_PATH

    # ① 해당 interrupt의 INTERRUPT_REQUESTED 행 조회
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT run_id, payload_json, ts FROM events WHERE seq = ? AND type = 'INTERRUPT_REQUESTED'",
            (interrupt_id,),
        ).fetchone()

    if row is None:
        raise ValueError(
            f"interrupt_id={interrupt_id}에 해당하는 INTERRUPT_REQUESTED가 없습니다."
        )

    run_id: str = row["run_id"]
    data: dict[str, Any] = json.loads(row["payload_json"])
    node_id: str = data.get("node_id", "")
    tool: str = data.get("tool", "")
    payload: dict[str, Any] = data.get("payload", {})
    task_id: int | None = payload.get("task_id")

    # ① events 테이블에 INTERRUPT_RESOLVED 추가
    store = EventStore(db_path=path)
    from office.events import InterruptResolved

    resolved_event = InterruptResolved(
        run_id=run_id,
        ts=_now_iso(),
        node_id=node_id,
        tool=tool,
        decision=decision,
    )
    store.append(resolved_event)

    # ② state/approvals/<interrupt_id>.json 파일에도 기록 (이중 폴백)
    # _write_file=False이면 건너뜀 — 파일 폴백 경로(poll_file_decisions)에서 이미
    # 파일을 .consumed로 rename한 후 이 함수를 호출하므로 재생성하면 안 됨.
    if _write_file:
        _write_approval_file(
            interrupt_id, decision, approvals_dir=approvals_dir, db_path=path
        )

    # ③ TaskQueue.resume — approve든 reject든 작업은 재개(엔진/게이팅이 분기 처리)
    # resume=False(웹 Inbox 버튼)면 건너뜀 — 재개는 폴링 루프 단일 경로(M3).
    if resume and task_id is not None:
        queue = TaskQueue(db_path=path)
        queue.resume(task_id)

    return {
        "interrupt_id": interrupt_id,
        "decision": decision,
        "run_id": run_id,
        "node_id": node_id,
        "tool": tool,
        "task_id": task_id,
    }


def _default_approvals_dir(db_path: str | Path | None = None) -> Path:
    """approvals 디렉토리 기본 경로를 반환한다.

    db_path와 같은 부모(state/)를 기준으로 state/approvals/을 만든다.
    """
    if db_path is not None:
        return Path(db_path).parent / "approvals"
    return DEFAULT_DB_PATH.parent / "approvals"


def _write_approval_file(
    interrupt_id: int,
    decision: str,
    approvals_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Path:
    """state/approvals/<interrupt_id>.json에 결정을 기록하고 경로를 반환한다."""
    adir = (
        Path(approvals_dir)
        if approvals_dir is not None
        else _default_approvals_dir(db_path)
    )
    adir.mkdir(parents=True, exist_ok=True)
    fpath = adir / f"{interrupt_id}.json"
    fpath.write_text(
        json.dumps(
            {"decision": decision, "interrupt_id": interrupt_id}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    return fpath


# ── 파일 폴백 폴링 헬퍼 ──────────────────────────────────────────────────────


def poll_file_decisions(
    approvals_dir: str | Path,
    store: EventStore,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """state/approvals/*.json 파일을 읽어 아직 INTERRUPT_RESOLVED 없는 것을 처리한다.

    처리 흐름:
    1. approvals_dir/*.json 파일을 순서대로 읽는다.
    2. 해당 interrupt_id(= seq)가 이미 INTERRUPT_RESOLVED 되었으면 건너뜀.
    3. 아직 미해결이면 _record_decision으로 ①②③ 수행.
    4. 처리된 파일을 .consumed로 rename (중복 처리 방지).

    Args:
        approvals_dir: 결정 파일이 쌓이는 디렉토리.
        store:         EventStore 인스턴스 (pending_interrupts 질의용).
        db_path:       SQLite 파일 경로. None이면 store._db_path 사용.

    Returns:
        이번 호출에서 처리된 결정 목록 (각 항목은 _record_decision 반환값).

    디스패처/엔진이 주기적으로 호출하는 순수 함수 — US-008에서 배선.
    """
    adir = Path(approvals_dir)
    if not adir.exists():
        return []

    effective_db = db_path if db_path is not None else store._db_path

    # 현재 pending interrupt_id 집합 — DB 기준 (seq 값)
    pending_list = _all_pending_interrupts(db_path=effective_db)
    pending_ids: set[int] = {item["interrupt_id"] for item in pending_list}

    processed: list[dict[str, Any]] = []

    for fpath in sorted(adir.glob("*.json")):
        # 이미 .consumed 파일은 건너뜀 (glob "*.json"으로 이미 제외됨)
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 읽기 실패한 파일은 건너뜀
            continue

        interrupt_id = data.get("interrupt_id")
        decision = data.get("decision")

        if interrupt_id is None or decision is None:
            continue

        try:
            interrupt_id = int(interrupt_id)
        except (TypeError, ValueError):
            continue

        # 이미 INTERRUPT_RESOLVED 된 항목이면 건너뜀(단 파일은 .consumed로 정리)
        if interrupt_id not in pending_ids:
            # 이미 처리된 것 — 파일만 정리
            fpath.rename(fpath.with_suffix(".consumed"))
            continue

        # 처리 전에 파일을 먼저 .consumed로 rename (중복 방지)
        consumed_path = fpath.with_suffix(".consumed")
        fpath.rename(consumed_path)

        try:
            result = _record_decision(
                interrupt_id=interrupt_id,
                decision=decision,
                db_path=effective_db,
                _write_file=False,  # 파일은 이미 .consumed로 rename됨 — 재생성 금지
            )
            processed.append(result)
        except (ValueError, Exception):
            # 처리 실패해도 파일은 .consumed로 유지 (재처리 방지)
            # 로그는 남기지 않음 — 순수 함수 계약
            pass

    return processed


# ── CLI 서브커맨드 핸들러 ─────────────────────────────────────────────────────


def cmd_list(db_path: str | Path | None = None) -> None:
    """대기 중인 인터럽트 목록을 출력한다.

    각 행: interrupt_id | run_id | 도구명 | 위험등급 | 경과시간
    """
    pending = _all_pending_interrupts(db_path=db_path)

    if not pending:
        print("대기 중인 승인 요청이 없습니다.")
        return

    # 헤더
    header = f"{'ID':>6}  {'run_id':<36}  {'도구':^20}  {'위험':^14}  경과시간"
    print(header)
    print("-" * len(header))

    for item in pending:
        print(
            f"{item['interrupt_id']:>6}  "
            f"{item['run_id']:<36}  "
            f"{item['tool']:^20}  "
            f"{item['risk']:^14}  "
            f"{item['elapsed']}"
        )


def cmd_approve(interrupt_id: int, db_path: str | Path | None = None) -> None:
    """interrupt_id를 승인한다."""
    try:
        result = _record_decision(interrupt_id, "approve", db_path=db_path)
        print(
            f"승인 완료: interrupt_id={result['interrupt_id']}, "
            f"tool={result['tool']}, task_id={result['task_id']}"
        )
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_reject(interrupt_id: int, db_path: str | Path | None = None) -> None:
    """interrupt_id를 거부한다."""
    try:
        result = _record_decision(interrupt_id, "reject", db_path=db_path)
        print(
            f"거부 완료: interrupt_id={result['interrupt_id']}, "
            f"tool={result['tool']}, task_id={result['task_id']}"
        )
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)


# ── __main__ 진입점 ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """CLI 진입점 — python -m office.approve."""
    parser = argparse.ArgumentParser(
        prog="python -m office.approve",
        description="워크플로 승인/거부 CLI",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="SQLite DB 파일 경로 (기본: state/office.db)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list 서브커맨드
    subparsers.add_parser("list", help="대기 중인 승인 요청 목록")

    # approve 서브커맨드
    approve_p = subparsers.add_parser("approve", help="승인")
    approve_p.add_argument("interrupt_id", type=int, help="승인할 interrupt_id")

    # reject 서브커맨드
    reject_p = subparsers.add_parser("reject", help="거부")
    reject_p.add_argument("interrupt_id", type=int, help="거부할 interrupt_id")

    args = parser.parse_args(argv)
    db_path = args.db  # None이면 기본값 사용

    if args.command == "list":
        cmd_list(db_path=db_path)
    elif args.command == "approve":
        cmd_approve(args.interrupt_id, db_path=db_path)
    elif args.command == "reject":
        cmd_reject(args.interrupt_id, db_path=db_path)


if __name__ == "__main__":
    main()
