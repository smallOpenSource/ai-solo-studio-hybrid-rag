"""도구 호출 시험 스위트."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.client import LLMClient
from bench.config import load_env
from bench.modes import build, extract
from bench.scoring import score

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_RESULTS_ROOT = _PROJECT_ROOT / "results"

_ALL_MODES = ["native_fc", "prompt_json", "schema_strict", "prompt_json_retry"]

_RETRY_CORRECTION_MSG = (
    "형식 오류: {reason}. 정확한 JSON만 다시 출력하십시오:\n"
    '{{"name": "함수이름", "arguments": {{"인자명": "값"}}}}'
)


def _load_cases(paths: list[str | Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
    return cases


def _record(
    out_path: Path,
    suite: str,
    case: dict[str, Any],
    mode: str,
    got: dict[str, Any] | None,
    verdict: str,
    detail: str,
    latency_ms: float,
    finish_reason: str,
    usage: dict[str, Any],
    timings: dict[str, Any],
    reasoning_content: str,
    content: str,
    thinking: str = "on",
    attempt: int = 1,
) -> None:
    row = {
        "suite": suite,
        "case_id": case.get("id"),
        "lang": case.get("lang"),
        "mode": mode,
        "thinking": thinking,
        "expected": case.get("expected"),
        "got": got,
        "verdict": verdict,
        "detail": detail,
        "latency_ms": round(latency_ms, 1),
        "finish_reason": finish_reason,
        "usage": usage,
        "timings_summary": timings,
        "reasoning_chars": len(reasoning_content),
        "content_chars": len(content),
        "attempt": attempt,
    }
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def _run_prompt_json_retry(
    client: LLMClient,
    case: dict[str, Any],
    model: str,
    schema_supported: bool,
    interval: float,
    out_path: Path,
    thinking: str = "on",
    thinking_extra_body: dict[str, Any] | None = None,
) -> None:
    """prompt_json_retry 모드: 검증 실패 시 ≤2회 재시도."""
    kwargs = build(case, "prompt_json_retry", schema_supported)
    messages: list[dict[str, Any]] = list(kwargs["messages"])

    got: dict[str, Any] | None = None
    resp: dict[str, Any] = {}
    verdict = "parse_fail"
    detail = ""

    # thinking_extra_body를 kwargs에 병합
    call_kwargs = {k: v for k, v in kwargs.items() if k not in ("messages",)}
    if thinking_extra_body:
        existing_extra = call_kwargs.get("extra_body") or {}
        merged_extra: dict[str, Any] = {**existing_extra, **thinking_extra_body}
        call_kwargs["extra_body"] = merged_extra

    for attempt in range(1, 4):  # 최대 3번 시도 (초기 1 + 재시도 2)
        try:
            resp = client.chat(
                model=model,
                messages=messages,
                max_tokens=case.get("max_tokens", 1024),
                interval=interval if attempt == 1 else 0.0,
                **call_kwargs,
            )
        except Exception as exc:  # 서버 오류 — 케이스를 error로 기록하고 계속
            _record(
                out_path=out_path,
                suite="toolcall",
                case=case,
                mode="prompt_json_retry",
                got=None,
                verdict="error",
                detail=f"호출 실패: {exc}",
                latency_ms=0.0,
                finish_reason="",
                usage={},
                timings={},
                reasoning_content="",
                content="",
                thinking=thinking,
                attempt=attempt,
            )
            return
        got = extract(resp, "prompt_json_retry")
        result = score(case.get("expected", {}), got)
        verdict = result["verdict"]
        detail = result["detail"]

        if verdict == "correct":
            break

        # 재시도: 교정 메시지 추가
        if attempt < 3:
            assistant_content = resp.get("content") or ""
            messages.append({"role": "assistant", "content": assistant_content})
            correction = _RETRY_CORRECTION_MSG.format(reason=detail or "JSON 파싱 실패")
            messages.append({"role": "user", "content": correction})
    else:
        attempt = 3

    _record(
        out_path=out_path,
        suite="toolcall",
        case=case,
        mode="prompt_json_retry",
        got=got,
        verdict=verdict,
        detail=detail,
        latency_ms=resp.get("latency_ms", 0.0),
        finish_reason=resp.get("finish_reason", ""),
        usage=resp.get("usage", {}),
        timings=resp.get("timings", {}),
        reasoning_content=resp.get("reasoning_content", ""),
        content=resp.get("content", ""),
        thinking=thinking,
        attempt=attempt,
    )


def run(
    model: str,
    cases_paths: list[str | Path],
    modes: list[str] | None = None,
    smoke: bool = False,
    limit: int | None = None,
    interval: float = 0.5,
    schema_supported: bool = True,
    env: dict[str, str] | None = None,
    thinking: str = "on",
    case_ids: list[str] | None = None,
) -> Path:
    """도구 호출 시험 스위트 실행.

    Args:
        model: 모델 alias
        cases_paths: JSONL 케이스 파일 경로 목록
        modes: 실행할 모드 목록 (None이면 전체)
        smoke: True면 첫 1케이스×native_fc만 실행
        limit: 케이스 수 제한
        interval: 요청 간 대기(초)
        schema_supported: schema_strict 사용 가능 여부
        env: 환경변수 dict (None이면 .env에서 로드)
        thinking: "on" 또는 "off" — off 시 enable_thinking=False extra_body 주입
        case_ids: 실행할 케이스 id 목록 (None이면 전체)

    Returns:
        results/<ts>/ 디렉토리 경로
    """
    e = env or load_env()
    cases = _load_cases(cases_paths)
    if case_ids is not None:
        cases = [c for c in cases if c.get("id") in case_ids]
    if limit is not None:
        cases = cases[:limit]

    active_modes = ["native_fc"] if smoke else (modes or _ALL_MODES)
    if smoke:
        cases = cases[:1]

    # thinking off 시 extra_body 준비
    thinking_extra_body: dict[str, Any] | None = None
    if thinking == "off":
        thinking_extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _RESULTS_ROOT / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "raw.jsonl"

    # meta.json 기록
    meta = {
        "suite": "toolcall",
        "model": model,
        "thinking": thinking,
        "ts": ts,
        "cases_paths": [str(p) for p in cases_paths],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with LLMClient(env=e) as client:
        for case in cases:
            for mode in active_modes:
                if mode == "schema_strict" and not schema_supported:
                    continue

                if mode == "prompt_json_retry":
                    _run_prompt_json_retry(
                        client=client,
                        case=case,
                        model=model,
                        schema_supported=schema_supported,
                        interval=interval,
                        out_path=out_path,
                        thinking=thinking,
                        thinking_extra_body=thinking_extra_body,
                    )
                    continue

                try:
                    kwargs = build(case, mode, schema_supported)
                except ValueError:
                    continue

                # thinking_extra_body 병합
                extra_body = thinking_extra_body.copy() if thinking_extra_body else None

                try:
                    resp = client.chat(
                        model=model,
                        messages=kwargs["messages"],
                        tools=kwargs.get("tools"),
                        response_format=kwargs.get("response_format"),
                        max_tokens=case.get("max_tokens", 1024),
                        interval=interval,
                        extra_body=extra_body,
                    )
                except Exception as exc:  # 서버 오류 — error 기록 후 다음 모드 계속
                    _record(
                        out_path=out_path,
                        suite="toolcall",
                        case=case,
                        mode=mode,
                        got=None,
                        verdict="error",
                        detail=f"호출 실패: {exc}",
                        latency_ms=0.0,
                        finish_reason="",
                        usage={},
                        timings={},
                        reasoning_content="",
                        content="",
                        thinking=thinking,
                    )
                    continue
                got = extract(resp, mode)
                result = score(case.get("expected", {}), got)

                _record(
                    out_path=out_path,
                    suite="toolcall",
                    case=case,
                    mode=mode,
                    got=got,
                    verdict=result["verdict"],
                    detail=result["detail"],
                    latency_ms=resp.get("latency_ms", 0.0),
                    finish_reason=resp.get("finish_reason", ""),
                    usage=resp.get("usage", {}),
                    timings=resp.get("timings", {}),
                    reasoning_content=resp.get("reasoning_content", ""),
                    content=resp.get("content", ""),
                    thinking=thinking,
                )

    return out_dir
