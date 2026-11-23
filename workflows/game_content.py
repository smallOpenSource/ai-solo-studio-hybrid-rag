"""game_content 워크플로 정의 — G005 US-G5-5 (콘텐츠·선형 플레이버 텍스트 병합).

노드 구성(★안정적 id — US-G5-7 역할 매핑용·변경 금지):
  n1_read_enemies   (tool · game_data_read · required=True)  enemies.json 읽기(입력 계약)
  n2_read_weapons   (tool · game_data_read · required=True)  weapons.json 읽기(입력 계약)
  n3_write_flavor   (longform)                               적/무기 플레이버(설명) 텍스트 양산
  n4_write_enemies  (tool · game_data_write)                 flavor 병합된 enemies.json 기록
  n5_write_weapons  (tool · game_data_write)                 flavor 병합된 weapons.json 기록

흐름: n1 → n2 → n3 → n4 → n5 → 종료

설계(.omc/prd.json US-G5-5 AC1·game_balance 선형 모델):
- ★결정론 선형 DAG(5노드). agent 노드 0 — game_balance 패턴과 동형으로
  longform/tool만 쓴다(자율 루프 없음). 워크플로 export = 모듈 전역
  NODES: list[Node] + WORKFLOW_NAME: str 두 심볼. next 문자열로 체이닝, 마지막
  노드 next=None.
- ★입력 계약(AC2): n1/n2는 game_data_read를 required=True로 호출한다.
  enemies.json 또는 weapons.json이 없으면 game_data_read가 FileNotFoundError를
  raise하고 엔진이 이를 error dict로 받은 뒤 NODE_FAILED로 드러내 워크플로가
  **성공하지 않는다**(이전 설계·밸런스 단계 미완이면 콘텐츠 단계가 명시 실패로 진행
  거부). 이 계약이 프로덕션에서 성립하려면 엔진이 error_fails_node=True여야 한다 —
  run.py/worker.py가 game 워크플로(GAME_WORKFLOWS)에 한해 그렇게 띄운다
  (g5-4-5-review Major-1).
- n3_write_flavor(longform·thinking=on): 읽은 enemies/weapons를 바탕으로
  각 항목에 대한 플레이버(설명) 텍스트를 설계한다(carryforward).
- n4·n5(tool·game_data_write): 기존 enemies/weapons 각 항목에 flavor 필드를
  **추가**해 기록한다. 기존 필수 필드(id/name/hp/... 등) **보존**·삭제/변형 금지 →
  병합 후에도 validate_enemies/validate_weapons ok:true(스키마 비파괴).
- 멱등(AC3): 재실행 시 같은 flavor면 중복 추가 0(game_data_write 내용해시 dedup +
  flavor 키 존재 시 덮어쓰기만).
- 새 이벤트 0: tool 노드는 TOOL_CALL_*(기존), longform 노드는 TEXT_MESSAGE(기존)만
  사용 → EVENT_REGISTRY 11종 유지. game_data_read·game_data_write는 always-allow
  (가역)라 게이팅 suspend 없이 자율 실행(tools_policy.yaml 등록 — US-G5-1·US-G5-5).
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import GAME_DATA_READ_SCHEMA, GAME_DATA_WRITE_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_read_enemies",
    kind="tool",
    prompt_file="prompts/game_content/n1_read_enemies.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 enemies.json 읽기(입력 계약)
    next="n2_read_weapons",
)

N2 = Node(
    id="n2_read_weapons",
    kind="tool",
    prompt_file="prompts/game_content/n2_read_weapons.txt",
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],  # required=True로 weapons.json 읽기(입력 계약)
    next="n3_write_flavor",
)

N3 = Node(
    id="n3_write_flavor",
    kind="longform",
    prompt_file="prompts/game_content/n3_write_flavor.txt",
    thinking="on",  # 플레이버 텍스트 설계 추론 — reasoning 동반
    next="n4_write_enemies",
)

N4 = Node(
    id="n4_write_enemies",
    kind="tool",
    prompt_file="prompts/game_content/n4_write_enemies.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next="n5_write_weapons",
)

N5 = Node(
    id="n5_write_weapons",
    kind="tool",
    prompt_file="prompts/game_content/n5_write_weapons.txt",
    thinking="off",
    tools=[GAME_DATA_WRITE_SCHEMA],
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2, N3, N4, N5]
WORKFLOW_NAME = "game_content"
