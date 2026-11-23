"""P7c — orch/video_assemble.py unit (로컬 ffmpeg·static·subprocess 격리/종료성).

★종료성 P0(MAJOR#5): run_isolated 가 timeout 시 **프로세스 그룹 전체(손자 포함)를 kill**해
orphan 0 임을 입증. + ffmpeg concat/audio-detect 1산출(로컬·게이트웨이 무관). 영상 *생성* (라이브)
은 ACTION(게이트웨이 degraded)·여기선 조립만.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from orch.video_assemble import (
    concat_clips,
    ffmpeg_exe,
    has_audio_stream,
    has_video_stream,
    run_isolated,
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _make_clip(path, *, with_audio=False, dur=1):
    args = [
        ffmpeg_exe(),
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={dur}:size=128x128:rate=10",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    args += ["-pix_fmt", "yuv420p", "-t", str(dur), str(path)]
    res = run_isolated(args, timeout=60)
    assert res.returncode == 0, res.stderr[-300:]


# ----------------------------------------------------------- 종료성 / orphan 차단


def test_run_isolated_timeout_kills_group(tmp_path):
    pidfile = tmp_path / "pid"
    # 손자 sleep 을 띄우는 셸 — group kill 이 안 되면 sleep 이 orphan 으로 남는다.
    with pytest.raises(subprocess.TimeoutExpired):
        run_isolated(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], timeout=1.0)
    time.sleep(0.6)
    child_pid = int(pidfile.read_text().strip())
    assert not _pid_alive(child_pid), (
        "손자 sleep 이 그룹 kill 되지 않고 orphan 으로 남음"
    )


def test_run_isolated_normal_returns():
    res = run_isolated(["sh", "-c", "echo hello"], timeout=10)
    assert res.returncode == 0
    assert "hello" in res.stdout


# --------------------------------------------------------------- ffmpeg 조립


def test_ffmpeg_exe_resolves():
    exe = ffmpeg_exe()
    assert exe.endswith("ffmpeg") or "ffmpeg" in exe


@pytest.mark.skipif(
    not os.path.exists(ffmpeg_exe()), reason="ffmpeg static binary absent"
)
def test_concat_clips_produces_video(tmp_path):
    c1 = tmp_path / "c1.mp4"
    c2 = tmp_path / "c2.mp4"
    _make_clip(c1)
    _make_clip(c2)
    out = tmp_path / "out.mp4"
    concat_clips([c1, c2], out, timeout=120)
    assert out.exists() and out.stat().st_size > 0
    assert has_video_stream(out)


@pytest.mark.skipif(
    not os.path.exists(ffmpeg_exe()), reason="ffmpeg static binary absent"
)
def test_has_audio_stream_detection(tmp_path):
    av = tmp_path / "av.mp4"
    vonly = tmp_path / "v.mp4"
    _make_clip(av, with_audio=True)
    _make_clip(vonly, with_audio=False)
    assert has_audio_stream(av) is True
    assert has_audio_stream(vonly) is False


@pytest.mark.skipif(
    not os.path.exists(ffmpeg_exe()), reason="ffmpeg static binary absent"
)
def test_assemble_trailer_concat_subtitle(tmp_path):
    from orch.video_assemble import assemble_trailer

    c1 = tmp_path / "a.mp4"
    c2 = tmp_path / "b.mp4"
    _make_clip(c1)
    _make_clip(c2)
    out = tmp_path / "trailer.mp4"
    assemble_trailer([c1, c2], out, subtitle="P7 DEMO", timeout=120)
    assert out.exists() and out.stat().st_size > 0
    assert has_video_stream(out)
