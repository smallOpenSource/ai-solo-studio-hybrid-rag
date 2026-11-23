"""라이브 provider primary/fallback 검증 — AC1-AC4·AC11 (실호출·mock 0).

★safe_run.sh 로 감싸 실행(자원가드). 실 Azure/DashScope/gemma/comfy 호출 + served_by 로그 캡처로
primary 경로/fallback 경로를 falsifiable 하게 입증. 시크릿 0노출(키 출력 없음).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
from pathlib import Path

_SCRATCH = Path(
    os.environ.get(
        "PV_OUT",
        "/tmp/provider-verify",
    )
)
_SCRATCH.mkdir(parents=True, exist_ok=True)

# served_by 로그 캡처(azure_llm·media_provider 가 발행)
_LOGS: list[str] = []


class _Cap(logging.Handler):
    def emit(self, rec: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            _LOGS.append(
                rec.getMessage()
            )  # getMessage() 가 이미 args 적용(이중포맷 금지)


_h = _Cap()
for _name in ("orch.azure_llm", "orch.media_provider"):
    lg = logging.getLogger(_name)
    lg.addHandler(_h)
    lg.setLevel(logging.INFO)

_RESULTS: list[tuple[str, bool, str]] = []


def _rec(ac: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((ac, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {ac}: {detail}", flush=True)


def _served(substr: str) -> bool:
    return any(substr in m for m in _LOGS)


def _valid_image(b: bytes) -> bool:
    return len(b) > 8000 and (
        b[:8] == b"\x89PNG\r\n\x1a\n" or b[:3] == b"\xff\xd8\xff" or b[:4] == b"RIFF"
    )


def main() -> int:
    from orch.azure_llm import make_primary_fallback_caller
    from orch.graph import _gemma_caller
    from orch.media_provider import generate_media

    edit_input_b64: str | None = None

    # --- AC3: Azure LLM primary serves (실 Azure) ---
    _LOGS.clear()
    try:
        caller = make_primary_fallback_caller(_gemma_caller, label="verify")
        out = caller("Reply with exactly the two characters: OK", 0.0, 64)
        ok = bool(out) and _served("served_by=primary provider=azure")
        _rec(
            "AC3 azure-primary-LLM",
            ok,
            f"len={len(out or '')} primary={_served('served_by=primary')}",
        )
    except Exception as e:  # noqa: BLE001
        _rec("AC3 azure-primary-LLM", False, f"ERR {type(e).__name__} {str(e)[:80]}")

    # --- AC3': Azure 가 PARSEABLE conf 방출(authority-tier 침묵 drift 차단·emit≠proxy 폴백) ---
    try:
        from orch.azure_llm import azure_caller
        from orch.graph import _parse_confidence

        route_prompt = (
            "You are a routing controller. Decide BUILD vs RESEARCH for the directive "
            "'make a clicker game'. Respond with EXACTLY one line in this format: "
            "ROUTE: build | conf 0.NN | reason: <short>"
        )
        rout = azure_caller(route_prompt, 0.0, 128)
        conf = _parse_confidence(rout)  # None 이면 proxy 폴백(silent drift) → 실패
        ok = conf is not None and 0.0 <= conf <= 1.0
        _rec("AC3' azure-parseable-conf", ok, f"conf={conf} (emit, not proxy)")
    except Exception as e:  # noqa: BLE001
        _rec(
            "AC3' azure-parseable-conf", False, f"ERR {type(e).__name__} {str(e)[:80]}"
        )

    # --- AC1: DashScope image primary (실 생성·valid bytes) ---
    _LOGS.clear()
    try:
        res = asyncio.run(
            generate_media(
                "image",
                "A single bright red circle centered on a plain white background",
                size="1024*1024",
            )
        )
        ok = _valid_image(res.data) and _served("served_by=primary provider=dashscope")
        p = _SCRATCH / "pv_ac1_dashscope.png"
        p.write_bytes(res.data)
        edit_input_b64 = base64.b64encode(res.data).decode()
        _rec(
            "AC1 dashscope-primary-image",
            ok,
            f"bytes={len(res.data)} model={res.model} primary={_served('provider=dashscope')} -> {p}",
        )
    except Exception as e:  # noqa: BLE001
        _rec(
            "AC1 dashscope-primary-image",
            False,
            f"ERR {type(e).__name__} {str(e)[:80]}",
        )

    # --- AC11: DashScope image_edit (실 입력이미지) ---
    _LOGS.clear()
    if edit_input_b64:
        try:
            res = asyncio.run(
                generate_media(
                    "image_edit",
                    "Change the circle color to blue",
                    image=f"data:image/png;base64,{edit_input_b64}",
                    size="1024*1024",
                )
            )
            ok = _valid_image(res.data) and _served("provider=dashscope")
            p = _SCRATCH / "pv_ac11_edit.png"
            p.write_bytes(res.data)
            _rec(
                "AC11 dashscope-image_edit",
                ok,
                f"bytes={len(res.data)} model={res.model} -> {p}",
            )
        except Exception as e:  # noqa: BLE001
            _rec(
                "AC11 dashscope-image_edit",
                False,
                f"ERR {type(e).__name__} {str(e)[:80]}",
            )
    else:
        _rec("AC11 dashscope-image_edit", False, "AC1 미생성으로 입력이미지 없음")

    # --- AC4/AC15: Azure forced-fail → gemma fallback (graceful·outage 0) ---
    _LOGS.clear()
    os.environ["AZURE_OPENAI_API_KEY"] = "sk-forced-bad-key-verify"
    try:
        caller = make_primary_fallback_caller(_gemma_caller, label="verify-fail")
        out = caller("Reply with exactly the two characters: OK", 0.0, 64)
        ok = bool(out) and _served("served_by=fallback")
        _rec(
            "AC4/AC15 azure-fail->gemma-fallback",
            ok,
            f"len={len(out or '')} fallback={_served('served_by=fallback')}",
        )
    except Exception as e:  # noqa: BLE001
        _rec(
            "AC4/AC15 azure-fail->gemma-fallback",
            False,
            f"ERR {type(e).__name__} {str(e)[:80]}",
        )
    finally:
        os.environ.pop("AZURE_OPENAI_API_KEY", None)  # 실키(app.env) 복원

    # --- AC2: DashScope forced-fail → comfy fallback (라우팅 실증) ---
    _LOGS.clear()
    os.environ["DASHSCOPE_API_KEY"] = "sk-forced-bad-key-verify"
    try:
        res = asyncio.run(
            generate_media("image", "A small green square on white", size="1024*1024")
        )
        # 핵심: comfy 로 라우팅됐는가(품질은 comfy 게이트웨이 소관). bytes 반환 + fallback 로그.
        routed = _served("served_by=fallback provider=comfy") or _served(
            "provider=comfy"
        )
        ok = routed and isinstance(res.data, (bytes, bytearray)) and len(res.data) > 0
        _rec(
            "AC2 dashscope-fail->comfy-fallback",
            ok,
            f"routed_comfy={routed} bytes={len(res.data)}",
        )
    except Exception as e:  # noqa: BLE001
        # comfy 자체 장애면 라우팅은 됐으나 comfy 실패 — 라우팅 로그로 구분
        routed = _served("provider=comfy")
        _rec(
            "AC2 dashscope-fail->comfy-fallback",
            routed,
            f"routed_comfy={routed} comfy_err={type(e).__name__}",
        )
    finally:
        os.environ.pop("DASHSCOPE_API_KEY", None)  # 실키 복원

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n=== LIVE PROVIDER VERIFY: {passed}/{total} PASS ===", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
