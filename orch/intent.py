"""orch/intent.py — directive 분류 + thread_id 그래프 마커(mint/parse 인접).

coexist 라우팅 2요소를 **한 모듈**에 둔다(리터럴 드리프트 차단):
1. ``classify_directive`` — directive 텍스트 → ``"game"|"org"`` (순수·결정론·**LLM 0**·핫패스
   gemma-affinity 보존). 불명확 → ``game``(현 동작 보존 편향·단일 규칙).
2. ``make_thread_id`` / ``graph_kind_of`` — 그래프 종류를 thread_id **접두사**(``cyc:``/``org:``)로
   새기고 읽는다. 접두사는 체크포인트 thread_id에 담겨 **재시작 내성**(인메모리 사이드테이블과 달리
   orch 재시작 후에도 보존) → resume이 시작 그래프를 정확히 재선택.
"""

from __future__ import annotations

import uuid
from typing import Literal

CYC_PREFIX = "cyc:"
ORG_PREFIX = "org:"

GraphKind = Literal["cycle", "org"]
Intent = Literal["game", "org"]

# 키워드(소문자 부분일치). org 가 game 보다 우선(예: "게임 시장조사"=org).
_ORG_KW = (
    "조사",
    "research",
    "리서치",
    "시장",
    "market",
    "조직",
    "부서",
    "department",
    "전략",
    "strategy",
    "보고서",
    "report",
)
_GAME_KW = (
    "빌드",
    "build",
    "게임",
    "game",
    "에셋",
    "asset",
    "qa",
    "balance",
    "밸런스",
    "content",
    "콘텐츠",
    "레벨",
    "level",
    "sprite",
    "스프라이트",
)


def classify_directive(text: str) -> Intent:
    """directive → ``"game"|"org"`` (순수·LLM 0). org 키워드 우선·불명확 → ``game``."""
    t = (text or "").lower()
    if any(k in t for k in _ORG_KW):
        return "org"
    if any(k in t for k in _GAME_KW):
        return "game"
    return "game"  # 불명확 기본 = game(현 동작 보존 편향·단일 규칙)


def kind_for_intent(intent: Intent) -> GraphKind:
    return "org" if intent == "org" else "cycle"


def make_thread_id(kind: GraphKind) -> str:
    """그래프 종류 접두사 + uuid4. (``off`` 모드는 무접두 bare uuid를 별도로 사용.)"""
    prefix = ORG_PREFIX if kind == "org" else CYC_PREFIX
    return f"{prefix}{uuid.uuid4()}"


def graph_kind_of(thread_id: str) -> GraphKind:
    """thread_id 접두사 → 그래프 종류. 무접두(레거시) → ``cycle``. 미지 접두사 → 거부(raise)."""
    if thread_id.startswith(ORG_PREFIX):
        return "org"
    if thread_id.startswith(CYC_PREFIX):
        return "cycle"
    if ":" in thread_id:  # 알 수 없는 접두사 — 무음 cycle 폴백 금지(마커 보장)
        raise ValueError("unknown graph prefix in thread_id")
    return "cycle"  # 무접두 레거시(off 시절 bare uuid) → cycle(현 동작)
