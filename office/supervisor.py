"""office/supervisor.py — P1 step3: 멀티 OS 프로세스 워커풀 감독자.

단일 직렬 워커(office.worker)를 N개 슬롯 워커로 확장하는 감독 프로세스. 동기 블로킹
실행 스택(llm_client httpx.Client·engine.run·run.py sleep)에서 실 동시성을 얻는 유일한
방법이 멀티 OS 프로세스이므로(asyncio/threading은 in-flight=0 false-positive·규약 위반),
supervisor가 정확히 N개의 `python -m office.worker --slot i`를 fork→exec로 띄우고 감독한다.

설계(.omc/plans/ralplan-p1-concurrency-prd.md §step3·원칙6·pre-mortem):
- ★정확히 N개·프로세스 상한: 슬롯당 1자식(dict[slot]→proc) — 구조적으로 N 초과 불가.
- ★죽은slot 청소는 LOCK_NB 게이팅(split-brain 차단): 슬롯 i 자식이 죽으면 재기동 전에
  worker.lock.{i}를 LOCK_NB로 획득 시도한다. **획득 성공 = 그 슬롯에 살아있는 워커 없음
  (진짜 죽음)** 일 때만 claimed_by_slot=i 고아 각인을 NULL 청소(release_all_slot_stamps)
  → 재기동 워커-i가 claim_running/claim_suspended로 원자 재점유. 획득 실패(다른 프로세스가
  보유) = 살아있는 워커 의심 → 청소·재기동 **둘 다 보류**(이중실행 0). 청소 후 즉시 락 해제
  (자식이 획득하도록). ★워커 락 fd는 O_CLOEXEC(worker.py)라 fork→exec 자식에 미상속.
- ★crash-loop 백오프+상한(폭주 차단·시스템다운 전례 2회): 슬롯이 window초 내 max회 이상
  죽으면 그 슬롯을 **포기**(재기동 중단·escalation 로그) — 무한 재스폰 금지. 그 외엔
  지수 백오프(base·cap)로 재기동.
- ★자기 자식만 신호(pkill 절대 금지): graceful shutdown은 추적 중인 Popen 핸들에만
  SIGTERM→그레이스→SIGKILL. 시스템 전역 pkill -f는 타 프로세스 불침해 위반이라 금지.
- ★supervisor 단일 인스턴스(state/supervisor.lock): 두 supervisor 동시 기동 차단
  (최악의 split-brain 방지). main()이 강제.
- conda env python 직접 exec(conda run 회피·제약 #3): python_exe 절대경로.
- ★supervisor 자신 start_guard.sh 워치독 하 기동: serve_supervisor.sh가 결속(자원 폭주
  자기종료). ★production 기동·concurrency:2 승격은 사용자 세션 승인 게이트(자율 발사 금지).

테스트 가능성: spawn/sleep/clock 주입 가능(기본 subprocess.Popen·time). 테스트는 fake
spawn(실 워커 0)·fake clock/sleep·max_cycles 바운드로 라이프사이클·청소·백오프·상한·
shutdown을 검증한다(★실 production supervisor 기동 0).

qwen mutex(PRD 언급)는 **의도적 deferral**: 이미지생성(qwen)이 워커 워크플로 실행 경로에
현재 없다(검증: qwen 참조는 core/config.py뿐·워커는 gemma 텍스트 워크플로만 실행). 단일 GPU
직렬화가 필요한 image-gen 워크플로가 풀에 올라올 때 tool 계층에서 단일-flight 락으로 qwen
호출을 게이팅해야 한다 — 지금 투기적 mutex 프리미티브를 추가하면 미사용 dead code(P9 정리
대상과 동형)라 추가하지 않는다. 이 deferral은 step3 리뷰/PRD에 박제(필요 시 저비용 후속).

신규 외부 의존성 0(stdlib: argparse·os·signal·subprocess·sys·time + 기존 office import).
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from office.db import DEFAULT_DB_PATH
from office.task_queue import TaskQueue
from office.worker import SingleInstanceLock

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PYTHON = "/opt/conda/envs/one_man_corp-engine/bin/python"
_DEFAULT_SUPERVISOR_LOCK = _PROJECT_ROOT / "state" / "supervisor.lock"
_DEFAULT_STATE_DIR = _PROJECT_ROOT / "state"


def _default_spawn(argv: list[str]) -> Any:
    """워커 자식을 fork→exec한다(conda env python 직접). close_fds로 부모 fd 미상속."""
    return subprocess.Popen(argv, close_fds=True)


class WorkerSupervisor:
    """N개 슬롯 워커를 기동·감독·재기동하는 감독자(정확히 N·crash-loop 안전·split-brain 차단).

    구조: slot(0..N-1)마다 자식 1개(self._procs[slot]). 슬롯당 1자식이라 프로세스 상한이
    구조적으로 N. monitor()가 각 슬롯의 returncode를 폴링해 죽음을 감지하고 _handle_death로
    청소(LOCK_NB 게이팅)+백오프 재기동 또는 crash-loop 포기를 수행한다.
    """

    def __init__(
        self,
        concurrency: int,
        *,
        db_path: str | Path | None = None,
        python_exe: str = _DEFAULT_PYTHON,
        poll_interval: float = 2.0,
        monitor_interval: float = 1.0,
        lock_dir: str | Path | None = None,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        crashloop_window: float = 60.0,
        crashloop_max: int = 5,
        spawn: Callable[[list[str]], Any] = _default_spawn,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1:
            raise ValueError(f"concurrency는 1 이상이어야 합니다: {concurrency}")
        self._n = concurrency
        self._db_path = db_path if db_path is not None else DEFAULT_DB_PATH
        self._queue = TaskQueue(self._db_path)
        self._python = python_exe
        self._poll_interval = poll_interval
        self._monitor_interval = monitor_interval
        self._lock_dir = Path(lock_dir) if lock_dir is not None else _DEFAULT_STATE_DIR
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._crashloop_window = crashloop_window
        self._crashloop_max = crashloop_max
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock
        self._procs: dict[int, Any] = {}
        self._restart_history: dict[int, list[float]] = {}
        self._given_up: set[int] = set()
        self._stop = False

    # ── 슬롯 경로·argv ───────────────────────────────────────────────────────

    def _lock_path(self, slot: int) -> Path:
        """슬롯 advisory 락 경로(worker._slot_lock_path와 동일 명명 — 자식에 --lock-path로 전달)."""
        return self._lock_dir / f"worker.lock.{slot}"

    def _build_worker_argv(self, slot: int) -> list[str]:
        """워커 자식 exec argv. --lock-path를 명시해 supervisor와 자식이 같은 락을 본다."""
        return [
            self._python,
            "-m",
            "office.worker",
            "--slot",
            str(slot),
            "--lock-path",
            str(self._lock_path(slot)),
            "--poll-interval",
            str(self._poll_interval),
        ]

    def _spawn_slot(self, slot: int) -> Any:
        """슬롯 워커 1개를 띄운다(conda env python 직접 exec)."""
        return self._spawn(self._build_worker_argv(slot))

    # ── 죽은slot 청소(LOCK_NB 게이팅·split-brain 차단) ─────────────────────────

    def _cleanup_dead_slot(self, slot: int) -> bool:
        """슬롯 i 고아 각인을 청소한다 — worker.lock.{i} LOCK_NB 획득 성공 시에만.

        획득 성공 = 그 슬롯에 살아있는 워커 없음(진짜 죽음) → release_all_slot_stamps(i)로
        claimed_by_slot=i 고아를 NULL 청소(재기동 워커가 원자 재점유 가능). 청소 후 즉시
        락 해제(자식이 획득하도록). 획득 실패 = 다른 프로세스가 보유(살아있는 워커 의심)
        → 청소 안 함(False 반환·재기동도 보류).

        Returns:
            True  — 진짜 죽음 확인·청소 완료(재기동 안전).
            False — 락 보유 중(살아있는 워커 의심)·청소 안 함(재기동 보류).
        """
        lock = SingleInstanceLock(self._lock_path(slot))
        if not lock.acquire():
            return False
        try:
            self._queue.release_all_slot_stamps(slot)
        finally:
            # ★즉시 해제 — 재기동 자식이 worker.lock.{slot}을 획득하도록. 해제~자식 획득
            # 사이 창은 경쟁자 부재로 무해(리뷰 F3): supervisor 단일 인스턴스 락이 동시
            # supervisor를 배제하고 worker.lock.{slot}은 슬롯-slot 워커 전용이라 그 창의
            # 유일 획득자는 재기동 자식이다.
            lock.release()
        return True

    # ── 라이프사이클 ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """N개 슬롯 워커를 기동한다(0..N-1)."""
        for slot in range(self._n):
            self._restart_history.setdefault(slot, [])
            self._procs[slot] = self._spawn_slot(slot)

    def _handle_death(self, slot: int, returncode: int) -> None:
        """죽은 슬롯을 crash-loop 판정→포기 또는 백오프→청소→재기동 한다.

        crash-loop: window초 내 재기동 이력이 max 이상이면 그 슬롯 포기(무한 재스폰 차단).
        그 외: 지수 백오프(base·2^과거재기동수·cap) 후 LOCK_NB 게이팅 청소 성공 시 재기동.
        청소 실패(락 보유=살아있는 워커 의심)면 재기동 보류(추적 해제).
        """
        now = self._clock()
        hist = self._restart_history.setdefault(slot, [])
        # window 밖 이력 제거(최근 window초만 셈)
        hist[:] = [t for t in hist if now - t < self._crashloop_window]
        if len(hist) >= self._crashloop_max:
            # ★폭주 차단: 최근 window초 내 max회 재기동 → 포기(escalation). 재스폰 안 함.
            self._given_up.add(slot)
            self._procs.pop(slot, None)
            print(
                f"[supervisor] ★slot {slot} crash-loop "
                f"(최근 {self._crashloop_window:.0f}s 내 {len(hist)}회 재기동·rc={returncode}) "
                "— 재기동 포기. 사람 개입/escalation 필요.",
                file=sys.stderr,
            )
            return
        # 지수 백오프(연속 빠른 재기동일수록 길게·cap 상한)
        backoff = min(self._backoff_base * (2 ** len(hist)), self._backoff_cap)
        if backoff > 0:
            self._sleep(backoff)
        if self._stop:
            return
        if not self._cleanup_dead_slot(slot):
            # 락 보유 중(살아있는 워커 의심) → 재기동 보류(split-brain 방지·이중실행 0).
            print(
                f"[supervisor] slot {slot} worker.lock 보유 중 — 재기동 보류"
                "(살아있는 워커 의심·split-brain 방지).",
                file=sys.stderr,
            )
            self._procs.pop(slot, None)
            return
        # 청소 완료 → 재기동
        self._procs[slot] = self._spawn_slot(slot)
        hist.append(self._clock())
        print(
            f"[supervisor] slot {slot} 재기동(rc={returncode}·백오프 {backoff:.1f}s·"
            f"최근 재기동 {len(hist)}회).",
            file=sys.stderr,
        )

    def monitor(self, max_cycles: int | None = None) -> int:
        """감독 루프 — 각 슬롯 returncode 폴링→죽음 감지→_handle_death.

        Args:
            max_cycles: 최대 사이클 수(테스트/바운드용). None이면 무한(프로덕션).
                        ★프로덕션 무한 루프는 start_guard.sh 워치독 하 + stop 플래그로 바운드.

        Returns:
            수행한 사이클 수.
        """
        cycles = 0
        while not self._stop:
            if max_cycles is not None and cycles >= max_cycles:
                break
            cycles += 1
            for slot in range(self._n):
                if slot in self._given_up:
                    continue
                proc = self._procs.get(slot)
                if proc is None:
                    continue
                rc = proc.poll()
                if rc is None:
                    continue  # 살아있음
                self._handle_death(slot, rc)
                if self._stop:
                    break
            if self._stop:
                break
            # ★F1(리뷰): 모든 슬롯이 crash-loop로 포기되면 감독할 워커가 0 → 무한 idle
            # 대신 escalation 후 탈출(main이 비-0 exit → 외부 감독[systemd]이 인지·재기동).
            # "조용한 무작동"도 사고로 보는 자원-안전 정책(시스템다운 전례 2회).
            if self._given_up and len(self._given_up) >= self._n:
                print(
                    f"[supervisor] ★전 슬롯({self._n}개) crash-loop 포기 — 감독할 워커 0. "
                    "supervisor 종료(외부 감독이 인지·재기동하도록 비-0 exit).",
                    file=sys.stderr,
                )
                break
            if self._monitor_interval > 0:
                self._sleep(self._monitor_interval)
        return cycles

    def request_stop(self, *_args: Any) -> None:
        """graceful shutdown 플래그를 set한다(SIGTERM/SIGINT 핸들러)."""
        self._stop = True

    def shutdown(self, grace: float = 5.0) -> None:
        """추적 중인 자식들에게만 SIGTERM→그레이스→SIGKILL(★pkill 금지·자기 자식만).

        시스템 전역 pkill -f는 타 프로세스 불침해 위반이라 절대 쓰지 않는다 — Popen 핸들로
        우리가 띄운 자식 PID에만 신호한다. grace초 내 안 죽으면 SIGKILL.
        """
        self._stop = True
        procs = list(self._procs.items())
        for _slot, proc in procs:
            try:
                proc.terminate()  # SIGTERM — 워커는 현재 tick 마치고 graceful 종료
            except Exception:
                pass
        deadline = self._clock() + grace
        for _slot, proc in procs:
            remaining = deadline - self._clock()
            try:
                proc.wait(timeout=remaining if remaining > 0 else 0.01)
            except Exception:
                # 그레이스 초과 → 강제 종료(SIGKILL) 후 reap.
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=grace)
                except Exception:
                    pass
        self._procs.clear()


# ── CLI 진입점 ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """supervisor 진입점 — 단일 인스턴스 락 → N 워커 기동 → 감독 루프.

    ★production 기동·concurrency:2 승격은 사용자 세션 승인 게이트(serve_supervisor.sh가
    start_guard.sh 워치독 하 기동). concurrency는 endpoint.yaml worker 블록(기본 1) 또는
    --concurrency 오버라이드.
    """
    parser = argparse.ArgumentParser(description="P1 워커풀 감독자(멀티 OS 프로세스)")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="동시 워커 슬롯 수(미지정이면 endpoint.yaml worker.concurrency·기본 1)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="워커 폴 간격(미지정이면 endpoint.yaml worker.poll_interval·기본 2.0)",
    )
    parser.add_argument(
        "--python-exe",
        type=str,
        default=None,
        help="워커 자식 exec용 conda env python 절대경로(미지정이면 endpoint.yaml)",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="감독 루프 최대 사이클(테스트/바운드용). 미지정이면 무한(프로덕션).",
    )
    args = parser.parse_args(argv)

    from core.config import get_worker_config

    cfg = get_worker_config()
    concurrency = (
        args.concurrency if args.concurrency is not None else cfg["concurrency"]
    )
    poll_interval = (
        args.poll_interval if args.poll_interval is not None else cfg["poll_interval"]
    )
    python_exe = args.python_exe or cfg["python_exe"]

    # ① supervisor 단일 인스턴스 락 — 두 supervisor 동시 기동 차단(최악의 split-brain).
    sup_lock = SingleInstanceLock(_DEFAULT_SUPERVISOR_LOCK)
    if not sup_lock.acquire():
        print(
            "[supervisor] 이미 다른 supervisor가 실행 중입니다(단일 인스턴스 락). 종료.",
            file=sys.stderr,
        )
        return 1

    sup = WorkerSupervisor(
        concurrency, python_exe=python_exe, poll_interval=poll_interval
    )

    # ② graceful shutdown — SIGTERM/SIGINT 시 플래그 set(감독 루프가 다음 사이클 전 탈출).
    signal.signal(signal.SIGTERM, sup.request_stop)
    signal.signal(signal.SIGINT, sup.request_stop)

    try:
        print(
            f"[supervisor] 워커풀 기동 concurrency={concurrency} "
            f"python={python_exe} poll={poll_interval}s"
        )
        sup.start()
        sup.monitor(max_cycles=args.max_cycles)
    finally:
        sup.shutdown()
        sup_lock.release()
    # ★F1(리뷰): 전 슬롯 crash-loop 포기로 탈출했으면 비-0 exit — 외부 감독(systemd
    # Restart=)이 인지·재기동하도록(silent 무작동 방지·시스템다운 전례 정책).
    if len(sup._given_up) >= concurrency:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
