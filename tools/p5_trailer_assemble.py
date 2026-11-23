"""SR/renewal P5b B6 — §28.1 #7 트레일러 *조립* 헬퍼 (로컬·ffmpeg concat).

스펙 권위(renewal-spec-is-authority · SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md):
  - §28.1 #7(SPEC:1911): 트레일러 = "LTX-2 클립(§17.2) + ffmpeg 조립" → ``art/video/trailer.mp4``.
  - §17.2 영상: LTX-2 비디오 생성(ComfyUI·Server B 의존).

★정직 경계(계획 §1.4 Principle 4·§6 weakest-criterion-wins·시나리오3):
  - **ffmpeg 조립(이 모듈)** = 로컬·Server B 무관 → P5 에서 *검증*(placeholder 클립 concat).
  - **LTX-2 실생성** = "code 100% / runtime pending Owner: Server B"(P7·§16 Owner 갭·≠pass).
    이 모듈은 LTX-2 를 호출하지 않는다(ComfyUI 클라이언트는 P7 ``orch/comfy.py`` 스코프).
  - ffmpeg 바이너리가 **부재**하면 가짜 mp4 를 만들지 않는다 — 조립 명령·클립을 산출하고
    ``trailer.meta.json`` 에 ``"trailer_mp4": "pending: ffmpeg bin"`` 으로 정직 표기한다
    (green-theater 차단·렌더-only/날조 0). ffmpeg 가 있으면 실제 ``.mp4`` 를 산출한다.

설계(범위 = 조립만·생성 0):
  1. ``build_title_card_clip``: 절차적 단색 타이틀 카드 프레임(PIL 있으면 PNG·없으면 manifest).
  2. ``build_concat_command``: ffmpeg concat-demuxer 인자 리스트(결정론·테스트 검증 표면).
  3. ``assemble_trailer``: 클립 ≥2 → concat → ``trailer-stub.mp4``(ffmpeg 있을 때) 또는
     pending 표기(없을 때). placeholder = 절차적 타이틀 카드 + (가능 시) 게임 스크린샷.

이 모듈은 orch 코어 그래프(graph/org_graph/app/outbound)와 **독립한 신규 도구 표면**이다
(동결셋 무관·코어 0수정). PIL/ffmpeg 는 *선택적*(import 시점 강제 안 함 → orch env 에서도
단위 테스트가 명령 구성/경로 로직을 검증). 실 산출은 PIL+ffmpeg 보유 env(crawl/agent)에서.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# §28.1 #7 트레일러 산출 위치(vault/art/video/). stub = 조립 검증 산출물명.
_VIDEO_DIR_REL = Path("art") / "video"
_STUB_NAME = "trailer-stub.mp4"
_META_NAME = "trailer.meta.json"
_PENDING_BIN = "pending: ffmpeg bin (조립 헬퍼 code 100%·바이너리 미설치)"
# §17.2 LTX-2 실생성 = Server B 의존(P7·≠pass). 메타 정직 표기 리터럴.
_PENDING_LTX2 = "code 100% / runtime pending Owner: Server B (P7·§16·≠pass)"


@dataclass
class TrailerResult:
    """트레일러 조립 결과(falsifiable·정직 표기).

    ``produced`` 이면 ffmpeg 로 실 mp4 산출. 아니면 ``mp4_status`` 에 pending 사유
    (가짜 mp4 0). ``clips`` = concat 대상 클립 경로, ``concat_cmd`` = 실행한(또는 할)
    ffmpeg 인자, ``meta_path`` = 정직 표기 메타 JSON 경로.
    """

    produced: bool
    out_path: Path
    clips: list[Path] = field(default_factory=list)
    concat_cmd: list[str] = field(default_factory=list)
    mp4_status: str = ""
    meta_path: Path | None = None


def ffmpeg_bin() -> str | None:
    """PATH 의 ffmpeg 경로(없으면 None·정직 deferral 판정용)."""
    return shutil.which("ffmpeg")


def _pil_available() -> bool:
    """PIL(Pillow) import 가능 여부(절차적 프레임 생성 가능성)."""
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def build_title_card_clip(
    out_dir: Path | str,
    *,
    text: str,
    color: tuple[int, int, int] = (24, 24, 32),
    size: tuple[int, int] = (640, 360),
    index: int = 0,
) -> Path:
    """절차적 단색 타이틀 카드 1프레임(PNG) 생성. 반환 = 프레임 경로(.png).

    PIL 있으면 단색 배경 + 텍스트 PNG 를 실제로 그린다(외부의존0·로컬). PIL 부재 시
    프레임 *manifest*(``.png.manifest.json``·의도 기록)만 남긴다(가짜 PNG 0·정직).
    클립명은 index 로 결정론(concat 순서 안정).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = out_dir / f"clip-{index:02d}.png"
    if _pil_available():
        from PIL import Image, ImageDraw

        img = Image.new("RGB", size, color)
        draw = ImageDraw.Draw(img)
        # 기본 비트맵 폰트(외부 폰트 의존0). 중앙 근처 배치.
        draw.text((size[0] // 12, size[1] // 2 - 8), text, fill=(235, 235, 245))
        img.save(frame, format="PNG")
        return frame
    # PIL 부재: 가짜 PNG 금지 → manifest 로 의도만 기록(정직).
    manifest = out_dir / f"clip-{index:02d}.png.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "kind": "title_card",
                "text": text,
                "color": list(color),
                "size": list(size),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def build_concat_command(
    clips: list[Path | str],
    out_path: Path | str,
    *,
    fps: int = 2,
    frame_secs: float = 1.5,
) -> list[str]:
    """ffmpeg concat-demuxer 인자 리스트를 결정론으로 구성(★테스트 검증 표면).

    각 클립(PNG 프레임)을 ``frame_secs`` 초간 보여주는 정지영상 시퀀스를 concat 한다.
    실행은 ``assemble_trailer`` 가 한다(이 함수는 *명령만* 만든다 — 부작용 0·단위 검증용).
    인자 계약: ``ffmpeg -y -f concat -safe 0 -i <list> -r <fps> -pix_fmt yuv420p <out>``.
    """
    out_path = Path(out_path)
    list_file = out_path.parent / "concat-list.txt"
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-r",
        str(fps),
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]


def _write_concat_list(clips: list[Path], out_dir: Path, frame_secs: float) -> Path:
    """ffmpeg concat-demuxer 입력 리스트 파일(`file '...'` + `duration N`)을 쓴다."""
    list_file = out_dir / "concat-list.txt"
    lines: list[str] = []
    for clip in clips:
        lines.append(f"file '{clip.resolve()}'")
        lines.append(f"duration {frame_secs}")
    # concat-demuxer 는 마지막 프레임 duration 적용을 위해 마지막 file 을 한 번 더 둔다.
    if clips:
        lines.append(f"file '{clips[-1].resolve()}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_file


def assemble_trailer(
    vault_dir: Path | str,
    *,
    clips: list[Path | str] | None = None,
    title_texts: tuple[str, ...] = ("AI GAME STUDIO", "SLIDE PUZZLE", "PLAY NOW"),
    frame_secs: float = 1.5,
) -> TrailerResult:
    """§28.1 #7 트레일러 *조립* — 클립 ≥2 concat → ``vault/art/video/trailer-stub.mp4``.

    동작:
      1. ``clips`` 미지정 시 절차적 타이틀 카드 ≥2 생성(``build_title_card_clip``).
      2. concat 리스트 + ``build_concat_command`` 로 ffmpeg 인자 구성.
      3. ffmpeg 있으면 실행해 실 mp4 산출(``produced=True``). 없으면 가짜 mp4 0 +
         ``trailer.meta.json`` 에 pending 표기(``produced=False``·정직 deferral).

    ★LTX-2 실생성(트레일러 전반부)은 호출하지 않는다 — meta 에 "pending Owner: Server B"
    로 표기(P7·§16·≠pass). 이 헬퍼는 조립 *후반부*(로컬 ffmpeg)만 검증한다.
    """
    vault_dir = Path(vault_dir)
    video_dir = vault_dir / _VIDEO_DIR_REL
    video_dir.mkdir(parents=True, exist_ok=True)
    out_path = video_dir / _STUB_NAME

    # 1) 클립 준비(≥2). 미지정 시 절차적 타이틀 카드.
    if clips is None:
        prepared = [
            build_title_card_clip(video_dir, text=t, index=i)
            for i, t in enumerate(title_texts)
        ]
    else:
        prepared = [Path(c) for c in clips]
    if len(prepared) < 2:
        raise ValueError("트레일러 조립은 클립 ≥2 필요(concat 검증 의미)")

    concat_cmd = build_concat_command(prepared, out_path, frame_secs=frame_secs)
    binpath = ffmpeg_bin()

    # PNG 프레임이 실제로 있는지(PIL 산출) — manifest(.json)뿐이면 ffmpeg 실행 불가.
    real_frames = [c for c in prepared if c.suffix.lower() == ".png" and c.is_file()]
    can_run = binpath is not None and len(real_frames) >= 2

    produced = False
    mp4_status = ""
    if can_run:
        _write_concat_list(real_frames, video_dir, frame_secs)
        try:
            subprocess.run(  # noqa: S603 — 인자 배열(셸 비경유)·로컬 ffmpeg.
                concat_cmd,
                check=True,
                capture_output=True,
                timeout=120,
            )
            produced = out_path.is_file()
            mp4_status = (
                "produced (local ffmpeg concat)"
                if produced
                else "ffmpeg ran but no output"
            )
        except (OSError, subprocess.SubprocessError) as exc:
            mp4_status = f"{_PENDING_BIN} (run failed: {type(exc).__name__})"
    else:
        # 가짜 mp4 금지 — 명령·클립은 산출하고 pending 정직 표기.
        reason = []
        if binpath is None:
            reason.append("no ffmpeg")
        if len(real_frames) < 2:
            reason.append("no PIL frames (only manifests)")
        mp4_status = f"{_PENDING_BIN} ({', '.join(reason)})"

    # 3) 정직 표기 메타(항상·falsifiable provenance).
    meta = {
        "deliverable": "§28.1 #7 트레일러 (조립)",
        "task_id": "p5-slide-puzzle",
        "clips": [str(c) for c in prepared],
        "concat_cmd": concat_cmd,
        "ffmpeg_bin": binpath or "(none)",
        "trailer_mp4": str(out_path) if produced else mp4_status,
        "ltx2_generation": _PENDING_LTX2,
        "note": (
            "조립 헬퍼는 로컬 ffmpeg concat(후반부)만 검증한다. LTX-2 실 클립 생성(전반부)은 "
            "Server B(ComfyUI·§16 Owner 갭) 의존 = P7 스코프·≠pass. ffmpeg 바이너리 부재 시 "
            "가짜 mp4 를 만들지 않고 본 메타에 pending 으로 표기한다(green-theater 차단)."
        ),
    }
    meta_path = video_dir / _META_NAME
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return TrailerResult(
        produced=produced,
        out_path=out_path,
        clips=prepared,
        concat_cmd=concat_cmd,
        mp4_status=mp4_status,
        meta_path=meta_path,
    )


def _vault_dir() -> Path:
    """repo 루트의 ``vault/``(이 파일 = ``tools/p5_trailer_assemble.py`` → parents[1])."""
    return Path(__file__).resolve().parents[1] / "vault"


if __name__ == "__main__":  # pragma: no cover — CLI 산출(실 vault).
    res = assemble_trailer(_vault_dir())
    print(
        json.dumps(
            {
                "produced": res.produced,
                "out_path": str(res.out_path),
                "mp4_status": res.mp4_status,
                "clips": [str(c) for c in res.clips],
                "meta": str(res.meta_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
