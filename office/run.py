"""워크플로 실행 진입점 (오케스트레이션) — US-008 (T7).

사용법:
    # smoke: 실서버 N1→N2 2노드만 (thinking=OFF, gemma 모델)
    python -m office.run --workflow crm_notify --smoke

    # 풀런: 4노드 전체 (N3 email_send는 게이팅 → 승인 필요 → 폴링 재개)
    python -m office.run --workflow crm_notify

    # 풀런 + Plan Mode: 시작 시 계획 1회 제시, needs-approval 일괄 통과
    python -m office.run --workflow crm_notify --plan-mode

    # mock 모드 (실서버 없이 결정론 fake LLM — 오프라인 검증용)
    python -m office.run --workflow crm_notify --mock

오케스트레이션 흐름 (계획 §4.4·산출물 3):
    TaskQueue.enqueue
      → claim (직접 claim — 디스패처 단일 루프 대체, 동시성 1 유지)
      → WorkflowEngine.run(task_id=...)
      → status=="suspended"이면:
           · suspended_state(JSON)를 task payload에 영속
           · TaskQueue.suspend(task_id)
           · "python -m office.approve list 로 승인하세요" 안내 출력
           · 폴링 루프: poll_file_decisions(파일 폴백) + DB INTERRUPT_RESOLVED 감지
           · 결정 감지 시 resume_after_approval(suspended_state, decision)
           · complete / fail 마킹
      → 런 요약 출력 (노드별 latency·finish_reason·reasoning_chars)

설계 메모:
- "동시성 1"은 LLM 호출 동시성 — 이 진입점은 디스패처 별도 스레드를 띄우지 않고
  직접 claim→run 한다(단일 프로세스 순차). 승인 CLI(office.approve)는 사람이
  별도 셸에서 실행하는 2-프로세스 모델(같은 SQLite, WAL).
- API 키는 .env에서만 로드 (소스 코드에 키 문자열 없음 — 수용 13).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from office.approve import _default_approvals_dir, poll_file_decisions
from office.db import DEFAULT_DB_PATH, get_connection
from office.engine import NodeExecutor, WorkflowEngine
from office.event_store import EventStore
from office.events import RunError
from office.gating import GatingPolicy
from office.nodes import Node
from office.report import write_run_report
from office.task_queue import TaskQueue
from office.tools import MOCK_TOOLS, REAL_TOOLS

_PROJECT_ROOT = Path(__file__).parent.parent

# office 기본 모델 — 전제 #1(기본 gemma-4-e4b-it). bench의 BENCH_PRIMARY_MODEL과
# 분리(M7d): office는 모델 alias를 직접 안다. 노드가 model을 지정하면 그게 우선.
OFFICE_DEFAULT_MODEL = "gemma-4-e4b-it"

# ── chat 대화기억 (S1) — 워커가 시딩 직전 이벤트스토어에서 chat 기원 턴 회수 ──────────
#
# ★단일 인바운드 소스 불변식: 본 빌더는 "인바운드 대화 소스가 정확히 1개"(현 배포=단일
#   바운드 Discord 채널·bot.py 채널 가드)일 때만 정확하다. user 턴=run_id가 "discord-"로
#   시작하는 TextMessage(role=user) 에코, assistant 턴=RUN_STARTED.workflow=="chat"인
#   run의 TextMessage(role=assistant). 둘 다 대화키가 아니라 정황 단서다 — 2번째 채널/
#   2번째 봇/web chat이 생기면 history가 섞인다(test_build_chat_history_single_source_
#   limitation이 이 한계를 박제). 다중 대화는 후속(payload conversation_id 키 + inbound
#   멱등 재설계). 엔진·events 스키마·inbound 0수정(read-only 파생·신규 이벤트 0).
# ★스캔 바운드: event_store 유일 전역 메서드 read_since(0)로 (seq,json)을 받되 끝에서
#   _HISTORY_SCAN_WINDOW 행만 파싱한다(전체 store 파싱 금지). fetchall 자체는 event_store
#   (보호파일) API 한계로 O(N)이나 파싱은 O(WINDOW)·PoC 규모 수용(후속 tail-cursor로 fetch도 바운드).
CHAT_MEMORY_TURNS = 6  # 회수할 최근 교환 수(최대 2*N개 메시지 시딩).
_HISTORY_SCAN_WINDOW = 200  # read_since(0) 결과 중 파싱할 말미 행 수(스캔 바운드).


def _build_chat_history(
    store: EventStore, *, max_turns: int = CHAT_MEMORY_TURNS
) -> list[dict[str, str]]:
    """이벤트스토어에서 chat 기원 대화 턴을 oldest→newest로 회수한다(순수·결정론).

    user 턴 = run_id가 "discord-"로 시작하는 TextMessage(role=user) 에코.
    assistant 턴 = RUN_STARTED.workflow=="chat"인 run_id의 TextMessage(role=assistant).
    비-chat(crm/research/game) 메시지 제외. 최근 max_turns 교환(2*max_turns 메시지)으로 캡.
    스캔은 read_since(0) 말미 _HISTORY_SCAN_WINDOW 행만 파싱(전체 store 비의존).
    ★단일 인바운드 소스 불변식 하에서만 정확(위 모듈 주석 참조).
    """
    rows = store.read_since(0)  # [(seq, payload_json)] 전역 seq asc.
    tail = rows[-_HISTORY_SCAN_WINDOW:] if len(rows) > _HISTORY_SCAN_WINDOW else rows

    chat_run_ids: set[str] = set()
    text_events: list[dict[str, Any]] = []
    for _seq, raw in tail:
        try:
            ev = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "RUN_STARTED" and ev.get("workflow") == "chat":
            rid = ev.get("run_id")
            if isinstance(rid, str):
                chat_run_ids.add(rid)
        elif etype == "TEXT_MESSAGE":
            text_events.append(ev)

    turns: list[dict[str, str]] = []
    for ev in text_events:
        rid = ev.get("run_id")
        role = ev.get("role")
        content = ev.get("content")
        if not isinstance(rid, str) or not isinstance(content, str) or not content:
            continue
        if role == "user" and rid.startswith("discord-"):
            turns.append({"role": "user", "content": content})
        # ★윈도 비대칭(코드리뷰 기록): assistant 턴은 그 run의 RUN_STARTED가 같은 tail
        # 윈도 안에 있어야 chat로 인식된다. 바쁜 store에서 RUN_STARTED가 윈도 밖으로 밀리면
        # 그 assistant 턴은 조용히 누락된다 — graceful degrade(history가 짧아지거나 user
        # 편향)일 뿐 비-chat 응답을 섞지 않는다(user 턴은 discord- prefix로 면역). 단일
        # 채널 정상 사용에선 윈도 200이 캡(CHAT_MEMORY_TURNS)을 넉넉히 초과한다.
        elif role == "assistant" and rid in chat_run_ids:
            turns.append({"role": "assistant", "content": content})

    if len(turns) > 2 * max_turns:
        turns = turns[-2 * max_turns :]
    return turns


def _chat_seed_messages(
    history: list[dict[str, str]], query: str
) -> list[dict[str, str]]:
    """history + 현재 query를 시드 messages로 합치되 현재 턴이 정확히 1회가 되게 한다.

    Discord 경로: inbound가 현재 발화를 이미 "discord-{id}" 에코로 append → history 말미가
      그 user 턴(content==query)이므로 그대로 반환(중복 0). web/CLI 경로: 에코 없음 →
      history(없거나 과거 턴)에 현재 query를 append. 교환이 user/assistant로 번갈아
      누적되므로 history 말미가 user인 경우는 Discord 에코뿐(견고).
    """
    current = {"role": "user", "content": query}
    if history and history[-1] == current:
        return list(history)
    return list(history) + [current]


# ── 직원 페르소나 (멀티봇 @멘션 — 지목 직원의 시선으로 응답) ─────────────────────
# 출처: .omc/plans/org-roster-roles.md(책임·직책). role 키 = web roleNames SSOT와 일치.
# messages seam(engine.py:212)으로 주입 → 엔진/노드/이벤트 0수정. 미정의 역할은 generic 폴백.
PERSONA_PROMPTS: dict[str, str] = {
    "secretary": "당신은 이 AI 게임·콘텐츠 스튜디오의 비서 이수빈입니다. 일정·접수·회의 요약·라우팅을 챙깁니다. 친절하고 깔끔하게 비서의 시선에서 한국어로 답하세요.",
    "pm": "당신은 PM 정민준입니다. 프로젝트 기획·일정·역할 조율을 담당합니다. 진행과 우선순위를 챙기는 PM의 시선에서 한국어로 답하세요.",
    "ceo": "당신은 대표 김도윤입니다. 전사 전략·우선순위·승인을 책임집니다. 큰 그림과 결정의 시선에서 간결하게 한국어로 답하세요.",
    "strategist": "당신은 사업전략 담당 이서연입니다. 시장·경쟁 분석·사업모델·로드맵을 봅니다. 전략가의 시선에서 한국어로 답하세요.",
    "cfo": "당신은 재무책임자 박지호입니다. 예산·현금흐름·손익·비용을 봅니다. 숫자와 재무의 시선에서 한국어로 답하세요.",
    "legal": "당신은 법무 담당 최예린입니다. 계약·규제·약관·개인정보를 봅니다. 신중하고 정확한 법무의 시선에서 한국어로 답하세요.",
    "planner": "당신은 기획자 강하은입니다. 제품·콘텐츠 기획·요구사항·스펙을 만듭니다. 기획자의 시선에서 한국어로 답하세요.",
    "hr": "당신은 인사 담당 신동현입니다. 에이전트 구성·온보딩·업무 분배를 봅니다. 인사의 시선에서 한국어로 답하세요.",
    "data": "당신은 데이터분석 담당 윤시우입니다. 지표·분석·대시보드·A/B를 봅니다. 데이터의 시선에서 근거 있게 한국어로 답하세요.",
    "accountant": "당신은 회계사 임수아입니다. 거래 기록·정산·인보이스·장부를 봅니다. 회계의 시선에서 정확하게 한국어로 답하세요.",
    "dev": "당신은 개발자 한지훈입니다. 기능 구현·버그 수정·기술 구현을 담당합니다. 개발자의 시선에서 솔직하고 구체적으로 한국어로 답하세요.",
    "devops": "당신은 데브옵스 오태경입니다. 배포·인프라·가동·모니터링을 봅니다. 안정성과 운영의 시선에서 한국어로 답하세요.",
    "designer": "당신은 디자이너 서유진입니다. UI/UX·비주얼·브랜드·자산을 봅니다. 디자이너의 시선에서 한국어로 답하세요.",
    "marketer": "당신은 마케터 김지원입니다. 콘텐츠·캠페인·채널·브랜드를 봅니다. 마케터의 시선에서 한국어로 답하세요.",
    "sales": "당신은 영업 담당 장현우입니다. 리드·세일즈·제안·협상을 봅니다. 영업의 시선에서 한국어로 답하세요.",
    "cs": "당신은 고객지원 담당 문가영입니다. 문의 응대·이슈 해결·FAQ를 봅니다. 친절한 고객지원의 시선에서 한국어로 답하세요.",
}


def _chat_persona_seed(
    role: str | None, history: list[dict[str, str]], query: str
) -> list[dict[str, str]]:
    """역할 페르소나 지시를 messages 맨 앞에 얹어 그 직원의 시선으로 답하게 한다.

    엔진 messages seam(engine.py:212)이 그대로 LLM에 전달(엔진 0수정). 미지목/미정의 역할
    (default 포함)이면 페르소나 없이 기존 시드(generic 폴백). 현재 턴 dedup은 _chat_seed_messages가 보장.
    """
    seeded = _chat_seed_messages(history, query)
    persona = PERSONA_PROMPTS.get(role or "")
    if persona:
        return [{"role": "user", "content": persona}, *seeded]
    return seeded


# G005 게임 워크플로 집합 — 입력 계약(game_data_read required=True)이 프로덕션에서도
# 성립하도록 error_fails_node=True로 엔진을 구성하는 대상이다(g5-4-5-review Major-1).
# 이 워크플로들은 n1에서 이전 단계 산출 파일(meta/enemies/...)을 required=True로 읽는데,
# 파일 부재 시 game_data_read가 예외→{"error":...}를 돌려준다. 기본(error_fails_node=
# False) 엔진은 그 error dict를 누적하고 다음 노드로 계속 진행해 가짜 데이터로 success로
# 끝난다(리뷰 실증). 게임 워크플로만 True로 띄워 "이전 단계 미완→명시 실패로 진행 거부"
# 라는 단계 계약을 프로덕션에서도 참으로 만든다. ★crm_notify 등 비-game 워크플로는
# 여기 없으므로 기본 False 그대로(수용 17 error-id→기존 동작 회귀 0).
# ※game_qa는 game_validate가 {"ok":false,"errors":[...]}(="error" 키 없음)를 돌려주므로
#   error_fails_node=True여도 노드 실패가 아니라 pass:false 리포트를 정상 기록한다(의도).
GAME_WORKFLOWS: frozenset[str] = frozenset(
    {"game_design", "game_balance", "game_content", "game_assemble", "game_qa"}
)


def _is_game_workflow(name: str) -> bool:
    """이 워크플로가 game 워크플로면 True(error_fails_node=True 대상)."""
    return name in GAME_WORKFLOWS


# ── Smoke/mock LLM ────────────────────────────────────────────────────────────


class _FakeSmokeLLM:
    """smoke/mock 모드용 결정론적 fake LLM (실서버 없이 검증)."""

    _RESPONSES: dict[str, str] = {
        "crm_get_customer": '{"name":"crm_get_customer","arguments":{"customer_id":"C-2041"}}',
        "order_get_latest": '{"name":"order_get_latest","arguments":{"customer_id":"C-2041"}}',
        "email_send": '{"name":"email_send","arguments":{"to":"minjun.kim@example.com","subject":"주문 확인","body":"안녕하세요 김민준 고객님."}}',
        "knowledge_search": '{"name":"knowledge_search","arguments":{"query":"테스트 질의"}}',
        # G005 US-G5-3: game_data_write tool-call JSON — arguments.content가
        # validate_meta ok:true인 유효 meta(theme/title/coreLoop/win/lose + continue
        # {freeRetries,costCurve(비감소),sessionCap}). path는 game/ 하위 data/meta.json.
        "game_data_write": (
            '{"name":"game_data_write","arguments":{"path":"data/meta.json",'
            '"content":{"theme":"폐허가 된 도시에서 살아남는 생존자",'
            '"title":"네온 서바이버","coreLoop":"이동하며 자동공격으로 몰려오는 적을 '
            '처치하고 경험치로 레벨업해 업그레이드를 고르는 것을 반복한다",'
            '"win":"10분 생존","lose":"플레이어 HP 0",'
            '"continue":{"freeRetries":1,"costCurve":[100,200,400],"sessionCap":3}}}}'
        ),
        # 설계(longform) 노드용 평문 응답.
        "game_design_concept": (
            "테마: 폐허 도시 생존. 제목: 네온 서바이버. 코어루프: 이동·자동공격·"
            "레벨업·업그레이드 반복. 승리: 10분 생존. 패배: HP 0. 컨티뉴: 무료 1회, "
            "비용 곡선 100/200/400, 세션 한도 3회."
        ),
        # G005 US-G5-4: 게임 밸런스 워크플로 노드별 응답.
        # n1 read meta — game_data_read tool-call(required=True·입력 계약).
        "game_balance_read_meta": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/meta.json","required":true}}'
        ),
        # n2 수치 설계 평문(longform·thinking=on).
        "game_balance_design": (
            "적 3종(워커/박쥐떼/브루트), 무기 2종(자동권총/마늘오라), 웨이브 2개"
            "(0~60s·60~120s), 업그레이드 3종(공격력/이속/공속) 수치를 설계했다. "
            "참조 무결성: waves.enemyId는 enemies.id(walker/bat/brute)만 참조한다."
        ),
        # n3 enemies — validate_enemies ok:true(>=3·behavior∈enum·고유 id).
        "game_balance_enemies": (
            '{"name":"game_data_write","arguments":{"path":"data/enemies.json",'
            '"content":{"enemies":['
            '{"id":"walker","name":"워커","hp":30,"speed":40,"damage":8,'
            '"spawnWeight":10,"behavior":"chase"},'
            '{"id":"bat","name":"박쥐 떼","hp":10,"speed":90,"damage":4,'
            '"spawnWeight":8,"behavior":"swarm"},'
            '{"id":"brute","name":"브루트","hp":80,"speed":50,"damage":20,'
            '"spawnWeight":3,"behavior":"charger"}]}}}'
        ),
        # n4 weapons — validate_weapons ok:true(>=2).
        "game_balance_weapons": (
            '{"name":"game_data_write","arguments":{"path":"data/weapons.json",'
            '"content":{"weapons":['
            '{"id":"auto_pistol","name":"자동권총","fireRate":2.0,"projectiles":1,'
            '"damage":12,"range":180,"pierce":0},'
            '{"id":"garlic_aura","name":"마늘 오라","fireRate":0.5,"projectiles":8,'
            '"damage":5,"range":80,"pierce":3}]}}}'
        ),
        # n5 waves — validate_waves ok:true(>=2·tStart 오름차순) + 참조 무결성
        # (enemyId∈{walker,bat,brute}).
        "game_balance_waves": (
            '{"name":"game_data_write","arguments":{"path":"data/waves.json",'
            '"content":{"waves":['
            '{"tStart":0,"tEnd":60,"spawn":['
            '{"enemyId":"walker","rate":0.5},{"enemyId":"bat","rate":0.3}],'
            '"difficulty":1.0},'
            '{"tStart":60,"tEnd":120,"spawn":['
            '{"enemyId":"walker","rate":1.0},{"enemyId":"brute","rate":0.1}],'
            '"difficulty":2.0}]}}}'
        ),
        # n6 upgrades — validate_upgrades ok:true(>=3).
        "game_balance_upgrades": (
            '{"name":"game_data_write","arguments":{"path":"data/upgrades.json",'
            '"content":{"upgrades":['
            '{"id":"dmg_boost","name":"공격력 증가","stat":"damage","delta":5,"cap":50},'
            '{"id":"move_speed","name":"이동 속도 증가","stat":"speed","delta":10,'
            '"cap":100},'
            '{"id":"fire_rate","name":"공격 속도 증가","stat":"fireRate","delta":0.2,'
            '"cap":5.0}]}}}'
        ),
        # n7 balance — validate_balance ok:true(survivalTargetSec>0·dpsCurve 비감소·
        # inRange bool·report{bool}).
        "game_balance_balance": (
            '{"name":"game_data_write","arguments":{"path":"data/balance.json",'
            '"content":{"survivalTargetSec":600,"dpsCurve":[12,18,30,48],'
            '"inRange":true,"report":{"enemyHpScaling":true,"waveProgression":true,'
            '"weaponBalance":true,"upgradeEffectiveness":true}}}}'
        ),
        "chat": "고객 김민준님의 최근 주문(노트북 Pro 15)이 배송완료 상태입니다. 확인 이메일을 발송하겠습니다.",
        # chat 워크플로(W-2·DC-2) 전용 결정론 응답 — CRM 특화 "chat" 키와 분리(자가당착
        # 방지). 자유대화 톤의 비CRM 텍스트라 chat 워크플로 mock 검증이 CRM 문자열에
        # 의존하지 않는다(테스트는 구조만 단언하나, mock 자체도 정합 유지).
        "chat_reply": "안녕하세요. 무엇을 도와드릴까요?",
        # chat 워크플로 n1 — game_data_read(meta·required=false). 회사가 개발 중인 게임
        # 설계를 읽어 n2 직원 응답에 grounding. required=false라 meta 부재 시 graceful.
        "chat_read_meta": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/meta.json","required":false}}'
        ),
        # G005 US-G5-5: 게임 콘텐츠 워크플로 노드별 응답.
        # n1 read enemies — game_data_read tool-call(required=True·입력 계약).
        "game_content_read_enemies": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/enemies.json","required":true}}'
        ),
        # n2 read weapons — game_data_read tool-call(required=True·입력 계약).
        "game_content_read_weapons": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/weapons.json","required":true}}'
        ),
        # n3 플레이버 텍스트 설계 평문(longform·thinking=on).
        "game_content_flavor": (
            "적 플레이버: zombie=도시의 어둠 속에서 무리지어 다가오는 언데드, "
            "bat_swarm=날개짓으로 공기를 가르며 덮쳐드는 박쥐 떼, "
            "skeleton_archer=뼈만 남은 손가락으로 화살을 쏘는 해골 궁수, "
            "grave_boar=묘지에서 깨어난 분노한 멧돼지. "
            "무기 플레이버: holy_cross=빛의 십자가로 언데드를 정화한다, "
            "garlic_aura=마늘 향기로 주변 적을 밀쳐낸다, "
            "silver_knife=은빛 날이 몬스터의 심장을 꿰뚫는다."
        ),
        # n4 enemies + flavor 병합 — validate_enemies ok:true(기존 필수필드 보존 + flavor 추가).
        "game_content_enemies": (
            '{"name":"game_data_write","arguments":{"path":"data/enemies.json",'
            '"content":{"enemies":['
            '{"id":"zombie","name":"좀비","hp":30,"speed":40,"damage":8,'
            '"spawnWeight":10,"behavior":"chase",'
            '"flavor":"도시의 어둠 속에서 무리지어 다가오는 언데드"},'
            '{"id":"bat_swarm","name":"박쥐 떼","hp":10,"speed":90,"damage":4,'
            '"spawnWeight":8,"behavior":"swarm",'
            '"flavor":"날개짓으로 공기를 가르며 덮쳐드는 박쥐 떼"},'
            '{"id":"skeleton_archer","name":"해골 궁수","hp":20,"speed":30,"damage":12,'
            '"spawnWeight":5,"behavior":"ranged",'
            '"flavor":"뼈만 남은 손가락으로 화살을 쏘는 해골 궁수"},'
            '{"id":"grave_boar","name":"묘지 멧돼지","hp":60,"speed":120,"damage":20,'
            '"spawnWeight":3,"behavior":"charger",'
            '"flavor":"묘지에서 깨어난 분노한 멧돼지"}]}}}'
        ),
        # n5 weapons + flavor 병합 — validate_weapons ok:true(기존 필수필드 보존 + flavor 추가).
        "game_content_weapons": (
            '{"name":"game_data_write","arguments":{"path":"data/weapons.json",'
            '"content":{"weapons":['
            '{"id":"holy_cross","name":"성십자가","fireRate":1.5,"projectiles":1,'
            '"damage":15,"range":200,"pierce":0,'
            '"flavor":"빛의 십자가로 언데드를 정화한다"},'
            '{"id":"garlic_aura","name":"마늘 오라","fireRate":0.5,"projectiles":8,'
            '"damage":5,"range":80,"pierce":3,'
            '"flavor":"마늘 향기로 주변 적을 밀쳐낸다"},'
            '{"id":"silver_knife","name":"은제 단검","fireRate":3.0,"projectiles":2,'
            '"damage":8,"range":150,"pierce":1,'
            '"flavor":"은빛 날이 몬스터의 심장을 꿰뚫는다"}]}}}'
        ),
        # G005 US-G5-6: 게임 조립 워크플로 노드별 응답.
        # n1~n5 read — game_data_read tool-call(각 required=True·입력 계약).
        "game_assemble_read_meta": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/meta.json","required":true}}'
        ),
        "game_assemble_read_enemies": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/enemies.json","required":true}}'
        ),
        "game_assemble_read_weapons": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/weapons.json","required":true}}'
        ),
        "game_assemble_read_waves": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/waves.json","required":true}}'
        ),
        "game_assemble_read_upgrades": (
            '{"name":"game_data_read","arguments":'
            '{"path":"data/upgrades.json","required":true}}'
        ),
        # n6 조립 노트 평문(coding·thinking=on).
        "game_assemble_note": (
            "런타임 바인딩 검토 완료: 적/무기/웨이브/업그레이드 정의가 순수 시뮬"
            "(createSim/tick)이 소비할 형태로 조립 가능하다. 참조 무결성(waves.enemyId∈"
            "enemies.id) 확인, 무기 자동공격·웨이브 스폰·레벨업 선택지 바인딩 확인, "
            "동시 엔티티는 ENTITY_CAP 내 풀링으로 관리된다. 조립 준비됨."
        ),
        # n7 assembly.json — game_data_write tool-call(ready:true).
        "game_assemble_write": (
            '{"name":"game_data_write","arguments":{"path":"data/assembly.json",'
            '"content":{"ready":true,"runtime":"survivor-sim","entry":"src/main.ts",'
            '"entityCap":300,"notes":"적/무기/웨이브/업그레이드 바인딩·참조 무결성·풀링 '
            '검토 완료"}}}'
        ),
        # G005 US-G5-6: 게임 QA 워크플로 노드별 응답.
        # n1 game_validate — tool-call(data 디렉터리 스키마 검증).
        "game_qa_validate": (
            '{"name":"game_validate","arguments":{"data_dir":"data"}}'
        ),
        # n2 qa_report.json — game_data_write tool-call(pass:true·errors:[]).
        # 베이스라인 데이터는 검증 통과라 pass:true. 깨진 데이터 케이스는 테스트가
        # mock game_validate로 ok:false를 주입해 pass:false를 단언한다(결정론).
        "game_qa_write_report": (
            '{"name":"game_data_write","arguments":{"path":"data/qa_report.json",'
            '"content":{"pass":true,"errors":[]}}}'
        ),
    }
    # chat류(자유대화·요약·설계 longform) 시퀀스 키 — reasoning_content를 채워
    # thinking 경로와 정합. game_design_concept·game_balance_design·game_content_flavor는
    # longform·thinking=on 설계 노드.
    _CHAT_KEYS = frozenset(
        {
            "chat",
            "chat_reply",
            "game_design_concept",
            "game_balance_design",
            "game_content_flavor",
            "game_assemble_note",
        }
    )

    def __init__(self, node_sequence: list[str]) -> None:
        self._seq = list(node_sequence)
        self._idx = 0

    def chat(
        self, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        key = self._seq[min(self._idx, len(self._seq) - 1)]
        self._idx += 1
        content = self._RESPONSES.get(key, '{"name":"fn","arguments":{}}')
        is_chat = key in self._CHAT_KEYS
        return {
            "message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": "추론 중..." if is_chat else "",
                "tool_calls": [],
            },
            "content": content,
            "reasoning_content": "추론 중..." if is_chat else "",
            "finish_reason": "stop",
            "usage": {},
            "latency_ms": 10.0,
        }


# ── 워크플로 로더 ─────────────────────────────────────────────────────────────


def _load_workflow(name: str) -> tuple[list[Node], str]:
    """워크플로 이름으로 nodes와 workflow_name을 반환한다."""
    if name == "crm_notify":
        from workflows.crm_notify import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "agent_smoke":
        # Phase 6 6b(OBJ-4·N-4): agent 격리 테스트 워크플로. running 고아가 되면 recovery
        # (worker.py)가 _load_workflow로 재로드하므로 여기 등록해야 ValueError를 피한다.
        from workflows.agent_smoke import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "smoke_email":
        # 라이브 SMTP 스모크 전용(prompt-only 주입): crm_notify 4노드를 재사용하되 N1
        # 프롬프트만 교체(C-SMOKE-1 조회)한다. N2/N2.5/N3는 바이트 동일 — carryforward로
        # C-SMOKE-1 email이 N3 `to`까지 흐른다. ★distinct WORKFLOW_NAME으로 이벤트 로그상
        # crm_notify와 구분(M-1). running 고아 recovery가 재로드하므로 여기 등록(ValueError 회피).
        # ※agent_smoke와 달리 정적 NODES가 아니라 replace로 노드 리스트를 인라인 구성한다.
        # ★N2/N2_5/N3는 crm_notify.NODES와 인스턴스를 참조 공유 — Node는 불변 config로 취급
        #   (in-place 변이 금지·변이 필요 시 replace로 새 인스턴스). test_smoke_workflow가 무변이 가드.
        from workflows.crm_notify import N1, N2, N2_5, N3

        smoke_n1 = replace(N1, prompt_file="prompts/smoke_email/n1_crm_get.txt")
        return [smoke_n1, N2, N2_5, N3], "smoke_email"
    if name == "research_summary":
        # Phase 8 C(NIT-2): 리서치-요약 2노드 워크플로(retrieve→summarize). 정적 NODES
        # 로더(smoke_email 선례와 동형이나 인라인 replace 불요 — 고정 식별자 0). running
        # 고아 recovery가 재로드하므로 여기 등록해야 ValueError를 피한다. ★워커 핵심
        # 루프(claim/recover/cancel/heartbeat)는 무변 — 이 분기는 로더만(AC-C7).
        from workflows.research_summary import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "chat":
        # DC-2 W-2: 자유대화 단일 chat 노드. 디스코드 인바운드(/api/inbound)가
        # enqueue(workflow="chat", payload={"query": text})로 트리거 → 워커가 query를
        # initial_state["messages"]로 시딩 → chat 노드가 assistant TEXT_MESSAGE 발행.
        # running 고아 recovery가 재로드하므로 여기 등록(ValueError 회피). 엔진 0수정.
        from workflows.chat import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "game_design":
        # G005 US-G5-3: 게임 설계 선형 워크플로(n1_design_concept→n2_write_meta).
        # planner·strategist가 테마·코어루프·승패·retry 과금을 설계(longform)해 game/
        # data/meta.json으로 기록(tool·game_data_write)한다. ★agent 노드 0(선형). 정적
        # NODES 로더(research_summary 선례와 동형). running 고아 recovery가 재로드하므로
        # 여기 등록해야 ValueError를 피한다.
        from workflows.game_design import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "game_balance":
        # G005 US-G5-4: 게임 밸런스 선형 워크플로(n1_read_meta → n2_design_numbers →
        # n3~n6 enemies/weapons/waves/upgrades 기록 → n7 balance 기록). data(윤시우)가
        # 이전 단계 meta.json을 읽어(game_data_read required=True·입력 계약) 수치를
        # 설계(longform)하고 각 파일로 기록(tool·game_data_write)한다. ★agent 노드 0(선형).
        # running 고아 recovery가 재로드하므로 여기 등록해야 ValueError를 피한다.
        from workflows.game_balance import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "game_content":
        # G005 US-G5-5: 게임 콘텐츠 선형 워크플로(n1_read_enemies → n2_read_weapons →
        # n3_write_flavor(longform) → n4_write_enemies → n5_write_weapons). 기존
        # enemies/weapons 각 항목에 flavor 필드를 추가해 병합 기록한다. ★agent 노드 0(선형).
        # running 고아 recovery가 재로드하므로 여기 등록해야 ValueError를 피한다.
        from workflows.game_content import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "game_assemble":
        # G005 US-G5-6: 게임 조립 선형 워크플로(n1~n5 read meta/enemies/weapons/waves/
        # upgrades required=True → n6 coding 조립노트 → n7 write assembly.json{ready:true}).
        # dev(한지훈)가 이전 단계 산출 데이터를 읽어(입력 계약) 런타임 바인딩을 검토하고
        # 조립 결과를 기록한다. ★agent 노드 0(선형). running 고아 recovery가 재로드하므로
        # 여기 등록해야 ValueError를 피한다.
        from workflows.game_assemble import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    if name == "game_qa":
        # G005 US-G5-6: 게임 QA 선형 워크플로(n1 game_validate → n2 write qa_report.json).
        # game/data 6파일의 스키마 + 참조 무결성을 결정론 검증(베이스라인=pass, 깨진
        # 데이터=fail)하고 리포트를 기록한다. ★agent 노드 0(선형). running 고아 recovery가
        # 재로드하므로 여기 등록해야 ValueError를 피한다.
        from workflows.game_qa import NODES, WORKFLOW_NAME

        return NODES, WORKFLOW_NAME
    raise ValueError(f"알 수 없는 워크플로: {name!r}")


# mock-mode _FakeSmokeLLM의 워크플로별 node_sequence — run_full(CLI)·worker(_build_engine)
# 공유 단일 출처(중복 제거·둘이 어긋날 위험 0). 미등록 워크플로는 기본(crm 4단계) 폴백(기존 동작 보존).
_MOCK_NODE_SEQUENCES: dict[str, list[str]] = {
    "crm_notify": ["crm_get_customer", "order_get_latest", "chat", "email_send"],
    "smoke_email": ["crm_get_customer", "order_get_latest", "chat", "email_send"],
    "research_summary": ["knowledge_search", "chat"],
    # DC-2 W-2: chat 워크플로(n1_read_meta→n2_reply 2노드)는 전용 결정론 응답을 쓴다
    # — CRM 특화 "chat" 캔드가 자유대화에 새지 않게(M2 자가당착 차단). 미등록 시
    # _DEFAULT_MOCK_NODE_SEQUENCE(crm 4단계)로 빠져 첫 노드에 crm_get_customer JSON이
    # 반환되는 함정을 막는다. n1=game_data_read(meta·required=false) tool-call,
    # n2=자유대화 톤 chat 응답.
    "chat": ["chat_read_meta", "chat_reply"],
    # G005 US-G5-3: 게임 설계 워크플로(n1_design_concept→n2_write_meta). 설계 노드는
    # 평문("game_design_concept"), 기록 노드는 game_data_write tool-call JSON
    # ("game_data_write")을 쓴다. mock content는 validate_meta ok:true인 유효 meta다.
    "game_design": ["game_design_concept", "game_data_write"],
    # G005 US-G5-4: 게임 밸런스 워크플로(7노드). n1=game_data_read(meta required=True),
    # n2=수치 설계 평문, n3~n7=각 파일 game_data_write tool-call. 각 write content는
    # 해당 validate_* ok:true + 참조 무결성(waves.enemyId∈enemies.id)을 충족하는 유효
    # 데이터다(가짜통과 금지). ★실 풀런(mock)은 game/data/meta.json이 있어야 n1 read가
    # 성공한다 — 베이스라인 meta.json이 커밋돼 있어 통과(US-G5-2).
    "game_balance": [
        "game_balance_read_meta",
        "game_balance_design",
        "game_balance_enemies",
        "game_balance_weapons",
        "game_balance_waves",
        "game_balance_upgrades",
        "game_balance_balance",
    ],
    # G005 US-G5-5: 게임 콘텐츠 워크플로(5노드). n1=game_data_read(enemies required=True),
    # n2=game_data_read(weapons required=True), n3=플레이버 평문(longform), n4~n5=각
    # flavor 병합 game_data_write tool-call. 각 write content는 기존 필수필드 보존 +
    # flavor 추가 → validate_enemies/validate_weapons ok:true(스키마 비파괴).
    "game_content": [
        "game_content_read_enemies",
        "game_content_read_weapons",
        "game_content_flavor",
        "game_content_enemies",
        "game_content_weapons",
    ],
    # G005 US-G5-6: 게임 조립 워크플로(7노드). n1~n5=game_data_read(meta/enemies/weapons/
    # waves/upgrades required=True·입력 계약), n6=조립 노트 평문(coding), n7=assembly.json
    # game_data_write tool-call. ★실 풀런(mock)은 game/data 5파일이 있어야 n1~n5 read가
    # 성공한다 — 베이스라인이 커밋돼 있어 통과(US-G5-2).
    "game_assemble": [
        "game_assemble_read_meta",
        "game_assemble_read_enemies",
        "game_assemble_read_weapons",
        "game_assemble_read_waves",
        "game_assemble_read_upgrades",
        "game_assemble_note",
        "game_assemble_write",
    ],
    # G005 US-G5-6: 게임 QA 워크플로(2노드). n1=game_validate tool-call(data 디렉터리
    # 스키마 검증), n2=qa_report.json game_data_write tool-call(pass:true·errors:[]).
    # ★베이스라인 데이터=pass:true 결정론(고의 깨진 데이터는 테스트가 mock 검증으로 fail 단언).
    "game_qa": [
        "game_qa_validate",
        "game_qa_write_report",
    ],
}
_DEFAULT_MOCK_NODE_SEQUENCE: list[str] = [
    "crm_get_customer",
    "order_get_latest",
    "chat",
    "email_send",
]


def _make_llm(mock: bool, node_sequence: list[str]) -> Any:
    """mock이면 결정론 fake, 아니면 실서버 LLMClient를 만든다."""
    if mock:
        return _FakeSmokeLLM(node_sequence)
    from core.llm_client import LLMClient

    return LLMClient()


def _persist_suspended_state(
    task_id: int, suspended_state: dict[str, Any], db_path: str | Path
) -> None:
    """suspend된 워크플로 state(JSON)를 task payload에 영속한다.

    재개 시 같은 state로 resume_after_approval을 부르기 위함.
    state는 JSON 직렬화 가능(_resume_* 키 보존, US-006 계약).
    """
    payload_str = json.dumps(
        {"suspended_state": suspended_state}, ensure_ascii=False, default=str
    )
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (payload_str, task_id),
        )
        conn.commit()


def _record_run_id_in_payload(task_id: int, run_id: str, db_path: str | Path) -> None:
    """claim된 task의 payload에 run_id를 1줄 병합 기록한다(Phase 6 6b · US-P6B-0).

    워커가 claim 직후 ~ RUN_STARTED 발행 전(engine.run 내부 run_id 생성 직후)에 호출해
    running task→run_id 매핑을 닫는다. 이 매핑이 2a(running 고아 식별·recovery.py)와
    cancel 거짓202(claim~RUN_STARTED 윈도)의 공통 갭을 메운다.

    ★RUN_STARTED 이벤트 필드는 건드리지 않는다(events.py:73 무변 — 11종 유지). 기존
    payload(예: {"mock": True})에 run_id를 **병합**해 mock 플래그를 보존한다. payload가
    파손/부재면 run_id만 담은 새 payload로 덮는다(식별 우선 — 손실은 mock 플래그뿐).

    suspend로 이어지면 _persist_suspended_state가 payload를 suspended_state로 덮어쓰나,
    그땐 suspended_state 안에 run_id가 박제(run()이 보존)돼 있어 식별이 끊기지 않는다 —
    이 기록은 **running 윈도** 한정 식별용이다.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        payload: dict[str, Any] = {}
        if row is not None:
            try:
                loaded = json.loads(row["payload_json"])
                if isinstance(loaded, dict):
                    payload = loaded
            except (json.JSONDecodeError, TypeError):
                payload = {}
        payload["run_id"] = run_id
        conn.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, default=str), task_id),
        )
        conn.commit()


def load_run_id(task_id: int, db_path: str | Path) -> str | None:
    """task payload에서 기록된 run_id를 읽는다(Phase 6 6b · US-P6B-0).

    running 고아 복구(recovery.py)가 task_id로 run_id를 식별할 때 쓴다. suspended task는
    payload가 {"suspended_state": {...}}라 여기 run_id가 없을 수 있으나(그쪽은
    load_suspended_state로 run_id를 얻음), running 고아는 _record_run_id_in_payload가
    심은 run_id를 그대로 돌려준다. 부재/파손이면 None.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def load_suspended_state(task_id: int, db_path: str | Path) -> dict[str, Any] | None:
    """task payload에서 영속된 suspended_state를 복원한다 (공개 — Phase 5 US-P5-3).

    재시작 복구 진입점(web_server.runner.resume_task)이 task_id로 suspended_state
    (run_id 포함)를 로드할 때 재사용한다. run_id가 박제돼 있어 같은 run_id로
    resume_after_approval을 부르면 B1 dedup·B2 멱등키가 동작해 email 정확히-1회.
    suspended_state가 없거나(running 고아 등 payload={}) JSON 파손이면 None.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None
    return payload.get("suspended_state")


# 하위 호환 별칭 — 기존 내부 호출처(run_full)·테스트가 쓰던 private 이름 보존.
_load_suspended_state = load_suspended_state


# ── 런 요약 출력 ──────────────────────────────────────────────────────────────


def format_run_summary(state: dict[str, Any]) -> str:
    """state["_node_metrics"]로 노드별 요약 문자열을 만든다 (수용 12).

    노드별 latency_ms·finish_reason·reasoning_chars·thinking을 한 줄씩.
    """
    metrics: dict[str, Any] = state.get("_node_metrics", {})
    status = state.get("_workflow_status", "unknown")
    run_id = state.get("run_id", "")
    lines = [f"=== 런 요약 (run_id={run_id}, status={status}) ==="]
    if not metrics:
        lines.append("(노드 메트릭 없음)")
        return "\n".join(lines)
    lines.append(
        f"{'노드':<16} {'thinking':<9} {'finish':<8} "
        f"{'latency_ms':>11} {'reasoning_chars':>16}"
    )
    for node_id, m in metrics.items():
        lines.append(
            f"{node_id:<16} {m.get('thinking', ''):<9} "
            f"{m.get('finish_reason', ''):<8} "
            f"{m.get('latency_ms', 0.0):>11.1f} "
            f"{m.get('reasoning_chars', 0):>16}"
        )
    return "\n".join(lines)


# ── 폴링 재개 (오케스트레이션의 핵심) ────────────────────────────────────────


def wait_for_decision_and_resume(
    engine: WorkflowEngine,
    suspended_state: dict[str, Any],
    task_id: int,
    store: EventStore,
    db_path: str | Path,
    approvals_dir: str | Path | None = None,
    poll_interval: float = 1.0,
    max_polls: int | None = None,
    cancel_event: threading.Event | None = None,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """승인 결정을 폴링하다가 감지되면 resume_after_approval로 재개한다.

    두 경로를 모두 본다 (계획 §4.4 경로 A·B):
      · poll_file_decisions: state/approvals/*.json 파일 폴백 처리(.consumed rename).
        이 헬퍼가 INTERRUPT_RESOLVED를 DB에 기록하고 TaskQueue.resume까지 한다.
      · DB INTERRUPT_RESOLVED 직접 감지: CLI(office.approve approve/reject)가
        이미 기록한 결정을 집어낸다.

    결정(approve/reject)을 찾으면 영속된 suspended_state로 resume_after_approval을
    호출하고(LLM 재호출 없음), 최종 state를 반환한다.

    Args:
        engine        : 재개에 쓸 WorkflowEngine (resume_after_approval 보유).
        suspended_state: run()이 suspend 시 반환한 state (JSON 직렬화 가능).
        task_id       : TaskQueue row id.
        store         : EventStore (이벤트 질의).
        db_path       : SQLite 경로.
        approvals_dir : 파일 폴백 디렉토리. None이면 state/approvals/.
        poll_interval : 폴링 간격(초).
        max_polls     : 최대 폴링 횟수. None이면 무기한(서버 풀런).
                        테스트는 유한값으로 무한 대기를 막는다.
        cancel_event  : 운영자 취소 신호(US-009/H-06). runner가 생성·보관·set하고
                        여기로 파라미터 관통한다(모듈 전역 Event 금지 — PM-3 (iii)
                        cross-thread 위치 오류 회피). 매 폴마다 is_set()을 검사해
                        set이면 RUN_ERROR 발행 후 즉시 탈출한다. None(CLI·기존
                        호출)이면 검사 자체가 없어 현 무기한 동작 100% 무변.
        should_cancel : 워커(별도 프로세스) DB 취소 신호 폴(Phase 6 6a). 제공 시
                        매 폴 cancel 검사를 cancel_event.is_set() *대신* should_cancel()
                        로 한다(인메모리 Event가 프로세스 경계를 못 넘으므로 DB
                        조회로 재배선). 둘 다 주어지면 OR(어느 한쪽이라도 취소면
                        취소) — 단 워커는 should_cancel만, runner(웹)는 cancel_event
                        만 준다. None이면 현 cancel_event 경로 그대로(동작 무변).
                        취소 감지 시 의미는 동일(_emit_cancelled + status=cancelled).
        heartbeat     : 워커 liveness 갱신 콜백(Phase 6 6a). 제공 시 매 폴 루프
                        첫 줄에서 호출해 suspended 폴링 중에도 heartbeat를 신선하게
                        유지한다(거짓 stale 방지 — 승인 대기가 길어도 워커는 살아있음).
                        None이면 무동작(기존 호출 무변).

    Returns:
        재개 후 최종 state. 결정을 못 찾고 max_polls 소진 시 suspended_state 그대로.
        cancel 신호가 감지되면 _workflow_status="cancelled"로 표시해 반환한다.
    """
    run_id = suspended_state.get("run_id", "")
    resume_node_id = suspended_state.get("_resume_node_id", "")
    adir = (
        approvals_dir if approvals_dir is not None else _default_approvals_dir(db_path)
    )

    polls = 0
    while True:
        # heartbeat 갱신(Phase 6): 매 폴 루프 첫 줄 — suspended 폴링 중에도 워커
        # liveness를 신선하게 유지(긴 승인 대기에도 거짓 stale 0). None이면 무동작.
        if heartbeat is not None:
            heartbeat()
        # 취소 신호(US-009/H-06): 매 폴 최우선 확인 → set이면 RUN_ERROR 발행 후
        # 즉시 탈출(영구 승인 대기 데드락의 복구 경로). 워커(should_cancel)면 DB
        # 신호를, 웹(cancel_event)이면 인메모리 Event를 본다(OR — 어느 쪽이든 취소).
        # 둘 다 None(CLI·기존 호출)이면 이 분기는 없는 것과 같아 동작 무변.
        _cancelled = (cancel_event is not None and cancel_event.is_set()) or (
            should_cancel is not None and should_cancel()
        )
        if _cancelled:
            _emit_cancelled(store, run_id)
            suspended_state["_workflow_status"] = "cancelled"
            return suspended_state
        if max_polls is not None and polls >= max_polls:
            return suspended_state
        polls += 1

        # 경로 B(파일 폴백): approvals/*.json 처리 — INTERRUPT_RESOLVED 기록 포함
        poll_file_decisions(adir, store, db_path=db_path)

        # 경로 A·B 공통: DB에서 이 노드의 INTERRUPT_RESOLVED 결정을 찾는다
        decision = _find_resolved_decision(run_id, resume_node_id, db_path)
        if decision is not None:
            final_state = engine.resume_after_approval(suspended_state, decision)
            return final_state

        # poll_interval>0이면 항상 sleep(프로덕션 1.0/2.0 무변·유한 max_polls의
        # CPU-스핀 차단, H-03). poll_interval=0이면 생략(테스트 0-지연 보존).
        if poll_interval > 0:
            time.sleep(poll_interval)


def _find_resolved_decision(
    run_id: str, node_id: str, db_path: str | Path
) -> str | None:
    """run_id+node_id의 INTERRUPT_RESOLVED 결정(approve/reject)을 반환한다.

    아직 결정이 없으면 None.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json FROM events
            WHERE run_id=? AND type='INTERRUPT_RESOLVED'
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
    for row in rows:
        data = json.loads(row["payload_json"])
        if data.get("node_id", "") == node_id:
            return data.get("decision")
    return None


def _emit_cancelled(store: EventStore, run_id: str) -> None:
    """취소 시 RUN_ERROR 이벤트를 발행한다(조용한 hang → 명시적 신호 전환, US-009).

    FailReason enum을 건드리지 않으려 NODE_FAILED 대신 RUN_ERROR(자유 텍스트
    error 필드)를 쓴다(`len(FailReason)==3` 스키마 보존). 발행이 실패해도 취소
    자체는 진행해야 하므로(이벤트 실패가 hang을 만들면 안 됨) 예외를 삼킨다.
    """
    try:
        store.append(
            RunError(
                run_id=run_id,
                ts=datetime.now(timezone.utc).isoformat(),
                error="workflow cancelled by operator (US-009 /api/run/cancel)",
            )
        )
    except BaseException:
        # 이벤트 발행 실패가 취소(=hang 복구)를 막아선 안 된다 — 삼키고 진행.
        pass


# ── smoke 모드 (N1→N2 2노드) ─────────────────────────────────────────────────


def run_smoke(workflow_name: str, mock: bool = False) -> dict[str, Any]:
    """smoke: N1→N2 2노드 왕복 (되먹임 1회 포함, 수용 10)."""
    all_nodes, wf_name = _load_workflow(workflow_name)
    # smoke: 처음 2노드만, 두 번째 노드 next=None으로 종료
    nodes = [all_nodes[0], replace(all_nodes[1], next=None)]

    db_path = DEFAULT_DB_PATH
    store = EventStore(db_path)
    llm = _make_llm(mock, ["crm_get_customer", "order_get_latest"])

    initial_state: dict[str, Any] = {}
    if not mock:
        model = OFFICE_DEFAULT_MODEL
        initial_state["model"] = model
        print(f"[smoke] 모델: {model}, thinking=OFF, N1→N2 2노드(되먹임 1회)")

    executor = NodeExecutor(llm_client=llm, event_store=store)
    engine = WorkflowEngine(executor=executor, event_store=store, tools=MOCK_TOOLS)

    state = engine.run(nodes=nodes, initial_state=initial_state, workflow=wf_name)
    return state


# ── 풀런 모드 (4노드, N3 게이팅 → 폴링 재개) ─────────────────────────────────


def run_full(
    workflow_name: str,
    mock: bool = False,
    plan_mode: bool = False,
    poll_interval: float = 2.0,
    max_polls: int | None = None,
    cancel_event: threading.Event | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """풀런: 4노드 전체. N3 email_send는 게이팅 → suspend → 폴링 재개.

    오케스트레이션(산출물 3): enqueue → claim → run(task_id) →
    suspended면 영속+안내+폴링→resume → complete/fail → 요약.

    cancel_event: 운영자 취소 신호(US-009/H-06). 폴링 루프로 관통한다. None(CLI)이면
    취소 채널 없이 현 동작 무변. runner(웹)만 Event를 주입한다.

    query: 동적질문(Phase 8 C·AC-C0). 제공 시 initial_state["messages"]에 user
    메시지로 시딩한다(worker._run_one과 동형 — engine.py:212가 첫 노드 LLM에 직접
    주입). None이면 messages 미시딩(기존 워크플로 동작 무변).
    """
    nodes, wf_name = _load_workflow(workflow_name)

    db_path = DEFAULT_DB_PATH
    store = EventStore(db_path)
    queue = TaskQueue(db_path)
    llm = _make_llm(
        mock, _MOCK_NODE_SEQUENCES.get(wf_name, _DEFAULT_MOCK_NODE_SEQUENCE)
    )

    gating_policy = GatingPolicy()  # config/tools_policy.yaml
    executor = NodeExecutor(llm_client=llm, event_store=store)
    # Phase 5 US-P5-1: 풀런은 실도구(REAL_TOOLS)를 쓴다 — crm/order는 business.db
    # 실조회, email_send는 email_outbox에 멱등키로 기록(비가역 정확히-1회 토대).
    # 시드가 기존 mock 값과 정합하므로 결과는 동일(4a 게이트·회귀 0).
    # ★g5-4-5-review Major-1: game 워크플로만 error_fails_node=True로 띄운다 — n1
    #   required read가 입력 부재 시 {"error":...}를 돌려주면 노드 실패(NODE_FAILED)로
    #   드러내 단계 계약을 프로덕션에서도 성립시킨다. 비-game(crm_notify 등)은 False(무변).
    engine = WorkflowEngine(
        executor=executor,
        event_store=store,
        tools=REAL_TOOLS,
        gating_policy=gating_policy,
        error_fails_node=_is_game_workflow(wf_name),
    )

    initial_state: dict[str, Any] = {}
    if not mock:
        initial_model = OFFICE_DEFAULT_MODEL
        initial_state["model"] = initial_model
        print(f"[fullrun] 모델: {initial_model}, plan_mode={plan_mode}")
    # ★동적질문 messages seam(Phase 8 C·AC-C0): worker 경로와 동형 — query가 있으면
    # user 메시지로 시딩(engine.py:212 → 첫 노드 LLM). None이면 미시딩(기존 무변).
    if query:
        initial_state["messages"] = [{"role": "user", "content": query}]

    # ① enqueue → ② claim (직접 claim — 단일 순차, 동시성 1 유지)
    task_id = queue.enqueue(wf_name, payload={})
    claimed = queue.claim()
    if claimed is None or claimed["id"] != task_id:
        # 다른 작업이 먼저 잡힌 경우(드묾) — 안전하게 fail 처리
        queue.fail(task_id)
        raise RuntimeError(f"작업 claim 실패: task_id={task_id}")

    # ③~⑥ claim된 task 실행 본체 — run_claimed_task로 추출(Phase 6 6a).
    # run_full은 시그니처·동작 100% 불변: enqueue→claim은 여기서, 그 이후
    # (run→suspend면 폴링 재개→complete/fail)는 run_claimed_task가 담당한다.
    # 워커(office.worker)도 같은 run_claimed_task를 호출해 코드 재사용 + 동작 동일.
    return run_claimed_task(
        task_id,
        nodes=nodes,
        wf_name=wf_name,
        engine=engine,
        store=store,
        queue=queue,
        db_path=db_path,
        initial_state=initial_state,
        plan_mode=plan_mode,
        poll_interval=poll_interval,
        max_polls=max_polls,
        cancel_event=cancel_event,
    )


def run_claimed_task(
    task_id: int,
    *,
    nodes: list[Node],
    wf_name: str,
    engine: WorkflowEngine,
    store: EventStore,
    queue: TaskQueue,
    db_path: str | Path,
    initial_state: dict[str, Any] | None = None,
    plan_mode: bool = False,
    poll_interval: float = 2.0,
    max_polls: int | None = None,
    cancel_event: threading.Event | None = None,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """이미 claim된 task 1건을 실행하고 complete/fail까지 마킹한다(Phase 6 6a).

    run_full의 ③~⑥ 본체를 추출한 함수다(enqueue·claim은 제외 — 호출자가 이미
    claim한 task_id를 받는다). 두 호출자가 공유한다:
      · run_full(CLI·기존 테스트): enqueue→claim 후 이 함수를 cancel_event 경로로.
      · office.worker(F2 별도 워커): claim 후 should_cancel(DB 신호)·heartbeat 경로로.

    흐름(run_full ③~⑥과 동일): engine.run(task_id=...) → suspended면 영속+suspend+
    안내+폴링 재개(wait_for_decision_and_resume) → complete/fail / 아니면 즉시
    complete/fail. cancel/heartbeat 콜백은 폴링 재개로 관통한다(워커 DB 신호 재배선).

    Args:
        task_id      : 이미 claim된 TaskQueue row id.
        nodes        : 워크플로 노드 리스트.
        wf_name      : 워크플로 이름.
        engine       : 실행·재개에 쓸 WorkflowEngine(REAL_TOOLS 등 설정 완료).
        store        : EventStore.
        queue        : TaskQueue(상태전이용).
        db_path      : SQLite 경로.
        initial_state: 초기 state(모델 등). None이면 빈 dict.
        plan_mode    : Plan Mode 여부.
        poll_interval: 승인 폴링 간격(초).
        max_polls    : 최대 폴링 횟수. None이면 무기한(프로덕션).
        cancel_event : 인메모리 취소 신호(웹·기존 경로). None이면 미사용.
        should_cancel: DB 취소 신호 폴(워커 경로). None이면 미사용.
        heartbeat    : liveness 갱신 콜백(워커 경로). None이면 무동작.
        run_id       : 이번 실행 식별자. None이면 새로 생성(US-P6B-0). 2a 재개
                       (recovery.py)는 **기존 run_id**를 넘겨 같은 run으로 재개한다
                       (B1 dedup·B2 멱등키로 비가역 이중발송 0).

    Returns:
        최종 state(_workflow_status 포함).
    """
    # ③ run(task_id=...) — interrupt 시 payload에 task_id가 실려 resume 가능
    # ★US-P6B-0: run_id를 여기서 생성해 RUN_STARTED 발행 전 task payload에 1줄 기록한다
    #   (running 고아 식별·recovery.py). engine.run에 같은 run_id를 넘겨 정합 보장 —
    #   suspended_state run_id(아래 _persist_suspended_state)와도 동일 run의 같은 값.
    #   2a 재개(recovery.py)가 기존 run_id를 넘기면 그대로 재사용한다(같은 run 재개).
    import uuid

    if run_id is None:
        run_id = str(uuid.uuid4())
    _record_run_id_in_payload(task_id, run_id, db_path)
    state = engine.run(
        nodes=nodes,
        initial_state=initial_state,
        run_id=run_id,
        workflow=wf_name,
        task_id=task_id,
        plan_mode=plan_mode,
    )

    status = state.get("_workflow_status", "unknown")

    if status == "suspended":
        # ④ suspended_state 영속 + suspend 마킹 + 안내
        _persist_suspended_state(task_id, state, db_path)
        queue.suspend(task_id)
        print("[fullrun] N3 email_send에서 멈춤(승인 대기). 다음으로 승인하세요:")
        print("  python -m office.approve list")
        print("  python -m office.approve approve <interrupt_id>")
        print("[fullrun] 승인 결정을 폴링합니다...")

        # ⑤ 폴링 → resume (cancel/heartbeat 콜백 관통 — 워커 DB 신호 재배선)
        loaded = load_suspended_state(task_id, db_path) or state
        final_state = wait_for_decision_and_resume(
            engine=engine,
            suspended_state=loaded,
            task_id=task_id,
            store=store,
            db_path=db_path,
            poll_interval=poll_interval,
            max_polls=max_polls,
            cancel_event=cancel_event,
            should_cancel=should_cancel,
            heartbeat=heartbeat,
        )
        # ⑥ complete / fail 마킹
        final_status = final_state.get("_workflow_status", "unknown")
        if final_status == "success":
            queue.complete(task_id)
        else:
            queue.fail(task_id)
        state = final_state
    else:
        # interrupt 없이 끝난 경우(plan_mode 일괄 통과 등)
        if status == "success":
            queue.complete(task_id)
        else:
            queue.fail(task_id)

    return state


# ── CLI 진입점 ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="워크플로 실행기 (오케스트레이션)")
    parser.add_argument(
        "--workflow", required=True, help="워크플로 이름 (예: crm_notify)"
    )
    parser.add_argument(
        "--smoke", action="store_true", help="smoke 모드: N1→N2 2노드만"
    )
    parser.add_argument(
        "--plan-mode",
        action="store_true",
        help="Plan Mode: 시작 시 계획 1회 제시, needs-approval 일괄 통과",
    )
    parser.add_argument(
        "--mock", action="store_true", help="mock 모드: 실서버 없이 fake LLM"
    )
    parser.add_argument("--report", action="store_true", help="실행 후 리포트 생성")
    args = parser.parse_args()

    if args.smoke:
        print(f"[run] smoke 모드: {args.workflow}")
        state = run_smoke(args.workflow, mock=args.mock)
    else:
        print(f"[run] 풀런 모드: {args.workflow}")
        state = run_full(args.workflow, mock=args.mock, plan_mode=args.plan_mode)

    status = state.get("_workflow_status", "unknown")
    run_id = state.get("run_id", "")
    print(f"[run] 완료: status={status}, run_id={run_id}")

    # 런 요약 (노드별 latency·finish_reason·reasoning_chars — 수용 12)
    print(format_run_summary(state))

    if args.report:
        report_path = write_run_report(run_id, state)
        print(f"[run] 리포트: {report_path}")

    # smoke 모드: success 아니면 오류 종료
    if args.smoke and status != "success":
        print(f"[run] smoke FAILED: {status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
