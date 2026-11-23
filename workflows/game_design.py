"""game_design 워크플로 정의 — G005 US-G5-3 (planner·선형 게임 설계 → meta.json).

노드 구성:
  n1_design_concept  (longform · thinking=on)  게임 컨셉·테마·코어루프·승패·retry 과금 설계
  n2_write_meta      (tool · thinking=off · game_data_write)  설계를 meta.json으로 기록

흐름: n1_design_concept → n2_write_meta → 종료

설계(.omc/prd.json US-G5-3 AC1·research_summary 선형 모델):
- ★결정론 선형 DAG(2노드). agent 노드 0 — research_summary 패턴과 동형으로
  chat/longform/tool만 쓴다(자율 루프 없음). 워크플로 export = 모듈 전역
  NODES: list[Node] + WORKFLOW_NAME: str 두 심볼. next 문자열로 체이닝, 마지막
  노드 next=None.
- n1_design_concept(longform·thinking=on): planner(강하은)·strategist(이서연)
  역할의 게임 설계 단계 — 테마·제목·코어루프·승리/패배 조건·retry(컨티뉴) 과금
  정책(freeRetries·costCurve·sessionCap)을 설계한다. 산출은 다음 노드가 meta.json
  으로 옮길 설계 텍스트(carryforward).
- n2_write_meta(tool·game_data_write): 설계를 schema_contract meta.json 형태
  (theme/title/coreLoop/win/lose/continue{freeRetries,costCurve,sessionCap})로
  game/data/meta.json에 기록한다. tools=[GAME_DATA_WRITE_SCHEMA]만 선언하고 args
  (path·content)는 런타임에 LLM이 JSON으로 추출한다(engine.py:480 _last_extracted).
- 새 이벤트 0: n1은 TEXT_MESSAGE(기존), n2는 TOOL_CALL_*(기존)만 사용 →
  EVENT_REGISTRY 11종 유지. game_data_write는 always-allow(가역·로컬 파일쓰기)라
  게이팅 suspend 없이 자율 기록(tools_policy.yaml 등록 — US-G5-1).
- ★node id는 안정적(n1_design_concept·n2_write_meta) — US-G5-7에서 역할 매핑에
  쓰이므로 변경 금지.
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import GAME_DATA_WRITE_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_design_concept",
    kind="longform",
    prompt_file="prompts/game_design/n1_design_concept.txt",
    thinking="on",  # 설계 추론 — reasoning 동반(planner 판단)
    next="n2_write_meta",
)

N2 = Node(
    id="n2_write_meta",
    kind="tool",
    prompt_file="prompts/game_design/n2_write_meta.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2]
WORKFLOW_NAME = "game_design"
