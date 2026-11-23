"""research_summary 워크플로 정의 — Phase 8 C (리서치-요약 2노드).

노드 구성:
  N1  retrieve   (tool · thinking=OFF · knowledge_search)
  N2  summarize  (chat · thinking=ON)

흐름: N1 → N2 → 종료

설계(계획 ralplan-phase8-research-summary.md §워크플로 설계 C):
- ★결정론 선형 DAG(2노드 — deliver 제거). 사용자의 리서치 질문은 engine.py:212
  messages seam으로 N1 LLM에 주입되고(워커 _run_one initial_state["messages"]),
  N1이 질문에서 knowledge_search query를 도출한다(프롬프트는 일반화 — 특정 질문
  하드코딩 0).
- N2 summarize는 검색 결과(source)만 근거로 요약하고 각 주장에 source를 인용한다.
  검색 결과가 없으면 "근거 없음"을 명시한다(환각 가드 — 계획 시나리오 3).
- 새 이벤트 0: N1은 TOOL_CALL_*, N2는 TEXT_MESSAGE(기존) 사용 → EVENT_REGISTRY
  11종 유지. 결과는 TEXT_MESSAGE 영속(event_store)으로 신규코드 0 도달(SSE·
  read_since·RUN_FINISHED-Notify).
- knowledge_search 실 바인딩 스왑(cross-env qdrant 해소·옵션 B)은 P 담당이며 이
  파일은 스키마(KNOWLEDGE_SEARCH_SCHEMA)만 참조한다(tools.py 무수정).
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import KNOWLEDGE_SEARCH_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_retrieve",
    kind="tool",
    prompt_file="prompts/research_summary/n1_retrieve.txt",
    thinking="off",
    tools=[KNOWLEDGE_SEARCH_SCHEMA],
    next="n2_summarize",
)

N2 = Node(
    id="n2_summarize",
    kind="chat",
    prompt_file="prompts/research_summary/n2_summarize.txt",
    thinking="on",  # 추론 모델(qwen) 권고 — reasoning_chars>0 (AC-C5)
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2]
WORKFLOW_NAME = "research_summary"
