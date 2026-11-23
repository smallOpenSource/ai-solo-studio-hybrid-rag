"""tools/redis_db_probe.py — Redis logical-db 비어있음 점검 + 안전한 .env REDIS_DB 확정.

★순수 읽기(read-only): `DBSIZE`·`CONFIG GET databases`·`PING`만 사용. `SET/DEL/FLUSHDB/
FLUSHALL` 등 쓰기/파괴 명령 **절대 사용 안 함**(공유 인프라·타 키스페이스 무손상).

동작:
  1) `.env`의 Redis Sentinel 접속정보로 master 접속(직접 IP 금지·`master_for` 경유).
  2) `CONFIG GET databases`로 db 개수 결정(ACL 거부 시 0–15 폴백).
  3) 각 db `DBSIZE` 조회.
  4) `decide_db` 규칙: 설정 `REDIS_DB`(기본 7)가 범위 내 & 비어있음 → 그대로 확정.
     아니면(범위 밖 또는 비-빈) → 범위 내 *첫 빈 db* 채택. 전부 비-빈 → None(종료코드 2).
  5) `--apply` 시에만, 채택 db가 설정값과 다르면 `.env`의 `REDIS_DB=` 한 줄을
     **atomic(temp+rename)**으로 갱신(다른 줄·시크릿 byte 무변).

★비밀값(비밀번호 등)은 **출력/로그 0**. 출력은 db 인덱스와 키 개수(정수)만.

실행:
    /opt/conda/envs/orch/bin/python tools/redis_db_probe.py          # 점검+결정(읽기)
    /opt/conda/envs/orch/bin/python tools/redis_db_probe.py --apply  # 필요 시 .env 갱신
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_DEFAULT_DB = 7
_FALLBACK_DB_COUNT = 16  # CONFIG 거부 시 표준 0–15


def _load_env(path: Path) -> dict[str, str]:
    """`.env`의 단순 KEY=VALUE 파싱(따옴표 제거). 비밀값은 호출측이 출력 금지."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            env[key] = val
    return env


def _parse_hosts(spec: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for hp in spec.split(","):
        hp = hp.strip()
        if ":" in hp:
            host, _, port = hp.rpartition(":")
            out.append((host, int(port)))
    return out


def decide_db(
    configured: int, db_count: int, sizes: dict[int, int]
) -> tuple[int, bool] | None:
    """빈 db 결정(순수 함수·네트워크 0).

    반환: ``(chosen_db, changed)`` 또는 전부 비-빈이면 ``None``.
    - 설정 db가 범위 내(``< db_count``)이고 ``DBSIZE==0`` → ``(configured, False)``.
    - 아니면 범위 내 첫 빈 db → ``(empty, True)``.
    - 빈 db 없음 → ``None`` (호출측이 종료코드 2 + 보고; **clobber 금지**).
    """
    if configured < db_count and sizes.get(configured, 1) == 0:
        return (configured, False)
    for i in range(db_count):
        if sizes.get(i, 1) == 0:
            return (i, True)
    return None


def _atomic_set_redis_db(path: Path, new_db: int) -> None:
    """`.env`의 `REDIS_DB=` 한 줄만 교체(없으면 추가)·temp+rename atomic·타 줄 byte 무변."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out, replaced = [], False
    for ln in lines:
        if ln.lstrip().startswith("REDIS_DB=") and not ln.lstrip().startswith("#"):
            nl = "\n" if ln.endswith("\n") else ""
            out.append(f"REDIS_DB={new_db}{nl}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"REDIS_DB={new_db}\n")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(out))
        os.replace(tmp, path)  # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _dbsizes(sentinel, name, pw, timeout, db_count) -> dict[int, int]:
    """각 db DBSIZE(읽기 전용). master_for 경유·직접 IP 금지."""
    sizes: dict[int, int] = {}
    for i in range(db_count):
        c = sentinel.master_for(name, password=pw, socket_timeout=timeout, db=i)
        sizes[i] = int(c.dbsize())
    return sizes


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv

    try:
        from redis.sentinel import Sentinel
    except Exception as exc:  # pragma: no cover - redis 미설치 안내
        print(f"redis 패키지 없음: {type(exc).__name__}", file=sys.stderr)
        return 3

    env = _load_env(_ENV_PATH)
    hosts = _parse_hosts(env.get("REDIS_SENTINEL_HOSTS", ""))
    name = env.get("REDIS_MASTER_NAME", "")
    pw = env.get("REDIS_PASSWORD") or None
    timeout = float(env.get("REDIS_SOCKET_CONNECT_TIMEOUT", "30") or 30)
    configured = int(env.get("REDIS_DB", str(_DEFAULT_DB)) or _DEFAULT_DB)
    if not hosts or not name:
        print("REDIS_SENTINEL_HOSTS / REDIS_MASTER_NAME 미설정", file=sys.stderr)
        return 3

    skw = {"password": pw} if pw else {}
    sentinel = Sentinel(hosts, sentinel_kwargs=skw, socket_timeout=timeout)
    try:
        addr = sentinel.discover_master(name)  # (host, port) — 비밀 아님
    except Exception as exc:
        print(f"Sentinel master 발견 실패: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"master 발견: {addr[0]}:{addr[1]} (db 점검·읽기 전용)")

    probe0 = sentinel.master_for(name, password=pw, socket_timeout=timeout, db=0)
    try:
        cfg = probe0.config_get("databases")
        db_count = int(cfg.get("databases", _FALLBACK_DB_COUNT))
    except Exception:
        db_count = _FALLBACK_DB_COUNT
        print(f"CONFIG GET databases 거부/불가 → 폴백 {db_count}개(0–{db_count - 1})")

    sizes = _dbsizes(sentinel, name, pw, timeout, db_count)
    print("DBSIZE:", " ".join(f"{i}:{n}" for i, n in sizes.items()))

    decision = decide_db(configured, db_count, sizes)
    if decision is None:
        print("빈 db 없음(전부 비-빈) → 중단(clobber 금지)", file=sys.stderr)
        return 2
    chosen, changed = decision
    if not changed:
        print(f"결정: 설정 db {configured} 비어있음 → 그대로 사용(변경 없음)")
    else:
        why = (
            f"설정 db {configured} 범위 밖(>= {db_count})"
            if configured >= db_count
            else f"설정 db {configured}에 키 {sizes.get(configured)}개"
        )
        print(f"결정: {why} → 빈 db {chosen} 채택")

    if changed and apply:
        _atomic_set_redis_db(_ENV_PATH, chosen)
        print(f".env REDIS_DB={chosen} 로 갱신(atomic·1줄·시크릿 무변)")
    elif changed:
        print(f"[dry-run] --apply 시 .env REDIS_DB={chosen} 로 갱신 예정")

    print(f"RESULT chosen_db={chosen} changed={changed} applied={changed and apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
