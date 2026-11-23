"""game_assemble 워크플로 정의 — G005 US-G5-6 (dev·선형 런타임 조립).

노드 구성(★전역 유일 id — US-G5-7 역할 매핑용·변경 금지. ★asm_ 프리픽스로 game_balance의
n1_read_meta와의 node id 충돌을 해소한다 — pickRoleKey(node_id)는 node_id 기준이므로
같은 id가 두 워크플로에 있으면 한 역할로만 매핑돼 game_assemble의 read 노드가 엉뚱한
역할로 표시되는 문제를 방지한다):
  asm_read_meta      (tool · game_data_read · required=True)  meta.json 읽기(입력 계약)
  asm_read_enemies   (tool · game_data_read · required=True)  enemies.json 읽기(입력 계약)
  asm_read_weapons   (tool · game_data_read · required=True)  weapons.json 읽기(입력 계약)
  asm_read_waves     (tool · game_data_read · required=True)  waves.json 읽기(입력 계약)
  asm_read_upgrades  (tool · game_data_read · required=True)  upgrades.json 읽기(입력 계약)
  asm_assemble       (coding)                                  런타임 바인딩 검토/조립 노트
  asm_write_assembly (tool · game_data_write)                  data/assembly.json{ready:true,...}

흐름: asm_read_meta → asm_read_enemies → asm_read_weapons → asm_read_waves
      → asm_read_upgrades → asm_assemble → asm_write_assembly → 종료

설계(.omc/prd.json US-G5-6 AC4·game_balance 선형 모델):
- ★결정론 선형 DAG(7노드). agent 노드 0 — game_balance/game_content 패턴과 동형으로
  tool/coding만 쓴다(자율 루프 없음). 워크플로 export = 모듈 전역 NODES: list[Node] +
  WORKFLOW_NAME: str 두 심볼. next 문자열로 체이닝, 마지막 노드 next=None.
- ★입력 계약(AC4): n1~n5는 game_data_read를 required=True로 호출한다. 5개 데이터 파일
  (meta/enemies/weapons/waves/upgrades) 중 하나라도 없으면 game_data_read가
  FileNotFoundError를 raise하고 엔진이 이를 error dict로 받은 뒤 NODE_FAILED로 드러내
  워크플로가 **성공하지 않는다**(이전 설계/밸런스/콘텐츠 단계 미완이면 조립 단계가 명시
  실패로 진행 거부 — 수동 체이닝 계약). 이 계약이 프로덕션에서 성립하려면 엔진이
  error_fails_node=True여야 한다 — run.py/worker.py가 game 워크플로(GAME_WORKFLOWS)에
  한해 그렇게 띄운다(g5-4-5-review Major-1).
- n6_assemble(coding·thinking=on): dev(한지훈) 역할의 런타임 바인딩 검토 단계 —
  읽어온 데이터가 game/src/sim 런타임(createSim/tick)이 소비하는 형태로 조립 가능한지
  검토하는 노트를 생성한다(carryforward). ★실제 시뮬 코드는 game/src/sim에 이미 구현돼
  있고(슬라이스 산출물), 이 노드는 "조립 준비됨"을 표현하는 백엔드 노트다.
- n7_write_assembly(tool·game_data_write): 조립 결과를 data/assembly.json
  ({ready:true,...})으로 기록한다. tools=[GAME_DATA_WRITE_SCHEMA]만 선언하고 args
  (path·content)는 런타임에 LLM이 JSON으로 추출한다(engine.py:480 _last_extracted).
- 새 이벤트 0: tool 노드는 TOOL_CALL_*(기존), coding 노드는 TEXT_MESSAGE(기존)만 사용 →
  EVENT_REGISTRY 11종 유지. game_data_read·game_data_write는 always-allow(가역)라
  게이팅 suspend 없이 자율 실행(tools_policy.yaml 등록 — US-G5-1·US-G5-6).
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import GAME_DATA_READ_SCHEMA, GAME_DATA_WRITE_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="asm_read_meta",
    kind="tool",
    prompt_file="prompts/game_assemble/n1_read_meta.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 meta.json 읽기(입력 계약)
    next="asm_read_enemies",
)

N2 = Node(
    id="asm_read_enemies",
    kind="tool",
    prompt_file="prompts/game_assemble/n2_read_enemies.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 enemies.json 읽기(입력 계약)
    next="asm_read_weapons",
)

N3 = Node(
    id="asm_read_weapons",
    kind="tool",
    prompt_file="prompts/game_assemble/n3_read_weapons.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 weapons.json 읽기(입력 계약)
    next="asm_read_waves",
)

N4 = Node(
    id="asm_read_waves",
    kind="tool",
    prompt_file="prompts/game_assemble/n4_read_waves.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 waves.json 읽기(입력 계약)
    next="asm_read_upgrades",
)

N5 = Node(
    id="asm_read_upgrades",
    kind="tool",
    prompt_file="prompts/game_assemble/n5_read_upgrades.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 upgrades.json 읽기(입력 계약)
    next="asm_assemble",
)

N6 = Node(
    id="asm_assemble",
    kind="coding",
    prompt_file="prompts/game_assemble/n6_assemble.txt",
    thinking="on",  # 런타임 바인딩 검토 — reasoning 동반(dev 판단)
    next="asm_write_assembly",
)

N7 = Node(
    id="asm_write_assembly",
    kind="tool",
    prompt_file="prompts/game_assemble/n7_write_assembly.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2, N3, N4, N5, N6, N7]
WORKFLOW_NAME = "game_assemble"
