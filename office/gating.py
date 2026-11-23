"""위험기반 게이팅 + Plan Mode — US-006 (T5).

계획 §4.3·§4.4 (수용 기준 5·6, v5.1 §4.3).

핵심 개념 (쉬운 말):
- 도구를 실제로 실행하기 직전에 "이 행동을 그냥 해도 되나, 사람에게 물어봐야
  하나, 아예 금지인가"를 판정한다. 판정 결과가 verdict.
- 판정은 두 가지를 본다:
    (1) 도구의 위험 등급 (tools_policy.yaml): always-allow / needs-approval / block
    (2) when 술어: needs-approval이라도 "이번 호출"이 정말 멈출 만한지 한 번 더 검사
       (술어가 없으면 등급만으로 판정 — needs-approval이면 무조건 멈춤)

- 게이팅 지점이 노드 실행 "전"이 아니라 도구 실행 "직전"인 이유:
    when 술어와 승인 payload에는 도구 인자가 필요한데, 인자는 LLM이 뽑은 뒤에야
    생긴다. 그래서 LLM 추출(state["_last_extracted"])이 끝난 시점에 판정한다.
    (US-004의 pre-node bool 훅은 placeholder였고 여기서 새 설계로 교체된다.)

- Plan Mode: 워크플로 시작 때 전체 계획을 사람이 한 번에 승인했다는 전제 모드.
    이 모드에서는 interrupt 판정을 allow로 격상한다. 단, block은 그대로 block
    (어떤 승인으로도 금지 도구는 통과 불가).

INTERRUPT_REQUESTED payload 계약 (approve.py §4.4 — 어기면 영구 suspended):
    InterruptRequested.payload = {
        "task_id" : int | None  — TaskQueue row id (resume용),
        "args"    : dict        — 정화된 도구 인자,
        "tool"    : str,
        "node_id" : str,
        "risk"    : str,
        "accumulated": dict,    — 재개용 캐리포워드 누적
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import yaml

# tools_policy.yaml 위치 — core/config.py와 같은 루트 계산 방식
_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_POLICY_PATH = _PROJECT_ROOT / "config" / "tools_policy.yaml"


# ── 위험 등급 상수 ────────────────────────────────────────────────────────────
RISK_ALWAYS_ALLOW = "always-allow"
RISK_NEEDS_APPROVAL = "needs-approval"
RISK_BLOCK = "block"


# ── 판정 결과 ────────────────────────────────────────────────────────────────


class Verdict(str, Enum):
    """게이팅 판정 결과.

    allow     — 그냥 도구 실행 진행
    interrupt — 사람 승인 필요 → INTERRUPT_REQUESTED 발행 후 suspend
    block     — 금지. Plan Mode로도 통과 불가 → NODE_FAILED(gating)+rejected
    """

    allow = "allow"
    interrupt = "interrupt"
    block = "block"


# ── 도구 호출 요청 ────────────────────────────────────────────────────────────


@dataclass
class ToolCallRequest:
    """게이팅이 판정하는 단위 — "이 도구를 이 인자로 부르려 한다"는 요청.

    LLM이 추출한 도구 이름·인자(정화 완료)와 어느 노드/실행에서 났는지를 담는다.
    when 술어가 이 요청을 받아 멈출지(True)/통과(False)를 답한다.
    """

    # 호출하려는 도구 이름
    name: str
    # 정화된 도구 인자
    args: dict[str, Any] = field(default_factory=dict)
    # 이 호출을 낸 노드 id
    node_id: str = ""
    # 이번 실행(run) 식별자
    run_id: str = ""


# ── when 술어 레지스트리 ──────────────────────────────────────────────────────
# 술어는 ToolCallRequest를 받아 "멈춰 세울까?"를 bool로 답한다 (True=멈춤).
# tools_policy.yaml의 when 값(문자열)이 이 dict의 키와 연결된다.


def _always_interrupt(request: ToolCallRequest) -> bool:
    """항상 멈춘다 (어떤 인자든 사람 승인 요구)."""
    return True


WHEN_PREDICATES: dict[str, Callable[[ToolCallRequest], bool]] = {
    "always_interrupt": _always_interrupt,
}


# ── 게이팅 정책 ───────────────────────────────────────────────────────────────


class GatingPolicy:
    """tools_policy.yaml을 로드해 도구별 위험 등급·when 술어로 판정한다.

    사용 예:
        policy = GatingPolicy()                          # 기본 정책 파일 로드
        req = ToolCallRequest(name="email_send", args={...}, node_id="n3")
        verdict = policy.evaluate(req)                    # Verdict.interrupt
        verdict = policy.evaluate(req, plan_mode=True)    # Verdict.allow (격상)
    """

    def __init__(self, policy_path: str | Path | None = None) -> None:
        """게이팅 정책 초기화.

        Args:
            policy_path: tools_policy.yaml 경로. None이면 config/tools_policy.yaml.
                         테스트는 tmp 경로로 임의 정책을 주입할 수 있다.
        """
        path = Path(policy_path) if policy_path is not None else _DEFAULT_POLICY_PATH
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}

        # 도구 이름 → {risk, when?} 매핑
        self._tools: dict[str, dict[str, Any]] = data.get("tools") or {}
        # 미등록 도구의 기본 등급 — 안전 우선(모르는 도구는 승인 요구)
        self._default_risk: str = data.get("default_risk", RISK_NEEDS_APPROVAL)

    def risk_of(self, tool_name: str) -> str:
        """도구의 위험 등급을 반환한다 (미등록이면 default_risk)."""
        entry = self._tools.get(tool_name)
        if entry is None:
            return self._default_risk
        return entry.get("risk", self._default_risk)

    def _when_predicate(
        self, tool_name: str
    ) -> Callable[[ToolCallRequest], bool] | None:
        """도구의 when 술어 함수를 반환한다. when이 없거나 미등록이면 None.

        when 이름이 레지스트리에 없으면 안전 우선으로 "항상 멈춤" 취급한다
        (정책 파일에 오타가 나도 위험 도구가 새지 않도록).
        """
        entry = self._tools.get(tool_name)
        if entry is None:
            return None
        when_name = entry.get("when")
        if when_name is None:
            return None
        return WHEN_PREDICATES.get(when_name, _always_interrupt)

    def evaluate(
        self,
        request: ToolCallRequest,
        plan_mode: bool = False,
    ) -> Verdict:
        """도구 호출 요청을 판정해 Verdict를 반환한다.

        판정 순서:
        1. block 등급 → 무조건 Verdict.block (Plan Mode로도 통과 불가).
        2. always-allow 등급 → Verdict.allow.
        3. needs-approval 등급(또는 기타):
           - when 술어가 있으면 술어 결과로 멈출지 결정(False면 allow).
           - 술어가 없으면 무조건 멈춤(interrupt).
           - Plan Mode면 interrupt를 allow로 격상.

        Args:
            request:   판정할 도구 호출 요청.
            plan_mode: True면 사전 일괄 승인 모드 — interrupt를 allow로 격상.
                       단 block은 그대로 block.

        Returns:
            Verdict (allow / interrupt / block).
        """
        risk = self.risk_of(request.name)

        # 1) block — 어떤 모드로도 통과 불가 (Plan Mode보다 우선)
        if risk == RISK_BLOCK:
            return Verdict.block

        # 2) always-allow — 자동 통과
        if risk == RISK_ALWAYS_ALLOW:
            return Verdict.allow

        # 3) needs-approval (또는 기타 등급) — when 술어로 한 번 더 검사
        predicate = self._when_predicate(request.name)
        should_interrupt = predicate(request) if predicate is not None else True

        if not should_interrupt:
            # 술어가 "이번엔 멈출 필요 없음"이라 답함
            return Verdict.allow

        # 멈춰야 함 — 단 Plan Mode면 일괄 승인됐으므로 allow로 격상
        if plan_mode:
            return Verdict.allow
        return Verdict.interrupt
