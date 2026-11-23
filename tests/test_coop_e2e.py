"""US-3 — 협업 게임빌드 mock e2e(직렬 체인 게이트) + 진입 라우터 계약.

★검증 둘:
  (A) mock e2e 직렬 체인(워커 구동·temp 격리): BuildCoordinator(temp db·temp game/data·
      mock=True)가 5단계(game_design→balance→content→assemble→qa)를 enqueue하면, 이
      테스트 전용 in-process 워커가 큐를 드레인해 각 단계를 결정론 fake LLM으로 풀런하고,
      코디네이터가 단계별 RUN_FINISHED를 폴링해 자동 체이닝한다. 최종:
        - temp game/data에 assembly.json{ready:true} + qa_report.json{pass:true} 산출.
        - 코디네이터 최종 state=done(5단계 전부 success).
      ★★격리(위반 시 라이브 오염·치명): 라이브 office.db / 실 game/data 절대 미사용.
        - DB는 tmp_path/office.db(코디네이터·워커 동일 temp db).
        - game tools(game_data_write/read/validate)는 temp game_root로 monkeypatch한
          REAL_TOOLS — 워커가 그 temp-패치 도구로 실행하므로 실 repo game/data 무접촉.
        - 테스트 끝에 실 game/data에 assembly.json·qa_report.json이 생성 안 됐음을 실검증.
        - 워커는 in-process 백그라운드 스레드. 빌드가 끝날 때까지 큐를 *연속* 드레인
          (max_ticks=None)하고 Worker.request_stop() 플래그 + join(timeout)으로 이중
          바운드한다 — 고정 tick 예산 소진 종료가 없어 워커가 코디네이터의 마지막 단계
          enqueue까지 항상 claim 가능(결정론·부하 무관). subprocess·pkill 불요(라이브
          worker PID 불침해).

  (B) 진입 라우터 계약(TestClient·빠른 스텁 코디네이터):
        - POST /api/coop/build → 202 + {"build_id": ...}(즉시 반환·백그라운드 실행).
        - GET /api/coop/status/{build_id} → 등록된 build_id면 state/stage/run_ids/error,
          미등록이면 404.

실행: scripts/run_tests.sh engine tests/test_coop_e2e.py
오프라인 결정론(실서버·LLM 0·mock=True). 모든 대기에 타임아웃 가드(hang 0).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import office.tools as tools_mod
from office.coordinator import STATE_DONE, BuildCoordinator
from office.event_store import EventStore
from office.game_schema import validate_all
from office.task_queue import TaskQueue
from office.worker import Worker

# 대기 상한(초) — mock은 빠르므로 작은 단계 타임아웃 + 총 상한(전부 이 안에 결판·hang 0).
_HANG_GUARD_S = 30.0
_STAGE_T_CLAIM = 8.0
_STAGE_T_RUN = 12.0
_POLL_S = 0.05
_WORKER_POLL_S = 0.02

# game tools가 만지는 파일 화이트리스트(temp/실 양쪽 동일 — 격리 단언용).
_STAGE_OUTPUTS = (
    "meta.json",
    "enemies.json",
    "weapons.json",
    "waves.json",
    "upgrades.json",
    "balance.json",
    "assembly.json",
    "qa_report.json",
)


# ── temp game_root로 미러한 game_data_write/read/validate(실 game/data 무접촉) ──
#
# office.tools.game_data_*는 repo_root/"game"을 하드코딩한다(env 오버라이드 없음). 워커는
# REAL_TOOLS를 쓰므로, 같은 경로 화이트리스트·멱등·스키마 검증 로직을 temp game_root로
# 미러한 도구 3종을 만들어 REAL_TOOLS 엔트리를 monkeypatch한다 → 워커 실행이 그 temp
# 도구를 타 실 repo game/data를 절대 만지지 않는다(test_game_pipeline_e2e._make_tmp_tools
# 패턴 재사용·worker-driven으로 변형).


def _make_tmp_game_tools(game_root: Path) -> dict[str, Any]:
    """temp game_root를 가리키는 game_data_write/read/validate 도구 3종을 만든다."""

    def _resolve(raw_path: str) -> Path | None:
        target = (game_root / raw_path).resolve()
        try:
            rel = target.relative_to(game_root.resolve())
        except ValueError:
            return None
        if str(rel) == ".":
            return None
        return target

    def _write(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw_path = args.get("path", "")
        content = args.get("content", "")
        target = _resolve(raw_path)
        if target is None or target.is_dir():
            return {
                "written": False,
                "error": "path outside game/ or dir",
                "path": raw_path,
            }
        text = (
            json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
            if isinstance(content, (dict, list))
            else str(content)
        )
        new_bytes = text.encode("utf-8")
        rel = str(target.relative_to(game_root.resolve()))
        if target.exists() and target.read_bytes() == new_bytes:
            return {
                "written": False,
                "path": rel,
                "bytes": len(new_bytes),
                "deduplicated": True,
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(new_bytes)
        return {
            "written": True,
            "path": rel,
            "bytes": len(new_bytes),
            "deduplicated": False,
        }

    def _read(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw_path = args.get("path", "")
        required = bool(args.get("required", False))
        target = _resolve(raw_path)
        if target is None:
            raise ValueError(f"path outside game/: {raw_path!r}")
        rel = str(target.relative_to(game_root.resolve()))
        if not target.exists():
            if required:
                raise FileNotFoundError(f"required game data file not found: {rel}")
            return {"exists": False, "content": None, "path": rel}
        return {
            "exists": True,
            "content": target.read_text(encoding="utf-8"),
            "path": rel,
        }

    def _validate(
        args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw_dir = args.get("data_dir") or "data"
        target = (game_root / raw_dir).resolve()
        try:
            target.relative_to(game_root.resolve())
        except ValueError:
            return {
                "ok": False,
                "errors": [f"data_dir outside game/: {raw_dir!r}"],
                "report": {"valid": False, "errorCount": 1},
            }
        res = validate_all(target)
        errors = res.get("errors", [])
        return {
            "ok": res["ok"],
            "errors": errors,
            "report": {"valid": res["ok"], "errorCount": len(errors)},
        }

    return {
        "game_data_write": _write,
        "game_data_read": _read,
        "game_validate": _validate,
    }


@pytest.fixture
def temp_game_root(tmp_path: Path) -> Path:
    """temp game 루트(game_root/data)를 만든다 — 실 repo game/ 무접촉."""
    game_root = tmp_path / "game"
    (game_root / "data").mkdir(parents=True)
    return game_root


@pytest.fixture
def patched_game_tools(monkeypatch: pytest.MonkeyPatch, temp_game_root: Path) -> Path:
    """REAL_TOOLS의 game_data_* 3종을 temp game_root 도구로 교체한다(라이브 game/data 격리).

    워커가 mock 엔진을 만들 때 tools=REAL_TOOLS(같은 dict 객체)를 쓰므로, 그 dict의
    엔트리만 temp 도구로 setitem하면 워커 실행이 temp game/data로 흐른다(실 repo 무접촉).
    monkeypatch라 테스트 종료 시 원복(다른 테스트·라이브 무영향).
    """
    tmp_tools = _make_tmp_game_tools(temp_game_root)
    for name, fn in tmp_tools.items():
        monkeypatch.setitem(tools_mod.REAL_TOOLS, name, fn)
    return temp_game_root


# ── (A) mock e2e 직렬 체인(워커 구동·temp 격리) ──────────────────────────────────


def test_coop_build_chains_five_stages_via_worker(
    tmp_path: Path, patched_game_tools: Path
) -> None:
    """temp 격리에서 BuildCoordinator(mock)가 5단계를 워커 구동으로 자동 체이닝 완주.

    - DB = tmp_path/office.db(코디네이터·워커 동일 temp db — 라이브 office.db 미사용).
    - game tools = temp game_root 패치(patched_game_tools — 실 repo game/data 무접촉).
    - 워커 = in-process 백그라운드 스레드(max_ticks≥5·max_polls 바운드 + join(timeout)).
    - 단언: assembly.json{ready:true} + qa_report.json{pass:true} + state=done.
    """
    game_root = patched_game_tools
    data_dir = game_root / "data"
    db_path = tmp_path / "office.db"

    # ★격리 단언(사전): 사용하는 DB가 라이브 office.db가 아니라 temp 경로임을 명시.
    real_office_db = Path(__file__).resolve().parent.parent / "state" / "office.db"
    assert db_path != real_office_db
    assert str(tmp_path) in str(db_path)

    # ★격리 단언(사전 스냅샷): 실 repo game/data의 현재 상태(파일명→mtime)를 스냅샷한다.
    #   사후에 *무변화*를 검사 — 절대-부재(라이브 협업 빌드가 정당하게 남긴 assembly/qa가
    #   있으면 거짓실패)가 아니라 "이 테스트가 실 game/data를 건드렸는가"를 결정론적으로 본다.
    real_data = Path(__file__).resolve().parent.parent / "game" / "data"

    def _real_data_snapshot() -> dict[str, int]:
        return {p.name: p.stat().st_mtime_ns for p in sorted(real_data.glob("*.json"))}

    real_data_before = _real_data_snapshot()

    store = EventStore(db_path=db_path)
    queue = TaskQueue(db_path=db_path)

    coordinator = BuildCoordinator(
        queue=queue,
        event_store=store,
        db_path=db_path,
        t_claim=_STAGE_T_CLAIM,
        t_run=_STAGE_T_RUN,
        poll_interval=_POLL_S,
    )

    # ── 워커: 빌드가 끝날 때까지 temp db를 *연속* 드레인(고정 예산 소진 후 종료 금지) ──
    # ★결정론(고정 max_ticks 레이스 제거): 코디네이터는 한 단계를 enqueue→폴링→다음 단계
    #   enqueue로 직렬 진행하므로, 워커가 고정 tick 예산(예: 20)으로 돌면 부하로 스케줄이
    #   지연될 때 코디네이터가 마지막 game_qa를 enqueue하기 *전*에 빈 폴로 예산을 소진하고
    #   종료해 game_qa가 미claim→코디네이터 t_claim 만료(claim_timeout)로 거짓실패한다.
    #   따라서 max_ticks=None(무한 드레인)으로 워커를 돌리고, 빌드 스레드 join 후
    #   worker.request_stop()(Worker가 제공하는 graceful stop 플래그)로 멈춘다 — 워커는
    #   코디네이터가 어느 단계를 enqueue하든 poll_interval(_WORKER_POLL_S=0.02s) 안에
    #   항상 claim 가능하다(연속 가용성이 결정론의 핵심·타임아웃 크기가 아님).
    # ★바운드 이중화(무한루프 0·hang 0): (a) 빌드 스레드 bt.join(timeout=_HANG_GUARD_S)로
    #   총 상한, (b) request_stop 플래그 + wt.join(timeout=_HANG_GUARD_S)로 워커 루프 상한.
    #   max_ticks=None 경로는 빈 폴마다 poll_interval sleep하며 매 루프 진입 시
    #   _stop_requested를 검사하므로(office/worker.py run() 루프), stop은 한 폴 안에 반영된다.
    worker = Worker(db_path=db_path, poll_interval=_WORKER_POLL_S)
    build_id = "coop-e2e-1"
    result_holder: dict[str, Any] = {}

    def _run_worker() -> None:
        # max_ticks=None: 빌드가 끝날 때까지 연속 드레인(빈 폴은 poll_interval sleep).
        # request_stop()로만 종료한다. max_polls 유한(suspend 없음·게임은 always-allow
        # 도구라 승인 대기 0이지만 hang 가드로 바운드).
        worker.run(max_ticks=None, max_polls=50)

    def _run_build() -> None:
        result_holder["status"] = coordinator.run_build(build_id, mock=True)

    wt = threading.Thread(target=_run_worker, name="coop-e2e-worker", daemon=True)
    bt = threading.Thread(target=_run_build, name="coop-e2e-build", daemon=True)
    wt.start()
    bt.start()

    # 빌드 스레드가 끝나길 대기(총 상한 — hang 0). 코디네이터가 done/failed로 마감.
    bt.join(timeout=_HANG_GUARD_S)
    assert not bt.is_alive(), "build 스레드가 상한 내 종료하지 못함(hang 의심)"
    # 빌드 종결 후 워커에 graceful stop 신호 → 워커 루프가 다음 폴에서 탈출. join으로 바운드.
    worker.request_stop()
    wt.join(timeout=_HANG_GUARD_S)
    assert not wt.is_alive(), "worker 스레드가 stop 후 상한 내 종료하지 못함(hang 의심)"

    status = result_holder.get("status")
    assert status is not None, "run_build 결과 미수신"
    assert status["state"] == STATE_DONE, (
        f"5단계 체인 미완주: state={status['state']} stage={status['stage']} "
        f"error={status['error']}"
    )
    # 5단계 전부 run_id 상관 기록(각 단계 워커가 claim·풀런했다는 증거).
    assert len(status["run_ids"]) == 5

    # ── 산출 단언(temp game/data): 종료신호 2파일 + 6 베이스라인 ──
    # ★AC(직렬체인 게이트): assembly.json{ready:true} + qa_report.json{pass:true} +
    #   state=done. 워커가 mock=True 결정론 fake LLM(_FakeSmokeLLM)으로 5단계를 풀런해
    #   각 단계 산출 파일을 temp game/data에 남기고, 종료신호 2파일을 produce했다는 단언.
    assembly = json.loads((data_dir / "assembly.json").read_text("utf-8"))
    assert assembly["ready"] is True
    qa_report = json.loads((data_dir / "qa_report.json").read_text("utf-8"))
    assert qa_report["pass"] is True, qa_report.get("errors")
    for fname in (
        "meta.json",
        "enemies.json",
        "weapons.json",
        "waves.json",
        "upgrades.json",
        "balance.json",
    ):
        assert (data_dir / fname).exists(), f"단계 산출 누락: {fname}"
    # 각 산출 파일이 유효 JSON임(워커가 실제 디스크에 기록한 결과물 — 손상 0).
    for fname in _STAGE_OUTPUTS:
        json.loads((data_dir / fname).read_text("utf-8"))
    # ★참고(validate_all ok:true는 단언하지 않음): worker-mock 경로는 frozen run.py의
    #   _FakeSmokeLLM 캔드 시퀀스를 쓰는데, game_content 단계가 enemies를 새 id
    #   (zombie/bat_swarm/...)로 덮어쓰는 반면 game_balance waves는 옛 id(walker/bat/brute)를
    #   참조해 cross-file 참조 무결성이 의도적으로 어긋난다(frozen 캔드 데이터의 성질·
    #   동결 7파일이라 미수정). qa_report{pass:true}는 game_qa_write_report 캔드가 고정
    #   발행(game_validate 결과와 독립)하므로 직렬체인 완주 게이트는 성립한다. 단계간
    #   consistent 데이터로 validate_all ok:true를 보는 건 별 메커니즘(per-stage _SeqLLM
    #   주입)인 tests/test_game_pipeline_e2e.py가 담당한다(slow). 여기선 worker-driven
    #   직렬체인 자동화 + temp 격리가 검증 대상이다.

    # ★★격리 단언(사후·무변화): 이 테스트가 실 repo game/data를 건드리지 않았음을 실검증.
    #   사전 스냅샷과 동일해야 한다(파일 생성/수정/삭제 0). 라이브 협업 빌드가 정당하게 남긴
    #   산출물(assembly/qa 등)이 존재해도, 이 테스트가 그것을 *변경하지 않았으면* 격리 성립
    #   → 절대-부재 단언의 환경의존 거짓실패를 제거(결정론).
    assert _real_data_snapshot() == real_data_before, (
        "라이브 game/data 오염: 이 e2e가 실 repo game/data를 생성/수정/삭제함(격리 실패)"
    )


# ── (B) 진입 라우터 계약(TestClient·빠른 스텁 코디네이터) ─────────────────────────


class _StubCoordinator:
    """run_build를 빠르게 마감하는 스텁(실 워커·LLM 불요 — 라우터 계약만 검증).

    run_build(build_id, mock, query)가 호출되면 self._builds에 즉시 done 상태를 기록한다
    (백그라운드 스레드가 짧게 돌고 끝). get_status는 실 코디네이터와 동일 시그니처.
    """

    def __init__(self) -> None:
        self._builds: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []

    def run_build(
        self,
        build_id: str,
        *,
        mock: bool,
        query: str | None = None,
        payload_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"build_id": build_id, "mock": mock, "query": query})
        status = {
            "state": STATE_DONE,
            "stage": None,
            "run_ids": {"game_design": "rid-1"},
            "error": None,
        }
        self._builds[build_id] = status
        return status

    def get_status(self, build_id: str) -> dict[str, Any] | None:
        return self._builds.get(build_id)


def test_build_returns_202_and_status_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/coop/build → 202 + build_id, GET /status/{id} → state 노출(스텁)."""
    from starlette.testclient import TestClient

    from web_server.app import create_app

    db_path = tmp_path / "office.db"
    store = EventStore(db_path=db_path)
    stub = _StubCoordinator()

    app = create_app(store=store)
    # 라우터가 보는 코디네이터를 스텁으로 강제(실 BuildCoordinator·워커 불요).
    app.state.coop_coordinator = stub

    with TestClient(app) as client:
        resp = client.post("/api/coop/build", json={"mock": True, "query": "테스트"})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        build_id = body["build_id"]
        assert isinstance(build_id, str) and build_id
        assert body["mock"] is True

        # 백그라운드 데몬 스레드가 run_build를 호출해 done을 기록할 때까지 폴링(hang 0).
        deadline = time.monotonic() + _HANG_GUARD_S
        status_body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            sresp = client.get(f"/api/coop/status/{build_id}")
            if sresp.status_code == 200 and sresp.json()["state"] == STATE_DONE:
                status_body = sresp.json()
                break
            time.sleep(0.02)

    assert status_body, "status가 상한 내 done에 도달하지 못함"
    assert status_body["build_id"] == build_id
    assert status_body["state"] == STATE_DONE
    assert status_body["run_ids"] == {"game_design": "rid-1"}
    assert status_body["error"] is None
    # run_build가 build_id·mock·query를 그대로 받았는지(라우터 배선).
    assert stub.calls and stub.calls[0]["build_id"] == build_id
    assert stub.calls[0]["mock"] is True
    assert stub.calls[0]["query"] == "테스트"


def test_status_unknown_build_id_returns_404(
    tmp_path: Path,
) -> None:
    """미등록 build_id 상태조회는 404(not_found)."""
    from starlette.testclient import TestClient

    from web_server.app import create_app

    db_path = tmp_path / "office.db"
    store = EventStore(db_path=db_path)
    app = create_app(store=store)
    app.state.coop_coordinator = _StubCoordinator()

    with TestClient(app) as client:
        resp = client.get("/api/coop/status/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["status"] == "not_found"


def test_build_defaults_mock_false_when_body_empty(
    tmp_path: Path,
) -> None:
    """body 생략 시 mock 기본 False로 202 반환(스텁이 mock=False로 호출됨)."""
    from starlette.testclient import TestClient

    from web_server.app import create_app

    db_path = tmp_path / "office.db"
    store = EventStore(db_path=db_path)
    stub = _StubCoordinator()
    app = create_app(store=store)
    app.state.coop_coordinator = stub

    with TestClient(app) as client:
        resp = client.post("/api/coop/build", json={})
        assert resp.status_code == 202
        assert resp.json()["mock"] is False
        build_id = resp.json()["build_id"]
        # run_build가 호출될 때까지 잠깐 폴(데몬 스레드).
        deadline = time.monotonic() + _HANG_GUARD_S
        while time.monotonic() < deadline and not stub.calls:
            time.sleep(0.02)
    assert stub.calls and stub.calls[0]["mock"] is False
    assert stub.calls[0]["query"] is None
