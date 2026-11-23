"""P7 영상 검증 — Server B 영상 생성 라이브 확인 (art env·ffmpeg/PIL·NET-NEW).

이미지 VAE 수리 검증(tools/p7_black_check.py)의 영상 짝. 영상은 동기-long 생성 →
산출물이 *디코드 가능한 비디오 스트림 + 비검정 프레임* 인지, ``--audio`` 면 오디오 스트림
존재까지 falsifiable 판별한다. ffmpeg stderr 파싱(ffprobe 불요·static ffmpeg)은
``orch.video_assemble`` 헬퍼 재사용(복사 0).

사용:
    python tools/p7_video_check.py <video_path> [--audio]
판정(falsifiable):
    · 비디오 스트림 없음/프레임 추출 실패 → exit 1
    · 샘플 프레임 전검정(VAE류 degraded·이미지 검정과 동일 신호) → exit 1
    · ``--audio`` 인데 오디오 스트림 없음 → exit 1
    · 그 외(비디오 스트림 + 비검정 프레임 [+ 오디오]) → exit 0
STDOUT: ``VIDEO_CHECK ... verdict=OK|BAD`` (집계 가능 한 줄).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops  # noqa: E402

from orch.video_assemble import (  # noqa: E402
    ffmpeg_exe,
    has_audio_stream,
    has_video_stream,
    run_isolated,
)

_THRESHOLD = 8  # 채널값 ≤8 은 사실상 검정(p7_black_check 와 동일 기준).


def _frame_non_black(video: str) -> tuple[bool, int, int]:
    """첫 프레임 1장 추출 → (비검정?, max_channel, non_black_px). 추출 실패 시 (False,-1,0)."""
    frame = f"{video}.frame.png"
    run_isolated(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-y",
            "-i",
            video,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            frame,
        ],
        timeout=60,
    )
    if not Path(frame).exists():
        return (False, -1, 0)
    im = Image.open(frame).convert("RGB")
    max_channel = max(ch[1] for ch in im.getextrema())
    r, g, b = im.split()
    per_pixel_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
    non_black = sum(per_pixel_max.histogram()[_THRESHOLD + 1 :])
    return (max_channel > 0 and non_black > 0, max_channel, non_black)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: p7_video_check.py <video_path> [--audio]")
        return 2
    video = sys.argv[1]
    want_audio = "--audio" in sys.argv
    if not Path(video).exists():
        print(f"VIDEO_CHECK path={video} verdict=BAD (파일 없음)")
        return 1
    size = Path(video).stat().st_size
    vstream = has_video_stream(video)
    non_black, max_channel, non_black_px = (
        _frame_non_black(video) if vstream else (False, -1, 0)
    )
    audio = has_audio_stream(video) if want_audio else None
    ok = vstream and non_black and (bool(audio) if want_audio else True)
    print(
        f"VIDEO_CHECK path={video} bytes={size} video_stream={vstream} "
        f"frame_max_channel={max_channel} frame_non_black={non_black_px} "
        f"audio_stream={audio} verdict={'OK' if ok else 'BAD'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
