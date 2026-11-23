"""P8 OD-P8-B — legacy 코퍼스 provenance 백필 (재임베드 0·set_payload·§11.1·NET-NEW).

legacy ~178pt 는 ``meta.provenance`` 가 없어 회수 시 [[22_renewal_p8_security]] ② fail-safe
로 전부 untrusted 간주되어 우회 중이었다. 이 도구가 기존 포인트에 provenance 페이로드를
부여해 *정확한* trusted/untrusted 신호를 실재화한다. provenance 는 벡터와 무관한
payload(meta) 필드이므로 **재임베드(bge) 0** — set_payload 만으로 충분(저렴).

판정 신호 = payload.source (컬렉션엔 trusted 플래그 없음):
    · ``http(s)://...`` (외부 크롤·knowledge/crawl.py source==url) → untrusted, source_url=<URL>
    · 그 외(상대경로·내부 산출·knowledge/ingest.py _relative_source) → trusted, source_url=None
형식 = capture_and_index 동형: ``meta["provenance"] = {"provenance":..., "source_url":...}``
    (knowledge/search.py ``_chunk_provenance`` 가 dict 안쪽 provenance 값을 읽는다).

모드:
    (기본)      백필 실행 — scroll → 판정 → set_payload.
    --dry-run   판정만 집계·쓰기 0.
    --verify    읽기전용 — 전 포인트가 meta.provenance 보유하는지 단언(exit 1=미보유 존재).

종료성 P0: scroll 페이지 유한 루프(offset None 까지)·포인트 단위 set_payload·무한 while 0.
가역성: 실행 전 전 payload 를 state/p8_provenance_backfill_backup.json 백업.
멱등: 동일 provenance 이미 존재 시 skip(재실행 안전).
환경: knowledge env (qdrant_client). 컬렉션·포트는 config(ports.json) 경유(리터럴 금지).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_COLLECTION = "knowledge"


def _qdrant_url() -> str:
    ports = json.loads((_ROOT / "config" / "ports.json").read_text(encoding="utf-8"))
    return f"http://127.0.0.1:{ports['ports']['PORT_QDRANT_REST']}"


def _classify(source: str) -> dict:
    s = (source or "").strip()
    if s.startswith("http://") or s.startswith("https://"):
        return {"provenance": "untrusted", "source_url": s}
    return {"provenance": "trusted", "source_url": None}


def _scroll_all(client, coll: str) -> list:
    points: list = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=coll,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def main() -> int:
    dry = "--dry-run" in sys.argv
    verify = "--verify" in sys.argv

    from qdrant_client import QdrantClient

    client = QdrantClient(url=_qdrant_url())
    if not client.collection_exists(_COLLECTION):
        print(f"BACKFILL_FAIL 컬렉션 '{_COLLECTION}' 없음")
        return 1
    points = _scroll_all(client, _COLLECTION)

    if verify:
        missing = unt = tr = 0
        for p in points:
            prov = ((p.payload or {}).get("meta") or {}).get("provenance")
            if not isinstance(prov, dict) or "provenance" not in prov:
                missing += 1
            elif prov.get("provenance") == "untrusted":
                unt += 1
            else:
                tr += 1
        print(
            f"BACKFILL_VERIFY total={len(points)} with_provenance={len(points) - missing} "
            f"missing={missing} untrusted={unt} trusted={tr}"
        )
        return 1 if missing else 0

    if not dry:
        backup = _ROOT / "state" / "p8_provenance_backfill_backup.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(
            json.dumps(
                [{"id": p.id, "payload": p.payload} for p in points],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    counts = {"untrusted": 0, "trusted": 0, "updated": 0, "skip_existing": 0}
    for p in points:
        payload = p.payload or {}
        meta = dict(
            payload.get("meta") or {}
        )  # 기존 키(chunker/heading/crawled_at) 보존
        prov = _classify(payload.get("source", ""))
        counts[prov["provenance"]] += 1
        existing = meta.get("provenance")
        if (
            isinstance(existing, dict)
            and existing.get("provenance") == prov["provenance"]
            and existing.get("source_url") == prov["source_url"]
        ):
            counts["skip_existing"] += 1
            continue
        meta["provenance"] = prov
        if not dry:
            client.set_payload(
                collection_name=_COLLECTION, payload={"meta": meta}, points=[p.id]
            )
        counts["updated"] += 1

    tag = "DRYRUN" if dry else "OK"
    print(
        f"BACKFILL_{tag} total={len(points)} untrusted={counts['untrusted']} "
        f"trusted={counts['trusted']} updated={counts['updated']} "
        f"skip_existing={counts['skip_existing']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
