"""orch/intent.py 결정론 단위 — directive 분류기(LLM 0) + thread_id 그래프 마커."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

import orch.intent as it
from orch.intent import (
    CYC_PREFIX,
    ORG_PREFIX,
    classify_directive,
    graph_kind_of,
    kind_for_intent,
    make_thread_id,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("클리커 게임 빌드 스크립트 점검", "game"),
        ("신규 게임 에셋 컨셉 잡아줘", "game"),
        ("레벨 밸런스 조정", "game"),
        ("QA 통과시켜줘", "game"),
        ("콘텐츠 추가", "game"),
        ("make a clicker game build", "game"),
        ("신규 캐주얼 게임 시장 트렌드를 조사", "org"),  # org 키워드 우선
        ("부서 전략 보고서 작성", "org"),
        ("시장 리서치 해줘", "org"),
        ("competitor market research", "org"),
        ("조직 구조 분석", "org"),
        ("", "game"),  # 불명확 → game
        ("그냥 아무거나 처리해줘", "game"),  # 불명확 → game
    ],
)
def test_classify_directive(text, expected):
    assert classify_directive(text) == expected


def test_kind_for_intent():
    assert kind_for_intent("org") == "org"
    assert kind_for_intent("game") == "cycle"


def test_make_thread_id_prefixes_and_roundtrip():
    assert make_thread_id("org").startswith(ORG_PREFIX)
    assert make_thread_id("cycle").startswith(CYC_PREFIX)
    assert graph_kind_of(make_thread_id("org")) == "org"
    assert graph_kind_of(make_thread_id("cycle")) == "cycle"


def test_graph_kind_of_legacy_and_unknown():
    bare = str(uuid.uuid4())  # 무접두 레거시(off 시절) → cycle
    assert graph_kind_of(bare) == "cycle"
    assert graph_kind_of("org:abc") == "org"
    assert graph_kind_of("cyc:abc") == "cycle"
    with pytest.raises(ValueError):  # 미지 접두사 → 무음 cycle 폴백 금지
        graph_kind_of("bogus:xyz")


def test_intent_module_has_no_llm():
    """핫패스 분류기 — LLM/네트워크 호출 0(gemma-affinity 보존)."""
    src = Path(it.__file__).read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(
        r"ChatOpenAI|_active_caller|use_caller|ainvoke|requests|httpx|openai", code
    )
