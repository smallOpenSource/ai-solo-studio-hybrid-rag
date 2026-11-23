"""워크플로 노드 정의 — Node dataclass + 노드 종류별 max_tokens 상수.

계획 §4.4.5·§4.6 (수용 기준 4, US-004 T4a).

설계 원칙:
- MAX_TOKENS_BY_KIND가 노드 종류별 max_tokens의 유일한 거처.
  models.yaml 아님 — models.yaml은 모델별이고 이 값은 노드 종류에 따른다.
- 32k 일괄 금지 — 전 값 ≤ 16384.
- kind="agent"는 Phase 6 6b부터 8192 (단일호출 출력 상한 — 누적 입력 상한과 별개).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ── 노드 종류별 최대 생성 토큰 (유일한 거처, 32k 금지) ────────────────────────
# tool    : 도구호출 JSON 생성 — 짧고 구조적, 512로 충분
# chat    : 대화·판단·요약 — 중간 길이
# longform: 장문 생성 (리포트·이메일 본문 등)
# coding  : 코드 생성 — 길 수 있음
# agent   : Phase 6 6b 자율 루프 — 단일호출 *출력* 상한(누적 입력 상한과 별개·≤16384).
MAX_TOKENS_BY_KIND: dict[str, int] = {
    "tool": 512,
    "chat": 4096,
    "longform": 8192,
    "coding": 16384,
    "agent": 8192,
}


@dataclass
class Node:
    """워크플로의 한 단계를 정의하는 노드.

    필드:
        id          : 노드 고유 식별자 (워크플로 내 유일)
        kind        : 노드 종류 — thinking·max_tokens 기본값을 결정
        prompt_file : 프롬프트 파일 경로 (prompts/ 하위, GEPA 교체 자리)
        prompt      : 인라인 프롬프트 (prompt_file 없을 때 사용)
        model       : 사용할 모델 alias. None이면 config 기본 모델.
        thinking    : "on" | "off" — 기본 "off"
        tools       : tool 노드용 도구 스키마 목록
        next        : 다음 노드 id. None이면 워크플로 종료.
        expected    : 사후검증 스펙 {"name": str, "args": dict} (tool 노드용, 선택)
        max_tokens  : 이 노드의 max_tokens 오버라이드. None이면 MAX_TOKENS_BY_KIND 사용.
    """

    # 필수 필드
    id: str
    kind: Literal["tool", "chat", "longform", "coding", "agent"]

    # 프롬프트 (둘 중 하나)
    prompt_file: str | None = None
    prompt: str | None = None

    # LLM 설정
    model: str | None = None
    thinking: str = "off"

    # 도구 목록 (tool 노드용)
    tools: list[dict[str, Any]] = field(default_factory=list)

    # 그래프 연결
    next: str | None = None

    # 사후검증 스펙 (선택)
    expected: dict[str, Any] | None = None

    # max_tokens 오버라이드 (None이면 MAX_TOKENS_BY_KIND[kind] 사용)
    max_tokens: int | None = None

    def resolve_max_tokens(self) -> int:
        """이 노드에 적용할 max_tokens를 반환한다.

        오버라이드 값이 있으면 우선 사용하고, 없으면 MAX_TOKENS_BY_KIND를 따른다.
        kind="agent"도 Phase 6 6b부터 8192(MAX_TOKENS_BY_KIND["agent"])로 지원한다.
        """
        if self.max_tokens is not None:
            return self.max_tokens
        return MAX_TOKENS_BY_KIND[self.kind]
