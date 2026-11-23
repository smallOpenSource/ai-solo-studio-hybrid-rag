"""orch.comfy — Server B 미디어 게이트웨이 OpenAI-compat 클라이언트 (§4.2·§24.6·P7·NET-NEW).

**OD-P7 = (B)** (``SR/renewal/COMFY_API_SHAPE.md``): 제공 Server B 게이트웨이는 OpenAI-호환이며,
본 게이트웨이에 한해 스펙 §24.6 native ComfyUI(``POST /prompt`` + workflow-JSON · dev-mode)와
§28.3 워크플로-JSON **추출**은 스펙 면제된다. 정본 경로는 **단일 ``OpenAICompatTransport``** —
``COMFY_EP_*`` 엔드포인트 + ``COMFY_*_MODEL`` 셀렉터로 submit → (sync view | bounded poll).

실측(``tools/comfy_probe.py`` + P7a 디스커버리 호출): 게이트웨이는 **동기**다 —
``POST {base}/images/generations`` → ``{"created", "data":[{"b64_json": ...}]}`` (이미지 직접 반환).
영상(``/videos/generations``)이 비동기(job id)면 ``_resolve_result`` 의 bounded poll 분기가 처리한다.

**종료성 P0** (자원 전소 자기종료 가드): 모든 poll 은 ``max_poll_count`` + ``deadline_s`` 이중가드
(무한 while 0). 동기 경로는 httpx read-timeout(=deadline_s)으로 바운드. 서버 B 동시성 1 =
모듈-레벨 ``asyncio.Semaphore(1)``.

**시크릿 규율**: ``COMFY_BASE``/``COMFY_API_KEY`` 값은 로그/에러/span 에 **절대 노출하지 않는다**
(``_auth_headers`` 는 헤더만 만들고 값을 로깅하지 않으며, 에러는 타입명만 redact).

**관찰성** (T8·OBSERVE-ONLY): ``comfy.submit``/``comfy.poll``/``comfy.view`` 수동 span 을 발행한다.
이는 **orch 최초의 *수동* span** 이다 — comfy 는 ``httpx`` 호출이라 기존 ``LangChainInstrumentor``
자동계측 대상이 **아니므로**(``orch/app.py`` 의 P1 OTLP 파이프에 export 만 합류), 수동 계측이 필요하다.
OpenTelemetry 가 없으면 no-op(테스트/오프라인 경로).

native 경로(``NativeComfyTransport``)는 **미구현**이다 — OD-P7(A)로 재개 시 ``COMFY_API_SHAPE.md``
의 "native 활성화 절차"를 따른다(현재 P7(B)에서는 수행하지 않음).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

import httpx

_ROOT = Path(__file__).resolve().parent.parent
_APP_ENV = _ROOT / "config" / "app.env"

# 종료성 기본값 (이미지=짧게·영상=길게·둘 다 바운드).
DEFAULT_DEADLINE_IMAGE_S = 300.0
DEFAULT_DEADLINE_VIDEO_S = 1800.0
DEFAULT_MAX_POLL_COUNT = 600
DEFAULT_POLL_INTERVAL_S = 3.0
_CONNECT_TIMEOUT_S = 10.0

# 서버 B 동시성 1 (§4.2·§24.6) — 이미지·영상 자연 직렬화.
_SEM = asyncio.Semaphore(1)


# --------------------------------------------------------------------------- env


def _parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE 줄을 stdlib 로 파싱(python-dotenv 의존 회피·session.py 선례). 따옴표/주석 처리."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _load_comfy_env() -> dict[str, str]:
    """config/app.env 를 os.environ 우선으로 병합해 COMFY_* 만 반환(값은 호출자만 보유)."""
    merged = _parse_env_file(_APP_ENV)
    merged.update({k: v for k, v in os.environ.items()})
    return {k: v for k, v in merged.items() if k.startswith("COMFY_")}


@dataclass(frozen=True)
class ComfyConfig:
    """게이트웨이 접속 설정 (값은 메모리에만·로깅 0)."""

    base: str
    api_key: str
    ep_image: str
    ep_image_edit: str
    ep_video: str
    image_model: str
    image_edit_model: str
    video_model: str
    video_audio_model: str
    module: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base and self.api_key)


def load_comfy_config(env: Optional[dict[str, str]] = None) -> ComfyConfig:
    """env(미지정 시 config/app.env + os.environ)에서 ComfyConfig 를 만든다."""
    e = env if env is not None else _load_comfy_env()
    return ComfyConfig(
        base=e.get("COMFY_BASE", "").rstrip("/"),
        api_key=e.get("COMFY_API_KEY", ""),
        ep_image=e.get("COMFY_EP_IMAGE", "/images/generations"),
        ep_image_edit=e.get("COMFY_EP_IMAGE_EDIT", "/images/edits"),
        ep_video=e.get("COMFY_EP_VIDEO", "/videos/generations"),
        image_model=e.get("COMFY_IMAGE_MODEL", ""),
        image_edit_model=e.get("COMFY_IMAGE_EDIT_MODEL", ""),
        video_model=e.get("COMFY_VIDEO_MODEL", ""),
        video_audio_model=e.get("COMFY_VIDEO_AUDIO_MODEL", ""),
        module=e.get("COMFY_MODULE", ""),
    )


def _auth_headers(cfg: ComfyConfig) -> dict[str, str]:
    """Authorization 헤더만 생성(값 로깅 0). 키가 없으면 빈 dict."""
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


# kind -> (endpoint attr, model attr). image_edit/video_audio 는 전용 모델 셀렉터.
_KIND_MAP: dict[str, tuple[str, str]] = {
    "image": ("ep_image", "image_model"),
    "image_edit": ("ep_image_edit", "image_edit_model"),
    "video": ("ep_video", "video_model"),
    "video_audio": ("ep_video", "video_audio_model"),
}


def _resolve_kind(cfg: ComfyConfig, kind: str) -> tuple[str, str]:
    if kind not in _KIND_MAP:
        raise ValueError(f"unknown comfy kind: {kind!r} (one of {sorted(_KIND_MAP)})")
    ep_attr, model_attr = _KIND_MAP[kind]
    return getattr(cfg, ep_attr), getattr(cfg, model_attr)


# ------------------------------------------------------------------ observability


@contextlib.contextmanager
def _span(name: str, **attrs: Any):
    """OBSERVE-ONLY 수동 span (otel 없으면 no-op). 값(키/base)은 attrs 에 넣지 않는다."""
    tracer = None
    with contextlib.suppress(Exception):
        from opentelemetry import trace  # type: ignore

        tracer = trace.get_tracer(__name__)
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as sp:  # pragma: no cover - otel optional
        with contextlib.suppress(Exception):
            for k, v in attrs.items():
                sp.set_attribute(k, v)
        yield sp


@dataclass
class MediaResult:
    """생성 결과 (bytes + 메타). 시크릿/원본 b64 는 raw 에 두지 않는다."""

    kind: str
    data: bytes
    content_type: str = "application/octet-stream"
    model: str = ""
    endpoint: str = ""
    poll_count: int = 0
    elapsed_s: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- transport


@runtime_checkable
class ComfyTransport(Protocol):
    """전송 추상화 (미래확장 훅·문서화-only). 현재 라이브 구현은 OpenAICompatTransport 하나뿐.

    OD-P7(A)로 native ComfyUI 가 제공되면 ``NativeComfyTransport`` 를 이 Protocol 로 추가한다
    (현재 미구현). 테스트 표면에 2-transport 를 두지 않는다(C6·YAGNI).
    """

    async def generate(self, kind: str, prompt: str, **kwargs: Any) -> MediaResult: ...


class OpenAICompatTransport:
    """OpenAI-호환 게이트웨이 전송 (정본·OD-P7(B)). submit → (sync view | bounded poll).

    동기(이미지·실측): 응답 ``data[0].b64_json``(또는 ``url``) → 디코드/다운로드 → bytes.
    비동기(영상 가능): 응답에 ``id``/``status`` → bounded poll(``max_poll_count`` + ``deadline_s``)
    → 완료 시 결과 회수. 어느 경로든 무한 대기 0.
    """

    def __init__(
        self,
        cfg: Optional[ComfyConfig] = None,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.cfg = cfg if cfg is not None else load_comfy_config()
        # 테스트 주입 seam: httpx.MockTransport 로 실 네트워크 없이 검증(기본 None=실 게이트웨이).
        self._transport = transport

    # --- public ---------------------------------------------------------------

    async def generate(
        self,
        kind: str,
        prompt: str,
        *,
        size: Optional[str] = None,
        image: Optional[
            str
        ] = None,  # 시그니처 호환(generate_media 라우팅·DashScope=edit primary)·comfy 미사용
        deadline_s: Optional[float] = None,
        max_poll_count: int = DEFAULT_MAX_POLL_COUNT,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        extra: Optional[dict[str, Any]] = None,
    ) -> MediaResult:
        """``kind`` 미디어 1건 생성. 종료성 이중가드·Semaphore(1)·수동 span."""
        ep, model = _resolve_kind(self.cfg, kind)
        if not self.cfg.configured:
            raise RuntimeError(
                "comfy gateway not configured (COMFY_BASE/COMFY_API_KEY)"
            )
        is_video = "video" in kind
        deadline = deadline_s or (
            DEFAULT_DEADLINE_VIDEO_S if is_video else DEFAULT_DEADLINE_IMAGE_S
        )
        body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        if size:
            body["size"] = size
        if not is_video:
            body["response_format"] = "b64_json"
        if extra:
            body.update(extra)

        started = time.monotonic()
        async with _SEM:  # 서버 B 동시성 1
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(deadline, connect=_CONNECT_TIMEOUT_S),
                transport=self._transport,
            ) as client:
                with _span("comfy.submit", endpoint=ep, model=model, kind=kind):
                    resp = await client.post(
                        self.cfg.base + ep, json=body, headers=_auth_headers(self.cfg)
                    )
                    resp.raise_for_status()
                payload = _safe_json(resp)
                result = await self._resolve_result(
                    client,
                    kind=kind,
                    endpoint=ep,
                    model=model,
                    resp=resp,
                    payload=payload,
                    deadline=deadline,
                    started=started,
                    max_poll_count=max_poll_count,
                    poll_interval_s=poll_interval_s,
                )
        result.elapsed_s = time.monotonic() - started
        return result

    # --- internals ------------------------------------------------------------

    async def _resolve_result(
        self,
        client: httpx.AsyncClient,
        *,
        kind: str,
        endpoint: str,
        model: str,
        resp: httpx.Response,
        payload: Optional[dict[str, Any]],
        deadline: float,
        started: float,
        max_poll_count: int,
        poll_interval_s: float,
    ) -> MediaResult:
        # (0) 비-JSON 바이너리 응답(직접 image/video bytes).
        ctype = resp.headers.get("content-type", "")
        if payload is None:
            if ctype.startswith(("image/", "video/")):
                return MediaResult(kind, resp.content, ctype, model, endpoint)
            raise RuntimeError(
                f"comfy: unexpected non-json response (content-type={ctype!r})"
            )

        # (1) 동기 OpenAI-compat: data[0].b64_json | url.
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            item = data[0]
            if item.get("b64_json"):
                with _span("comfy.view", endpoint=endpoint, model=model):
                    raw = base64.b64decode(item["b64_json"])
                ct = "video/mp4" if "video" in kind else "image/png"
                return MediaResult(
                    kind, raw, item.get("content_type", ct), model, endpoint
                )
            if item.get("url"):
                with _span("comfy.view", endpoint=endpoint, model=model):
                    raw, ct = await _download(
                        client, item["url"], _auth_headers(self.cfg)
                    )
                return MediaResult(kind, raw, ct, model, endpoint)

        # (2) 비동기: id/status → bounded poll (종료성 이중가드·무한 while 0).
        job_id = (
            payload.get("id") or payload.get("task_id") or payload.get("request_id")
        )
        status_url = payload.get("status_url") or payload.get("url")
        if job_id or status_url:
            return await self._poll(
                client,
                kind=kind,
                endpoint=endpoint,
                model=model,
                job_id=job_id,
                status_url=status_url,
                deadline=deadline,
                started=started,
                max_poll_count=max_poll_count,
                poll_interval_s=poll_interval_s,
            )

        raise RuntimeError(
            f"comfy: unrecognized response shape (keys={sorted(payload.keys())})"
        )

    async def _poll(
        self,
        client: httpx.AsyncClient,
        *,
        kind: str,
        endpoint: str,
        model: str,
        job_id: Optional[str],
        status_url: Optional[str],
        deadline: float,
        started: float,
        max_poll_count: int,
        poll_interval_s: float,
    ) -> MediaResult:
        """비동기 작업 bounded poll. ``max_poll_count`` 와 ``deadline`` 둘 중 먼저 도달 시 TimeoutError."""
        poll_target = status_url or f"{self.cfg.base}{endpoint}/{job_id}"
        count = 0
        # ★종료성 이중가드: 횟수 캡 + 마감시각 캡 (무한 while 금지).
        for count in range(1, max_poll_count + 1):
            if time.monotonic() - started > deadline:
                raise TimeoutError(
                    f"comfy poll deadline exceeded ({deadline}s·count={count}·kind={kind})"
                )
            with _span("comfy.poll", endpoint=endpoint, model=model, poll_count=count):
                r = await client.get(poll_target, headers=_auth_headers(self.cfg))
                r.raise_for_status()
                p = _safe_json(r) or {}
            status = str(p.get("status", "")).lower()
            data = p.get("data")
            if status in ("completed", "succeeded", "success", "done") or (
                isinstance(data, list) and data
            ):
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    item = data[0]
                    if item.get("b64_json"):
                        raw = base64.b64decode(item["b64_json"])
                        ct = "video/mp4" if "video" in kind else "image/png"
                        return MediaResult(
                            kind,
                            raw,
                            item.get("content_type", ct),
                            model,
                            endpoint,
                            count,
                        )
                    if item.get("url"):
                        raw, ct = await _download(
                            client, item["url"], _auth_headers(self.cfg)
                        )
                        return MediaResult(kind, raw, ct, model, endpoint, count)
                result_url = p.get("result_url") or p.get("output_url")
                if result_url:
                    raw, ct = await _download(
                        client, result_url, _auth_headers(self.cfg)
                    )
                    return MediaResult(kind, raw, ct, model, endpoint, count)
                raise RuntimeError("comfy: job completed but no retrievable output")
            if status in ("failed", "error", "cancelled"):
                raise RuntimeError(f"comfy job failed (status={status!r})")
            await asyncio.sleep(poll_interval_s)
        raise TimeoutError(
            f"comfy poll max_poll_count exceeded ({max_poll_count}·kind={kind})"
        )


# NativeComfyTransport: 미구현 (OD-P7(A) 재개 시·COMFY_API_SHAPE.md 절차). 의도적 부재(C6/MAJOR#3).


def _safe_json(resp: httpx.Response) -> Optional[dict[str, Any]]:
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype:
        return None
    with contextlib.suppress(Exception):
        out = resp.json()
        if isinstance(out, dict):
            return out
    return None


async def _download(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> tuple[bytes, str]:
    """결과 URL 다운로드(인증 헤더는 동일 게이트웨이일 때만 유효·외부면 무해)."""
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")


# 편의 동기 래퍼 (스크립트/도구용·이벤트 루프 없을 때).
def generate_sync(kind: str, prompt: str, **kwargs: Any) -> MediaResult:
    """이벤트 루프 밖에서 1건 생성(asyncio.run). 라이브 호출은 safe_run.sh 로 감싸 실행.

    ★generate_media 경유(이미지 primary=DashScope·fallback=comfy·AC12). lazy import 로
    comfy↔media_provider 순환 회피.
    """
    from orch.media_provider import generate_media  # noqa: PLC0415 — 순환 회피(lazy)

    return asyncio.run(generate_media(kind, prompt, **kwargs))
