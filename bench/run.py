"""CLI 진입점 — python -m bench.run --probe | --suite {toolcall,carryforward}"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bench.bge_client import BGEClient
from bench.client import LLMClient
from bench.config import (
    get_primary_model,
    load_env,
    update_models_thinking_off,
)
from bench.modes import build

_PROJECT_ROOT = Path(__file__).parent.parent
_CAPTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "endpoint_captures"

_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "도시의 날씨를 조회한다",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "도시 이름"},
            },
            "required": ["city"],
        },
    },
}

_PROBE_MESSAGES = [{"role": "user", "content": "서울 날씨 알려줘"}]


def _strip_secrets(obj: Any, env: dict[str, str]) -> Any:
    """응답 dict에서 API 키 평문을 제거한다."""
    secrets = {v for v in env.values() if len(v) > 8}
    text = json.dumps(obj, ensure_ascii=False)
    for secret in secrets:
        text = text.replace(secret, "***")
    return json.loads(text)


def _save_capture(name: str, data: Any, env: dict[str, str]) -> None:
    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = _CAPTURES_DIR / f"{name}.json"
    cleaned = _strip_secrets(data, env)
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [capture] {path.name} 저장됨")


def run_probe(capture: bool = False) -> None:
    """연결 점검 — 순차 ≤8콜."""
    env = load_env()
    model = get_primary_model(env)
    print(f"\n=== probe 시작 (model={model}) ===\n")

    with LLMClient(env=env) as client:
        # ① GET /models
        print("① GET /models ...")
        try:
            models = client.models()
            print(f"  모델 수: {len(models)}")
            if capture:
                _save_capture("models_list", {"data": models}, env)
        except Exception as exc:
            print(f"  실패: {exc}")

        # ② native_fc 1콜 — reasoning 모델의 thinking 절단(finish_reason=length)은
        # "미지원"이 아니므로 thinking-OFF 우선, 절단 시 큰 예산으로 1회 재시도
        print("\n② native_fc 1콜 (thinking-OFF 우선, 절단≠미지원) ...")
        fc_supported = False
        try:
            resp = client.chat(
                model=model,
                messages=_PROBE_MESSAGES,
                tools=[_PROBE_TOOL],
                max_tokens=256,
                interval=0.5,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            if not resp.get("tool_calls") and resp.get("finish_reason") == "length":
                print("  1차(256tok·thinking-OFF) 절단 → 2차(768tok) 재시도")
                resp = client.chat(
                    model=model,
                    messages=_PROBE_MESSAGES,
                    tools=[_PROBE_TOOL],
                    max_tokens=768,
                    interval=0.5,
                )
            fc_supported = bool(resp.get("tool_calls"))
            print(f"  finish_reason={resp.get('finish_reason')!r}")
            print(f"  tool_calls 수: {len(resp.get('tool_calls') or [])}")
            if resp.get("finish_reason") == "length" and not fc_supported:
                print("  (주의: 응답 절단 — '미지원'으로 단정 불가)")
            print(f"  native_fc 지원: {fc_supported}")
            if capture:
                _save_capture("probe_native_fc", resp.get("raw", {}), env)
        except Exception as exc:
            print(f"  실패: {exc}")

        # ③ schema_strict 1콜 시도 → 지원 여부
        print("\n③ schema_strict 1콜 시도 (max_tokens=256) ...")
        schema_supported = False
        schema_resp: dict[str, Any] = {}
        try:
            case = {
                "prompt": "서울 날씨 알려줘",
                "tools": [_PROBE_TOOL],
                "expected": {"name": "get_weather", "args": {"city": None}},
            }
            kwargs = build(case, "schema_strict", schema_supported=True)
            schema_resp = client.chat(
                model=model,
                messages=kwargs["messages"],
                response_format=kwargs.get("response_format"),
                max_tokens=256,
                interval=0.5,
            )
            schema_supported = schema_resp.get("finish_reason") in (
                "stop",
                "length",
            ) or bool(schema_resp.get("content"))
            print(f"  finish_reason={schema_resp.get('finish_reason')!r}")
            print(f"  schema_strict 지원: {schema_supported}")
            if capture:
                cap_data = schema_resp.get("raw", {})
                if not schema_supported:
                    cap_data = {"provisional": True, **cap_data}
                _save_capture("probe_schema_strict", cap_data, env)
        except Exception as exc:
            print(f"  실패 (미지원 가능): {exc}")
            if capture:
                _save_capture(
                    "probe_schema_strict", {"provisional": True, "error": str(exc)}, env
                )

        # ④ thinking-OFF 2변형 시도
        print("\n④ thinking-OFF 지원 여부 탐색 ...")
        thinking_off_supported = False

        # 변형 A: chat_template_kwargs extra_body
        print("  [A] chat_template_kwargs enable_thinking=false ...")
        try:
            resp_a = client.chat(
                model=model,
                messages=_PROBE_MESSAGES,
                max_tokens=256,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                interval=0.5,
            )
            rc_a = resp_a.get("reasoning_content") or ""
            thinking_off_a = len(rc_a) == 0
            print(
                f"    reasoning_content 길이={len(rc_a)}, thinking-OFF={thinking_off_a}"
            )
            if capture:
                cap = resp_a.get("raw", {})
                if not thinking_off_a:
                    cap = {"provisional": True, **cap}
                _save_capture("probe_thinking_off_a", cap, env)
            if thinking_off_a:
                thinking_off_supported = True
        except Exception as exc:
            print(f"    실패: {exc}")

        # 변형 B: /no_think 프롬프트 접두어
        print("  [B] /no_think 프롬프트 ...")
        try:
            no_think_messages = [
                {"role": "user", "content": "/no_think 서울 날씨 알려줘"}
            ]
            resp_b = client.chat(
                model=model,
                messages=no_think_messages,
                max_tokens=256,
                interval=0.5,
            )
            rc_b = resp_b.get("reasoning_content") or ""
            thinking_off_b = len(rc_b) == 0
            print(
                f"    reasoning_content 길이={len(rc_b)}, thinking-OFF={thinking_off_b}"
            )
            if capture:
                cap = resp_b.get("raw", {})
                if not thinking_off_b:
                    cap = {"provisional": True, **cap}
                _save_capture("probe_thinking_off_b", cap, env)
            if thinking_off_b:
                thinking_off_supported = True
        except Exception as exc:
            print(f"    실패: {exc}")

        print(f"\n  thinking-OFF 지원: {thinking_off_supported}")

        # models.yaml 갱신
        update_models_thinking_off(
            model, thinking_off_supported if thinking_off_supported else None
        )
        print(
            f"  models.yaml 갱신 완료 (supports_thinking_off={thinking_off_supported or None})"
        )

    # ⑤ BGE embed(["ping"]) dim 확인
    print("\n⑤ BGE embed(['ping']) dim 확인 ...")
    bge = BGEClient(env=env)
    embed_dim: int | None = None
    try:
        vecs = bge.embed(["ping"])
        embed_dim = len(vecs[0]) if vecs else None
        print(f"  dim={embed_dim}")
        if capture:
            _save_capture("probe_bge_embed", {"dense_embeddings": [vecs[0][:4]]}, env)
    except Exception as exc:
        print(f"  실패: {exc}")

    # ⑥ rerank 1콜
    print("\n⑥ BGE rerank 1콜 ...")
    try:
        rr = bge.rerank(
            query="What is Azure?",
            documents=["Azure is a cloud platform.", "Bananas are yellow."],
        )
        print(f"  응답 타입: {type(rr).__name__}")
        if capture:
            _save_capture("probe_bge_rerank", rr, env)
    except Exception as exc:
        print(f"  실패: {exc}")

    print("\n=== probe 완료 ===")
    print(
        f"  native_fc={fc_supported}, schema_strict={schema_supported}, "
        f"thinking_off={thinking_off_supported}, bge_dim={embed_dim}"
    )


def run_suite(
    suite_name: str,
    model: str | None,
    smoke: bool,
    limit: int | None,
    reps: int,
    interval: float,
    thinking: str = "on",
    case_ids: list[str] | None = None,
    chain_ids: list[str] | None = None,
) -> None:
    env = load_env()
    m = model or get_primary_model(env)

    if suite_name == "toolcall":
        from bench.suites.toolcall import run as tc_run

        cases_dir = _PROJECT_ROOT / "cases"
        paths = list(cases_dir.glob("toolcall_*.jsonl")) if cases_dir.exists() else []
        if not paths:
            print("cases/ 디렉토리에 toolcall_*.jsonl 파일이 없습니다.")
            return
        out_dir = tc_run(
            model=m,
            cases_paths=paths,
            smoke=smoke,
            limit=limit,
            interval=interval,
            env=env,
            thinking=thinking,
            case_ids=case_ids,
        )
        print(f"\n결과 저장: {out_dir}")

        from bench.report import generate_report, _load_jsonl

        rows = _load_jsonl(out_dir / "raw.jsonl")
        generate_report(rows, out_dir / "report.md")

    elif suite_name == "carryforward":
        from bench.suites.carryforward import run as cf_run

        chains_path = _PROJECT_ROOT / "cases" / "multistep.jsonl"
        if not chains_path.exists():
            print(f"chains 파일 없음: {chains_path}")
            return
        out_dir = cf_run(
            model=m,
            chains_path=chains_path,
            reps=reps,
            interval=interval,
            env=env,
            thinking=thinking,
            chain_ids=chain_ids,
        )
        print(f"\n결과 저장: {out_dir}")

        from bench.report import generate_report, _load_jsonl

        rows = _load_jsonl(out_dir / "raw.jsonl")
        generate_report(rows, out_dir / "report.md")

    else:
        print(f"알 수 없는 스위트: {suite_name}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="one_man_corp 모델 검증 벤치")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true", help="연결 점검 (≤8콜, 순차)")
    group.add_argument(
        "--suite", choices=["toolcall", "carryforward"], help="스위트 실행"
    )

    parser.add_argument(
        "--capture", action="store_true", help="probe 응답을 fixtures에 저장"
    )
    parser.add_argument(
        "--model", help="사용할 모델 alias (기본: .env BENCH_PRIMARY_MODEL)"
    )
    parser.add_argument(
        "--smoke", action="store_true", help="첫 1케이스×native_fc만 실행"
    )
    parser.add_argument("--limit", type=int, help="케이스 수 제한")
    parser.add_argument("--reps", type=int, default=3, help="carryforward 반복 횟수")
    parser.add_argument("--interval", type=float, default=0.5, help="요청 간 대기(초)")
    parser.add_argument(
        "--thinking",
        choices=["on", "off"],
        default="on",
        help="thinking 모드 (기본: on). off 시 enable_thinking=False 주입",
    )
    parser.add_argument(
        "--case",
        dest="case_ids",
        help="toolcall: 실행할 케이스 id (쉼표 구분, 예: ko-02,en-05)",
    )
    parser.add_argument(
        "--chain",
        dest="chain_ids",
        help="carryforward: 실행할 체인 id (쉼표 구분)",
    )

    args = parser.parse_args()

    case_ids: list[str] | None = None
    if args.case_ids:
        case_ids = [c.strip() for c in args.case_ids.split(",") if c.strip()]

    chain_ids: list[str] | None = None
    if args.chain_ids:
        chain_ids = [c.strip() for c in args.chain_ids.split(",") if c.strip()]

    if args.probe:
        run_probe(capture=args.capture)
    else:
        run_suite(
            suite_name=args.suite,
            model=args.model,
            smoke=args.smoke,
            limit=args.limit,
            reps=args.reps,
            interval=args.interval,
            thinking=args.thinking,
            case_ids=case_ids,
            chain_ids=chain_ids,
        )


if __name__ == "__main__":
    main()
