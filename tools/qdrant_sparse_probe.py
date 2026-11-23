#!/usr/bin/env python3
"""S3 — Qdrant named-sparse-vector HARD precondition probe (P2 hybrid 전제·§5.2).

서버에 2-원소 named sparse vector upsert → sparse query 회수.
✔ SUPPORTED / BLOCKED-with-version 출력 + 종료코드(0 ok·1 empty·2 error).
임시 컬렉션은 종료 시 삭제(타 데이터 무침해)."""

import sys

import importlib.metadata as md
from qdrant_client import QdrantClient, models

URL = "http://127.0.0.1:2811"
COLL = "sparse_probe_tmp"


def main():
    try:
        qcv = md.version("qdrant-client")
    except Exception:
        qcv = "?"
    c = QdrantClient(url=URL, timeout=10)
    rc = 2
    try:
        try:
            c.delete_collection(COLL)
        except Exception:
            pass
        c.create_collection(
            collection_name=COLL,
            vectors_config={},
            sparse_vectors_config={"text": models.SparseVectorParams()},
        )
        c.upsert(
            COLL,
            points=[
                models.PointStruct(
                    id=1,
                    vector={
                        "text": models.SparseVector(indices=[0, 5], values=[0.9, 0.5])
                    },
                    payload={"t": "a"},
                ),
                models.PointStruct(
                    id=2,
                    vector={
                        "text": models.SparseVector(indices=[2, 5], values=[0.7, 0.3])
                    },
                    payload={"t": "b"},
                ),
            ],
        )
        res = c.query_points(
            COLL,
            query=models.SparseVector(indices=[5], values=[1.0]),
            using="text",
            limit=3,
        )
        hits = res.points
        ok = len(hits) >= 1
        print(f"qdrant-client={qcv}  server={URL}")
        print(f"sparse upsert+query: {len(hits)} hits (ids={[h.id for h in hits]})")
        print(
            "RESULT: ✔ named-sparse-vector SUPPORTED"
            if ok
            else "RESULT: BLOCKED (query returned 0)"
        )
        rc = 0 if ok else 1
    except Exception as e:
        print(f"qdrant-client={qcv}")
        print(f"RESULT: BLOCKED-with-version — {type(e).__name__}: {e}")
        rc = 2
    finally:
        try:
            c.delete_collection(COLL)
        except Exception:
            pass
    sys.exit(rc)


if __name__ == "__main__":
    main()
