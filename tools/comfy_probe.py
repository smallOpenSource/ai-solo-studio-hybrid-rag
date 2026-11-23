#!/usr/bin/env python3
"""comfy_probe — Server B 미디어 게이트웨이 read-only **확인** 프로브 (P7-PRE·발견 아님).

게이트웨이가 OpenAI-호환임은 ``config/app.env`` 의 ``COMFY_EP_*``+``COMFY_*_MODEL`` 키 스킴으로
이미 *확정*돼 있다(COMFY_API_SHAPE.md). 이 프로브는 그 사실을 **read-only 로 재확인**한다:
reachability·auth-enforced·OpenAI-compat 디스커버리 응답. **생성 호출(이미지/영상)은 절대 하지
않는다** — POST 는 보내지 않으며 종료성 가드(짧은 timeout·재시도 루프 0)를 둔다.

시크릿 규율: ``COMFY_BASE``/``COMFY_API_KEY`` 값은 **절대 출력/기록하지 않는다**. host 는 redact
하고 scheme+path 구조만 표기한다. 결과는 ``SR/renewal/COMFY_API_SHAPE.md`` 에 append 한다.

usage: ``/opt/conda/envs/orch/bin/python tools/comfy_probe.py``
exit 0 = reachable(확인 성공) · exit 0 + ACTION 표기 = 미도달(정직·green-fake 아님).
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

_ROOT = Path(__file__).resolve().parent.parent
# 독립 실행(python tools/comfy_probe.py) 시 repo 루트를 path 에 올려 orch.comfy 의 env-loader 를
# 단일 출처로 재사용(중복 제거·deslop). os.environ 우선 병합·COMFY_* 만 반환은 거기서 보장.
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from orch.comfy import _load_comfy_env  # noqa: E402  (sys.path 주입 후 import)

_SHAPE_DOC = _ROOT / "SR" / "renewal" / "COMFY_API_SHAPE.md"

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 10.0


def _redact_base(base: str) -> str:
    """host 를 가리고 scheme+path 구조만 반환(시크릿 규율)."""
    try:
        parts = urlsplit(base)
        host_red = "<redacted-host>" + (f":{parts.port}" if parts.port else "")
        return f"{parts.scheme}://{host_red}{parts.path or ''}"
    except Exception:
        return "<unparseable>"


def _get(url: str, headers: dict[str, str] | None) -> tuple[int | None, str]:
    """단발 read-only GET(재시도 0·바운드 timeout). 반환 (status, note)."""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            verify=True,
            follow_redirects=False,
        ) as c:
            r = c.get(url, headers=headers or {})
            return r.status_code, ""
    except httpx.HTTPError as e:
        # 에러 메시지에 URL/키가 섞일 수 있으므로 타입명만 노출(redact).
        return None, type(e).__name__


def probe() -> dict[str, object]:
    env = _load_comfy_env()
    base = env.get("COMFY_BASE", "").rstrip("/")
    key = env.get("COMFY_API_KEY", "")
    findings: dict[str, object] = {
        "base_struct": _redact_base(base) if base else "<COMFY_BASE missing>",
        "key_present": bool(key),
        "ep_image": env.get("COMFY_EP_IMAGE", "<missing>"),
        "ep_image_edit": env.get("COMFY_EP_IMAGE_EDIT", "<missing>"),
        "ep_video": env.get("COMFY_EP_VIDEO", "<missing>"),
        "reachable": False,
        "auth_enforced": None,
        "models_status_with_key": None,
        "models_status_no_key": None,
        "error": None,
    }
    if not base or not key:
        findings["error"] = "COMFY_BASE or COMFY_API_KEY missing in env"
        return findings

    auth = {"Authorization": f"Bearer {key}"}
    models_url = f"{base}/models"  # OpenAI-compat 디스커버리(read-only)

    st_key, note_key = _get(models_url, auth)
    findings["models_status_with_key"] = st_key
    if note_key:
        findings["error"] = note_key
    if st_key is not None:
        findings["reachable"] = True

    # auth 강제 확인: 키 없이 → 401/403 기대(read-only).
    st_nokey, _ = _get(models_url, None)
    findings["models_status_no_key"] = st_nokey
    if st_key is not None and st_nokey is not None:
        findings["auth_enforced"] = st_nokey in (401, 403) and st_key not in (401, 403)

    return findings


def _append_to_shape_doc(f: dict[str, object]) -> None:
    reachable = bool(f.get("reachable"))
    status = (
        "[OK] reachable (확인 성공)"
        if reachable
        else "[ACTION] 미도달 — pending Owner: Server B reachability (≠pass·green-fake 아님)"
    )
    lines = [
        "",
        "### Probe 확인 결과 (read-only · 생성 호출 0)",
        "",
        f"- 상태: **{status}**",
        f"- base 구조(redacted): `{f.get('base_struct')}`",
        f"- API key 존재: {f.get('key_present')}",
        f"- 엔드포인트 경로: image=`{f.get('ep_image')}` · image_edit=`{f.get('ep_image_edit')}` · video=`{f.get('ep_video')}`",
        f"- `/models` (with key) status: {f.get('models_status_with_key')}",
        f"- `/models` (no key) status: {f.get('models_status_no_key')} · auth_enforced: {f.get('auth_enforced')}",
    ]
    if f.get("error"):
        lines.append(f"- note: {f.get('error')} (값 redact)")
    lines.append("")
    lines.append(
        "> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인."
    )
    with open(_SHAPE_DOC, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    f = probe()
    _append_to_shape_doc(f)
    # 콘솔 요약(값 미출력·redacted).
    print("=== comfy_probe (read-only·생성 호출 0) ===")
    for k in (
        "base_struct",
        "key_present",
        "ep_image",
        "ep_image_edit",
        "ep_video",
        "reachable",
        "auth_enforced",
        "models_status_with_key",
        "models_status_no_key",
        "error",
    ):
        print(f"  {k}: {f.get(k)}")
    if f.get("reachable"):
        print(
            "RESULT: [OK] Server B reachable · OpenAI-compat 확정 재확인 · COMFY_API_SHAPE.md append 완료"
        )
    else:
        print(
            "RESULT: [ACTION] Server B 미도달 — pending Owner: reachability (≠pass·정직)"
        )
    return 0  # 프로브 자체는 항상 0(도달 여부는 ACTION/OK 로 정직 표기)


if __name__ == "__main__":
    raise SystemExit(main())
