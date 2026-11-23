"""orch.video_assemble — §17.2 영상 조립 (ffmpeg/MoviePy·P7·NET-NEW·종료성 격리).

§17.2: 오픈 모델(LTX-2/Wan)이 ~짧은 클립을 생성하므로 **고정 시드 다중 클립 + ffmpeg 조립**으로
긴 영상을 만든다(concat/subtitle/audio-mix). 영상 *생성* 은 ``orch.comfy`` (서버 B), 본 모듈은
**로컬 조립**만 담당한다(게이트웨이 무관).

★종료성 P0 (MAJOR#5·과거 2회 시스템다운 + 손자 orphan): 모든 외부 프로세스는 ``run_isolated``
로 실행한다 — ``start_new_session=True`` 로 **새 프로세스 그룹**을 만들고, timeout 시 ``os.killpg``
로 **그룹 전체(손자 ffmpeg 포함)를 SIGTERM→SIGKILL** 한다. ``safe_run.sh`` 는 ``setsid`` 가 없어
손자 프로세스 그룹을 보장하지 못하므로(실측) 여기서 자체 격리한다. 무한 대기 0(항상 timeout).

ffmpeg 는 ``IMAGEIO_FFMPEG_EXE`` 또는 ``.omc/bin/ffmpeg`` (static v7.0.2). MoviePy 경로는 선택적
(``conda -n art``)이며 ``clip.close()`` 를 ``finally`` 에서 호출한다(파일핸들/손자 누수 차단).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FFMPEG = _ROOT / ".omc" / "bin" / "ffmpeg"


def ffmpeg_exe() -> str:
    """ffmpeg 경로: IMAGEIO_FFMPEG_EXE > .omc/bin/ffmpeg(static) > PATH 'ffmpeg'."""
    env = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if env and Path(env).exists():
        return env
    if _DEFAULT_FFMPEG.exists():
        return str(_DEFAULT_FFMPEG)
    return "ffmpeg"


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


def run_isolated(
    cmd: list[str], *, timeout: float, input_bytes: Optional[bytes] = None
) -> ProcResult:
    """외부 프로세스를 **격리 실행**(새 프로세스 그룹·timeout 시 그룹 전체 kill·손자 orphan 0).

    ``start_new_session=True`` 로 새 세션/프로세스 그룹을 만들고, timeout 시 ``os.killpg`` 로
    SIGTERM→(5s 후 잔존 시)SIGKILL 을 그룹에 보낸다. ``safe_run.sh`` 의 setsid-부재 갭을 보완.
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_bytes is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # ★새 프로세스 그룹(손자 ffmpeg 포함 격리)
    )
    try:
        out, err = proc.communicate(input=input_bytes, timeout=timeout)
        return ProcResult(proc.returncode, _dec(out), _dec(err))
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise
    finally:
        # 잔존 시 보강 정리(release 누수 0).
        if proc.poll() is None:
            _kill_group(proc)


def _kill_group(proc: subprocess.Popen) -> None:
    """프로세스 그룹 전체 종료(SIGTERM→SIGKILL). 손자 orphan 차단."""
    with _suppress():
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            with _suppress():
                proc.wait(timeout=5)


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True  # 정리 경로의 예외는 삼킨다(종료성 우선)


def _dec(b: Optional[bytes]) -> str:
    return (b or b"").decode("utf-8", "replace")


# ------------------------------------------------------------------ ffmpeg ops


def has_audio_stream(path: str | Path, *, timeout: float = 30.0) -> bool:
    """``ffmpeg -i`` 의 stderr 에서 Audio 스트림 존재 여부 파싱(ffprobe 불요·static ffmpeg 만)."""
    res = run_isolated([ffmpeg_exe(), "-hide_banner", "-i", str(path)], timeout=timeout)
    # ffmpeg -i 는 출력 미지정 시 rc!=0 이지만 stderr 에 스트림 정보를 찍는다.
    return bool(re.search(r"Stream #\d+:\d+.*: Audio:", res.stderr))


def has_video_stream(path: str | Path, *, timeout: float = 30.0) -> bool:
    res = run_isolated([ffmpeg_exe(), "-hide_banner", "-i", str(path)], timeout=timeout)
    return bool(re.search(r"Stream #\d+:\d+.*: Video:", res.stderr))


def concat_clips(
    clips: list[str | Path], out: str | Path, *, timeout: float = 600.0
) -> Path:
    """ffmpeg concat demuxer 로 클립들을 이어붙인다(재인코딩 copy)."""
    out_p = Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    listfile = out_p.with_suffix(".concat.txt")
    listfile.write_text(
        "\n".join(f"file '{Path(c).resolve()}'" for c in clips), encoding="utf-8"
    )
    try:
        res = run_isolated(
            [
                ffmpeg_exe(),
                "-hide_banner",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listfile),
                "-c",
                "copy",
                str(out_p),
            ],
            timeout=timeout,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed (rc={res.returncode}): {res.stderr[-300:]}"
            )
    finally:
        with _suppress():
            listfile.unlink()
    return out_p


def add_subtitle(
    video: str | Path, text: str, out: str | Path, *, timeout: float = 600.0
) -> Path:
    """drawtext 로 단순 자막 1줄 오버레이(폰트 비의존·격리 실행)."""
    out_p = Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    safe = text.replace(":", r"\:").replace("'", "")
    res = run_isolated(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"drawtext=text='{safe}':x=(w-text_w)/2:y=h-60:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5",
            "-c:a",
            "copy",
            str(out_p),
        ],
        timeout=timeout,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg subtitle failed (rc={res.returncode}): {res.stderr[-300:]}"
        )
    return out_p


def mix_audio(
    video: str | Path, audio: str | Path, out: str | Path, *, timeout: float = 600.0
) -> Path:
    """영상에 오디오 트랙을 믹스(짧은 쪽 기준)."""
    out_p = Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    res = run_isolated(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out_p),
        ],
        timeout=timeout,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mix_audio failed (rc={res.returncode}): {res.stderr[-300:]}"
        )
    return out_p


_FILTER_CACHE: dict[str, bool] = {}


def _has_filter(name: str, *, timeout: float = 15.0) -> bool:
    """ffmpeg 빌드에 특정 필터가 있는지 확인(캐시). static 빌드는 일부 필터(drawtext 등)를 제외할 수 있다."""
    if name not in _FILTER_CACHE:
        res = run_isolated([ffmpeg_exe(), "-hide_banner", "-filters"], timeout=timeout)
        _FILTER_CACHE[name] = bool(re.search(rf"\b{re.escape(name)}\b", res.stdout))
    return _FILTER_CACHE[name]


def assemble_trailer(
    clips: list[str | Path],
    out: str | Path,
    *,
    subtitle: Optional[str] = None,
    audio: Optional[str | Path] = None,
    timeout: float = 900.0,
) -> Path:
    """클립 concat → (자막·best-effort) → (오디오 믹스) 순으로 트레일러 1편 조립(전부 격리 실행).

    자막은 ``drawtext`` 필터에 의존하므로, ffmpeg 빌드에 해당 필터가 없으면(static 빌드 등) **정직하게
    스킵**한다(concat 코어는 유지). drawtext 가용 빌드에서는 자막이 적용된다.
    """
    out_p = Path(out)
    work = out_p.with_suffix(".concat.mp4")
    concat_clips(clips, work, timeout=timeout)
    cur = work
    if subtitle and _has_filter("drawtext"):
        sub_out = out_p.with_suffix(".sub.mp4")
        add_subtitle(cur, subtitle, sub_out, timeout=timeout)
        cur = sub_out
    if audio:
        mix_audio(cur, audio, out_p, timeout=timeout)
    else:
        Path(cur).replace(out_p)
    # 중간 산출 정리
    for tmp in (work, out_p.with_suffix(".sub.mp4")):
        if Path(tmp).exists() and Path(tmp) != out_p:
            with _suppress():
                Path(tmp).unlink()
    return out_p


def moviepy_concat(clips: list[str | Path], out: str | Path) -> Path:
    """선택적 MoviePy 조립 경로(conda -n art·lazy). clip.close() 를 finally 에서 호출."""
    from moviepy import VideoFileClip, concatenate_videoclips  # lazy (art env)

    loaded = []
    try:
        loaded = [VideoFileClip(str(c)) for c in clips]
        final = concatenate_videoclips(loaded)
        try:
            final.write_videofile(
                str(out),
                codec="libx264",
                audio_codec="aac",
                ffmpeg_params=["-y"],
                logger=None,
            )
        finally:
            final.close()
    finally:
        for clip in loaded:
            with _suppress():
                clip.close()
    return Path(out)
