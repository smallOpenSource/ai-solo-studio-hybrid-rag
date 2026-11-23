"""tools/p6_wa_probe.py — P6d WorkAdventure 6브릭 연결성 프로브(바운드·정직 pending).

SR/renewal §9.1·AGPL_COMPLIANCE.md 결정④. **로컬 가능 범위만** 검증하고, 도메인/TLS/맵URL
의존분은 `[PENDING]`(≠pass·≠fail·green-fake 금지)로 표기한다.

검증 단계(전부 바운드 — 무한 대기/네트워크 hang 0):
  1. 이미지 핀 정합: deploy/systemd/wa/*.container 가 upstream prebuilt 이미지만 참조하고
     로컬 build 컨텍스트가 없음을 확인(AGPL §13 비수정 단언·정적).
  2. 이미지 pull(바운드·결정④ 좌열): podman 가용 시 6 이미지를 timeout 하에 pull 시도.
     성공분은 이미지 inspect 로 ExposedPorts(play 3000·back gRPC 50051 등)를 확인한다.
     **pull 불가(네트워크/podman 부재) 시 "code 100%/runtime pending: 이미지 pull"** 정직 출력.
  3. 라이브 핸드셰이크(play↔back gRPC·pusher↔redis pub/sub·map-storage fetch)·라이브 아바타
     입장: 전체 스택 가동 + 도메인/TLS/START_ROOM_URL 필요 → 항상 `[PENDING Owner]`.

종료코드: 정적 단언(1단계)이 깨지면 1. pull 은 가능분만 OK·불가분 pending(종료코드 비오염).
사용: PYTHONPATH=/opt/studio /opt/conda/envs/orch/bin/python tools/p6_wa_probe.py
       [--pull]   # 이미지 pull 까지 시도(미지정 시 정적 단언 + 핀 확인만·네트워크 0).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WA_DIR = _ROOT / "deploy" / "systemd" / "wa"

# 결정④ 좌열(로컬 가능) 브릭 이미지의 기대 노출 포트(컨테이너 내부) — inspect 단언용.
_EXPECTED_PORTS = {
    "play": "3000",
    "back": "50051",  # room-api gRPC(내부)
    "map-storage": "3000",
    "icon": "8080",
    "uploader": "8080",
    "redis": "6379",
}

# pull 바운드(초) — 큰 이미지라 넉넉히 두되 무한 대기 차단.
_PULL_TIMEOUT = 300


def _container_units() -> list[Path]:
    return sorted(_WA_DIR.glob("*.container"))


def extract_image(text: str) -> str | None:
    """*.container 본문에서 `Image=` 값(주석 제외·첫 비주석 매치)을 뽑는다(순수)."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if s.startswith("Image="):
            return s.split("=", 1)[1].strip()
    return None


def check_image_pins() -> tuple[bool, list[str]]:
    """1단계: 모든 .container 가 upstream prebuilt 이미지 핀만 참조·build 컨텍스트 0.

    Returns (ok, lines). AGPL §13 비수정 단언 — workadventure-*:<tag> 또는 공식 부속
    (iconserver/redis) 핀이어야 하고, 로컬 build 지시(Build=/build:)가 없어야 한다.
    """
    lines: list[str] = []
    ok = True
    units = _container_units()
    if not units:
        return False, ["[FAIL] .container 유닛을 찾지 못함"]
    for unit in units:
        text = unit.read_text(encoding="utf-8")
        image = extract_image(text)
        if not image:
            ok = False
            lines.append(f"[FAIL] {unit.name}: Image= 핀 없음")
            continue
        # build 컨텍스트(소스 빌드) 금지 — AGPL 비수정 경로.
        if re.search(r"(?mi)^\s*Build\s*=", text) or "build:" in text:
            ok = False
            lines.append(
                f"[FAIL] {unit.name}: 로컬 build 컨텍스트 발견(AGPL 비수정 위반)"
            )
            continue
        # 태그 핀(immutable) 확인 — :latest/:master 같은 가변 태그 경고.
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        mutable = tag in ("", "latest", "master", "develop")
        pinned = "[OK]" if not mutable else "[WARN]"
        if mutable:
            lines.append(f"{pinned} {unit.name}: {image} (★가변 태그 — 불변 핀 권장)")
        else:
            lines.append(f"{pinned} {unit.name}: {image}")
    return ok, lines


def _podman() -> str | None:
    return shutil.which("podman") or (
        "/usr/bin/podman" if Path("/usr/bin/podman").exists() else None
    )


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    """바운드 서브프로세스(무한 대기 0). Returns (rc, combined_output)."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"[timeout {timeout}s]"
    except Exception as exc:  # noqa: BLE001
        return 1, f"[error] {exc}"


def pull_and_inspect(podman: str) -> list[str]:
    """2단계: 6 이미지 바운드 pull + 성공분 ExposedPorts 확인(결정④ 좌열)."""
    lines: list[str] = []
    for unit in _container_units():
        image = extract_image(unit.read_text(encoding="utf-8"))
        if not image:
            continue
        brick = unit.stem.replace("aigs-wa-", "")
        rc, out = _run([podman, "pull", "--quiet", image], _PULL_TIMEOUT)
        if rc != 0:
            tail = out.strip().splitlines()[-1] if out.strip() else ""
            lines.append(
                f"[PENDING] {brick}: pull 실패 — code 100%/runtime pending: 이미지 pull ({tail})"
            )
            continue
        # inspect 로 ExposedPorts 확인(이미지가 기대 포트를 노출하는지·로컬 가능).
        rc2, out2 = _run(
            [
                podman,
                "image",
                "inspect",
                image,
                "--format",
                "{{json .Config.ExposedPorts}}",
            ],
            30,
        )
        want = _EXPECTED_PORTS.get(brick, "")
        exposed = ""
        try:
            ports = json.loads(out2.strip() or "null") or {}
            exposed = ",".join(sorted(ports.keys()))
        except Exception:  # noqa: BLE001
            exposed = "(inspect 파싱 실패)"
        marker = "[OK]" if (want and want in exposed) else "[INFO]"
        lines.append(
            f"{marker} {brick}: pull OK · ExposedPorts={exposed or '없음'} (기대 {want})"
        )
    return lines


def pending_runtime_lines() -> list[str]:
    """3단계: 라이브 핸드셰이크/입장 — 항상 pending Owner(≠pass·green-fake 금지)."""
    return [
        "[PENDING Owner] play↔back gRPC :50051 핸드셰이크 — 전체 스택 가동 필요(이미지 pull 후 로컬 가능 후보·결정④)",
        "[PENDING Owner] pusher(play)↔redis pub/sub 왕복 — 전체 스택 가동 필요",
        "[PENDING Owner] map-storage fetch — 전체 스택 가동 필요",
        "[PENDING Owner] 라이브 아바타 입장 e2e — START_ROOM_URL(GitHub Pages 맵)+공개 도메인+TLS(§9.3 PVE host)",
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="P6d WA 6브릭 연결성 프로브(바운드·정직 pending)"
    )
    ap.add_argument(
        "--pull", action="store_true", help="이미지 pull 까지 시도(미지정 시 정적+핀만)"
    )
    args = ap.parse_args(argv)

    print("=== P6d WA 6브릭 프로브 (결정④·바운드·green-fake 0) ===\n")

    print("[1] 이미지 핀 정합 + AGPL 비수정 단언(정적)")
    ok, lines = check_image_pins()
    for ln in lines:
        print("   " + ln)
    if not ok:
        print("\n[FAIL] 이미지 핀/build 컨텍스트 정적 단언 실패 → 종료코드 1")
        return 1

    print("\n[2] 이미지 pull + ExposedPorts 확인(결정④ 좌열·로컬 가능 범위)")
    podman = _podman()
    if not args.pull:
        print(
            "   [SKIP] --pull 미지정 — 네트워크 0(정적 단언만). pull 검증은 `--pull`."
        )
    elif not podman:
        print(
            "   [PENDING] podman 부재 — code 100%/runtime pending: 이미지 pull(podman 미가용)"
        )
    else:
        for ln in pull_and_inspect(podman):
            print("   " + ln)

    print("\n[3] 라이브 핸드셰이크/입장 (Owner-gated·항상 pending·≠pass)")
    for ln in pending_runtime_lines():
        print("   " + ln)

    print("\n=== 요약: 정적 핀 OK. 라이브 입장 = code 100% / runtime pending Owner ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
