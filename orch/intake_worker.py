"""SR/renewal P9 US-9c-1/9c-2/9c-3 — 인입 워커(§15 Knowledge Intake).

공유 Drive(또는 로컬 fixture) → 동기화 → PDF/CSV→MD 정규화 →
vault/research/ 저장 → capture_and_index(untrusted) 증분 색인 → dedup 멱등.

★§11.1 MAJOR-A: 모든 외부 적재물 색인은 capture_and_index(provenance=untrusted) 단일 경로.
  raw store.upsert / ingest 직접 호출 절대 금지.

§11.1④ P8b-1 quarantine: injection-dominant 문서(탐지 임계 초과)는
  vault/quarantine/<ts>-<slug>.md 로 격리 + 인덱싱 차단 + 로그 통지.
  임계 미만(소수 라인)은 기존 strip 유지 + 통과. strip은 대체 아님(둘 다 허용).

종료성(P0): rpop 폴 바운드·rclone는 argv 조립만(실행 경계 명시).
시크릿(rclone.conf·OAuth 토큰) 값은 절대 출력/로깅하지 않는다.
"""

from __future__ import annotations

import contextlib
import csv
import io
import logging
import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Protocol

from orch.knowledge_lifecycle import ArtifactProvenance, capture_and_index

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# §11.1④ P8b-1 quarantine 임계
# ---------------------------------------------------------------------------
# 문서 전체 라인 대비 injection 라인 비율이 이 임계를 초과하면 injection-dominant로 판정.
# 임계 미만(소수 라인)은 기존 strip으로 통과.
_QUARANTINE_RATIO_THRESHOLD: float = 0.15  # 15% 이상이면 격리
_QUARANTINE_MIN_INJECTION_LINES: int = 3  # 최소 3라인 이상이어야 격리 (비율 단독 방지)

# ---------------------------------------------------------------------------
# §11.1 새니타이즈 패턴 — 활성 콘텐츠·인젝션 시도·숨김 텍스트 제거
# ---------------------------------------------------------------------------

# 스크립트/HTML 태그
_RE_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_RE_HTML_TAG = re.compile(r"<[^>]+>")
# 프롬프트 인젝션 패턴 ("ignore previous", 도구호출 지시 등)
_RE_INJECTION = re.compile(
    r"(ignore\s+previous|<\s*tool_call|</?\s*function|SYSTEM:|USER:|ASSISTANT:)",
    re.IGNORECASE,
)
# 제어 문자(탭·개행 제외)
_RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 제로폭 문자·유니코드 오프스크린
# 제로폭/방향제어/BOM 문자(스테가노그래피·프롬프트인젝션 은닉 표면)·명시 코드포인트(가독성·deslop MINOR-3)
_RE_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2028-\u202e\ufeff]")


def _sanitize(text: str) -> str:
    """§11.1 인입 새니타이즈: 활성·숨김 콘텐츠 제거(결정론).

    - 스크립트/HTML 태그 제거
    - 인젝션 패턴 라인 제거(strip — §11.1④ 허용)
    - 제어문자·제로폭 문자 제거
    - 유니코드 정규화(NFC)
    """
    text = _RE_SCRIPT.sub("", text)
    text = _RE_HTML_TAG.sub("", text)
    # 인젝션 패턴 포함 라인 전체 제거
    lines = [ln for ln in text.splitlines() if not _RE_INJECTION.search(ln)]
    text = "\n".join(lines)
    text = _RE_CTRL.sub("", text)
    text = _RE_ZERO_WIDTH.sub("", text)
    text = unicodedata.normalize("NFC", text)
    return text


def _count_injection_lines(text: str) -> tuple[int, int]:
    """텍스트에서 injection 패턴 라인 수와 전체 라인 수를 반환한다(결정론).

    Returns:
        (injection_line_count, total_line_count)
    """
    all_lines = text.splitlines()
    total = len(all_lines)
    injection = sum(1 for ln in all_lines if _RE_INJECTION.search(ln))
    return injection, total


def _is_injection_dominant(text: str) -> bool:
    """§11.1④ P8b-1: 문서가 injection-dominant인지 판정(결정론).

    임계: injection 라인 수 >= _QUARANTINE_MIN_INJECTION_LINES
          AND injection 라인 비율 >= _QUARANTINE_RATIO_THRESHOLD.
    두 조건 모두 충족해야 격리(단순 1-2라인 strip으로 처리 가능한 경우 제외).
    """
    injection_count, total = _count_injection_lines(text)
    if total == 0:
        return False
    ratio = injection_count / total
    return (
        injection_count >= _QUARANTINE_MIN_INJECTION_LINES
        and ratio >= _QUARANTINE_RATIO_THRESHOLD
    )


def _quarantine_document(
    text: str,
    slug: str,
    vault_dir: Path,
    source_url: str = "",
) -> Path:
    """§11.1④ P8b-1: injection-dominant 문서를 vault/quarantine/ 에 격리.

    Args:
        text: 원본 문서 텍스트(strip 전·정보손실 없는 격리).
        slug: 파일명 슬러그.
        vault_dir: vault 기본 경로(vault/quarantine/ 은 여기서 파생).
        source_url: 출처 URI(로그/격리파일 헤더에 기록).

    Returns:
        격리된 파일 경로.
    """
    # vault/quarantine/ 은 vault_dir 의 부모 아래(vault/research/ → vault/)
    quarantine_dir = vault_dir.parent / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"{ts}-{slug}.md"
    dest = quarantine_dir / filename
    # 격리파일 헤더: 보안 이벤트 마커 + 출처(시크릿 값 0노출)
    header = (
        f"<!-- QUARANTINE: injection-dominant document\n"
        f"     source_url: {source_url}\n"
        f"     quarantined_at: {ts}\n"
        f"-->\n\n"
    )
    dest.write_text(header + text, encoding="utf-8")
    logger.warning(
        "[P8b-1 QUARANTINE] injection-dominant 문서 격리: slug=%s source=%s dest=%s",
        slug,
        source_url,
        dest,
    )
    return dest


# ---------------------------------------------------------------------------
# 정규화: PDF/CSV → MD (§15 파이프라인)
# ---------------------------------------------------------------------------


def normalize_to_md(path: Path) -> str:
    """PDF/CSV/MD 파일을 마크다운 문자열로 정규화한다(결정론·§11.1 새니타이즈 포함).

    지원:
    - .pdf  → pypdf 텍스트 추출 → MD (intake env 필요)
    - .csv  → stdlib csv → 표 마크다운
    - .md / .txt / 기타 → 그대로 읽기

    Args:
        path: 대상 파일 경로.

    Returns:
        새니타이즈된 마크다운 문자열.

    Raises:
        ValueError: 지원하지 않는 형식 또는 추출 실패.
    """
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _pdf_to_md(path)
    if suffix == ".csv":
        return _csv_to_md(path)
    # .md / .txt / 기타: 원문 그대로(새니타이즈 적용)
    text = path.read_text(encoding="utf-8", errors="replace")
    return _sanitize(text)


def _pdf_to_md(path: Path) -> str:
    """PDF → 마크다운 텍스트(pypdf·intake env 필요)."""
    try:
        import pypdf  # noqa: PLC0415 — intake env 지연 import
    except ImportError as exc:
        raise ImportError(
            "pypdf를 import할 수 없음 — intake conda env에서 실행하거나 "
            "`pip install pypdf`를 선행하세요."
        ) from exc

    reader = pypdf.PdfReader(str(path))
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        extracted = page.extract_text() or ""
        if extracted.strip():
            pages.append(f"## Page {i + 1}\n\n{extracted.strip()}")

    raw = "\n\n".join(pages)
    return _sanitize(raw)


def _csv_to_md(path: Path) -> str:
    """CSV → 마크다운 표(stdlib csv·결정론)."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return ""

    header = rows[0]
    sep = ["---"] * len(header)
    body_rows = rows[1:]

    def _row(cells: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    lines = [_row(header), _row(sep)] + [_row(r) for r in body_rows]
    raw = "\n".join(lines)
    return _sanitize(raw)


# ---------------------------------------------------------------------------
# SyncSource Protocol + 어댑터 2종
# ---------------------------------------------------------------------------


class SyncSource(Protocol):
    """동기화 소스 추상화 — sync() → 신규/변경 파일 목록."""

    def sync(self, dest: Path) -> list[Path]: ...


class LocalFixtureSource:
    """로컬 디렉토리를 dest에 복사하는 동기화 소스(테스트·로컬 fixture·라이브 공용).

    Args:
        src: 원본 디렉토리(PDF/CSV/MD 파일 포함).
    """

    def __init__(self, src: Path) -> None:
        self._src = src

    def sync(self, dest: Path) -> list[Path]:
        """src의 파일을 dest에 복사하고 목록을 반환한다."""
        import shutil  # noqa: PLC0415

        dest.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for f in sorted(self._src.iterdir()):
            if f.is_file():
                target = dest / f.name
                shutil.copy2(f, target)
                copied.append(target)
        return copied


class RcloneSource:
    """rclone argv 조립 + (선택) 실행 동기화 소스(§15 Drive 연동).

    rclone 바이너리가 없는 환경에서도 argv 조립만 하면 테스트 통과.
    실제 OAuth Drive 연동은 라이브 환경(ACTION) — rclone 미설치 시 BLOCKED 명시.

    Args:
        remote: rclone remote 경로(예: "gdrive:intake" 또는 ":local:/path/to").
        rclone_bin: rclone 바이너리 경로(기본 "rclone").
        dry_run: True면 argv 조립만·실행 안 함(테스트용).
        extra_args: rclone copy에 추가할 인자 목록.
    """

    def __init__(
        self,
        remote: str,
        rclone_bin: str = "rclone",
        dry_run: bool = False,
        extra_args: list[str] | None = None,
    ) -> None:
        self._remote = remote
        self._rclone_bin = rclone_bin
        self._dry_run = dry_run
        self._extra_args = extra_args or []

    def build_argv(self, dest: Path) -> list[str]:
        """rclone copy 명령 argv를 조립한다(시크릿/토큰 값 포함 금지).

        Returns:
            실행 가능한 argv 목록(시크릿 0노출).
        """
        return [
            self._rclone_bin,
            "copy",
            self._remote,
            str(dest),
            *self._extra_args,
        ]

    def sync(self, dest: Path) -> list[Path]:
        """rclone copy를 실행해 dest에 동기화한 후 파일 목록을 반환한다.

        dry_run=True면 argv 조립만(실행 0).
        rclone 미설치 시: ACTION — 라이브 Drive 연동은 rclone + OAuth 필요.
        """
        dest.mkdir(parents=True, exist_ok=True)
        argv = self.build_argv(dest)

        if self._dry_run:
            logger.info("RcloneSource dry_run: argv=%s", argv)
            return []

        # 실행 경계 — rclone이 없으면 명시적 ACTION 오류
        rclone_path = self._rclone_bin
        import shutil as _shutil  # noqa: PLC0415

        if not _shutil.which(rclone_path):
            raise RuntimeError(
                f"ACTION: rclone 바이너리({rclone_path!r})를 찾을 수 없음. "
                "설치 후 재시도하거나 dry_run=True로 argv만 검증하세요."
            )

        result = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"rclone copy 실패 (exit {result.returncode}): {result.stderr[:200]}"
            )

        return sorted(dest.iterdir()) if dest.exists() else []


# ---------------------------------------------------------------------------
# 인입 파이프라인
# ---------------------------------------------------------------------------


def _slug(path: Path) -> str:
    """파일명(확장자 포함)에서 vault 저장용 안전한 슬러그를 만든다.

    확장자를 포함해 슬러그를 생성함으로써 동일 stem 다른 확장자 파일(sample.csv vs
    sample.md)이 vault에서 덮어쓰이는 것을 방지한다.
    """
    name = path.stem + (path.suffix.replace(".", "_") if path.suffix else "")
    slug = re.sub(r"[^\w\-]", "_", name)
    return slug[:80]  # 최대 80자


def ingest_files(
    paths: list[Path],
    *,
    store: object,
    vault_dir: Path = Path("vault/research"),
) -> dict:
    """정규화 → vault/research/<slug>.md 저장 → capture_and_index(untrusted) 색인.

    §11.1④ P8b-1: injection-dominant 문서는 vault/quarantine/ 격리 + 인덱싱 차단.
    임계 미만은 기존 strip 유지 + 통과(strip은 대체 아님).

    Args:
        paths: 정규화할 파일 경로 목록.
        store: knowledge.store._Store Protocol 구현체(KnowledgeStore 또는 mock).
        vault_dir: vault 저장 기본 경로.

    Returns:
        {"processed": 처리수, "indexed": 총_chunk수, "skipped": 스킵수,
         "vault_paths": [저장된_md_경로, ...],
         "quarantined": [격리된_파일_경로, ...]}  ← add-only(기존 키 보존)

    ★§11.1 MAJOR-A: store.upsert / ingest 직접 호출 절대 금지.
    모든 색인은 capture_and_index(provenance=untrusted) 단일 경로.
    """
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    indexed = 0
    skipped = 0
    vault_paths: list[str] = []
    quarantined: list[str] = []  # §11.1④ P8b-1 add-only

    for path in paths:
        source_url = path.as_uri()  # file:// URI — 출처 검증 가능
        slug = _slug(path)
        try:
            # normalize_to_md 내부에서 _sanitize(strip) 적용됨
            # 단, injection-dominant 판정은 원본 텍스트 기준으로 수행해야 하므로
            # 먼저 원본 텍스트를 읽어 판정한 후 정규화한다.
            raw_text = _read_raw_text(path)
        except Exception:
            logger.warning("원본 읽기 실패, 스킵: %s", path, exc_info=True)
            skipped += 1
            continue

        # §11.1④ P8b-1: injection-dominant 판정(원본 기준)
        if _is_injection_dominant(raw_text):
            q_path = _quarantine_document(
                raw_text, slug, vault_dir, source_url=source_url
            )
            quarantined.append(str(q_path))
            # 인덱싱 차단 — capture_and_index 미호출
            logger.info("[P8b-1] 격리 완료(미인덱싱): %s -> %s", path, q_path)
            continue

        # injection-dominant 아님: 기존 strip + 정규화 + 통과
        try:
            md = normalize_to_md(path)
        except Exception:
            logger.warning("정규화 실패, 스킵: %s", path, exc_info=True)
            skipped += 1
            continue

        if not md.strip():
            logger.debug("빈 마크다운, 스킵: %s", path)
            skipped += 1
            continue

        # vault/research/<slug>.md 저장
        vault_path = vault_dir / f"{slug}.md"
        vault_path.write_text(md, encoding="utf-8")
        vault_paths.append(str(vault_path))

        # §11.1 단일 인입 경로: capture_and_index(untrusted)
        provenance = ArtifactProvenance(
            provenance="untrusted",
            source_url=source_url,
        )
        try:
            n = capture_and_index(
                store,
                md,
                source=source_url,
                provenance=provenance,
            )
            indexed += n
            processed += 1
        except Exception:
            logger.warning("capture_and_index 실패, 스킵: %s", path, exc_info=True)
            skipped += 1

    return {
        "processed": processed,
        "indexed": indexed,
        "skipped": skipped,
        "vault_paths": vault_paths,
        "quarantined": quarantined,  # add-only — 호출자 무회귀(기존 키 보존)
    }


def _read_raw_text(path: Path) -> str:
    """injection-dominant 판정용 원본 텍스트 읽기(HTML 제거 후·strip 전).

    PDF/CSV는 파싱 후 텍스트를 추출하고, MD/txt는 그대로 읽는다.
    _sanitize 의 strip(injection 라인 제거) *전* 텍스트를 반환해야 정확한 임계 판정이 가능.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pypdf  # noqa: PLC0415
        except ImportError:
            return path.read_text(encoding="utf-8", errors="replace")
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p for p in pages if p.strip())
    if suffix == ".csv":
        return path.read_text(encoding="utf-8-sig", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Redis rpop 바운드 폴 헬퍼(워커 루프 소비측 골격)
# ---------------------------------------------------------------------------

_INTAKE_QUEUE_KEY = "aigs:intake:queue"
_INTAKE_DEFAULT_MAX_POLL = 100  # 종료성 상한 — 무한 루프 금지


def poll_intake_queue(
    store: object,
    vault_dir: Path = Path("vault/research"),
    max_poll: int = _INTAKE_DEFAULT_MAX_POLL,
) -> dict:
    """Redis aigs:intake:queue에서 메시지를 바운드 폴해 ingest_files로 처리한다.

    Redis 미설정 시 graceful no-op(503 아님).
    종료성(P0): max_poll 상한·무한 루프 절대 금지.

    메시지 형식(JSON): {"path": "/absolute/path/to/file"}

    Returns:
        {"polled": 폴링수, "processed": 처리수, "indexed": 총_chunk수}
    """
    import json  # noqa: PLC0415

    try:
        import redis  # noqa: PLC0415
    except ImportError:
        logger.debug("redis 미설치 — intake queue 폴 no-op")
        return {"polled": 0, "processed": 0, "indexed": 0}

    try:
        import os  # noqa: PLC0415

        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        client = redis.from_url(redis_url, decode_responses=True, socket_timeout=1.0)
        client.ping()
    except Exception:
        logger.debug("Redis 연결 불가 — intake queue 폴 no-op")
        return {"polled": 0, "processed": 0, "indexed": 0}

    polled = 0
    total_processed = 0
    total_indexed = 0

    for _ in range(max_poll):  # 종료성 바운드
        msg = client.rpop(_INTAKE_QUEUE_KEY)
        if msg is None:
            break
        polled += 1
        try:
            data = json.loads(msg)
            file_path = Path(data["path"])
            result = ingest_files([file_path], store=store, vault_dir=vault_dir)
            total_processed += result["processed"]
            total_indexed += result["indexed"]
        except Exception:
            logger.warning("intake queue 메시지 처리 실패: %r", msg, exc_info=True)

    return {"polled": polled, "processed": total_processed, "indexed": total_indexed}


# ---------------------------------------------------------------------------
# 워커 데몬 entrypoint (MAJOR-1 — 소비측 데몬·systemd aigs-intake.service)
# ---------------------------------------------------------------------------
#
# ★종료성 P0: 바운드 폴 루프 — 무한 루프 절대 금지. 두 겹 상한:
#   (1) 각 라운드 poll_intake_queue 가 max_poll(=100) 로 rpop 바운드.
#   (2) 외부 루프는 max_rounds 안전 상한(SIGTERM 수신 시 즉시 정상 종료).
# Redis/Store 미설정 시 graceful 정상 종료(rc=0·503 아님). 시크릿 0출력.
# 라이브 Drive 동기화(rclone OAuth·§16)는 여전히 ACTION이나 데몬/배선은 코드 완성.

_DEFAULT_POLL_INTERVAL_S = 5.0  # 라운드 간 대기(초)
_DEFAULT_MAX_ROUNDS = (
    0  # 0 = systemd 관리 무기한(SIGTERM 까지)·>0 = 라운드 상한(테스트/일회성)
)


def _build_knowledge_store() -> object | None:
    """ingest_files 용 KnowledgeStore 를 endpoint.yaml 설정으로 연다.

    실패(서버 미가동·설정 부재) 시 None 반환(graceful) — 데몬은 정상 종료한다.
    """
    try:
        from knowledge.store import KnowledgeStore  # noqa: PLC0415

        return KnowledgeStore()
    except Exception:
        logger.warning(
            "KnowledgeStore 생성 실패 — intake 데몬 no-op 종료", exc_info=True
        )
        return None


def run_worker_daemon(
    *,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    max_rounds: int = _DEFAULT_MAX_ROUNDS,
    vault_dir: Path = Path("vault/research"),
    store: object | None = None,
) -> dict:
    """aigs:intake:queue 를 바운드 폴 루프로 소비하는 데몬(§15 인입 워커).

    종료성 P0:
      - max_rounds==0: SIGTERM(또는 KeyboardInterrupt) 수신까지 라운드 반복(systemd 관리).
      - max_rounds>0: 정확히 그 라운드 수만큼만 폴(테스트/일회성·무한루프 0).
      - 각 라운드는 poll_intake_queue(max_poll 바운드) → poll_interval_s 대기.
    Redis/Store 미설정 시 graceful 정상 종료(rc=0).

    Args:
        poll_interval_s: 라운드 간 대기(초).
        max_rounds: 라운드 상한(0=무기한·systemd SIGTERM 까지).
        vault_dir: vault 저장 경로.
        store: 주입 store(테스트). None이면 KnowledgeStore 를 연다.

    Returns:
        {"rounds": 수행 라운드, "polled": 총폴, "processed": 총처리, "indexed": 총색인,
         "stopped": "signal"|"max_rounds"|"no_store"}.
    """
    import signal  # noqa: PLC0415
    import time  # noqa: PLC0415

    if store is None:
        store = _build_knowledge_store()
    if store is None:
        return {
            "rounds": 0,
            "polled": 0,
            "processed": 0,
            "indexed": 0,
            "stopped": "no_store",
        }

    # SIGTERM(systemd stop) → 플래그 셋 → 루프 정상 탈출(라운드 경계서 깔끔히 종료).
    stop = {"flag": False}

    def _on_term(signum, _frame):  # noqa: ANN001
        stop["flag"] = True

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGINT, _on_term)

    rounds = polled = processed = indexed = 0
    reason = "max_rounds"
    # ★종료성: max_rounds==0 이면 SIGTERM 까지(stop 플래그)·>0 이면 그 횟수까지. 무한 0.
    while not stop["flag"]:
        if max_rounds and rounds >= max_rounds:
            break
        try:
            res = poll_intake_queue(store, vault_dir=vault_dir)
            polled += res.get("polled", 0)
            processed += res.get("processed", 0)
            indexed += res.get("indexed", 0)
        except Exception:
            logger.warning("intake 라운드 처리 실패(계속)", exc_info=True)
        rounds += 1
        if max_rounds and rounds >= max_rounds:
            break
        # 라운드 간 대기 — SIGTERM 즉시 반응 위해 짧은 슬라이스로 분할 sleep.
        slept = 0.0
        while slept < poll_interval_s and not stop["flag"]:
            time.sleep(min(0.5, poll_interval_s - slept))
            slept += 0.5
    if stop["flag"]:
        reason = "signal"

    return {
        "rounds": rounds,
        "polled": polled,
        "processed": processed,
        "indexed": indexed,
        "stopped": reason,
    }


def main() -> int:
    """intake 워커 데몬 CLI entrypoint(systemd aigs-intake.service ExecStart).

    환경변수:
      INTAKE_POLL_INTERVAL_S — 라운드 간 대기(기본 5).
      INTAKE_MAX_ROUNDS      — 라운드 상한(기본 0=SIGTERM 까지).
      INTAKE_VAULT_DIR       — vault 저장 경로(기본 vault/research).
    """
    import os  # noqa: PLC0415

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    interval = float(os.environ.get("INTAKE_POLL_INTERVAL_S", _DEFAULT_POLL_INTERVAL_S))
    rounds_cap = int(os.environ.get("INTAKE_MAX_ROUNDS", _DEFAULT_MAX_ROUNDS))
    vdir = Path(os.environ.get("INTAKE_VAULT_DIR", "vault/research"))
    result = run_worker_daemon(
        poll_interval_s=interval, max_rounds=rounds_cap, vault_dir=vdir
    )
    logger.info(
        "intake 데몬 종료: rounds=%d polled=%d processed=%d indexed=%d (%s)",
        result["rounds"],
        result["polled"],
        result["processed"],
        result["indexed"],
        result["stopped"],
    )
    return 0  # graceful 종료는 항상 success(systemd Restart=on-failure 와 정합)


if __name__ == "__main__":
    import sys  # noqa: PLC0415

    sys.exit(main())
