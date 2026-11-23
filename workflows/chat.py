"""chat 워크플로 정의 — W-2 (회사 직원 자유대화, DC-2 인바운드 응답 트리거).

노드 구성:
  n1_read_meta  (tool · thinking=off · game_data_read)  현재 개발 중인 게임 설계 로드
  n2_reply      (chat · thinking=off)                   스튜디오 직원으로서 자연어 응답

흐름: n1_read_meta → n2_reply → 종료

설계(2026-06-15 "대화봇을 회사 직원으로" — 컨텍스트 단절 수정):
- ★기존 단일 chat 노드(1줄 "사내 어시스턴트" 프롬프트)는 회사가 게임 스튜디오라는 것도,
  개발 중인 게임도 전혀 몰라 "게임 다 만들었어?" → "어떤 게임이요?"라는 단절을 냈다.
  이를 [실데이터 grounding → 직원 페르소나] 2노드 선형 DAG로 고친다(game_balance의
  [read meta → 설계] 선례와 동형 · agent 노드 0 · 결정론 선형).
- n1_read_meta(tool·game_data_read): 회사 산출물 game/data/meta.json을 read-only로 읽어
  현재 게임의 정체성(제목·테마·코어루프·승패·컨티뉴)을 carryforward로 n2에 넘긴다.
  ★required=False — 파일 부재 시 노드 실패가 아니라 {exists:false} 조용히 반환(자유대화가
  게임 미존재 상황에서도 끊기지 않게). game_data_read는 always-allow(가역·읽기전용)라
  게이팅 suspend 없이 자율 실행. research_summary의 knowledge_search와 달리 "근거 없음"
  토큰을 만들지 않는다(파일 내용/exists 플래그만 반환) → 자유대화 정합 유지.
- n2_reply(chat·thinking=off): carryforward된 게임 설계를 사실 근거로, AI 게임·콘텐츠
  스튜디오의 동료 직원으로서 한국어로 자연스럽게 답한다. 설계가 없으면 솔직히 준비 중이라
  답하고, 완성 여부·출시일 등은 근거 없이 단정하지 않는다(날조 금지).
- 엔진 0수정·새 이벤트 0: n1은 TOOL_CALL_*(기존), n2는 TEXT_MESSAGE(role=assistant·기존)만
  발행 → EVENT_REGISTRY 11종·노드 5종 유지. assistant TEXT_MESSAGE는 정확히 1건(n1은 tool
  노드라 assistant 메시지를 만들지 않음) → 인바운드 라운드트립 가드 보존. 디스코드 회신은
  FanoutPoller 팬아웃 재사용(신규 배선 0). thinking=OFF(자유대화 디폴트 gemma 정합).
"""

from __future__ import annotations

from office.nodes import Node
from office.tools import GAME_DATA_READ_SCHEMA

# ── 노드 정의 ─────────────────────────────────────────────────────────────────

N1 = Node(
    id="n1_read_meta",
    kind="tool",
    prompt=(
        "회사가 현재 개발 중인 게임의 설계 정보를 확인해야 한다. "
        "game_data_read 도구로 'data/meta.json' 파일을 읽어라. "
        "required는 false로 둔다(파일이 아직 없으면 빈 결과로 조용히 반환된다)."
    ),
    thinking="off",
    tools=[GAME_DATA_READ_SCHEMA],
    next="n2_reply",
)

N2 = Node(
    id="n2_reply",
    kind="chat",
    prompt=(
        "당신은 AI 에이전트들이 함께 운영하는 작은 게임·콘텐츠 스튜디오의 직원(동료)입니다. "
        "회사는 지금 직원들이 분업으로 첫 게임을 제작하고 있습니다. "
        "사용자의 메시지에 한국어로, 동료처럼 자연스럽고 친근하게 답하세요.\n"
        "이전 대화 내용이 함께 제공되면 그 맥락을 기억해 자연스럽게 이어가세요.\n"
        "방금 회사 산출물에서 읽어온 게임 설계 정보(meta.json)가 함께 제공되면, "
        "그 내용(제목·테마·핵심 진행 방식 등)을 사실 근거로 우리 게임을 정확히 안내하세요. "
        "설계 정보가 없거나 비어 있으면(exists=false) 솔직히 '아직 기획·준비 중'이라고 답하세요.\n"
        "★주어진 정보에 없는 완성 여부·출시일·구체 수치는 지어내지 말고, "
        "확실하지 않으면 솔직하게 말하세요. 답변은 너무 길지 않게 핵심만 전하세요."
    ),
    thinking="off",
    next=None,  # 워크플로 종료
)

# ── 워크플로 ──────────────────────────────────────────────────────────────────

NODES: list[Node] = [N1, N2]
WORKFLOW_NAME = "chat"
