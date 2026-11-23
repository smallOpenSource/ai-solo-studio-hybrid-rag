"""game_qa 워크플로 정의 — G005 US-G5-6 (QA·선형 결정론 게이트).

노드 구성(★안정적 id — US-G5-7 역할 매핑용·변경 금지):
  n1_validate    (tool · game_validate)     game/data 6파일 스키마 + 참조 무결성 검증
  n2_write_report(tool · game_data_write)   결과를 data/qa_report.json{pass,errors}로 기록

흐름: n1_validate → n2_write_report → 종료

설계(.omc/prd.json US-G5-6 AC4·game_balance 선형 모델):
- ★결정론 선형 DAG(2노드). agent 노드 0 — tool만 쓴다(자율 루프 없음). 워크플로 export =
  모듈 전역 NODES: list[Node] + WORKFLOW_NAME: str 두 심볼. next 문자열로 체이닝, 마지막
  노드 next=None.
- n1_validate(tool·game_validate): office.game_schema.validate_all로 game/data의 6파일을
  로드해 각 validate_* + cross-file 참조 무결성(waves.spawn.enemyId∈enemies.id)을
  검증한다. ★베이스라인 데이터=ok:true, 고의 깨진 데이터=ok:false 결정론(빌드성공 ≠
  플레이가능의 데이터 측 게이트).
- n2_write_report(tool·game_data_write): 검증 결과를 data/qa_report.json
  ({pass:bool, errors:list})으로 기록한다. tools=[GAME_DATA_WRITE_SCHEMA]만 선언하고
  args(path·content)는 런타임에 LLM이 n1 결과(carryforward)를 바탕으로 JSON으로
  추출한다(engine.py:480 _last_extracted).
- 새 이벤트 0: 두 노드 모두 tool → TOOL_CALL_*(기존)만 사용 → EVENT_REGISTRY 11종 유지.
  game_validate·game_data_write는 always-allow(읽기전용/가역)라 게이팅 suspend 없이
  자율 실행(tools_policy.yaml 등록 — US-G5-1·US-G5-6).
- ★플레이가능 게이트의 데이터 측 표현: 이 워크플로는 game/data가 schema_contract를
  만족하는지(런타임이 안전하게 소비 가능한지)를 결정론으로 게이팅한다. 헤드리스
  플레이가능 단언(600틱·30초 생존·레벨업·웨이브·엔티티상한)은 game/src/__tests__/
  playable.test.ts(프론트 게이트)가 담당한다 — 두 게이트가 합쳐 "빌드성공 ≠ 플레이가능"을
  막는다.
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import GAME_DATA_WRITE_SCHEMA, GAME_VALIDATE_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_validate",
    kind="tool",
    prompt_file="prompts/game_qa/n1_validate.txt",
    thinking="off",
    tools=[GAME_VALIDATE_SCHEMA],
    next="n2_write_report",
)

N2 = Node(
    id="n2_write_report",
    kind="tool",
    prompt_file="prompts/game_qa/n2_write_report.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2]
WORKFLOW_NAME = "game_qa"
