"""P2-rest-B §27.5 Blackboard — dept-keyed shared_facts(fan-out 손실 0) + blackboard_sink.

결정론·LLM 0·orch env. 라이브 미접촉(stub caller·MemorySaver·stub store·tmp vault).
"""

from __future__ import annotations

import asyncio

from orch.org_graph import blackboard_sink
from orch.tests.test_org_graph import _run, make_stub


# ── dept-keyed shared_facts (병렬 Send fan-out 동시쓰기 손실 0) ──────────────────
def test_shared_facts_dept_keyed_fanout_no_loss():
    out = _run(
        make_stub(ceo="coo", coo_pick="research, art"), "조사+에셋 동시", "t-bb-fan"
    )
    sf = out["shared_facts"]
    # 두 부서가 자기 dept 키만 채움 → operator.or_ 머지로 둘 다 보존(키 충돌 0).
    assert "research" in sf and "art" in sf
    assert sf["research"]["by"] == "research" and sf["art"]["by"] == "art"
    # finalize 도 예약키로 기여(부서 키와 충돌 0).
    assert "_finalize" in sf and "bluf" in sf["_finalize"]
    # 구조화 dict(자유형식 prose 아님·telephone game 차단).
    assert isinstance(sf["research"], dict) and "finding" in sf["research"]


def test_shared_facts_single_dept():
    out = _run(make_stub(ceo="dev_lead", dev_pick="dev"), "fix build", "t-bb-one")
    sf = out["shared_facts"]
    assert set(sf) == {"dev", "_finalize"}


# ── blackboard_sink: Redis(stub) + vault 스냅샷 + graceful ──────────────────────
def test_blackboard_sink_redis_and_snapshot(tmp_path):
    calls = []

    class StubStore:
        async def bb_set(self, proj, field, value):
            calls.append((proj, field, value))

    values = {
        "shared_facts": {
            "research": {"by": "research", "finding": "x"},
            "art": {"by": "art", "finding": "y"},
        }
    }
    path = asyncio.run(
        blackboard_sink(
            values,
            StubStore(),
            proj="org:p1",
            vault_dir=str(tmp_path),
            today="2026-06-22",
        )
    )
    # Redis: dept 별 bb_set 정확히 1회씩(aigs:blackboard:<proj>).
    assert {c[1] for c in calls} == {"research", "art"}
    assert all(c[0] == "org:p1" for c in calls)
    # vault 스냅샷 파일.
    assert path.is_file() and path.name == "blackboard-2026-06-22.md"
    txt = path.read_text(encoding="utf-8")
    assert "proj=org:p1" in txt and "research" in txt and "art" in txt


def test_blackboard_sink_graceful_no_store(tmp_path):
    # store=None(REDIS 미설정) → bb_set 0·스냅샷만·예외 0.
    values = {"shared_facts": {"dev": {"by": "dev", "finding": "z"}}}
    path = asyncio.run(
        blackboard_sink(
            values, None, proj="org:p2", vault_dir=str(tmp_path), today="2026-06-22"
        )
    )
    assert path.is_file()
    assert "dev" in path.read_text(encoding="utf-8")


def test_blackboard_sink_empty_facts_no_crash(tmp_path):
    # shared_facts 없음 → 헤더만·예외 0.
    path = asyncio.run(
        blackboard_sink(
            {}, None, proj="org:p3", vault_dir=str(tmp_path), today="2026-06-22"
        )
    )
    assert path.is_file()


def test_blackboard_sink_appends_across_runs(tmp_path):
    # 같은 날 2회 sink → 같은 파일에 2 섹션 append(이력 보존·단일 헤더).
    v1 = {"shared_facts": {"research": {"by": "research", "finding": "a"}}}
    v2 = {"shared_facts": {"dev": {"by": "dev", "finding": "b"}}}
    asyncio.run(
        blackboard_sink(
            v1, None, proj="org:r1", vault_dir=str(tmp_path), today="2026-06-22"
        )
    )
    path = asyncio.run(
        blackboard_sink(
            v2, None, proj="org:r2", vault_dir=str(tmp_path), today="2026-06-22"
        )
    )
    txt = path.read_text(encoding="utf-8")
    assert txt.count("# Blackboard 회의록") == 1  # 헤더 1개
    assert "proj=org:r1" in txt and "proj=org:r2" in txt  # 2 섹션
