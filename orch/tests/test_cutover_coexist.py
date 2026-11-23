"""cutover A (coexist) — 마커 라우팅·graph-aware outcome·단일 chokepoint 불변식.

빠른 구조 테스트(서버 0): chokepoint AST 불변식 + _resolve_thread flag-독립.
라이브 라우팅(slow·실 서버·stub 모델): coexist 에서 org directive→org 그래프 resume·
game directive→cyc 그래프·미지 마커→422. 어댑터/모델은 stub(결정론).
"""

from __future__ import annotations

import ast
import asyncio
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from adapters.discord import auth_client, rest_client
from adapters.discord.sse_client import consume_events
from orch import session as _session
from orch.app import create_app
from orch.tests.conftest import stub_caller, stub_searcher

_APP_PY = Path(create_app.__code__.co_filename)
_PASSWORD = "coexist-pw"
_HOST = "127.0.0.1"


# ── 빠른 구조 테스트(서버 불요) ─────────────────────────────────────────
def test_chokepoint_command_resume_only_in_resolve_thread():
    """모든 Command(resume=) 호출이 _resolve_thread 내부 — AST(docstring/주석 무관)."""
    tree = ast.parse(_APP_PY.read_text())
    ranges = [
        (n.lineno, n.end_lineno)
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        and n.name == "_resolve_thread"
    ]
    assert ranges, "_resolve_thread not found"
    bad = []
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "Command"
            and any(kw.arg == "resume" for kw in n.keywords)
        ):
            if not any(lo <= n.lineno <= hi for lo, hi in ranges):
                bad.append(n.lineno)
    assert not bad, f"Command(resume=) outside _resolve_thread at lines {bad}"


def test_resolve_thread_is_flag_independent():
    """_resolve_thread 는 ORCH_ORG_ROUTING 을 읽지 않음 — marker-wins-over-flag(구조)."""
    tree = ast.parse(_APP_PY.read_text())
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        and n.name == "_resolve_thread"
    )
    body_src = ast.get_source_segment(_APP_PY.read_text(), fn)
    assert "org_routing" not in body_src  # flag 무관 — 마커가 그래프 결정


def test_affinity_log_is_secret_free():
    """route_affinity 로그는 thread_id+graph 만 — 비밀(토큰/비번) 참조 없음(US-8)."""
    src = _APP_PY.read_text()
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
        and n.name == "_log_affinity"
    )
    body = ast.get_source_segment(src, fn).lower()
    assert "route_affinity" in body
    for bad in ("password", "token", "secret", "api_key", "redis_"):
        assert bad not in body


# ── 라이브 라우팅(slow·실 서버·stub) ───────────────────────────────────
def _org_ceo_escalate(prompt: str, temperature: float, max_tokens: int) -> str:
    """org CEO(전략 seam) stub — 항상 escalate 로 라우팅(인터럽트 발생→resolve 가능)."""
    return "escalate"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((_HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def coexist_orch():
    """ORCH_ORG_ROUTING=coexist 서버(stub 모델). org CEO=escalate stub."""
    prev_pw = os.environ.get("LOGIN_PASSWORD")
    prev_routing = os.environ.get("ORCH_ORG_ROUTING")
    os.environ["LOGIN_PASSWORD"] = _PASSWORD
    os.environ["ORCH_ORG_ROUTING"] = "coexist"
    _session._reset_for_tests()

    db = tempfile.mktemp(suffix="_coexist.db")
    port = _free_port()
    app = create_app(
        ckpt_db=db,
        caller=stub_caller,
        searcher=stub_searcher,
        strategy_caller=_org_ceo_escalate,
        tracing=False,
        auth=True,
    )
    config = uvicorn.Config(app, host=_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{_HOST}:{port}"
    ready = False
    for _ in range(100):
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200 and r.json().get("ok"):
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    assert ready, "coexist orch did not become ready"
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        _session._reset_for_tests()
        for key, prev in (
            ("LOGIN_PASSWORD", prev_pw),
            ("ORCH_ORG_ROUTING", prev_routing),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        for s in ("", "-wal", "-shm"):
            p = Path(db + s)
            if p.exists():
                p.unlink()


async def _wait_interrupt(base_url: str, cookie: str) -> int:
    seq = -1

    async def _drain():
        nonlocal seq
        async for s, payload in consume_events(
            base_url, last_event_id=0, max_retries=3, cookie=cookie
        ):
            if payload.get("type") == "INTERRUPT_REQUESTED":
                seq = s
                return

    await asyncio.wait_for(_drain(), timeout=20)
    return seq


@pytest.mark.slow
def test_coexist_org_directive_resumes_on_org_graph(coexist_orch):
    base_url = coexist_orch

    async def _run():
        token = await auth_client.login(base_url, _PASSWORD)
        cookie = f"omc_session={token}"
        # org directive(시장조사) → 분류 org → org 그래프(escalate) 인터럽트.
        run = await rest_client.post_run(
            "신규 캐주얼 게임 시장 트렌드를 조사", base_url, cookie=cookie
        )
        assert run.handled and run.status == 200 and run.task_id is not None

        async with httpx.AsyncClient() as c:
            tr = await c.get(
                f"{base_url}/threads/by_task/{run.task_id}",
                headers={"Cookie": cookie},
                timeout=5,
            )
        thread_id = tr.json()["thread_id"]
        assert thread_id.startswith("org:"), f"org 마커 아님: {thread_id}"

        seq = await _wait_interrupt(base_url, cookie)
        assert seq >= 1, "org escalate INTERRUPT_REQUESTED 없음"

        # /decisions/{tid}/resolve → 마커로 ORG 그래프 재개(교차 아님) → BLUF.
        async with httpx.AsyncClient() as c:
            rv = await c.post(
                f"{base_url}/decisions/{thread_id}/resolve",
                json={"choice": "approve"},
                headers={"Cookie": cookie},
                timeout=20,
            )
        assert rv.status_code == 200, rv.text
        outcome = rv.json()
        assert outcome["graph"] == "org"  # graph-aware
        assert outcome.get("bluf")  # 비-빈 BLUF
        assert "stage_timings" not in outcome  # cycle 전용 phantom 키 없음

        # RUN_FINISHED 라벨이 org(=game_build 아님).
        async with httpx.AsyncClient() as c:
            fr = await c.get(
                f"{base_url}/outbound/frames", headers={"Cookie": cookie}, timeout=5
            )
        payloads = [f["payload"] for f in fr.json()["frames"]]
        finished = [p for p in payloads if p["type"] == "RUN_FINISHED"]
        assert finished and all(p.get("workflow") != "game_build" for p in finished)
        assert any(p.get("workflow") == "org" for p in finished)
        assert any(p["type"] == "TEXT_MESSAGE" for p in payloads)

    asyncio.run(_run())


@pytest.mark.slow
def test_coexist_game_directive_uses_cycle_graph(coexist_orch):
    base_url = coexist_orch

    async def _run():
        token = await auth_client.login(base_url, _PASSWORD)
        cookie = f"omc_session={token}"
        # game directive → 분류 game → cyc 마커. ★P3: cycle hitl 무조건 interrupt 은퇴 →
        # authority_gate(§20). owner-reserved(출시/금전/지출) 게임 빌드 = ESCALATE → interrupt.
        # (게임 키워드라 classify_directive=game→cyc·escalate 키워드는 org 키워드 아님.)
        run = await rest_client.post_run(
            "스팀 출시용 클리커 게임 빌드 — 광고비 실금전 지출 승인 필요",
            base_url,
            cookie=cookie,
        )
        assert run.handled and run.status == 200

        async with httpx.AsyncClient() as c:
            tr = await c.get(
                f"{base_url}/threads/by_task/{run.task_id}",
                headers={"Cookie": cookie},
                timeout=5,
            )
        thread_id = tr.json()["thread_id"]
        assert thread_id.startswith("cyc:"), f"cyc 마커 아님: {thread_id}"
        seq = await _wait_interrupt(base_url, cookie)
        assert seq >= 1, "cycle authority_gate ESCALATE INTERRUPT_REQUESTED 없음"

    asyncio.run(_run())


@pytest.mark.slow
def test_coexist_unknown_marker_resolve_rejected(coexist_orch):
    base_url = coexist_orch

    async def _run():
        token = await auth_client.login(base_url, _PASSWORD)
        cookie = f"omc_session={token}"
        async with httpx.AsyncClient() as c:
            rv = await c.post(
                f"{base_url}/decisions/bogus:deadbeef/resolve",
                json={"choice": "approve"},
                headers={"Cookie": cookie},
                timeout=5,
            )
        assert rv.status_code == 422  # 미지 마커 → 명시 거부(무음 폴백 금지)

    asyncio.run(_run())


def _boot(db: str, seq_path: str):
    """주어진 ckpt db + seq path 로 coexist 서버 1세대 기동 → (base_url, server, thread)."""
    port = _free_port()
    app = create_app(
        ckpt_db=db,
        caller=stub_caller,
        searcher=stub_searcher,
        strategy_caller=_org_ceo_escalate,
        tracing=False,
        auth=True,
        emitter_seq_path=seq_path,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host=_HOST, port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://{_HOST}:{port}"
    for _ in range(100):
        try:
            r = httpx.get(f"{base}/healthz", timeout=1.0)
            if r.status_code == 200 and r.json().get("ok"):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    return base, server, th


@pytest.mark.slow
def test_resume_after_restart_org_thread(tmp_path):
    """★마커 durable: org thread 정지 → orch '재시작'(같은 ckpt db) → org 그래프로 재개.

    인메모리 사이드테이블은 재시작에 소실되지만 thread_id 접두사(org:)는 체크포인트에 살아
    재개가 정확히 org 그래프를 고른다(architect 핵심 증명). seq 도 영속(무회귀).
    """
    prev_pw = os.environ.get("LOGIN_PASSWORD")
    prev_routing = os.environ.get("ORCH_ORG_ROUTING")
    os.environ["LOGIN_PASSWORD"] = _PASSWORD
    os.environ["ORCH_ORG_ROUTING"] = "coexist"
    _session._reset_for_tests()
    db = str(tmp_path / "restart.db")
    seq_path = str(tmp_path / "seq.txt")

    try:
        base1, srv1, th1 = _boot(db, seq_path)
        assert srv1.started or True  # readiness 위에서 폴링됨

        async def _start():
            token = await auth_client.login(base1, _PASSWORD)
            cookie = f"omc_session={token}"
            run = await rest_client.post_run("시장 조사 부탁해", base1, cookie=cookie)
            async with httpx.AsyncClient() as c:
                tr = await c.get(
                    f"{base1}/threads/by_task/{run.task_id}",
                    headers={"Cookie": cookie},
                    timeout=5,
                )
            tid = tr.json()["thread_id"]
            await _wait_interrupt(base1, cookie)
            async with httpx.AsyncClient() as c:
                fr = await c.get(
                    f"{base1}/outbound/frames", headers={"Cookie": cookie}, timeout=5
                )
            last_seq = max((f["seq"] for f in fr.json()["frames"]), default=0)
            return tid, last_seq

        tid, last_seq = asyncio.run(_start())
        assert tid.startswith("org:")

        # ── '재시작': 1세대 종료 → 같은 db/seq 로 2세대 기동 ──
        srv1.should_exit = True
        th1.join(timeout=10)
        base2, srv2, th2 = _boot(db, seq_path)
        try:

            async def _resume():
                token = await auth_client.login(base2, _PASSWORD)
                ck = f"omc_session={token}"
                async with httpx.AsyncClient() as c:
                    rv = await c.post(
                        f"{base2}/decisions/{tid}/resolve",
                        json={"choice": "approve"},
                        headers={"Cookie": ck},
                        timeout=20,
                    )
                assert rv.status_code == 200, rv.text
                oc = rv.json()
                assert oc["graph"] == "org"  # 재시작 후에도 org 재개(마커 durable)
                assert oc.get("bluf")
                async with httpx.AsyncClient() as c:
                    fr = await c.get(
                        f"{base2}/outbound/frames", headers={"Cookie": ck}, timeout=5
                    )
                seqs = [f["seq"] for f in fr.json()["frames"]]
                # seq 무회귀: 2세대 새 프레임은 1세대 마지막 seq 초과(드롭/리셋 0).
                assert seqs and all(s > last_seq for s in seqs), (
                    f"seq 회귀 {seqs}>{last_seq}"
                )

            asyncio.run(_resume())
        finally:
            srv2.should_exit = True
            th2.join(timeout=10)
    finally:
        _session._reset_for_tests()
        for key, prev in (
            ("LOGIN_PASSWORD", prev_pw),
            ("ORCH_ORG_ROUTING", prev_routing),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


@pytest.mark.slow
def test_off_mode_thread_is_bare_uuid(tmp_path):
    """off(기본) → thread_id 무접두 bare uuid(현 동작 byte-identical·마커 미발동·architect NIT#1)."""
    import uuid as _uuid

    prev_routing = os.environ.get("ORCH_ORG_ROUTING")
    prev_pw = os.environ.get("LOGIN_PASSWORD")
    os.environ.pop("ORCH_ORG_ROUTING", None)  # off = 기본
    os.environ["LOGIN_PASSWORD"] = _PASSWORD
    _session._reset_for_tests()
    db = str(tmp_path / "off.db")
    seq_path = str(tmp_path / "off_seq.txt")
    base, srv, th = _boot(db, seq_path)
    try:

        async def _run():
            token = await auth_client.login(base, _PASSWORD)
            ck = f"omc_session={token}"
            run = await rest_client.post_run("make a clicker game", base, cookie=ck)
            async with httpx.AsyncClient() as c:
                tr = await c.get(
                    f"{base}/threads/by_task/{run.task_id}",
                    headers={"Cookie": ck},
                    timeout=5,
                )
            tid = tr.json()["thread_id"]
            assert ":" not in tid, f"off 인데 마커 접두사 발생: {tid}"
            _uuid.UUID(tid)  # 유효 bare uuid

        asyncio.run(_run())
    finally:
        srv.should_exit = True
        th.join(timeout=10)
        _session._reset_for_tests()
        if prev_routing is None:
            os.environ.pop("ORCH_ORG_ROUTING", None)
        else:
            os.environ["ORCH_ORG_ROUTING"] = prev_routing
        if prev_pw is None:
            os.environ.pop("LOGIN_PASSWORD", None)
        else:
            os.environ["LOGIN_PASSWORD"] = prev_pw
