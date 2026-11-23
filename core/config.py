"""설정 로더 — endpoint.yaml + models.yaml + .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values


_PROJECT_ROOT = Path(__file__).parent.parent


def load_env() -> dict[str, str]:
    """프로젝트 루트의 .env를 로드하고 os.environ과 병합한다.

    os.environ이 .env보다 우선한다 (배포 환경 오버라이드 허용).
    """
    env_path = _PROJECT_ROOT / ".env"
    file_values: dict[str, str | None] = (
        dotenv_values(env_path) if env_path.exists() else {}
    )
    merged: dict[str, str] = {}
    for k, v in file_values.items():
        if v is not None:
            merged[k] = v
    # os.environ 우선
    merged.update({k: v for k, v in os.environ.items()})
    return merged


def load_endpoint_config() -> dict[str, Any]:
    """config/endpoint.yaml을 로드한다."""
    path = _PROJECT_ROOT / "config" / "endpoint.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_web_config() -> dict[str, Any]:
    """endpoint.yaml의 web 블록(웹서버 host/port·정적 경로)을 돌려준다.

    웹서버 설정을 코드에 하드코딩하지 않기 위한 로더(수용 18). web 블록이
    없으면 안전한 기본값(localhost·8800)을 돌려준다 — PoC 평문·localhost 전제.
    정적 경로는 프로젝트 루트 기준 상대로 두고, 호출부가 절대경로로 변환한다.
    """
    cfg = load_endpoint_config()
    web = cfg.get("web", {}) or {}
    return {
        "host": web.get("host", "127.0.0.1"),
        "port": int(web.get("port", 8800)),
        "static_dist": web.get("static_dist", "web/dist"),
        "static_work": web.get("static_work", "work"),
        "ssl_certfile": web.get("ssl_certfile"),
        "ssl_keyfile": web.get("ssl_keyfile"),
        # 로그인 세션 인증 설정(없으면 빈 dict — auth 모듈이 기본값 적용).
        "auth": web.get("auth", {}) or {},
    }


def get_worker_config() -> dict[str, Any]:
    """endpoint.yaml의 worker 블록(P1 멀티 OS 프로세스 워커풀·step3)을 돌려준다.

    concurrency=동시 워커 슬롯 수. ★머지 기본 1 = 단일워커 동치(claimed_by_slot NULL·
    기존 경로·즉시 롤백). >1이면 supervisor가 슬롯 워커 N개를 기동(state/worker.lock.{0..N-1}).
    ★concurrency:2 라이브 승격·supervisor production 기동은 반드시 사용자 세션 승인(자율 금지).
    python_exe=워커 자식 exec용 conda env python 절대경로(conda run 회피·제약 #3).
    worker 블록이 없으면 안전 기본값(concurrency 1).
    """
    cfg = load_endpoint_config()
    w = cfg.get("worker", {}) or {}
    return {
        "concurrency": int(w.get("concurrency", 1)),
        "poll_interval": float(w.get("poll_interval", 2.0)),
        "python_exe": w.get(
            "python_exe", "/opt/conda/envs/one_man_corp-engine/bin/python"
        ),
    }


def get_llm_base_url(env: dict[str, str] | None = None) -> str:
    """LLM base URL을 환경변수에서 읽는다."""
    e = env or load_env()
    cfg = load_endpoint_config()
    key = cfg["llm"]["base_url_env"]
    val = e.get(key, "")
    if not val:
        raise ValueError(f"환경변수 {key} 가 설정되지 않았습니다")
    return val


def get_llm_api_key(env: dict[str, str] | None = None) -> str:
    """LLM API 키를 환경변수에서 읽는다."""
    e = env or load_env()
    cfg = load_endpoint_config()
    key = cfg["llm"]["api_key_env"]
    val = e.get(key, "")
    if not val:
        raise ValueError(f"환경변수 {key} 가 설정되지 않았습니다")
    return val


def get_primary_model(env: dict[str, str] | None = None) -> str:
    """주 모델 alias를 환경변수에서 읽는다."""
    e = env or load_env()
    return e.get("BENCH_PRIMARY_MODEL", "qwen3.6-35b-a3b")


def update_models_thinking_off(alias: str, supports: bool | None) -> None:
    """models.yaml의 supports_thinking_off 필드를 갱신한다."""
    path = _PROJECT_ROOT / "config" / "models.yaml"
    with open(path, encoding="utf-8") as f:
        models: list[dict[str, Any]] = yaml.safe_load(f) or []
    for m in models:
        if m.get("alias") == alias:
            m["supports_thinking_off"] = supports
            break
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(models, f, allow_unicode=True, default_flow_style=False)
