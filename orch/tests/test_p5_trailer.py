"""tools.p5_trailer_assemble 단위 (P5b B6·ffmpeg concat 조립·정직 deferral).

검증(green-theater 차단):
  (a) build_concat_command 인자 계약(결정론·ffmpeg concat-demuxer·출력 경로).
  (b) assemble_trailer 클립 ≥2 강제(1개면 ValueError).
  (c) ffmpeg 부재 시 가짜 mp4 0 + meta 에 pending 정직 표기(produced=False).
  (d) ffmpeg 있으면(가짜 클립 PNG 주입) 실 mp4 산출(produced=True) — 환경 조건부.
  (e) LTX-2 실생성 = "pending Owner: Server B" meta 표기(≠pass·날조 0).

env: ``/opt/conda/envs/orch/bin/python`` (PIL/ffmpeg 선택적·없어도 a/b/c/e 실행).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import p5_trailer_assemble as tr


# ── (a) concat 명령 인자 계약 ────────────────────────────────────────────────
def test_build_concat_command_arg_contract(tmp_path: Path):
    out = tmp_path / "art" / "video" / "trailer-stub.mp4"
    clips = [tmp_path / "clip-00.png", tmp_path / "clip-01.png"]
    cmd = tr.build_concat_command(clips, out, fps=2)
    # ffmpeg concat-demuxer 계약: -f concat -safe 0 -i <list> -pix_fmt yuv420p <out>.
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
    assert "-safe" in cmd and cmd[cmd.index("-safe") + 1] == "0"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    # -i 다음은 concat-list 파일(출력과 같은 디렉토리).
    list_arg = cmd[cmd.index("-i") + 1]
    assert list_arg.endswith("concat-list.txt")
    assert Path(list_arg).parent == out.parent
    # 출력 경로가 마지막 인자.
    assert cmd[-1] == str(out)
    # 결정론: 동일 입력 동일 출력.
    assert cmd == tr.build_concat_command(clips, out, fps=2)


# ── (b) 클립 ≥2 강제 ─────────────────────────────────────────────────────────
def test_assemble_requires_two_clips(tmp_path: Path):
    one_clip = [tmp_path / "only.png"]
    with pytest.raises(ValueError, match="클립 ≥2"):
        tr.assemble_trailer(tmp_path / "vault", clips=one_clip)


# ── (c) ffmpeg 부재 → 가짜 mp4 0 + pending 정직 표기 ──────────────────────────
def test_assemble_no_ffmpeg_honest_pending(tmp_path: Path, monkeypatch):
    """ffmpeg 가 없으면(강제 None) 실 mp4 0 + meta pending(가짜 mp4 금지·green-theater 차단)."""
    monkeypatch.setattr(tr, "ffmpeg_bin", lambda: None)
    vault = tmp_path / "vault"
    res = tr.assemble_trailer(vault)
    # 클립 ≥2 산출(절차적 타이틀 카드·PIL 없으면 manifest).
    assert len(res.clips) >= 2
    # ★가짜 mp4 0: produced=False AND 실제 mp4 파일 부재.
    assert res.produced is False
    assert not res.out_path.is_file()
    assert "pending" in res.mp4_status.lower()
    # concat 명령은 구성됨(조립 헬퍼 code 100%).
    assert res.concat_cmd and res.concat_cmd[0] == "ffmpeg"
    # meta 정직 표기(falsifiable).
    assert res.meta_path is not None and res.meta_path.is_file()
    meta = json.loads(res.meta_path.read_text(encoding="utf-8"))
    assert "pending" in str(meta["trailer_mp4"]).lower()
    # (e) LTX-2 = Server B 정직 표기(≠pass).
    assert "Server B" in meta["ltx2_generation"]
    assert "pending" in meta["ltx2_generation"].lower()


# ── (d) ffmpeg 있으면 실 mp4 산출(환경 조건부·없으면 skip) ─────────────────────
def test_assemble_real_mp4_when_ffmpeg_and_frames(tmp_path: Path):
    """ffmpeg + PIL 둘 다 있으면 실 mp4 를 산출(produced=True). 없으면 skip(정직).

    실 산출 경로를 *실제로* 닫는다 — fake 클립이 아니라 PIL 로 그린 PNG 2장을 concat.
    """
    if tr.ffmpeg_bin() is None:
        pytest.skip(
            "ffmpeg 바이너리 부재 — 조립 실산출 검증은 ffmpeg 보유 env 에서(정직 deferral)"
        )
    if not tr._pil_available():
        pytest.skip("PIL 부재 — 절차적 프레임 생성 불가(crawl/agent env 에서 검증)")
    vault = tmp_path / "vault"
    res = tr.assemble_trailer(vault, title_texts=("A", "B"))
    assert res.produced is True
    assert res.out_path.is_file()
    assert res.out_path.stat().st_size > 0


# ── (e·보강) PIL 있으면 절차적 프레임이 실 PNG(가짜 0) ─────────────────────────
def test_title_card_real_png_when_pil(tmp_path: Path):
    """PIL 있으면 .png 실파일·없으면 manifest(.json) — 둘 다 가짜 PNG 아님."""
    out = tr.build_title_card_clip(tmp_path, text="HELLO", index=0)
    assert out.is_file()
    if tr._pil_available():
        assert out.suffix == ".png"
        # PNG 매직 바이트(실 이미지·날조 0).
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        # PIL 부재: manifest(의도 기록·가짜 PNG 0).
        assert out.name.endswith(".manifest.json")
        meta = json.loads(out.read_text(encoding="utf-8"))
        assert meta["kind"] == "title_card" and meta["text"] == "HELLO"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
