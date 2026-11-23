"""★AC10/AC16 — eval 격리 메커니즘(C1) falsifiable 단언(라이브 0).

검증(ORCH_LLM_PRIMARY=azure 가 설정된 상태에서):
  - run_anchor/run_golden 의 *기본 경로*(caller=None)가 **로컬 gemma**(graph._gemma_caller)를
    바인딩한다 — Azure-wrapped 기본(graph._ACTIVE_CALLER)에 *도달하지 않는다*.
  - gate.score_judge_majority 의 *기본 경로*(caller=None)가 **로컬 qwen judge**
    (deliberation._judge_caller)를 바인딩한다 — Azure-wrapped 기본 미도달.
  - 두 경로 모두 azure_caller 가 **단 한 번도** 호출되지 않는다(served-by=local 등가).

★negative-mutation(AC16): 격리를 *제거*하면(use_caller 미바인딩) azure_caller 가 도달함을
  실증해, 본 테스트가 격리 제거 시 *하드 실패*함을 보장(green-theater 차단).

라이브 0: _gemma_caller/_judge_caller 를 결정론 spy 로 치환하고, azure_caller 는 호출되면
폭발(AssertionError)하도록 설정한다. graph 노드는 I/O-0 이라 stub caller 로 완주.
"""

from __future__ import annotations

import json

import pytest

import orch.azure_llm as az
import orch.deliberation as deliberation
import orch.evals.gate as gate
import orch.evals.runner as runner


class _AzureSpy:
    """azure_caller 호출 카운터. 래퍼가 ``except Exception`` 으로 raise 를 fallback 으로
    삼키므로(설계), '도달 여부'는 예외 전파가 아니라 **호출 카운트**로 단언한다."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, prompt, temperature, max_tokens):
        self.count += 1
        # 도달 시 fallback 으로 흘려보내 그래프가 완주하게 한다(카운트는 이미 기록됨).
        raise RuntimeError("azure reached (spy)")


@pytest.fixture
def _azure_spy(monkeypatch):
    """ORCH_LLM_PRIMARY=azure + Azure 구성됨 → *격리가 없으면* azure_caller 가 도달한다.

    반환된 spy.count 로 도달 여부를 단언(0=미도달=격리 성립·≥1=도달=격리 부재).
    """
    monkeypatch.setenv("ORCH_LLM_PRIMARY", "azure")
    monkeypatch.setattr(az, "azure_configured", lambda env=None: True)
    spy = _AzureSpy()
    monkeypatch.setattr(az, "azure_caller", spy)
    return monkeypatch, spy


# ── 결정론 로컬 spy(라이브 gemma/qwen 미접촉) ─────────────────────────────────────────
def _local_gemma_spy():
    calls: list[str] = []

    def _spy(prompt: str, temperature: float, max_tokens: int) -> str:
        calls.append(prompt)
        p = prompt.lower()
        if "ceo router" in p:
            return "ROUTE: build | clicker requested"
        if "design stage" in p:
            return "core loop: click."
        if "content stage" in p:
            return "Name: X. items: A(10)"
        if "qa stage" in p:
            return "check: ok. VERDICT: PASS"
        if "bluf" in p:
            return "결론: ok.\n필요행동: 없음.\n근거:\n- research: x"
        return "stub"

    return _spy, calls


_ANCHOR = {
    "id": "iso-1",
    "type": "agent_task",
    "graph": "cycle",
    "input": "make a clicker game",
    "expected": {"completion": ["결론"]},
    "expects_tool": True,
    "expected_tool": "knowledge_search",
}


def _stub_searcher(query: str, k: int) -> dict:
    return {
        "results": [
            {"text": "f2p engagement", "source": "https://x.test/a", "score": -0.1}
        ],
        "count": 1,
    }


# ── AC10 (i)+(ii): run_anchor 기본 경로 = 로컬 gemma·azure 호출 0 ──────────────────────
def test_run_anchor_default_path_binds_local_gemma(_azure_spy):
    monkeypatch, spy = _azure_spy
    gspy, calls = _local_gemma_spy()
    # runner 가 import 한 로컬 심볼을 spy 로 치환(기본 caller=None → 이 심볼이 바인딩됨).
    monkeypatch.setattr(runner, "_gemma_caller", gspy)
    # caller 미지정(None) = 라이브 default 경로.
    cap = runner.run_anchor(_ANCHOR, caller=None, searcher=_stub_searcher)
    assert calls, "local gemma spy was never called on the default path"
    assert cap["route"] == "build"  # 로컬 spy 출력으로 라우팅 완주(azure 미도달)
    assert spy.count == 0  # ★격리: azure_caller 단 한 번도 도달 0(AC10 ii)


def test_run_golden_default_path_binds_local_gemma(_azure_spy, tmp_path):
    monkeypatch, spy = _azure_spy
    gspy, calls = _local_gemma_spy()
    monkeypatch.setattr(runner, "_gemma_caller", gspy)
    golden = tmp_path / "g.jsonl"
    golden.write_text(json.dumps(_ANCHOR) + "\n", encoding="utf-8")
    # run_golden 은 caller=None 을 run_anchor 로 전달 → 앵커별 로컬 기본이 적용된다.
    recs = runner.run_golden(str(golden), caller=None, searcher=_stub_searcher)
    assert len(recs) == 1 and recs[0]["route"] == "build"
    assert calls and spy.count == 0  # azure 미도달


# ── AC10 judge: gate 기본 경로 = 로컬 qwen judge·azure 호출 0 ──────────────────────────
def test_gate_judge_default_path_binds_local_judge(_azure_spy):
    monkeypatch, spy = _azure_spy
    judge_calls: list[str] = []

    def _local_judge_spy(prompt: str, temperature: float, max_tokens: int) -> str:
        judge_calls.append(prompt)
        return "VERDICT: PASS\nok"

    # gate 는 함수 내부에서 deliberation._judge_caller 를 기본 로컬로 바인딩한다 → 치환.
    monkeypatch.setattr(deliberation, "_judge_caller", _local_judge_spy)
    cap = {
        "anchor_id": "iso-1",
        "type": "routing",
        "route": "build",
        "bluf": "ok",
        "tool_calls": [],
        "search_results": [],
    }
    # caller=None = 기본 경로.
    out = gate.score_judge_majority([cap], k=1, m=1, caller=None)
    assert judge_calls, "local judge spy was never called on the default path"
    assert out["total"] > 0
    assert spy.count == 0  # ★격리: judge 기본도 azure 미도달(AC10)


# ── ★AC16 negative-mutation: 격리 제거 시 azure 가 도달함을 실증(테스트 변별력 보증) ──────
def test_negative_mutation_without_isolation_azure_is_reached(_azure_spy):
    """격리(use_caller 로컬 바인딩)를 *건너뛰면* Azure-wrapped 기본이 도달함을 실증.

    위 테스트들이 '격리가 실제로 있을 때만' 통과함을 보장한다 — 격리를 제거하면
    azure_caller 가 도달(spy.count≥1)하므로 spy.count==0 단언이 *깨진다*(falsifier).
    래퍼는 raise 를 fallback 으로 삼키므로 '도달'은 카운트로 측정한다(예외 전파 아님).
    """
    _monkeypatch, spy = _azure_spy
    # graph 가 기본에 쓰는 것과 *동일한* 래퍼(make_primary_fallback_caller)를, 라이브 gemma 를
    # 안 때리도록 명시 stub fallback 으로 새로 만들어 직접 호출한다(import-시 캡처된 graph
    # 기본의 fallback 은 실 _gemma_caller 라 네트워크를 칠 수 있으므로 회피). 격리가 없으면
    # (= use_caller 로 로컬을 덮어쓰지 않으면) 이 Azure-wrapped 경로가 그대로 실행된다.
    wrapped = az.make_primary_fallback_caller(
        lambda p, t, m: "local-fallback", label="neg"
    )
    out = wrapped(
        "ceo router: route this", 0.0, 16
    )  # azure 도달 → spy.count++ → fallback
    assert spy.count >= 1, "azure should be reached when isolation is absent (AC16)"
    assert out == "local-fallback"  # 래퍼가 fallback 으로 graceful 복구
