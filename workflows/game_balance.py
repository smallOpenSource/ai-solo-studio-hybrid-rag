"""game_balance 워크플로 정의 — G005 US-G5-4 (data·선형 밸런스 수치 설계).

노드 구성(★안정적 id — US-G5-7 역할 매핑용·변경 금지):
  n1_read_meta       (tool · game_data_read · required=True)  이전 단계 meta.json 읽기(입력 계약)
  n2_design_numbers  (longform)                                meta 바탕 적/무기/웨이브/업그레이드 수치 설계
  n3_write_enemies   (tool · game_data_write)                  enemies.json 기록
  n4_write_weapons   (tool · game_data_write)                  weapons.json 기록
  n5_write_waves     (tool · game_data_write)                  waves.json 기록(참조 무결성 enemyId∈enemies.id)
  n6_write_upgrades  (tool · game_data_write)                  upgrades.json 기록
  n7_write_balance   (tool · game_data_write)                  수치범위 검증 리포트 balance.json 기록

흐름: n1 → n2 → n3 → n4 → n5 → n6 → n7 → 종료

설계(.omc/prd.json US-G5-4 AC1·game_design 선형 모델):
- ★결정론 선형 DAG(7노드). agent 노드 0 — game_design/research_summary 패턴과 동형으로
  chat/longform/tool만 쓴다(자율 루프 없음). 워크플로 export = 모듈 전역
  NODES: list[Node] + WORKFLOW_NAME: str 두 심볼. next 문자열로 체이닝, 마지막
  노드 next=None.
- ★입력 계약(AC2): 첫 노드 n1_read_meta는 game_data_read를 required=True로 호출한다.
  meta.json(game/data/)이 없으면 game_data_read가 FileNotFoundError를 raise하고, 엔진이
  이를 error dict로 받은 뒤 NODE_FAILED로 드러내 워크플로가 **성공하지 않는다**(수동
  체이닝 계약 — 이전 설계 단계 미완이면 밸런스 단계가 명시 실패로 진행 거부). 이 계약이
  프로덕션에서 성립하려면 엔진이 error_fails_node=True여야 한다 — run.py/worker.py가 game
  워크플로(GAME_WORKFLOWS)에 한해 그렇게 띄운다(g5-4-5-review Major-1). game_data_read·
  tool 예외 전파는 office/engine.py:1279(예외→error dict)·1294(error_fails_node→
  NODE_FAILED) 참조.
- n2_design_numbers(longform·thinking=on): data(윤시우) 역할의 수치 설계 단계 —
  meta(테마·코어루프·승패)를 근거로 적/무기/웨이브/업그레이드 수치를 설계한다(carryforward).
- n3~n6(tool·game_data_write): 설계 수치를 각 schema_contract 파일로 기록한다. 각 content는
  validate_enemies/weapons/waves/upgrades ok:true·참조 무결성(waves.spawn.enemyId∈enemies.id)
  을 충족해야 한다. tools=[GAME_DATA_WRITE_SCHEMA]만 선언하고 args(path·content)는 런타임에
  LLM이 JSON으로 추출한다(engine.py:480 _last_extracted).
- n7_write_balance(tool·game_data_write): 수치범위 검증 리포트를 balance.json
  (survivalTargetSec/dpsCurve(비감소)/inRange/report)으로 기록한다.
- 새 이벤트 0: tool 노드는 TOOL_CALL_*(기존), longform 노드는 TEXT_MESSAGE(기존)만 사용 →
  EVENT_REGISTRY 11종 유지. game_data_read·game_data_write는 always-allow(읽기/로컬 파일쓰기
  =가역)라 게이팅 suspend 없이 자율 실행(tools_policy.yaml 등록 — US-G5-1·US-G5-4).
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import GAME_DATA_READ_SCHEMA, GAME_DATA_WRITE_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_read_meta",
    kind="tool",
    prompt_file="prompts/game_balance/n1_read_meta.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 meta.json 읽기(입력 계약)
    next="n2_design_numbers",
)

N2 = Node(
    id="n2_design_numbers",
    kind="longform",
    prompt_file="prompts/game_balance/n2_design_numbers.txt",
    thinking="on",  # 수치 설계 추론 — reasoning 동반(data 판단)
    next="n3_write_enemies",
)

N3 = Node(
    id="n3_write_enemies",
    kind="tool",
    prompt_file="prompts/game_balance/n3_write_enemies.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next="n4_write_weapons",
)

N4 = Node(
    id="n4_write_weapons",
    kind="tool",
    prompt_file="prompts/game_balance/n4_write_weapons.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next="n5_write_waves",
)

N5 = Node(
    id="n5_write_waves",
    kind="tool",
    prompt_file="prompts/game_balance/n5_write_waves.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next="n6_write_upgrades",
)

N6 = Node(
    id="n6_write_upgrades",
    kind="tool",
    prompt_file="prompts/game_balance/n6_write_upgrades.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next="n7_write_balance",
)

N7 = Node(
    id="n7_write_balance",
    kind="tool",
    prompt_file="prompts/game_balance/n7_write_balance.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2, N3, N4, N5, N6, N7]
WORKFLOW_NAME = "game_balance"
