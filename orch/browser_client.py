"""orch.browser_client — safe_run 래핑 browser-use 호출 헬퍼.

orch 노드에서 browser-use 를 사용할 때 이 모듈만 import 한다.
browser_worker.py 를 **별도 프로세스**로 실행 → safe_run.sh timeout/vmem 상한이 실재.

★ in-process browser_use.Agent.run() 호출 금지 (종료성 P0 위반).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

# 워커 프로세스 실행 환경 (playwright env)
_PLAYWRIGHT_PYTHON = "/opt/conda/envs/playwright/bin/python"
_WORKER_MODULE = Path(__file__).resolve().parent / "browser_worker.py"
_SAFE_RUN = Path(__file__).resolve().parent.parent / ".omc" / "guards" / "safe_run.sh"


def run_browser_task(
    task: str,
    url: str = "",
    *,
    timeout_s: int = 120,
    vmem_kb: int = 4_000_000,
) -> dict:
    """browser-use Agent 를 safe_run 래핑 서브프로세스로 실행.

    Args:
        task: 자연어 지시.
        url:  시작 URL (선택).
        timeout_s: safe_run 벽시계 timeout (초). 워커가 이 시간 내 종료 안 하면 SIGTERM.
        vmem_kb:   가상메모리 상한 (KB). 0=타임아웃 전용.

    Returns:
        {"ok": bool, "result": str, ...} — 워커 stdout JSON 파싱값.
        프로세스 비-정상 종료 시: {"ok": False, "error": "...", "returncode": int}.
    """
    cmd = [
        "bash",
        str(_SAFE_RUN),
        str(timeout_s),
        str(vmem_kb),
        "--",
        _PLAYWRIGHT_PYTHON,
        str(_WORKER_MODULE),
        "--task",
        task,
        "--url",
        url,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # 새 프로세스 그룹 — 손자 orphan 차단
    )
    try:
        # safe_run 자체 timeout 보다 조금 더 넉넉하게 대기 (SIGKILL +5s 여유)
        out, err = proc.communicate(timeout=timeout_s + 15)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return {
            "ok": False,
            "error": f"communicate timeout exceeded ({timeout_s + 15}s)",
            "returncode": -1,
        }
    finally:
        if proc.poll() is None:
            _kill_group(proc)

    stdout_text = (out or b"").decode("utf-8", "replace").strip()
    if proc.returncode == 0 and stdout_text:
        try:
            return json.loads(stdout_text)
        except json.JSONDecodeError:
            return {"ok": True, "result": stdout_text}

    stderr_text = (err or b"").decode("utf-8", "replace").strip()
    return {
        "ok": False,
        "error": stderr_text[-400:] if stderr_text else "worker exited non-zero",
        "returncode": proc.returncode,
    }


def _kill_group(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """프로세스 그룹 전체 SIGTERM → SIGKILL (손자 orphan 차단)."""
    with _suppress():
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            with _suppress():
                proc.wait(timeout=5)


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return True  # 정리 경로 예외 삼킴
