"""P2-rest-A US-A3 — R-INDEX: 라이브 knowledge 컬렉션을 named dense+sparse 스키마로 재생성.

라이브 178pt 코퍼스 = 크롤 Wikipedia + wiki/*.md (payload에 text 보존). 재크롤 없이
**기존 payload를 읽어 실 bge로 재임베드 + sparse 재업서트**(멱등 _point_id). bge 결정론
→ 재임베드 dense 벡터 동일 → 이웃 동등성(Architect#3) 보존.

안전: ① 삭제 전 전 payload를 백업 파일로 저장(크롤 텍스트의 유일 소스가 됨·재실행 복구).
② 앵커 dense top-k pre/post 비교로 이웃 동등성 단언. downtime 허용(v5)·실패 시 백업으로 재실행.

사용: PYTHONPATH=/opt/studio \
      /opt/conda/envs/one_man_corp-knowledge/bin/python tools/p2rest_reindex.py [--apply]
  --apply 없으면 드라이런(백업+pre 스냅샷만·삭제/재생성 안 함).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from knowledge.chunk import Chunk  # noqa: E402
from knowledge.store import KnowledgeStore  # noqa: E402

BACKUP = _REPO / "state" / "p2rest_reindex_payloads.json"
ANCHORS = _REPO / "cases" / "knowledge_eval.jsonl"
TOPK = 10


def _load_anchor_queries(limit: int = 24) -> list[str]:
    out: list[str] = []
    if not ANCHORS.exists():
        return out
    for line in ANCHORS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        q = json.loads(line).get("query", "")
        if q:
            out.append(q)
        if len(out) >= limit:
            break
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    store = KnowledgeStore()  # 서버(endpoint.yaml qdrant_url=2811)·실 bge embedder
    client = store._client
    coll = store.collection

    # ── 1. 전 payload 백업(삭제 전·크롤 텍스트의 유일 소스가 됨) ──────────────
    print(f"[1] scroll '{coll}' payload 백업 …")
    payloads: list[dict] = []
    offset = None
    while True:
        pts, offset = client.scroll(
            collection_name=coll,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        payloads.extend(p.payload for p in pts)
        if offset is None:
            break
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    BACKUP.write_text(json.dumps(payloads, ensure_ascii=False), encoding="utf-8")
    print(f"    백업 {len(payloads)}pt → {BACKUP.relative_to(_REPO)}")

    # ── 2. 앵커 dense pre-스냅샷(이웃 동등성 기준·OLD unnamed 컬렉션 raw 쿼리) ──
    anchors = _load_anchor_queries()
    emb = store._get_embedder()
    pre: dict[str, list] = {}
    for q in anchors:
        qv = emb.embed([q])[0]
        resp = client.query_points(  # OLD unnamed → using 미지정
            collection_name=coll,
            query=list(qv),
            limit=TOPK,
            with_payload=["source", "chunk_id"],
        )
        pre[q] = [
            (p.payload.get("source"), p.payload.get("chunk_id")) for p in resp.points
        ]
    print(f"[2] 앵커 {len(anchors)}개 dense pre-스냅샷 캡처")

    if not apply:
        print("[DRY-RUN] --apply 없음 → 삭제/재생성 생략. 백업+pre 스냅샷만 저장.")
        return 0

    # ── 3. 삭제 + named 스키마 재생성 ────────────────────────────────────────
    print(f"[3] '{coll}' 삭제 + named dense+sparse 재생성 …")
    client.delete_collection(coll)
    store.ensure_collection()

    # ── 4. payload → Chunk 재구성 → 재업서트(실 bge dense + sparse) ──────────
    chunks = [
        Chunk(
            text=p.get("text", ""),
            source=p.get("source", ""),
            chunk_id=int(p.get("chunk_id", 0)),
            meta=p.get("meta", {}) or {},
        )
        for p in payloads
    ]
    # ★배치 임베드: 178 단일콜은 bge ReadTimeout(30s↑) → 배치(24)로 나눠 임베드.
    n = 0
    batch = 24
    for i in range(0, len(chunks), batch):
        n += store.upsert(chunks[i : i + batch])
        print(f"    재업서트 {n}/{len(chunks)} …", flush=True)
    cnt = store.count()
    print(f"[4] 재업서트 {n}pt · count={cnt}")
    assert cnt == len(payloads), f"count {cnt} != 백업 {len(payloads)}"

    # ── 5. dense 이웃 동등성(Architect#3·pre/post top-k 겹침) ────────────────
    print("[5] dense 이웃 동등성(pre/post top-k) …")
    drift = 0
    for q in anchors:
        post = [(c.source, c.chunk_id) for c, _ in store.search_dense(q, top_n=TOPK)]
        overlap = len(set(pre[q]) & set(post))
        if overlap < TOPK:
            drift += 1
            if drift <= 3:
                print(f"    drift '{q[:30]}…' overlap={overlap}/{TOPK}")
    if drift == 0:
        print(f"    ✔ 전 앵커 top-{TOPK} 이웃 동등(drift 0)")
    else:
        print(f"    ⚠ {drift}/{len(anchors)} 앵커 이웃 변동(재임베드 결정론 확인 필요)")

    print(
        f"R-INDEX 완료: {cnt}pt named dense+sparse · 동등성 drift={drift}/{len(anchors)}"
    )
    return 0 if drift == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
