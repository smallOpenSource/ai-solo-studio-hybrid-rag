"""P5 §28.1 #7 트레일러 합성 — 실 게임 footage + LTX-2 b-roll → 트레일러 (NET-NEW).

서로 다른 코덱/해상도 클립(게임 캡처 webm/vp9 + Server B ltx-2 mp4/h264)을 공통 규격
(h264·720p·30fps·yuv420p)으로 정규화한 뒤 ``orch.video_assemble.concat_clips`` 로 이어붙인다.
concat_clips 는 ``-c copy`` 라 입력 스트림 규격이 동일해야 하므로 정규화가 선행한다.

★실 footage: ``tools/capture_puzzle_footage.mjs`` 가 녹화한 *실제 게임플레이* + Server B ltx-2
b-roll → 렌더-trap/날조 0(p7_video_check 로 비검정·유효 단언 가능).

사용: python tools/p5_trailer_compose.py <out.mp4> <clip1> <clip2> [...]
환경: ffmpeg(.omc/bin/ffmpeg·libx264). 검증은 art env ``tools/p7_video_check.py``.
종료성: ffmpeg 호출은 run_isolated(timeout·프로세스그룹 kill). 클립 수 유한·루프 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orch.video_assemble import concat_clips, ffmpeg_exe, run_isolated  # noqa: E402

_W, _H, _FPS = 1280, 720, 30


def _normalize(src: str, dst: str) -> str:
    """공통 규격(h264·720p letterbox·동일 fps·무음)으로 재인코딩 → concat copy 가능."""
    vf = (
        f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
        f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_FPS}"
    )
    res = run_isolated(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dst),
        ],
        timeout=180,
    )
    if res.returncode != 0:
        raise RuntimeError(f"normalize 실패 {src}: {res.stderr[-300:]}")
    return dst


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: p5_trailer_compose.py <out.mp4> <clip1> <clip2> [...]")
        return 2
    out, clips = sys.argv[1], sys.argv[2:]
    segs: list[str] = []
    for i, clip in enumerate(clips):
        if not Path(clip).is_file():
            print(f"TRAILER_FAIL 클립 없음: {clip}")
            return 1
        seg = f"/tmp/p5_seg_{i}.mp4"
        _normalize(clip, seg)
        segs.append(seg)
    concat_clips(segs, out)
    size = Path(out).stat().st_size if Path(out).is_file() else 0
    print(f"TRAILER_OK out={out} segments={len(segs)} bytes={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
