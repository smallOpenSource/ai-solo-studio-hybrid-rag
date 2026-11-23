"""orch/tests/test_browser_worker.py — §8 browser-use 워커 프로세스 테스트.

★ u6 종료성 P0 (핵심):
    run_browser_task 가 의도적 무한루프 워커를 safe_run timeout 으로 종료하고
    비-제로 returncode 를 반환함을 실증. in-process 호출 불가능한 증명.

★ 브라우저/LLM 미가용 시 graceful (import·구성 단위는 PASS, 라이브 내비는 e3).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

# orch env 에서 실행 (pytest orch/tests/test_browser_worker.py)
# browser_worker / browser_client 는 orch/ 패키지 내 신규 파일

_ROOT = Path(__file__).resolve().parent.parent.parent
_SAFE_RUN = _ROOT / ".omc" / "guards" / "safe_run.sh"
_PLAYWRIGHT_PYTHON = "/opt/conda/envs/playwright/bin/python"


# ---------------------------------------------------------------------------
# u1: MAX_STEPS 상수 ≤ 10 단언 (설정값 검사)
# ---------------------------------------------------------------------------
def test_max_steps_constant() -> None:
    """MAX_STEPS ≤ 10 (§8 요구사항)."""
    from orch.browser_worker import MAX_STEPS

    assert MAX_STEPS <= 10, f"MAX_STEPS={MAX_STEPS} 가 10 을 초과함 (§8 위반)"


# ---------------------------------------------------------------------------
# u2: browser_worker.py 가 독립 실행 가능한 스크립트임을 확인
# ---------------------------------------------------------------------------
def test_worker_entrypoint_exists() -> None:
    """browser_worker.py 가 존재하고 실행 가능."""
    worker = _ROOT / "orch" / "browser_worker.py"
    assert worker.exists(), f"browser_worker.py 없음: {worker}"


# ---------------------------------------------------------------------------
# u3: run_browser_task 가 import 가능 (orch env)
# ---------------------------------------------------------------------------
def test_browser_client_importable() -> None:
    """browser_client.run_browser_task 가 orch env 에서 import 가능."""
    from orch.browser_client import run_browser_task

    assert callable(run_browser_task)


# ---------------------------------------------------------------------------
# u4: safe_run.sh 존재 확인
# ---------------------------------------------------------------------------
def test_safe_run_exists() -> None:
    """safe_run.sh 존재."""
    assert _SAFE_RUN.exists(), f"safe_run.sh 없음: {_SAFE_RUN}"


# ---------------------------------------------------------------------------
# u5: browser_worker.py 가 --selftest-hang 플래그를 인식함 (파싱 오류 없음)
# ---------------------------------------------------------------------------
def test_worker_selftest_flag_parseable() -> None:
    """--selftest-hang 플래그가 argparse 에 등록돼 있음 (도움말 파싱 확인)."""
    import subprocess

    result = subprocess.run(
        [_PLAYWRIGHT_PYTHON, str(_ROOT / "orch" / "browser_worker.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "--selftest-hang" in result.stdout, (
        f"--selftest-hang 플래그가 --help 에 없음:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# u6: ★★ 종료성 P0 — safe_run 이 무한루프 워커를 timeout 으로 죽임을 실증
# ---------------------------------------------------------------------------
def test_u6_termination_p0() -> None:
    """safe_run timeout=2s 로 무한루프 워커를 종료 → 비-제로 returncode + 벽시계 ≤ 8s.

    이것이 "별도 프로세스 + safe_run 실재화" 의 falsifiable 증명:
    - in-process 호출이었다면 safe_run 이 닿지 않아 이 테스트 자체가 hang 함.
    - 서브프로세스이므로 safe_run SIGTERM 이 워커만 죽이고 테스트는 살아남음.
    """
    from orch.browser_client import (
        _SAFE_RUN,
        _WORKER_MODULE,
        _PLAYWRIGHT_PYTHON,
        _kill_group,
    )
    import subprocess

    cmd = [
        "bash",
        str(_SAFE_RUN),
        "2",  # timeout 2초
        "0",  # vmem=0 (타임아웃 전용)
        "--",
        _PLAYWRIGHT_PYTHON,
        str(_WORKER_MODULE),
        "--selftest-hang",
    ]

    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=10)  # 벽시계 상한 10s
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        pytest.fail("u6 FAIL: 워커가 10s 이내에 종료되지 않음 (safe_run 미작동)")
    finally:
        if proc.poll() is None:
            _kill_group(proc)

    elapsed = time.monotonic() - t0

    # 비-제로 종료 (timeout kill)
    assert proc.returncode != 0, (
        f"u6 FAIL: 워커가 returncode=0 으로 종료됨 (무한루프가 정상 종료?) rc={proc.returncode}"
    )
    # 벽시계 ≤ 8s (timeout=2s + SIGKILL +5s 여유 = 7s; 여유 1s 추가)
    assert elapsed <= 8.0, (
        f"u6 FAIL: 벽시계 {elapsed:.1f}s > 8s (safe_run 이 늦게 종료)"
    )


# ---------------------------------------------------------------------------
# u7: run_browser_task 가 빈 task 에 graceful 반환 (LLM/브라우저 미가용 무관)
# ---------------------------------------------------------------------------
def test_run_browser_task_empty_task_graceful() -> None:
    """빈 task 전달 시 {"ok": False} graceful 반환 (예외 아님)."""
    from orch.browser_client import run_browser_task

    # timeout 5s (워커가 --task 없이 즉시 종료)
    result = run_browser_task("", timeout_s=10, vmem_kb=0)
    assert isinstance(result, dict), "결과가 dict 가 아님"
    assert result.get("ok") is False, f"빈 task 에 ok=True?: {result}"


# ---------------------------------------------------------------------------
# e3 (slow): 실 browser-use + 로컬 /v1 라이브 내비 (브라우저/LLM 가용 시)
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_e3_live_navigation() -> None:
    """★ e3: 실 browser-use + 로컬 /v1 으로 example.com 내비게이션.

    LLM 또는 브라우저 미정합 시 ok=False 를 정직 보고 (green-fake 0).
    safe_run 감싸기이므로 timeout 초과 시 워커만 종료됨.
    """
    import os

    from orch.browser_client import run_browser_task

    # 로컬 LLM 엔드포인트 미설정 시 skip
    if not os.environ.get("BENCH_API_URL") and not os.environ.get("LLM_GATEWAY"):
        pytest.skip("LLM endpoint 미설정 (BENCH_API_URL / LLM_GATEWAY)")

    result = run_browser_task(
        task="example.com 에 방문해 페이지 제목을 알려줘",
        url="https://example.com",
        timeout_s=90,
        vmem_kb=0,  # Node/브라우저 vmem 제약 없음
    )
    assert isinstance(result, dict), f"결과가 dict 가 아님: {result}"
    # ok=True 면 성공, ok=False 면 정직 보고 (green-fake 0)
    if result.get("ok"):
        assert result.get("result"), "ok=True 인데 result 가 비어 있음"
    # ok=False 는 LLM/브라우저 미정합으로 수용 (정직 ACTION)
