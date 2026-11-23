#!/usr/bin/env python3
"""S4 — 구 임베디드 백업의 벡터를 신규 Qdrant 서버 knowledge 컬렉션으로 직접 복사.

재임베딩 0(벡터 직접 복사·무손실) + 점수 동등성 검증(동일 벡터로 백업 vs 서버 top-k ID 일치).
구 임베디드는 비교검증 백업으로만 사용(SR/renewal pre-mortem #1). 종료코드 0=동등·1=불일치·2=blocked."""

import sys

from qdrant_client import QdrantClient, models

BACKUP = "/opt/studio/.omc/backups/qdrant-embedded-preP1"
SRC_COLL = "one_man_corp_wiki"
SERVER = "http://127.0.0.1:2811"
DST_COLL = "knowledge"
DIM = 1024


def main():
    src = QdrantClient(path=BACKUP)
    dst = QdrantClient(url=SERVER, timeout=30)
    if not src.collection_exists(SRC_COLL):
        print(f"BLOCKED: backup에 컬렉션 {SRC_COLL} 없음")
        sys.exit(2)
    n_src = src.count(SRC_COLL).count
    print(f"source {SRC_COLL}: {n_src} points")
    if not dst.collection_exists(DST_COLL):
        dst.create_collection(
            DST_COLL,
            vectors_config=models.VectorParams(
                size=DIM, distance=models.Distance.COSINE
            ),
        )
    migrated, offset, sample_vec = 0, None, None
    while True:
        pts, offset = src.scroll(
            SRC_COLL, limit=128, offset=offset, with_vectors=True, with_payload=True
        )
        if not pts:
            break
        dst.upsert(
            DST_COLL,
            points=[
                models.PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                for p in pts
            ],
        )
        migrated += len(pts)
        if sample_vec is None:
            sample_vec = pts[0].vector
        if offset is None:
            break
    n_dst = dst.count(DST_COLL).count
    print(f"migrated {migrated} → server {DST_COLL}: {n_dst} points")
    rc = 1
    if sample_vec is not None:
        ib = [
            p.id for p in src.query_points(SRC_COLL, query=sample_vec, limit=5).points
        ]
        iv = [
            p.id for p in dst.query_points(DST_COLL, query=sample_vec, limit=5).points
        ]
        match = ib == iv
        print(
            f"equivalence top-5 IDs: backup={ib} server={iv} → {'MATCH ✔' if match else 'MISMATCH ✗'}"
        )
        rc = 0 if (match and n_dst >= n_src) else 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
