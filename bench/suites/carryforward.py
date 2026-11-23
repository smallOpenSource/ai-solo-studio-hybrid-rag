"""기억 전달(carryforward) 시험 스위트."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.client import LLMClient
from bench.config import load_env
from bench.modes import extract
from bench.scoring import score
from core.history import _sanitize_assistant

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_RESULTS_ROOT = _PROJECT_ROOT / "results"


def _flatten_tool_result(tool_result: dict[str, Any]) -> list[tuple[str, Any]]:
    """tool_result dict을 k=v 쌍 목록으로 평탄화한다."""
    pairs: list[tuple[str, Any]] = []
    for k, v in tool_result.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                pairs.append((f"{k}.{sub_k}", sub_v))
        else:
            pairs.append((k, v))
    return pairs


def _build_carry_note(accumulated: list[tuple[str, Any]]) -> str:
    """이전 스텝 결과 누적값을 user 노트 문자열로 만든다."""
    if not accumulated:
        return ""
    parts = ", ".join(f"{k}={v!r}" for k, v in accumulated)
    return f"지금까지 확인된 값: {parts}"


def _record(
    out_path: Path,
    chain_id: str,
    lang: str,
    variant: str,
    rep: int,
    step_idx: int,
    expected: dict[str, Any],
    got: dict[str, Any] | None,
    verdict: str,
    detail: str,
    latency_ms: float,
    finish_reason: str,
    usage: dict[str, Any],
    reasoning_content: str,
    content: str,
    thinking: str = "on",
) -> None:
    row = {
        "suite": "carryforward",
        "chain_id": chain_id,
        "lang": lang,
        "variant": variant,
        "rep": rep,
        "step_idx": step_idx,
        "thinking": thinking,
        "expected": expected,
        "got": got,
        "verdict": verdict,
        "detail": detail,
        "latency_ms": round(latency_ms, 1),
        "finish_reason": finish_reason,
        "usage": usage,
        "reasoning_chars": len(reasoning_content),
        "content_chars": len(content),
    }
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def _run_chain(
    client: LLMClient,
    chain: dict[str, Any],
    model: str,
    carry_on: bool,
    rep: int,
    interval: float,
    out_path: Path,
    thinking: str = "on",
    thinking_extra_body: dict[str, Any] | None = None,
) -> bool:
    """단일 체인 1회 실행. 전 스텝 correct면 True 반환."""
    tools: list[dict[str, Any]] = chain.get("tools") or []
    steps: list[dict[str, Any]] = chain.get("steps") or []
    goal: str = chain.get("goal", "")
    chain_id: str = chain.get("id", "")
    lang: str = chain.get("lang", "")
    variant = "carry_on" if carry_on else "carry_off"

    messages: list[dict[str, Any]] = []
    accumulated: list[tuple[str, Any]] = []
    all_correct = True

    for step_idx, step in enumerate(steps):
        expected: dict[str, Any] = step.get("expected", {})
        tool_result: dict[str, Any] = step.get("tool_result", {})

        # carry_on: 매 턴 앞에 누적 노트 주입
        if carry_on and accumulated:
            note = _build_carry_note(accumulated)
            messages.append({"role": "user", "content": note})

        # 첫 턴: 목표 메시지
        if step_idx == 0:
            messages.append({"role": "user", "content": goal})

        try:
            resp = client.chat(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=1024,
                interval=interval,
                extra_body=thinking_extra_body.copy() if thinking_extra_body else None,
            )
        except (
            Exception
        ) as exc:  # 재시도 소진 후 서버 오류 — 체인만 중단, 스위트는 계속
            _record(
                out_path=out_path,
                chain_id=chain_id,
                lang=lang,
                variant=variant,
                rep=rep,
                step_idx=step_idx,
                expected=expected,
                got=None,
                verdict="error",
                detail=f"호출 실패: {exc}",
                latency_ms=0.0,
                finish_reason="",
                usage={},
                reasoning_content="",
                content="",
                thinking=thinking,
            )
            return False

        got = extract(resp, "native_fc")
        result = score(expected, got)

        _record(
            out_path=out_path,
            chain_id=chain_id,
            lang=lang,
            variant=variant,
            rep=rep,
            step_idx=step_idx,
            expected=expected,
            got=got,
            verdict=result["verdict"],
            detail=result["detail"],
            latency_ms=resp.get("latency_ms", 0.0),
            finish_reason=resp.get("finish_reason", ""),
            usage=resp.get("usage", {}),
            reasoning_content=resp.get("reasoning_content", ""),
            content=resp.get("content", ""),
            thinking=thinking,
        )

        if result["verdict"] != "correct":
            all_correct = False

        # 모델 응답을 히스토리에 추가 — 절단된 tool_call arguments(invalid JSON)가
        # 다음 콜의 서버 템플릿 파싱을 500으로 오염시키지 않도록 정화 후 추가
        messages.append(_sanitize_assistant(resp["message"]))

        # tool 결과를 tool 역할로 주입
        tool_call_id = ""
        if resp.get("tool_calls"):
            tool_call_id = resp["tool_calls"][0].get("id", "")
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
        )

        # 누적 값 갱신
        accumulated.extend(_flatten_tool_result(tool_result))

    return all_correct


def run(
    model: str,
    chains_path: str | Path,
    reps: int = 3,
    interval: float = 0.5,
    env: dict[str, str] | None = None,
    thinking: str = "on",
    chain_ids: list[str] | None = None,
) -> Path:
    """기억 전달 스위트 실행.

    Args:
        model: 모델 alias
        chains_path: multistep JSONL 파일 경로
        reps: ON/OFF 각각 반복 횟수
        interval: 요청 간 대기(초)
        env: 환경변수 dict
        thinking: "on" 또는 "off" — off 시 enable_thinking=False extra_body 주입
        chain_ids: 실행할 체인 id 목록 (None이면 전체)

    Returns:
        results/<ts>/ 디렉토리 경로
    """
    e = env or load_env()
    chains: list[dict[str, Any]] = []
    path = Path(chains_path)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chains.append(json.loads(line))

    if chain_ids is not None:
        chains = [c for c in chains if c.get("id") in chain_ids]

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
        "suite": "carryforward",
        "model": model,
        "thinking": thinking,
        "ts": ts,
        "cases_paths": [str(chains_path)],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with LLMClient(env=e) as client:
        for chain in chains:
            for carry_on in (False, True):
                for rep in range(1, reps + 1):
                    _run_chain(
                        client=client,
                        chain=chain,
                        model=model,
                        carry_on=carry_on,
                        rep=rep,
                        interval=interval,
                        out_path=out_path,
                        thinking=thinking,
                        thinking_extra_body=thinking_extra_body,
                    )

    return out_dir
