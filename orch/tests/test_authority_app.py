"""P3 §20 — app.py _run 권한 emit 경로(프레임 레벨·Critic MAJOR#2 green-theater 차단).

★Autonomous cycle 이 interrupt 없이 완료될 때 BLUF 가 실제 outbound 프레임으로 방출되는지를
state 가 아니라 **프레임 레벨**로 단언(state["bluf"]만 보면 라이브 무음 갭을 못 잡음). model STUBBED.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_AUTONOMOUS = "make a clicker game"  # 가역·인용 → AUTONOMOUS(무차단)
_ESCALATE = "스팀 출시용 클리커 게임 — 광고비 실금전 지출 승인 필요"  # owner-reserved → ESCALATE


def _client(tmp_db: str) -> TestClient:
    app = create_app(
        ckpt_db=tmp_db,
        caller=stub_caller,
        searcher=stub_searcher,
        tracing=False,
        auth=False,
    )
    return TestClient(app)


@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_auth.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


def _poll_frames(client: TestClient, pred, tries: int = 80):
    """bounded-poll /outbound/frames (fire-and-forget _run 레이스 회피)."""
    for _ in range(tries):
        frames = client.get("/outbound/frames").json()["frames"]
        if pred(frames):
            return frames
        time.sleep(0.05)
    return None


def test_autonomous_cycle_emits_bluf_frame(tmp_db):
    # ★Critic MAJOR#2: Autonomous cycle → 실제 TEXT_MESSAGE 프레임(run_id 매칭+non-empty)+RUN_FINISHED.
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _AUTONOMOUS})

        def has_text(frames):
            return any(f["payload"].get("type") == "TEXT_MESSAGE" for f in frames)

        frames = _poll_frames(client, has_text)
        assert frames is not None, (
            "Autonomous cycle 가 TEXT_MESSAGE 프레임을 방출 안 함(무음 갭)"
        )

        texts = [f for f in frames if f["payload"].get("type") == "TEXT_MESSAGE"]
        finished = [f for f in frames if f["payload"].get("type") == "RUN_FINISHED"]
        interrupts = [
            f for f in frames if f["payload"].get("type") == "INTERRUPT_REQUESTED"
        ]

        # attribution: TEXT_MESSAGE content 비어있지 않음(BLUF) + 같은 run_id 의 RUN_FINISHED 후행.
        tm = texts[0]["payload"]
        assert tm.get("content"), "BLUF content 비어있음(RUN_FINISHED-only 위장 차단)"
        rids = {f["payload"].get("run_id") for f in finished}
        assert tm.get("run_id") in rids, "TEXT_MESSAGE 와 RUN_FINISHED 의 run_id 불일치"
        # Autonomous = interrupt 0(escalate 와 구별).
        assert not interrupts, "Autonomous 인데 INTERRUPT_REQUESTED 발생"


def test_escalate_cycle_emits_interrupt_frame(tmp_db):
    # 비가역(owner-reserved) → ESCALATE → INTERRUPT_REQUESTED 프레임(승인 대기·무차단 아님).
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _ESCALATE})

        def has_interrupt(frames):
            return any(
                f["payload"].get("type") == "INTERRUPT_REQUESTED" for f in frames
            )

        frames = _poll_frames(client, has_interrupt)
        assert frames is not None, "Escalate cycle 가 INTERRUPT_REQUESTED 를 방출 안 함"
        # 인터럽트 전엔 BLUF/RUN_FINISHED 없음(아직 승인 대기).
        assert not any(f["payload"].get("type") == "RUN_FINISHED" for f in frames)


def test_digest_wired_in_live_run_paths(tmp_db, monkeypatch):
    # ★Critic MAJOR#2: 라이브 _run 경로가 digest(append_decision)를 실제 호출함을 단언(unit primitive
    # 격리 아님). autonomous(else-branch·🟢) + escalate(if-interrupts·🔴 dead-path 폐쇄) 둘 다 배선 확인.
    import orch.app as appmod

    recorded: list[str] = []

    async def spy(tier, summary, **kw):
        recorded.append(getattr(tier, "value", str(tier)))
        return None

    monkeypatch.setattr(appmod, "append_decision", spy)
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _AUTONOMOUS})
        _poll_frames(
            client,
            lambda fr: any(f["payload"].get("type") == "TEXT_MESSAGE" for f in fr),
        )
        client.post("/api/run", json={"workflow": _ESCALATE})
        _poll_frames(
            client,
            lambda fr: any(
                f["payload"].get("type") == "INTERRUPT_REQUESTED" for f in fr
            ),
        )
        for _ in range(40):  # 라이브 _run(fire-and-forget) digest 호출 대기
            if "autonomous" in recorded and "escalate" in recorded:
                break
            time.sleep(0.05)
    assert "autonomous" in recorded, "autonomous cycle digest 미배선(else-branch 🟢)"
    assert "escalate" in recorded, "escalate digest 미배선(if-interrupts 🔴 dead-path)"
