"""orch.browser_worker — §8 browser-use 워커 프로세스 entrypoint.

★★ 이 파일은 **독립 프로세스**로 실행된다 (orch 노드에서 import 금지).
   playwright env 에서 실행: /opt/conda/envs/playwright/bin/python -m orch.browser_worker

safe_run.sh 가 이 프로세스를 감싸므로 timeout/vmem 상한이 토폴로지상 실재한다.
in-process agent.run() 호출은 종료성 P0 위반 → 이 아키텍처로 회피.

사용:
    python browser_worker.py --task "..." --url "https://..." [--max-steps 10]
    python browser_worker.py --selftest-hang            # u6 종료성 테스트 모드

stdout: JSON {"ok": true, "result": "..."} 또는 {"ok": false, "error": "..."}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

# ★ max_steps 상수 (설계 상한 10 — 변경 금지·테스트가 단언함)
MAX_STEPS: int = 10

# 벽시계 timeout (safe_run timeout 보다 짧아야 함; 워커 자체 방어선)
_WALL_TIMEOUT_S: float = float(os.environ.get("BROWSER_WORKER_TIMEOUT_S", "90"))


def _emit(payload: dict) -> None:
    """JSON 결과를 stdout 에 출력하고 즉시 flush."""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _selftest_hang() -> None:
    """--selftest-hang: 의도적 무한루프. safe_run SIGTERM 으로 죽임을 실증."""
    # u6 종료성 P0 테스트용: 이 루프를 safe_run timeout 이 종료해야 한다.
    while True:
        time.sleep(0.1)


async def _run_agent(task: str, url: str, max_steps: int) -> dict:
    """browser-use Agent 를 max_steps 상한 + 벽시계 timeout 으로 실행."""
    # ★ 로컬 /v1 OpenAI-호환 엔드포인트 (BENCH_API_URL 환경변수 / LLM_GATEWAY 폴백)
    base_url = os.environ.get("BENCH_API_URL") or os.environ.get("LLM_GATEWAY")
    if not base_url:
        return {
            "ok": False,
            "error": "LLM endpoint not configured (BENCH_API_URL / LLM_GATEWAY)",
        }

    model_name = os.environ.get("BENCH_PRIMARY_MODEL") or os.environ.get(
        "LLM_BASE_MODEL", "gemma"
    )
    api_key = os.environ.get("BENCH_API_KEY", "dummy-local")

    try:
        from browser_use import Agent  # type: ignore[import]
        from browser_use.browser.profile import BrowserProfile  # type: ignore[import]
        from browser_use.browser.session import BrowserSession  # type: ignore[import]
        from browser_use.llm.openai.like import ChatOpenAILike  # type: ignore[import]
    except ImportError as exc:
        return {"ok": False, "error": f"browser-use import failed: {exc}"}

    # browser-use 자체 OpenAI-like 어댑터 (langchain_openai 불필요)
    llm = ChatOpenAILike(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
    )

    # Chromium headless — PLAYWRIGHT_BROWSERS_PATH 우선, 없으면 자동.
    # enable_default_extensions=False: uBlock 등 확장 다운로드 스킵 (속도·안정성).
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    profile = BrowserProfile(
        headless=True,
        executable_path=_find_chromium(browsers_path),
        enable_default_extensions=False,
    )
    session = BrowserSession(browser_profile=profile)

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        enable_signal_handler=False,  # 워커 프로세스 자체 시그널 처리 불필요
    )

    try:
        history = await asyncio.wait_for(
            agent.run(max_steps=max_steps),
            timeout=_WALL_TIMEOUT_S,
        )
        # AgentHistoryList → 마지막 결과 텍스트 추출
        result_text = _extract_result(history)
        return {"ok": True, "result": result_text, "steps": len(history.history)}
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": f"wall-clock timeout ({_WALL_TIMEOUT_S}s) exceeded",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        # 세션 정리 (브라우저 프로세스 누수 방지)
        with _suppress():
            await session.close()


def _find_chromium(browsers_path: str | None) -> str | None:
    """PLAYWRIGHT_BROWSERS_PATH 에서 chromium 실행파일 위치 탐색."""
    import glob as _glob

    if not browsers_path:
        return None
    patterns = [
        f"{browsers_path}/chromium-*/chrome-linux/chrome",
        f"{browsers_path}/chromium-*/chrome",
    ]
    for pat in patterns:
        matches = _glob.glob(pat)
        if matches:
            return matches[0]
    return None


def _extract_result(history) -> str:  # type: ignore[type-arg]
    """AgentHistoryList 에서 최종 결과 텍스트 추출."""
    try:
        # browser-use 0.13.x: final_result() 메서드
        r = history.final_result()
        if r:
            return str(r)
    except Exception:  # noqa: BLE001
        pass
    # 폴백: 마지막 model_output
    try:
        last = history.history[-1]
        return str(last.model_output or "")
    except Exception:  # noqa: BLE001
        return ""


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return True  # 정리 경로 예외 삼킴 (종료성 우선)


def main() -> int:
    parser = argparse.ArgumentParser(description="browser-use 워커 프로세스")
    parser.add_argument("--task", default="", help="자연어 지시")
    parser.add_argument("--url", default="", help="시작 URL")
    parser.add_argument(
        "--max-steps", type=int, default=MAX_STEPS, help=f"최대 단계 (≤{MAX_STEPS})"
    )
    parser.add_argument(
        "--selftest-hang",
        action="store_true",
        help="★테스트 모드: 의도적 무한루프 (safe_run 종료성 실증용)",
    )
    args = parser.parse_args()

    if args.selftest_hang:
        _selftest_hang()  # 절대 반환 안 함; safe_run SIGTERM 으로 종료됨
        return 0  # unreachable

    # max_steps 상한 강제 (호출자 초과 요청 방어)
    max_steps = min(args.max_steps, MAX_STEPS)

    if not args.task:
        _emit({"ok": False, "error": "--task 가 비어 있음"})
        return 1

    result = asyncio.run(_run_agent(args.task, args.url, max_steps))
    _emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
