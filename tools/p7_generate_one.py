"""P7 라이브 미디어 1건 생성 (Server B 게이트웨이·VAE 수리 검증용·NET-NEW).

Owner 가 "Server B 게이트웨이 VAE 수리완료"를 통보 → qwen-image 검정(black) 출력이
해소됐는지 *실측*하기 위한 최소 생성 도구. ``orch.comfy.generate_sync`` 1건 호출.

사용:
    python tools/p7_generate_one.py <kind> <prompt> <out_path> [size]
    kind ∈ image|image_edit|video|video_audio. repo 루트에서 실행(config/app.env 자동 로드).

종료성 P0: comfy.py 의 deadline(이미지 300s)·Semaphore(1) 로 바운드. 1건만(루프 0).
    라이브 호출은 .omc/guards/safe_run.sh 로 감싸 실행(자원 전소 가드).
검정 판별은 본 도구가 하지 않는다(PIL 은 art env) → tools/p7_black_check.py 로 분리.
시크릿 규율: COMFY_BASE/KEY 값 미출력(model/content_type/bytes/elapsed 만 보고).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orch.comfy import generate_sync  # noqa: E402


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: p7_generate_one.py <kind> <prompt> <out_path> [size]")
        return 2
    kind, prompt, out = sys.argv[1], sys.argv[2], sys.argv[3]
    size = sys.argv[4] if len(sys.argv) > 4 else "1024x1024"
    kwargs = {} if "video" in kind else {"size": size}
    try:
        res = generate_sync(kind, prompt, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 라이브 호출 실패는 정직 보고(타입명만)
        print(f"GEN_FAIL kind={kind} error={type(exc).__name__}: {str(exc)[:120]}")
        return 1
    Path(out).write_bytes(res.data)
    print(
        f"GEN_OK kind={res.kind} bytes={len(res.data)} ctype={res.content_type} "
        f"model={res.model} elapsed_s={res.elapsed_s:.1f} out={out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
