"""US-G6-1 — 자연어 게임제작 지시 분류기(pure·결정론·LLM 0).

디스코드 자유 발화를 (게임 단계 지시 | 모호 | 일반대화)로 분류한다. 봇 불변식
'LLM 호출 0' 유지 — 외부호출 없는 키워드/의도 매칭(테스트가능·결정론).

분류 규칙(★오탐 0 우선·보수적):
- GameStage  : 단계 키워드 AND 제작 지시어(directive) 동시 → 해당 game_* 워크플로.
- Ambiguous  : 제작 지시어 AND 게임 맥락어 있으나 단계 불명 → 단계 목록 안내.
- Chat       : 그 외 전부(일반 대화·게임 캐주얼 언급·질문) → 기존 인바운드 폴백.
같은 입력 → 항상 같은 출력.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 단계 키워드(KO+EN·소문자 비교). 매칭 우선순위=이 dict 순서(design→qa).
STAGE_KEYWORDS: dict[str, list[str]] = {
    "game_design": ["디자인", "설계", "기획", "컨셉", "메타", "design", "concept"],
    "game_balance": ["밸런스", "벨런스", "수치", "난이도", "balance", "balancing"],
    "game_content": [
        "콘텐츠",
        "컨텐츠",
        "플레이버",
        "대사",
        "텍스트",
        "content",
        "flavor",
    ],
    "game_assemble": ["조립", "어셈블", "통합", "빌드", "런타임", "assemble", "build"],
    "game_qa": ["qa", "검증", "품질", "검수", "테스트", "quality"],
}
STAGE_LABELS: dict[str, str] = {
    "game_design": "설계",
    "game_balance": "밸런스",
    "game_content": "콘텐츠",
    "game_assemble": "조립",
    "game_qa": "QA",
}
# 제작 지시어(명령 의도 — 이게 있어야 GameStage/Ambiguous로 승격·오탐 차단).
# ★한국어=부분일치(조사/어미 결합 대응). 영어=★단어경계 일치(아래 _has_directive) —
#   substring 이면 "do"가 download/done 에, "run"이 running 에 오탐된다. 또 "do"는
#   너무 일반적인 동사라(질문 "what do you think") 제외해 영어 오탐을 차단한다.
_DIRECTIVE_KO = [
    "시작",
    "진행",
    "작업",
    "만들",
    "제작",
    "생성",
    "조정",
    "추가",
    "갱신",
    "해줘",
    "해주",
    "하자",
    "하기",
    "돌려",
    "실행",
    "수행",
    "업데이트",
]
_DIRECTIVE_EN = {
    "start",
    "make",
    "build",
    "run",
    "create",
    "generate",
    "produce",
    "update",
}
# 게임 맥락어(Ambiguous 판정 보조).
_GAME_CONTEXT = ["게임", "game"]

# ★비명령 신호(질문/부정/과거보고/희망) — 단계어+지시어가 있어도 '명령'이 아니므로
# GameStage/Ambiguous 로 승격하지 않고 Chat 으로 돌린다. 키워드 분류기는 의도(명령 vs
# 질문/평서)를 완벽히 가릴 수 없어 100% 오탐 0 은 원리상 불가하나, 이 가드가 흔한 비명령
# (질문·부정·과거 평서·희망)을 차단해 오탐을 실질적으로 0 에 수렴시킨다(LLM 미사용).
_NON_COMMAND_KO = [
    "?",
    "？",
    "어때",
    "어떄",
    "어떻게",
    "궁금",
    "될까",
    "할까",
    "을까",
    "ㄹ까",  # 질문
    "싫",
    "별로",
    "말고",
    "안돼",
    "안 돼",
    "하지마",
    "하지 마",  # 부정
    "됐",
    "봤",
    "였어",
    "했어",
    "었어",
    "았어",  # 과거 보고
    "하고 싶",
    "했으면",  # 희망(명령 아님)
]
_NON_COMMAND_EN = {
    "no",
    "not",
    "never",
    "dont",
    "cant",
    "wont",
    "didnt",
    "failed",
    "yesterday",
    "broke",
    "was",
    "were",
    "want",
}

GAME_STAGES = list(STAGE_KEYWORDS.keys())


@dataclass
class Intent:
    kind: str  # "stage" | "ambiguous" | "chat"
    workflow: str | None = None
    stage_label: str | None = None


def _match_stage(low: str) -> str | None:
    for wf, kws in STAGE_KEYWORDS.items():
        if any(k in low for k in kws):
            return wf
    return None


def _has_directive(low: str) -> bool:
    """제작 지시어 존재 여부 — 한국어는 부분일치, 영어는 단어경계 일치.

    영어를 단어경계로 보는 이유: substring 이면 "do"가 done/download 에, "run"이
    running/trunk 에 오탐된다(질문 "what do you think of the game design?" 같은
    일반 발화가 GameStage 로 잘못 분류됨). 한국어는 조사/어미가 붙으므로 부분일치가
    자연스럽다.
    """
    if any(d in low for d in _DIRECTIVE_KO):
        return True
    words = set(re.findall(r"[a-z]+", low))
    return bool(words & _DIRECTIVE_EN)


def _is_non_command(low: str) -> bool:
    """질문/부정/과거보고/희망 등 ★비명령 신호 존재 여부.

    있으면 단계어+지시어가 있어도 '명령'이 아니므로 GameStage 로 승격하지 않는다.
    영어는 아포스트로피 제거 후 단어경계 토큰으로 본다(don't→dont, can't→cant 매칭).
    """
    if any(m in low for m in _NON_COMMAND_KO):
        return True
    words = set(re.findall(r"[a-z]+", low.replace("'", "")))
    return bool(words & _NON_COMMAND_EN)


def classify_intent(text: str) -> Intent:
    """자연어 발화를 Intent로 분류(결정론·LLM 0).

    우선순위: ① 비명령(질문/부정/과거/희망) → Chat(★워크플로 트리거 0). ② 단계어 +
    제작 지시어 → GameStage. ③ 지시어 + 게임 맥락(단계 불명) → Ambiguous. ④ 그 외 → Chat.
    """
    low = (text or "").lower()
    # ① 비명령(질문/부정/과거 평서/희망)은 명령이 아니므로 절대 워크플로를 트리거하지 않음.
    if _is_non_command(low):
        return Intent(kind="chat")
    has_directive = _has_directive(low)
    has_context = any(g in low for g in _GAME_CONTEXT)
    stage = _match_stage(low)
    # ② GameStage: 단계 키워드 + 제작 지시어 동시(질문·캐주얼 언급은 지시어 없어 Chat).
    if stage is not None and has_directive:
        return Intent(kind="stage", workflow=stage, stage_label=STAGE_LABELS[stage])
    # ③ Ambiguous: 명확한 제작 지시(지시어+게임맥락)인데 단계 불명.
    if has_directive and has_context and stage is None:
        return Intent(kind="ambiguous")
    # ④ 그 외 전부 Chat(★오탐 보수적).
    return Intent(kind="chat")
