"""orch/librarian.py — §18.2 Curate(LIBRARIAN): dedup-merge · MOC. LLM 0(gemma-affinity·결정론).

적재물을 큐레이션한다 — 중복 병합 + MOC(Map of Content) 링크 생성:
- exact: 공백 정규화 content-hash 동일 → 병합.
- near-dup(옵션): 주입 ``similarity(a_text, b_text)->float`` ≥ threshold → 먼저 본 것에 병합.
  similarity 미주입이면 exact만(보수적·false-merge 방지).
첫 등장이 대표(kept)·나머지는 MOC에 흡수 링크(원본 보존=이력). 키가 해시/코사인이라 **LLM 없이
결정론**(§18.5는 dedup 알고리즘 미명시 → 본 모듈이 구체화·gemma-affinity 핫패스 LLM 0).
near-dup 유사도는 임베딩 코사인 등을 주입(duck-typed·실서버 0 테스트).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Artifact:
    """큐레이션 대상 적재물 — id(vault 경로/식별자) + 본문 + 태그."""

    id: str
    text: str
    tags: list[str] = field(default_factory=list)


@dataclass
class DedupResult:
    """dedup 결과 — 대표 목록(kept) + MOC(kept_id → 흡수된 중복 id 목록)."""

    kept: list[Artifact]
    moc: dict[str, list[str]]


def _content_key(text: str) -> str:
    """공백 정규화 후 sha256 — exact 중복 판정 키(결정론)."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def dedup_merge(
    artifacts: list[Artifact],
    similarity: Callable[[str, str], float] | None = None,
    threshold: float = 0.95,
) -> DedupResult:
    """중복 아티팩트를 병합한다(§18.2 Curate).

    (1) exact: 정규화 content-hash 동일 → 먼저 본 대표에 병합.
    (2) near-dup(옵션): ``similarity`` 주입 시 대표들과 비교해 ≥ ``threshold`` 면 병합.
    첫 등장이 대표(kept)이고 나머지는 ``moc[대표id]`` 에 흡수 기록(원본은 링크로 보존).

    Returns:
        DedupResult(kept, moc) — kept는 입력 순서 보존, moc는 대표→흡수목록.
    """
    kept: list[Artifact] = []
    moc: dict[str, list[str]] = {}
    seen_keys: dict[str, str] = {}  # content_key -> kept_id

    for art in artifacts:
        key = _content_key(art.text)
        if key in seen_keys:  # exact 중복
            moc[seen_keys[key]].append(art.id)
            continue
        merged = False
        if similarity is not None:  # near-dup
            for rep in kept:
                if similarity(art.text, rep.text) >= threshold:
                    moc[rep.id].append(art.id)
                    merged = True
                    break
        if merged:
            continue
        kept.append(art)
        moc[art.id] = []
        seen_keys[key] = art.id

    return DedupResult(kept=kept, moc=moc)


def render_moc(result: DedupResult, title: str = "MOC") -> str:
    """MOC(Map of Content) 마크다운 — 대표 아티팩트 + 흡수된 중복 위키링크(§18.2)."""
    lines = [f"# {title}", ""]
    for art in result.kept:
        absorbed = result.moc.get(art.id, [])
        line = f"- [[{art.id}]]"
        if absorbed:
            line += " (중복 병합: " + ", ".join(f"[[{a}]]" for a in absorbed) + ")"
        lines.append(line)
    return "\n".join(lines) + "\n"
