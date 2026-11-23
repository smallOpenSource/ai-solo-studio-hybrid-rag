"""직접 위키 수기 인덱싱 — 마크다운 디렉토리→청킹→임베딩→업서트 (Phase 2 K5).

직접 작성한 위키 문서(마크다운)를 읽어 검색 창고(Qdrant)에 넣는다. 절차:
  1) 디렉토리의 *.md 파일을 정렬된 순서로 읽는다(결정론).
  2) chunk_markdown으로 각 파일을 청크로 쪼갠다.
  3) 출처 메타(파일 경로 + 버전 표식)를 청크 meta에 더한다.
  4) store.upsert로 임베딩(순차 배치)→업서트한다.

**멱등**(계획 수용5): 같은 디렉토리를 2회 인덱싱해도 동일 상태가 된다 —
청크 id가 source+chunk_id로 결정론이라(store._point_id) 재업서트가 덮어쓰기다.

**버전 메타**(rev2 M6/F5): git 저장소면 커밋 해시·파일 mtime을, *아니면 파일
mtime만* payload에 넣는다. 현재 이 저장소는 비-git이라 mtime 경로가 실제로
탄다(git rev-parse 실패 시 자동 폴백). 코드/설정 키 하드코딩 0(임베딩은
core.bge_client가 .env에서 키를 읽는다). 호출은 동시성 1 순차다.

CLI:
    python -m knowledge.ingest <dir>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from knowledge.chunk import chunk_markdown

if TYPE_CHECKING:
    from knowledge.chunk import Chunk
    from knowledge.store import KnowledgeStore

_PROJECT_ROOT = Path(__file__).parent.parent


def ingest(wiki_dir: str | Path, store: KnowledgeStore) -> int:
    """위키 마크다운 디렉토리를 청킹→임베딩→업서트하고 업서트한 청크 수를 돌려준다.

    디렉토리의 모든 *.md 파일을(이름 정렬, 결정론) 읽어 chunk_markdown으로
    쪼갠 뒤, 버전 메타(git 커밋 또는 파일 mtime)를 각 청크 meta에 더해
    store.upsert로 넣는다.

    멱등: 같은 입력을 2회 호출해도 동일 상태가 된다(청크 id 결정론 — store가
    source+chunk_id로 안정 id를 만든다). 따라서 재인덱싱이 안전하다.

    Args:
        wiki_dir: 위키 마크다운이 든 디렉토리(상대경로면 프로젝트 루트 기준).
        store: 업서트 대상 KnowledgeStore(임베더는 store가 보유·주입).

    Returns:
        업서트한 청크(포인트) 수. 디렉토리가 없거나 *.md가 0개면 0.
    """
    root = Path(wiki_dir)
    if not root.is_absolute():
        root = _PROJECT_ROOT / root
    if not root.is_dir():
        return 0

    git_commit = _git_commit(root)

    all_chunks: list[Chunk] = []
    for md_path in sorted(root.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        source = _relative_source(md_path)
        version = _version_meta(md_path, git_commit)
        chunks = chunk_markdown(text, source)
        for chunk in chunks:
            # 버전 표식을 meta에 더한다(청킹 meta는 보존). git이면 커밋+mtime,
            # 아니면 mtime만 — payload로 들어가 재채점·증분 판단에 쓸 수 있다.
            chunk.meta.update(version)
        all_chunks.extend(chunks)

    if not all_chunks:
        return 0
    return store.upsert(all_chunks)


def _relative_source(md_path: Path) -> str:
    """파일 경로를 프로젝트 루트 기준 상대 문자열로(검색 결과 source 보존용)."""
    try:
        return str(md_path.relative_to(_PROJECT_ROOT))
    except ValueError:
        return str(md_path)


def _version_meta(md_path: Path, git_commit: str | None) -> dict[str, object]:
    """청크에 더할 버전 메타를 만든다 — git이면 커밋+mtime, 아니면 mtime만.

    파일 mtime은 항상 넣는다(증분 판단·재인덱싱 추적용). git 저장소이고
    커밋 해시를 읽었으면 git_commit도 더한다(rev2 M6/F5 — 현재 비-git이라
    git_commit은 None → mtime 경로).
    """
    meta: dict[str, object] = {"mtime": md_path.stat().st_mtime}
    if git_commit is not None:
        meta["git_commit"] = git_commit
    return meta


def _git_commit(root: Path) -> str | None:
    """root가 git 저장소면 HEAD 커밋 해시를, 아니면 None을 돌려준다.

    `git rev-parse HEAD`가 실패하면(비-git·git 미설치) None으로 폴백한다 →
    호출부가 mtime만 메타에 넣는다(현재 이 저장소가 그 경로다).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점 — `python -m knowledge.ingest <dir>`.

    위키 디렉토리를 기본 store(endpoint.yaml knowledge — Qdrant 실 path)에
    인덱싱하고 업서트한 청크 수를 출력한다. 실서버(bge) 임베딩을 부르므로
    오프라인 테스트는 ingest(dir, store)에 mock store를 주입해 검증한다.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("사용법: python -m knowledge.ingest <위키_디렉토리>", file=sys.stderr)
        return 2

    wiki_dir = args[0]
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore()
    count = ingest(wiki_dir, store)
    print(f"인덱싱 완료: {count}개 청크 업서트 (컬렉션 '{store.collection}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
