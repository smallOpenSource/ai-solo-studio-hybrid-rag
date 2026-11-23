"""P3b-3 §19.2.6 — app.py _run 숙의 ADR sink + escalate 도달성 + digest 마커(프레임 레벨).

★green-theater 차단: state 플래그가 아니라 **실제 outbound 프레임**(TEXT_MESSAGE/
INTERRUPT_REQUESTED) + **실제 vault/decisions/ADR-*.md 파일**을 단언한다. fire-and-forget
``_run`` 이라 caller/judge stub 은 컨텍스트매니저가 배경태스크에 안 걸리므로:
  - gemma/searcher = create_app(caller=,searcher=) 주입(_bind_cycle 이 _run 안에서 use_caller
    바인딩 → nested 숙의 서브그래프 노드에 ContextVar 전파).
  - judge = orch.deliberation._judge 모듈심볼 monkeypatch(태스크 경계 무관·context-local 아님).
  - vault_dir = ORCH_VAULT_DIR=tmp_path env override(실 vault 누적 오염 차단·실파일 검증).

설계 핵심(도달성): needs_deliberation=가역(two_way) 필수 ↔ ESCALATE=one_way/owner_reserved/
high_cost 필수 → 상호배타 → "숙의→escalate"는 decide()로 도달 불가. judge 가 ESCALATE:yes
권고하면(recommend_escalate) 재진입 authority_gate 가 interrupt → 도달 가능(HIGH-1 b).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import orch.app as appmod
import orch.deliberation as delibmod
from orch.app import create_app

# 가역(one-way/owner-reserved 키워드 無)·고위험신호("전략","방향") 동반 → needs_deliberation 후보.
_DELIB = "전략 방향 대전환 검토"


# ── stub callers (모듈레벨·배경태스크 전파) ──────────────────────────────────────
def _gemma_stub(prompt: str, temperature: float, max_tokens: int) -> str:
    """cycle 스테이지 + 숙의 서브그래프 공용. ceo_route 가 conf 0.40 emit(<TAU_NOTIFY) → 숙의 후보."""
    p = prompt.lower()
    if "ceo router" in p:
        return "ROUTE: build | conf 0.40 | strategic pivot under review"
    if "design stage" in p:
        return "core loop: click. reward: +1 coin. upgrade: auto-clicker."
    if "content stage" in p:
        return "Name: Clicky Quest. items: Cursor(10) Grandma(20) Factory(30)"
    if "qa stage" in p:
        return "check1: loop ok. check2: costs ok. risk: balance. VERDICT: PASS"
    if "bluf" in p:
        return (
            "결론: 전략 검토 결과 진행 가능.\n"
            "필요행동: 없음.\n"
            "근거:\n- research: F2P (https://example.test/wiki/F2P)\n"
            "- design: ok\n- content: ok\n- qa: PASS"
        )
    if "독립 제안" in prompt:
        return "제안: 단계적 전환(독립)."
    if "proponent" in p:
        return "찬성: 전환이 성장 기회."
    if "challenger" in p:
        return "반론: 전환 비용·리스크 큼(이의)."
    if "premortem" in p:
        return "실패원인: 전환 혼선으로 사용자 이탈."
    return "stub-artifact"


def _searcher_stub(query: str, k: int) -> dict:
    return {
        "results": [
            {
                "text": "F2P sustained engagement.",
                "source": "https://x.test/F2P",
                "score": -0.5,
            }
        ],
        "count": 1,
    }


def _judge_notify(prompt: str, temperature: float, max_tokens: int) -> str:
    """Judge stub — conf 0.40(>=0.35 floor) + ESCALATE:no → recommend_escalate False → NOTIFY 경로."""
    if "synthesizer" in prompt.lower() or "Synthesizer" in prompt:
        return "DECISION: 단계적 전환 채택 | conf 0.40 | MINORITY: 즉시 전환 반대 | ESCALATE: no"
    return "stub"


def _judge_escalate(prompt: str, temperature: float, max_tokens: int) -> str:
    """Judge stub — ESCALATE:yes(conf 는 0.40 이라 floor 무관) → recommend_escalate True → escalate."""
    if "synthesizer" in prompt.lower() or "Synthesizer" in prompt:
        return "DECISION: 신중 전환 | conf 0.40 | MINORITY: 반대 | ESCALATE: yes"
    return "stub"


def _judge_high_conf(prompt: str, temperature: float, max_tokens: int) -> str:
    """Judge stub — conf 0.80 + ESCALATE:no → AUTONOMOUS(재평가 3분기 中 고확신)."""
    if "synthesizer" in prompt.lower() or "Synthesizer" in prompt:
        return "DECISION: 자신감 전환 | conf 0.80 | MINORITY: 없음 | ESCALATE: no"
    return "stub"


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_db():
    db = tempfile.mktemp(suffix="_delibapp.db")
    yield db
    for s in ("", "-wal", "-shm"):
        p = Path(db + s)
        if p.exists():
            p.unlink()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """ORCH_VAULT_DIR=tmp_path → ADR sink 가 격리 디렉토리에 씀(실 vault 누적 오염 0)."""
    monkeypatch.setenv("ORCH_VAULT_DIR", str(tmp_path))
    return tmp_path


def _client(tmp_db: str) -> TestClient:
    app = create_app(
        ckpt_db=tmp_db,
        caller=_gemma_stub,
        searcher=_searcher_stub,
        tracing=False,
        auth=False,
    )
    return TestClient(app)


def _poll_frames(client: TestClient, pred, tries: int = 100):
    """bounded-poll /outbound/frames (fire-and-forget _run 레이스 회피)."""
    for _ in range(tries):
        frames = client.get("/outbound/frames").json()["frames"]
        if pred(frames):
            return frames
        time.sleep(0.05)
    return None


def _adr_files(vault_dir: Path) -> list[Path]:
    dec = vault_dir / "decisions"
    return sorted(dec.glob("ADR-*.md")) if dec.is_dir() else []


def _wait_adr(vault_dir: Path, tries: int = 100) -> list[Path]:
    """ADR 파일이 디스크에 나타날 때까지 bounded-poll(배경태스크 sink 레이스)."""
    for _ in range(tries):
        files = _adr_files(vault_dir)
        if files:
            return files
        time.sleep(0.05)
    return []


# ── (a) 가역 숙의 → else 분기(HIGH-1 a): BLUF 프레임 + ADR 파일 ──────────────────
def test_reversible_deliberation_emits_bluf_and_writes_adr(tmp_db, vault, monkeypatch):
    """가역+저확신+고위험신호 → 숙의 → NOTIFY → finalize. TEXT_MESSAGE(BLUF·run_id 매칭)+
    RUN_FINISHED 프레임 AND vault/decisions/ADR-*.md 1건(else 분기 ADR sink·HIGH-1 a)."""
    monkeypatch.setattr(delibmod, "_judge", _judge_notify)
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _DELIB})

        frames = _poll_frames(
            client,
            lambda fr: any(f["payload"].get("type") == "TEXT_MESSAGE" for f in fr),
        )
        assert frames is not None, (
            "가역 숙의 완료가 TEXT_MESSAGE(BLUF) 방출 안 함(무음 갭)"
        )
        texts = [f for f in frames if f["payload"].get("type") == "TEXT_MESSAGE"]
        finished = [f for f in frames if f["payload"].get("type") == "RUN_FINISHED"]
        interrupts = [
            f for f in frames if f["payload"].get("type") == "INTERRUPT_REQUESTED"
        ]
        tm = texts[0]["payload"]
        assert tm.get("content"), "BLUF content 비어있음(RUN_FINISHED-only 위장 차단)"
        rids = {f["payload"].get("run_id") for f in finished}
        assert tm.get("run_id") in rids, "TEXT_MESSAGE/RUN_FINISHED run_id 불일치"
        assert not interrupts, (
            "가역 NOTIFY 인데 INTERRUPT_REQUESTED 발생(escalate 오분류)"
        )
        # ★실파일: ADR sink 가 vault/decisions/ADR-*.md 생성(state 플래그만으론 FAIL).
        adrs = _wait_adr(vault)
        assert len(adrs) >= 1, "가역 숙의 완료 후 ADR 파일 미생성(else 분기 sink 무음)"
        body = adrs[0].read_text(encoding="utf-8")
        assert body.startswith("# ADR-"), "ADR 파일 헤더 형식 불일치"
        assert "## 소수의견" in body and "## Premortem" in body, (
            "ADR 숙의 합집합 섹션 누락"
        )


# ── (b) 숙의→escalate → if-interrupt 분기(HIGH-1 b·★핵심 도달성) ────────────────
def test_deliberation_escalate_emits_interrupt_and_writes_adr(
    tmp_db, vault, monkeypatch
):
    """judge ESCALATE:yes → recommend_escalate True → 재진입 authority_gate interrupt.
    INTERRUPT_REQUESTED 프레임 AND vault/decisions/ADR-*.md 둘 다(if-interrupt ADR sink·
    무음유실 0). ★decide()로는 도달 불가(가역↔escalate 상호배타)한 경로를 §19.2.6 으로 surface."""
    monkeypatch.setattr(delibmod, "_judge", _judge_escalate)
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _DELIB})

        frames = _poll_frames(
            client,
            lambda fr: any(
                f["payload"].get("type") == "INTERRUPT_REQUESTED" for f in fr
            ),
        )
        assert frames is not None, (
            "숙의→escalate(recommend_escalate)가 INTERRUPT_REQUESTED 미방출 — "
            "escalate 도달성 실패(dead code)"
        )
        # 인터럽트 전엔 RUN_FINISHED 없음(아직 Owner 승인 대기).
        assert not any(f["payload"].get("type") == "RUN_FINISHED" for f in frames)
        # ★실파일: if-interrupt 분기 ADR sink 가 숙의 ADR 영속(무음유실 0).
        adrs = _wait_adr(vault)
        assert len(adrs) >= 1, (
            "숙의→escalate 시 ADR 파일 미생성(if-interrupt sink 무음유실)"
        )
        body = adrs[0].read_text(encoding="utf-8")
        assert body.startswith("# ADR-") and "## Premortem" in body


# ── (c) digest 숙의 마커(MED-5) ──────────────────────────────────────────────────
def test_digest_has_deliberation_marker_and_adr_ref(tmp_db, vault, monkeypatch):
    """(a) 경로(가역 숙의 NOTIFY) digest 엔트리에 '(숙의)' 마커 + [ADR-NNNN] ref 포함
    (일반 NOTIFY 와 구별). 실제 _run 경로가 append_decision 을 그 인자로 호출함을 spy 단언."""
    monkeypatch.setattr(delibmod, "_judge", _judge_notify)
    recorded: list[dict] = []

    async def spy(tier, summary, **kw):
        recorded.append(
            {"tier": getattr(tier, "value", str(tier)), "summary": summary, **kw}
        )
        return None

    monkeypatch.setattr(appmod, "append_decision", spy)
    with _client(tmp_db) as client:
        client.post("/api/run", json={"workflow": _DELIB})
        _poll_frames(
            client,
            lambda fr: any(f["payload"].get("type") == "TEXT_MESSAGE" for f in fr),
        )
        for _ in range(60):  # 배경태스크 digest 호출 대기
            if any("(숙의)" in (r.get("summary") or "") for r in recorded):
                break
            time.sleep(0.05)
    delib_entries = [r for r in recorded if "(숙의)" in (r.get("summary") or "")]
    assert delib_entries, f"digest 에 '(숙의)' 마커 미반영(MED-5): {recorded}"
    e = delib_entries[0]
    assert e["tier"] == "notify", "가역 숙의(conf 0.40)는 NOTIFY 여야 함"
    assert (e.get("ref") or "").startswith("ADR-"), f"digest ref 가 ADR-NNNN 아님: {e}"


# ── (d) 재평가 3분기(Missing-2): NOTIFY / ESCALATE / AUTONOMOUS ──────────────────
def test_reassessment_three_branches(tmp_db, vault, monkeypatch):
    """동일 가역 directive 가 judge 출력에 따라 3분기로 갈림(재평가 도달성·Missing-2):
      - judge conf 0.40 + ESCALATE:no → NOTIFY(else BLUF·🟡)
      - judge ESCALATE:yes           → ESCALATE(if-interrupt·🔴·recommend_escalate)
      - judge conf 0.80 + ESCALATE:no → AUTONOMOUS(else BLUF·🟢)
    각 분기를 독립 클라이언트로 구동해 outcome(프레임/tier)을 단언."""
    # 분기 1: NOTIFY
    monkeypatch.setattr(delibmod, "_judge", _judge_notify)
    notify_recorded: list[str] = []

    async def spy_notify(tier, summary, **kw):
        notify_recorded.append(getattr(tier, "value", str(tier)))
        return None

    monkeypatch.setattr(appmod, "append_decision", spy_notify)
    with _client(tmp_db + ".n") as client:
        client.post("/api/run", json={"workflow": _DELIB})
        _poll_frames(
            client,
            lambda fr: any(f["payload"].get("type") == "TEXT_MESSAGE" for f in fr),
        )
        for _ in range(60):
            if "notify" in notify_recorded:
                break
            time.sleep(0.05)
    assert "notify" in notify_recorded, "재평가 NOTIFY 분기 미도달(conf 0.40 가역)"

    # 분기 2: ESCALATE(recommend_escalate)
    monkeypatch.setattr(delibmod, "_judge", _judge_escalate)
    with _client(tmp_db + ".e") as client:
        client.post("/api/run", json={"workflow": _DELIB})
        frames = _poll_frames(
            client,
            lambda fr: any(
                f["payload"].get("type") == "INTERRUPT_REQUESTED" for f in fr
            ),
        )
    assert frames is not None, "재평가 ESCALATE 분기 미도달(recommend_escalate 무효)"

    # 분기 3: AUTONOMOUS(고확신)
    monkeypatch.setattr(delibmod, "_judge", _judge_high_conf)
    auto_recorded: list[str] = []

    async def spy_auto(tier, summary, **kw):
        auto_recorded.append(getattr(tier, "value", str(tier)))
        return None

    monkeypatch.setattr(appmod, "append_decision", spy_auto)
    with _client(tmp_db + ".a") as client:
        client.post("/api/run", json={"workflow": _DELIB})
        _poll_frames(
            client,
            lambda fr: any(f["payload"].get("type") == "TEXT_MESSAGE" for f in fr),
        )
        for _ in range(60):
            if "autonomous" in auto_recorded:
                break
            time.sleep(0.05)
    assert "autonomous" in auto_recorded, (
        "재평가 AUTONOMOUS 분기 미도달(conf 0.80 가역+근거)"
    )
    # cleanup extra dbs
    for sfx in (".n", ".e", ".a"):
        for s in ("", "-wal", "-shm"):
            p = Path(tmp_db + sfx + s)
            if p.exists():
                p.unlink()
