"""orch.dashscope_image — DashScope Qwen-image primary transport (image gen + edit).

Owner 지시: image 생성/편집 primary = DashScope(Alibaba Model Studio); 현 Server B OpenAI-compat
(``orch.comfy``) = fallback. ``ComfyTransport`` Protocol 을 구현해 ``generate_media`` 라우터가
``OpenAICompatTransport`` 와 교환 가능하게 사용한다.

네이티브 **동기** 엔드포인트(spike 검증): ``POST {DASHSCOPE_BASE}/services/aigc/multimodal-
generation/generation``, 메시지 스키마 → ``output.choices[0].message.content[0].image``(url·24h).
image → ``DASHSCOPE_IMAGE_MODEL``(qwen-image-2.0-pro). image_edit → ``DASHSCOPE_IMAGE_EDIT_MODEL``
(qwen-image-edit-max) + 입력이미지를 message content 에 포함. video 미지원(raise → 라우터가 comfy).

종료성 P0: httpx timeout = deadline. 시크릿 규율: ``DASHSCOPE_API_KEY`` 는 헤더로만(로그 0)·에러는
타입명만.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from orch.comfy import MediaResult  # 결과 타입 재사용(라우터 동형)

_ROOT = Path(__file__).resolve().parent.parent
_APP_ENV = _ROOT / "config" / "app.env"

_GEN_PATH = "/services/aigc/multimodal-generation/generation"
DEFAULT_DEADLINE_S = 300.0
_CONNECT_TIMEOUT_S = 10.0


def _parse_env_file(path: Path) -> dict[str, str]:
    """stdlib KEY=VALUE 파싱(comfy.py 선례·python-dotenv 금지)."""
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


def _load_env() -> dict[str, str]:
    merged = _parse_env_file(_APP_ENV)
    merged.update(os.environ)
    return merged


def dashscope_configured(env: Optional[dict[str, str]] = None) -> bool:
    e = env if env is not None else _load_env()
    return bool(e.get("DASHSCOPE_API_KEY") and e.get("DASHSCOPE_BASE"))


class DashScopeImageTransport:
    """DashScope Qwen-image transport (image + image_edit). ``ComfyTransport`` Protocol 호환."""

    def __init__(
        self,
        env: Optional[dict[str, str]] = None,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._env = env if env is not None else _load_env()
        self._transport = transport  # httpx.MockTransport 주입 seam(테스트)

    async def generate(
        self,
        kind: str,
        prompt: str,
        *,
        size: Optional[str] = None,
        image: Optional[str] = None,
        deadline_s: Optional[float] = None,
        **_ignore: Any,
    ) -> MediaResult:
        if kind not in ("image", "image_edit"):
            raise ValueError(
                f"dashscope unsupported kind: {kind!r} (image/image_edit only)"
            )
        e = self._env
        base = e.get("DASHSCOPE_BASE", "").rstrip("/")
        key = e.get("DASHSCOPE_API_KEY", "")
        if not (base and key):
            raise RuntimeError("dashscope not configured")
        model = (
            e.get("DASHSCOPE_IMAGE_EDIT_MODEL", "qwen-image-edit-max")
            if kind == "image_edit"
            else e.get("DASHSCOPE_IMAGE_MODEL", "qwen-image-2.0-pro")
        )
        content: list[dict[str, Any]] = []
        if kind == "image_edit":
            if not image:
                raise ValueError("image_edit requires an input image (data-uri or url)")
            content.append({"image": image})
        content.append({"text": prompt})
        params: dict[str, Any] = {"n": 1, "watermark": False}
        if size:
            params["size"] = size
        body = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }
        deadline = deadline_s or DEFAULT_DEADLINE_S
        started = time.monotonic()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(deadline, connect=_CONNECT_TIMEOUT_S),
                transport=self._transport,
            ) as client:
                resp = await client.post(base + _GEN_PATH, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code"):  # DashScope 오류는 code/message 로 신호
                    raise RuntimeError(f"dashscope error code={data.get('code')!r}")
                img_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
                dl = await client.get(img_url)  # 24h 만료 → 즉시 다운로드
                dl.raise_for_status()
                raw = dl.content
                ct = dl.headers.get("content-type", "image/png")
        except Exception as exc:  # 타입명만 — 비밀/본문 0노출
            raise RuntimeError(
                f"dashscope generate failed: {type(exc).__name__}"
            ) from None
        return MediaResult(
            kind=kind,
            data=raw,
            content_type=ct,
            model=model,
            endpoint=_GEN_PATH,
            elapsed_s=time.monotonic() - started,
        )
