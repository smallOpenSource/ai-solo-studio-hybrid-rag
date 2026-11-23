"""한국어 발행 텍스트 윤문(de-AI-tell).

글 발행 등 텍스트 콘텐츠 발행 시, AI 특유의 '티'(번역투·결산 피벗 lexicon·이모지·균일 리듬·
hedging 등)를 제거해 자연스러운 한국어로 윤문한다. ``orch/humanize_rules.md``(이 저장소에서
직접 작성한 룰북)를 system 룰북으로 **단일 콜 탐지+윤문+자체검증**을 모델 seam 으로 수행한다.

설계 불변:
- ★결정론/격리: ``caller``(ModelCaller seam)를 인자로 받는다 — 기본은 graph._active_caller()
  (provider primary/fallback). 테스트/eval 은 stub caller 주입으로 우회(ContextVar 미사용).
- ★의미 불변: 룰북 Do-NOT(고유명사·수치·날짜·인용 보존)을 프롬프트로 강제 + 과윤문 가드
  (변경률 > 50% = 원문 롤백). 빈 입력·룰북 부재·모델 실패·비한국어 = 원문 그대로(발행 비차단).
- ★graph-agnostic: graph 를 top-level import 하지 않는다(graph 가 humanize 를 import → 순환 회피).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).resolve().parent / "humanize_rules.md"
_OVER_EDIT_ABORT = 0.50  # 변경률 초과 = 과윤문 → 원문 롤백
_MIN_KOREAN = (
    0.30  # 한글 비율 미만이면 비한국어로 보고 스킵(영문 게임콘텐츠 등 오윤문 방지)
)
_MAX_TOKENS = 4096

ModelCaller = Callable[[str, float, int], str]


def _load_rules() -> str:
    try:
        return _RULES_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _korean_ratio(s: str) -> float:
    """문자(알파넘) 중 한글 음절 비율(0..1)."""
    han = sum(1 for c in s if "가" <= c <= "힣")
    letters = sum(1 for c in s if c.isalnum())
    return han / letters if letters else 0.0


def _change_rate(a: str, b: str) -> float:
    """문자 수준 변경률(0..1) = 1 − difflib 유사도(의존 0)."""
    import difflib  # noqa: PLC0415

    if not a and not b:
        return 0.0
    return round(1.0 - difflib.SequenceMatcher(None, a, b).ratio(), 4)


def _default_caller() -> ModelCaller:
    # lazy import — graph ↔ humanize 순환 회피(graph 가 humanize 를 import 하므로).
    from orch.graph import _active_caller  # noqa: PLC0415

    return _active_caller()


def _strip_fences(out: str) -> str:
    out = out.strip()
    if out.startswith("```"):
        out = out.strip("`")
        for tag in ("markdown\n", "md\n", "text\n"):
            if out.lower().startswith(tag):
                out = out[len(tag) :]
        out = out.strip()
    return out


def humanize_korean(text: str, *, caller: Optional[ModelCaller] = None) -> dict:
    """발행 텍스트를 룰북으로 1콜 윤문한다.

    Returns: ``{"text", "applied"(bool), "change_rate"(float), "note"(str)}``.
    원문 보존 우선 — 빈 입력/룰북 부재/비한국어/모델 실패/과윤문(>50%) 시 원문 그대로(applied=False).
    """
    src = text or ""
    if not src.strip():
        return {"text": src, "applied": False, "change_rate": 0.0, "note": "empty"}
    if _korean_ratio(src) < _MIN_KOREAN:
        return {"text": src, "applied": False, "change_rate": 0.0, "note": "not-korean"}
    rules = _load_rules()
    if not rules:
        return {"text": src, "applied": False, "change_rate": 0.0, "note": "no-rules"}

    call = caller or _default_caller()
    prompt = (
        "당신은 한국어 윤문 전문가다. 아래 [룰북]의 패턴을 탐지해 '[원문]'의 AI 티를 제거하고 "
        "자연스러운 한국어로 윤문하라.\n"
        "★철칙: 의미 불변 — 사실·주장·수치·날짜·고유명사·큰따옴표 직접 인용은 한 글자도 바꾸지 "
        "말 것. 원문에 없던 비유·수사·정보를 추가하지 말 것. 장르·격식(register) 보존. 변경률 30% 이하.\n"
        "출력은 윤문된 본문 텍스트만 — 설명·머리말·코드펜스 없이.\n\n"
        f"[룰북]\n{rules}\n\n[원문]\n{src}"
    )
    try:
        raw = call(prompt, 0.2, _MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("humanize 모델 호출 실패(%s) — 원문 반환", type(exc).__name__)
        return {
            "text": src,
            "applied": False,
            "change_rate": 0.0,
            "note": "call-failed",
        }

    out = _strip_fences(raw or "")
    if not out:
        return {"text": src, "applied": False, "change_rate": 0.0, "note": "empty-out"}
    rate = _change_rate(src, out)
    if rate > _OVER_EDIT_ABORT:
        logger.warning("humanize 과윤문(Δ%.0f%% > 50%%) — 원문 롤백", rate * 100)
        return {
            "text": src,
            "applied": False,
            "change_rate": rate,
            "note": "over-edit-rollback",
        }
    return {"text": out, "applied": True, "change_rate": rate, "note": "ok"}
