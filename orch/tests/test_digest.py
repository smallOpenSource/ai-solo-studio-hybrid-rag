"""P3 §20.3/§20.4b digest — 누적(REDIS+vault 폴백) + 렌더 + graceful. 결정론·LLM 0."""

from __future__ import annotations

import asyncio

from orch.authority import Tier
from orch.digest import append_decision, read_digest_entries, render_digest


def test_append_decision_redis_and_file(tmp_path):
    pushed = []

    class StubStore:
        async def push_task(self, queue, item):
            pushed.append((queue, item))

    path = asyncio.run(
        append_decision(
            Tier.NOTIFY,
            "클리커 밸런스 조정",
            confidence=0.7,
            ref="ADR-12",
            role="COO",
            store=StubStore(),
            vault_dir=str(tmp_path),
            today="2026-06-22",
        )
    )
    # REDIS: digest:<date> 큐에 1건.
    assert len(pushed) == 1 and pushed[0][0] == "digest:2026-06-22"
    assert pushed[0][1]["tier"] == "notify"
    # vault 파일.
    assert path.is_file() and path.name == "2026-06-22.md"
    txt = path.read_text(encoding="utf-8")
    assert "클리커 밸런스 조정" in txt and "conf0.70" in txt and "ADR-12" in txt


def test_append_decision_graceful_no_store(tmp_path):
    # store=None(REDIS 미설정) → 파일만·예외 0.
    path = asyncio.run(
        append_decision(
            Tier.AUTONOMOUS,
            "에셋 팔레트 확정",
            role="ART",
            store=None,
            vault_dir=str(tmp_path),
            today="2026-06-22",
        )
    )
    assert path.is_file()
    assert "에셋 팔레트 확정" in path.read_text(encoding="utf-8")


def test_append_accumulates_no_per_decision_ping(tmp_path):
    # 여러 건 누적 → 같은 파일에 묶임(per-decision 핑 아님·§20.3).
    for i in range(3):
        asyncio.run(
            append_decision(
                Tier.NOTIFY,
                f"결정{i}",
                role="COO",
                store=None,
                vault_dir=str(tmp_path),
                today="2026-06-22",
            )
        )
    txt = (tmp_path / "digest" / "2026-06-22.md").read_text(encoding="utf-8")
    assert txt.count("# 일일 다이제스트") == 1  # 헤더 1개(묶음)
    assert txt.count("결정") == 3


def test_render_digest_grouped_counts():
    entries = [
        {
            "tier": "autonomous",
            "summary": "a",
            "role": "COO",
            "confidence": 0.8,
            "ref": "ADR-1",
        },
        {"tier": "autonomous", "summary": "b", "role": "RESEARCH"},
        {"tier": "notify", "summary": "c", "role": "DEV"},
        {"tier": "escalate", "summary": "d", "role": "CEO"},
    ]
    out = render_digest(entries, "2026-06-22")
    assert "🟢 자율 결정 2건" in out
    assert "🟡 통지 1건 / 🔴 대기 중 승인 1건" in out
    assert "ADR-1" in out


def test_read_digest_entries_graceful():
    assert asyncio.run(read_digest_entries("2026-06-22", store=None)) == []
