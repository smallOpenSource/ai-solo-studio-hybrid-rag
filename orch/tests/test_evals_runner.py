"""Unit tests (deterministic·LLM-0·not slow) for orch.evals.runner (P4a).

Covers the §1.5 runner matrix:
  (a) stub caller 주입 시 capture 결정론(동일 stub 2회 byte-identical).
  (b) ★격리 정적가드: runner.py 소스에 "app.create_app"·"OutboundEmitter"·
      "blackboard_sink"·"_adr_sink" 문자열 부재(C-1·MAJOR-1·파일 읽어 assert).
  (c) 런타임 부수효과 부재: stub 그래프 invoke 후 outbound/Redis 미접촉
      (모듈 import 부재 + checkpointer=None 으로 durable/Redis/OTLP 미접촉).
  (d) escalate 앵커 capture 가 interrupt 를 graceful 처리(에러 없이 route="escalate").

★실 그래프(노드 I/O-0)를 stub caller/searcher 로 구동 — 라이브 gemma/Discord/Redis 미접촉.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from orch.evals.runner import run_anchor, run_golden
from orch.tests.conftest import stub_caller, stub_searcher

# ── 픽스처 앵커(synthetic·결정론) ─────────────────────────────────────────────────
_BUILD_ANCHOR = {
    "id": "rn-build-1",
    "type": "agent_task",
    "graph": "cycle",
    "input": "make a clicker game",
    "expected": {"completion": ["결론"]},
    "category": "build",
    "expects_tool": True,
    "expected_tool": "knowledge_search",
}
# owner-reserved 키워드(출시/금전) → ESCALATE → authority_gate interrupt().
_ESCALATE_ANCHOR = {
    "id": "rn-esc-1",
    "type": "routing",
    "graph": "cycle",
    "input": "스팀 출시용 클리커 게임 — 광고비 실금전 지출 승인 필요",
    "expected": {"route": "escalate"},
    "category": "escalate",
    "expects_tool": False,
}


def _org_stub(prompt: str, temperature: float, max_tokens: int) -> str:
    """org 그래프용 결정론 stub(ceo 라우팅 + 부서 디스패치)."""
    p = prompt.lower()
    if "라우팅 작업" in prompt or "route:" in p:
        return "ROUTE: dev_lead | conf 0.80 | technical build task"
    if "디스패치" in prompt or "pick:" in p:
        return "pick: dev"
    return "org stub artifact"


_ORG_ANCHOR = {
    "id": "rn-org-1",
    "type": "routing",
    "graph": "org",
    "input": "build a login API endpoint",
    "expected": {"route": "dev_lead"},
    "category": "routing",
    "expects_tool": False,
}


# ── (a) 캡처 결정론 (동일 stub 2회 byte-identical) ──────────────────────────────────
def test_capture_deterministic_cycle():
    c1 = run_anchor(_BUILD_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    c2 = run_anchor(_BUILD_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)


def test_capture_record_contract_shape():
    """캡처 레코드가 고정 계약 키를 전부 보유(채점 신호 존재·C-4)."""
    cap = run_anchor(_BUILD_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    for key in (
        "anchor_id",
        "type",
        "graph",
        "route",
        "decision_confidence",
        "bluf",
        "tool_calls",
        "search_results",
        "final_state",
    ):
        assert key in cap, f"capture record missing key: {key}"
    # cycle build 은 knowledge_search 1콜·검색결과 1건 캡처(scorer 채점 신호 존재).
    assert cap["route"] == "build"
    assert cap["tool_calls"] and cap["tool_calls"][0]["name"] == "knowledge_search"
    assert cap["search_results"] and len(cap["search_results"][0]) == 2
    assert cap["bluf"].startswith("결론")


def test_capture_deterministic_org():
    c1 = run_anchor(_ORG_ANCHOR, caller=_org_stub, searcher=None)
    c2 = run_anchor(_ORG_ANCHOR, caller=_org_stub, searcher=None)
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)
    assert c1["route"] == "dev_lead"


# ── (b) ★격리 정적가드 (C-1·MAJOR-1) ─────────────────────────────────────────────
def test_runner_isolation_static_guard():
    """runner.py 소스에 프로덕션 sink 식별자가 *코드로* 등장하지 않음.

    문서/docstring 의 언급(설명)은 허용하되, import·호출 형태는 금지 — AST 로
    import/call 식별자만 검사해 docstring 우연일치 false-positive 를 배제한다.
    """
    import ast

    src_path = Path(__file__).resolve().parent.parent / "evals" / "runner.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden = {"create_app", "OutboundEmitter", "blackboard_sink", "_adr_sink"}
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.name)
                if alias.asname:
                    imported.add(alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)

    assert not (forbidden & imported), (
        f"runner imports forbidden sink: {forbidden & imported}"
    )
    assert not (forbidden & called), (
        f"runner calls forbidden sink: {forbidden & called}"
    )
    # app 모듈 자체를 import 하지 않음(create_app 우회 = app._run 미경유).
    assert "orch.app" not in imported and "app" not in imported


def test_runner_does_not_import_app_module():
    """runner import 가 orch.app 을 (전이적으로도) 끌어오지 않음 — 격리 런타임 증거.

    fresh subprocess 에서 orch.evals.runner import 후 sys.modules 에 orch.app 부재.
    (이미 로드됐을 수 있는 같은 프로세스 대신 subprocess 로 깨끗이 검증.)
    """
    import subprocess

    code = (
        "import orch.evals.runner, sys; "
        "assert 'orch.app' not in sys.modules, 'runner pulled in orch.app'; "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )
    assert proc.returncode == 0, f"isolation import failed: {proc.stderr}"
    assert "OK" in proc.stdout


# ── (c) 런타임 부수효과 부재 (outbound/Redis 미호출) ────────────────────────────────
def test_runner_no_outbound_or_redis_side_effects(monkeypatch):
    """invoke 가 outbound emit·Redis state_store 를 호출하지 않음(부수효과 0).

    부수효과 chokepoint 는 *클래스 메서드*다(OutboundEmitter._emit·StateStore.* 쓰기).
    그 메서드들에 spy 를 심고 run_anchor(build+escalate) 후 호출 0 을 단언한다.
    ★non-vacuous 보증: 패치한 spy 수>0 을 먼저 단언(메서드명 오타로 아무것도
    패치 안 돼 calls==[] 가 거짓통과하는 green-theater 차단). 격리 근거 = 노드 I/O-0
    + checkpointer=None + emitter/store 미주입(runner 가 인스턴스 자체를 안 만듦).
    """
    calls: list[str] = []
    patched = 0

    from orch.outbound import OutboundEmitter
    from orch.state_store import StateStore

    # outbound 의 단일 emit chokepoint(_emit) — 모든 emit_* 가 경유.
    assert hasattr(OutboundEmitter, "_emit"), "OutboundEmitter._emit chokepoint 사라짐"
    monkeypatch.setattr(
        OutboundEmitter,
        "_emit",
        lambda self, *a, **k: calls.append("outbound._emit"),
    )
    patched += 1

    # StateStore 의 쓰기/카운터 메서드(Redis aigs:* 접촉 경로).
    for name in ("push_task", "set_status", "bb_set", "incr"):
        assert hasattr(StateStore, name), f"StateStore.{name} chokepoint 사라짐"

        async def _spy(self, *a, _n=name, **k):
            calls.append(f"state.{_n}")

        monkeypatch.setattr(StateStore, name, _spy)
        patched += 1

    assert patched >= 5, "spy 가 충분히 부착되지 않음(vacuous 테스트 방지)"

    run_anchor(_BUILD_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    run_anchor(_ESCALATE_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    assert calls == [], f"runner produced side effects: {calls}"


# ── (d) ★escalate graceful interrupt 처리 ──────────────────────────────────────────
def test_escalate_anchor_graceful_interrupt():
    """ESCALATE 앵커가 interrupt 에 도달해도 에러 없이 route='escalate' 캡처.

    checkpointer=None 그래프는 interrupt 도달 시 예외 없이 __interrupt__ 키를
    반환 dict 에 surface → runner 가 graceful 감지(durable/Redis 미접촉).
    """
    cap = run_anchor(_ESCALATE_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    assert cap["route"] == "escalate"
    assert cap["final_state"].get("route") == "escalate"
    # interrupt 페이로드에서 confidence 보강(하드코딩 아님·계산값).
    assert isinstance(cap["decision_confidence"], float)
    # 결정론(동일 2회).
    cap2 = run_anchor(_ESCALATE_ANCHOR, caller=stub_caller, searcher=stub_searcher)
    assert json.dumps(cap, sort_keys=True) == json.dumps(cap2, sort_keys=True)


# ── run_golden (앵커 전량 캡처·유한 루프·termination P0) ──────────────────────────────
def test_run_golden_captures_all_anchors(tmp_path):
    """run_golden 이 JSONL 의 앵커 전량을 캡처(빈 줄 skip·유한)."""
    golden = tmp_path / "mini_golden.jsonl"
    golden.write_text(
        json.dumps(_BUILD_ANCHOR) + "\n\n" + json.dumps(_ESCALATE_ANCHOR) + "\n",
        encoding="utf-8",
    )
    records = run_golden(str(golden), caller=stub_caller, searcher=stub_searcher)
    assert len(records) == 2
    assert {r["anchor_id"] for r in records} == {"rn-build-1", "rn-esc-1"}
    assert records[1]["route"] == "escalate"
