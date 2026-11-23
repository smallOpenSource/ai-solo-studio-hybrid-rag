"""멱등 커서 모듈 — 디스코드 중복 게시 방지.

디스코드 게시는 영구 부작용(채널에 메시지가 남는다)이므로, 이미 게시한 seq를
다시 게시하지 않도록 마지막으로 게시 성공한 seq를 파일에 영속한다.

at-least-once 정책:
    advance_cursor()는 게시 성공 **후** 호출자가 불러야 한다.
    게시 직후 크래시가 나면 커서가 전진하지 않으므로 재시작 시 해당 seq를
    한 번 더 게시할 수 있다(최대 1건 재게시). "누락 0건" > "중복 1건"이
    알림 도메인에서 더 안전하므로 이 트레이드오프를 의도적으로 선택한다.

동시성 전제:
    단일 워커 프로세스 + 단일 봇 루프를 전제로 한다.
    동시 쓰기가 없으므로 파일 락은 불요하다.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# 프로젝트 루트 기준 기본 커서 경로
_DEFAULT_CURSOR_PATH = (
    Path(__file__).parent.parent.parent / "state" / "discord" / "cursor.json"
)


def _resolve_path(path: "str | Path | None") -> Path:
    if path is None:
        return _DEFAULT_CURSOR_PATH
    return Path(path)


def read_cursor(path: "str | Path | None" = None) -> int:
    """마지막으로 게시 성공한 seq를 반환한다.

    파일이 없거나 읽을 수 없으면 0을 반환한다(한 번도 게시 안 한 초기 상태).

    Args:
        path: cursor.json 경로. None이면 ``state/discord/cursor.json`` 사용.

    Returns:
        last_posted_seq (int). 파일 없을 시 0.
    """
    p = _resolve_path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return int(data["last_posted_seq"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return 0


def cursor_initialized(path: "str | Path | None" = None) -> bool:
    """커서 파일이 존재하고 유효한 정수를 담고 있으면 True를 반환한다.

    ``read_cursor()``는 '파일 부재(한 번도 게시 안 함)'와 '정당히 0'을 모두 0으로
    뭉갠다. 콜드스타트에서 이 둘을 구분해야 한다 — 부재면 현재 head부터 시작해 과거
    전체 재방송을 막고, 정당한 0이면 정상 resume. 그 판정을 제공한다.

    Args:
        path: cursor.json 경로. None이면 기본 경로 사용.

    Returns:
        cursor.json이 존재하고 last_posted_seq를 int로 파싱 가능하면 True, 아니면 False.
    """
    p = _resolve_path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        int(data["last_posted_seq"])
        return True
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return False


def should_post(seq: int, path: "str | Path | None" = None) -> bool:
    """해당 seq를 게시해야 하면 True를 반환한다.

    이미 게시한 seq(``seq <= last_posted_seq``)는 False를 반환해 중복 게시를 막는다.

    Args:
        seq: 게시 후보 이벤트 seq.
        path: cursor.json 경로. None이면 기본 경로 사용.

    Returns:
        ``seq > read_cursor()`` 이면 True, 아니면 False.
    """
    return seq > read_cursor(path)


def advance_cursor(seq: int, path: "str | Path | None" = None) -> None:
    """커서를 seq로 전진시킨다(원자적 쓰기).

    temp 파일에 JSON을 쓴 뒤 ``os.replace``로 rename하므로, 프로세스가 쓰는
    도중 크래시가 나도 기존 cursor.json은 손상되지 않는다.

    디렉터리가 없으면 자동 생성한다.

    Args:
        seq: 게시 성공한 이벤트 seq.
        path: cursor.json 경로. None이면 기본 경로 사용.
    """
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps({"last_posted_seq": seq}, ensure_ascii=False)

    # 같은 디렉터리에 temp 파일 생성 → os.replace(atomic rename)
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, p)
    except Exception:
        # 쓰기 실패 시 temp 파일 정리 후 예외 재발생
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
