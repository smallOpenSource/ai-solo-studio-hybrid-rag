"""마크다운 청킹 — Docling Hybrid 우선, 단순분할 폴백 (Phase 2 K2).

긴 위키 문서를 검색 단위(청크)로 잘게 쪼갠다.

청킹 방식이 검색 품질의 *측정 대상*이다(계획 §6.0, rev2 M6/F6):
- **단순분할 baseline**: 헤딩·빈 줄(문단) 경계로 나누는 가벼운 기본 방식.
  외부 의존이 없어 게이트 A를 비차단으로 보장한다(폴백 보장).
- **Docling Hybrid**: import 가능하면 사용하는 구조 인식 청킹(측정 비교용).
  무겁다(transformers·torch 계열) — 미설치/실패면 단순분할로 폴백한다.

즉 첫 게이트는 단순분할만으로 닫히고, Docling은 후속 품질개선용 A/B 측정이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 단순분할 baseline 파라미터 — 한 청크의 대략 글자 수 상·하한.
# 너무 잘게(검색 단위 파편화)도, 너무 크게(Context Rot)도 안 되게 잡는다.
_TARGET_CHARS = 500
_MIN_CHARS = 80


@dataclass
class Chunk:
    """검색 단위 청크 한 조각.

    Attributes:
        text: 청크 본문(검색·임베딩 대상).
        source: 출처 식별자(보통 파일 경로). 검색 결과의 source로 보존된다.
        chunk_id: 출처 안에서의 청크 순번(0부터).
        meta: 부가 정보(헤딩·청킹 방식 등). 검색 필터·표시에 쓸 수 있다.
    """

    text: str
    source: str
    chunk_id: int
    meta: dict[str, Any] = field(default_factory=dict)


def chunk_markdown(text: str, source: str) -> list[Chunk]:
    """마크다운 텍스트를 청크 목록으로 분해한다.

    Docling Hybrid 청킹이 import 가능하면 그것을 쓰고, ImportError나 실패면
    단순분할(헤딩·문단 경계 기준)로 폴백한다 — 게이트 비차단 보장.

    Args:
        text: 마크다운 원문.
        source: 출처 식별자(파일 경로 등). 모든 청크에 그대로 보존된다.

    Returns:
        Chunk 목록(빈 입력이면 빈 목록). chunk_id는 0부터 순번.
    """
    chunks = _chunk_with_docling(text, source)
    if chunks is not None:
        return chunks
    # 폴백: 단순분할 baseline (게이트 A를 비차단으로 보장)
    return _chunk_simple(text, source)


def _chunk_with_docling(text: str, source: str) -> list[Chunk] | None:
    """Docling Hybrid 청킹 시도. 미설치/실패면 None을 돌려 폴백을 유도한다.

    Docling은 선택 의존(무거움)이라 import 가능 여부로 분기한다. 어떤 이유로든
    실패하면 조용히 None을 돌려 단순분할로 폴백한다(게이트 비차단).
    """
    try:
        from docling.chunking import HybridChunker
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None

    try:
        converter = DocumentConverter()
        # Docling은 마크다운 문자열을 직접 받지 않으므로 임시 파일 경로가 필요하다.
        # 측정용 경로라 실패 시 단순분할로 폴백한다(게이트 비차단).
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        try:
            doc = converter.convert(str(tmp_path)).document
            chunker = HybridChunker()
            chunks: list[Chunk] = []
            for idx, dc in enumerate(chunker.chunk(doc)):
                body = (dc.text or "").strip()
                if not body:
                    continue
                chunks.append(
                    Chunk(
                        text=body,
                        source=source,
                        chunk_id=len(chunks),
                        meta={"chunker": "docling_hybrid"},
                    )
                )
        finally:
            tmp_path.unlink(missing_ok=True)
        # Docling이 한 조각도 못 만들면 폴백
        return chunks or None
    except Exception:
        # 측정 경로라 어떤 실패도 폴백(단순분할)으로 흡수 — 게이트 비차단
        return None


def _chunk_simple(text: str, source: str) -> list[Chunk]:
    """단순분할 baseline — 헤딩·문단 경계 기준으로 나눈다.

    절차:
      1) 마크다운 헤딩(`#`로 시작하는 줄)을 섹션 경계로 잡아 헤딩별로 묶는다.
         본문이 비어 있는 헤딩(상위 제목 등)은 *다음* 섹션의 맥락 접두로 넘긴다
         — 제목만 든 파편 청크를 만들지 않는다.
      2) 각 섹션은 최소 한 청크가 된다(헤딩 경계 존중 — 서로 다른 섹션은 안 합침).
         섹션이 목표 글자수(_TARGET_CHARS)를 넘으면 문단(빈 줄) 경계로 더 나눈다.
      3) 짧은 꼬리 조각 합치기는 *같은 섹션 안에서만* 한다(파편화 완화). 헤딩
         경계를 넘는 병합은 하지 않는다.
    """
    sections = _split_by_heading(text)
    chunks: list[Chunk] = []
    carried_heading = ""  # 본문 없는 헤딩을 다음 섹션 앞에 붙이기 위한 캐리

    for heading, body in sections:
        if not body:
            # 본문 없는 헤딩(상위 제목 등) → 다음 섹션 맥락으로 이월
            carried_heading = (
                (carried_heading + "\n" + heading).strip()
                if carried_heading
                else heading
            )
            continue
        effective_heading = (
            (carried_heading + "\n" + heading).strip()
            if carried_heading and heading
            else (carried_heading or heading)
        )
        carried_heading = ""
        pieces = _merge_short_within_section(_split_section(effective_heading, body))
        for piece in pieces:
            meta: dict[str, Any] = {"chunker": "simple_split"}
            if heading:
                meta["heading"] = heading
            chunks.append(
                Chunk(text=piece, source=source, chunk_id=len(chunks), meta=meta)
            )

    # 본문이 끝까지 없고 헤딩만 이월됐으면(예: 제목뿐인 문서) 그 헤딩을 청크로
    if not chunks and carried_heading:
        chunks.append(
            Chunk(
                text=carried_heading,
                source=source,
                chunk_id=0,
                meta={"chunker": "simple_split"},
            )
        )
    return chunks


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """마크다운을 (헤딩, 본문) 섹션 목록으로 나눈다.

    헤딩(`#`~`######`로 시작) 줄을 만나면 새 섹션을 연다. 첫 헤딩 앞의
    서두 본문은 헤딩 없는 섹션("")으로 둔다. 본문에는 헤딩 줄을 포함하지 않는다.
    """
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    def _flush() -> None:
        body = "\n".join(current_lines).strip()
        if body or current_heading:
            sections.append((current_heading, body))

    for line in text.splitlines():
        if _is_heading(line):
            _flush()
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    _flush()
    return sections


def _is_heading(line: str) -> bool:
    """줄이 마크다운 ATX 헤딩(`#`~`######` + 공백)인지 판정."""
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return False
    hashes = len(stripped) - len(stripped.lstrip("#"))
    return 1 <= hashes <= 6 and stripped[hashes : hashes + 1] == " "


def _split_section(heading: str, body: str) -> list[str]:
    """한 섹션을 청크 후보 문자열 목록으로 나눈다.

    섹션이 목표 글자수 이하면 통째로 한 조각. 넘으면 문단(빈 줄) 경계로
    누적해 목표 글자수 근처에서 끊는다. 각 조각 앞에는 맥락용 헤딩을 붙인다.
    """
    prefix = f"{heading}\n" if heading else ""
    body = body.strip()
    if not body:
        # 헤딩만 있는 섹션은 헤딩 자체를 짧은 청크로(드묾)
        return [heading] if heading else []

    if len(prefix) + len(body) <= _TARGET_CHARS:
        return [f"{prefix}{body}".strip()]

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    pieces: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        para_len = len(para)
        if buf and buf_len + para_len > _TARGET_CHARS:
            pieces.append(f"{prefix}" + "\n\n".join(buf))
            buf = []
            buf_len = 0
        buf.append(para)
        buf_len += para_len + 2
    if buf:
        pieces.append(f"{prefix}" + "\n\n".join(buf))
    return [p.strip() for p in pieces if p.strip()]


def _merge_short_within_section(pieces: list[str]) -> list[str]:
    """한 섹션 안의 짧은 꼬리 조각을 직전 조각에 합친다(파편화 완화).

    같은 헤딩 섹션에서 나온 조각들끼리만 호출된다 — 헤딩 경계는 넘지 않는다.
    _MIN_CHARS 미만 조각을 직전 조각에 이어 붙인다(맨 앞 조각은 그대로 둔다).
    """
    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < _MIN_CHARS:
            merged[-1] = f"{merged[-1]}\n\n{piece}".strip()
        else:
            merged.append(piece)
    return merged
