"""orch.media_provider — 이미지 primary/fallback 라우터(DashScope primary · comfy fallback).

Owner 지시: image 생성/편집 primary = DashScope, fallback = 현 Server B OpenAI-compat(comfy).
video/video_audio 는 DashScope 미지원 → 항상 comfy. ``ORCH_IMAGE_PRIMARY`` 를 **call-time** 에
읽는다(off → comfy 직행). served_by 로그는 greppable·redact.

순환 회피: comfy(상위)를 top-import 하고, comfy.generate_sync 는 본 모듈을 **lazy** import 한다
(comfy↔media_provider 단방향 import-time).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from orch.comfy import MediaResult, OpenAICompatTransport

logger = logging.getLogger(__name__)


def _image_primary_enabled() -> bool:
    """``ORCH_IMAGE_PRIMARY`` 를 CALL-TIME 에 읽는다. 미설정→dashscope(Owner default)·``off``→비활성."""
    return os.environ.get("ORCH_IMAGE_PRIMARY", "dashscope").strip().lower() != "off"


async def generate_media(kind: str, prompt: str, **kw: Any) -> MediaResult:
    """미디어 1건 생성(primary/fallback 라우팅).

    - image/image_edit & primary ON & DashScope 구성됨 → DashScope 시도 → 실패 시 comfy fallback.
    - video/video_audio · primary off · DashScope 미구성 → comfy 직행.
    """
    if kind in ("image", "image_edit") and _image_primary_enabled():
        from orch.dashscope_image import (  # noqa: PLC0415 — lazy(선택 의존·순환 회피)
            DashScopeImageTransport,
            dashscope_configured,
        )

        if dashscope_configured():
            try:
                out = await DashScopeImageTransport().generate(kind, prompt, **kw)
                logger.info("served_by=primary provider=dashscope kind=%s", kind)
                return out
            except Exception as exc:  # noqa: BLE001 — primary 실패 → comfy fallback
                logger.warning(
                    "served_by=fallback provider=comfy kind=%s reason=%s",
                    kind,
                    type(exc).__name__,
                )
    out = await OpenAICompatTransport().generate(kind, prompt, **kw)
    logger.info("served_by=comfy provider=comfy kind=%s", kind)
    return out
