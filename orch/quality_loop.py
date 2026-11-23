"""SR/renewal P4c — 연속 품질 루프(§21.4) *배관*. Opus 정밀검수는 사람·코드 밖(§7 HARD).

스펙 권위([[renewal-spec-is-authority]] · SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md):
  - §21.4 ①정밀검수→②피드백(핸드오프)→③재검수(라운드캡)→④교훈적재(KB)→⑤RPI 선조회.
  - §29 근거기반 보고 형식(감사 리포트 = 결함·심각도·근거·개선제안·확신).
  - §27.1 핸드오프 브리프 정본 · §18.2 Capture→Index→Retrieve · §20 가역성/우선순위.

★§7 HARD 경계(DR-3): 이 모듈은 최강 모델을 **자동호출하지 않는다**. ①정밀검수(최대
사고)는 *사람이 개시하는 대화형 세션*이고, 그 산출(§29 감사 리포트)을 이 모듈은 **파일
경로로 입력받아** 후속 배관만 수행한다. 비대화 호출 표면(headless ``-p`` 플래그·Agent
SDK·벤더 클라이언트·API 키 환경변수)은 일절 두지 않는다 — test_quality_loop §7 단위가
이 소스를 정적 grep 으로 강제(금지 리터럴은 *테스트에만* 존재·본 소스엔 부재).

이 모듈은 orch 코어 그래프(graph.py / org_graph.py / app.py)와 **독립한 신규 표면**이다
(동결셋 무관·코어 0수정). 기존 표면을 재사용한다(중복 생성 금지):
  - 핸드오프 = ``orch.handoff``(HandoffBrief/write_brief).
  - 교훈 색인 = ``orch.knowledge_lifecycle``(ArtifactProvenance/capture_and_index/
    is_searchable) — store 는 주입(duck-typed).
  - 가역성/우선순위 = ``orch.authority``(decide/Tier).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, Union

from pydantic import BaseModel, Field

from orch import authority
from orch.handoff import HandoffBrief, write_brief
from orch.knowledge_lifecycle import (
    ArtifactProvenance,
    capture_and_index,
    is_searchable,
)

# §29.3 / §21.4 심각도 어휘(감사 리포트 표기). blocker > major > minor.
Severity = Literal["blocker", "major", "minor"]

# §21.4② 책임 역할(DEV/ART/RESEARCH/기획). 한+영 키워드 → 정규 역할(결정론·LLM 0).
_ROLE_CANON: tuple[str, ...] = ("DEV", "ART", "RESEARCH", "기획")
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "DEV": (
        "dev",
        "코드",
        "구현",
        "엔지니어",
        "engineer",
        "backend",
        "frontend",
        "버그",
    ),
    "ART": ("art", "아트", "에셋", "asset", "이미지", "비주얼", "visual", "디자인"),
    "RESEARCH": ("research", "리서치", "조사", "근거", "citation", "출처"),
    "기획": ("기획", "plan", "planner", "design-doc", "gdd", "콘텐츠", "content"),
}

# §29 감사 리포트 결함 표기 인식. 심각도 토큰(✅/🟡/❌ 또는 blocker/major/minor·이모지 🔴).
_SEV_TOKEN: dict[str, Severity] = {
    "blocker": "blocker",
    "🔴": "blocker",
    "critical": "blocker",
    "치명": "blocker",
    "major": "major",
    "🟡": "major",
    "중대": "major",
    "minor": "minor",
    "사소": "minor",
}

# 결함 섹션 헤더 인식(§29 "결함"·"defect"·"5. 블로커" 류). 대소문자 무시.
_DEFECT_HEADER = re.compile(
    r"^\s{0,3}#{1,6}\s*.*(결함|defect|블로커|blocker|finding|이슈|issue)",
    re.IGNORECASE,
)
# 결함 항목 줄(불릿 + 본문). "- 🔴 [major] 설명 …" / "- (blocker) 설명".
_DEFECT_ITEM = re.compile(r"^\s*[-*]\s+(?P<body>\S.*)$")

# §21.4③ 재검수 라운드 상한(termination P0·노드 외부 정수 카운터·합의 종료 금지).
ROUND_CAP = 3


class Defect(BaseModel):
    """§29 감사 리포트에서 추출한 결함 한 건(§21.4② 피드백 단위)."""

    description: str
    severity: Severity = "major"
    role: str = "DEV"  # 책임 역할 ∈ _ROLE_CANON


def _classify_severity(text: str) -> Severity:
    """결함 줄에서 심각도 토큰을 뽑는다(없으면 보수적으로 major)."""
    low = text.lower()
    for token, sev in _SEV_TOKEN.items():
        if token in low or token in text:  # 영문 토큰은 low·이모지/한글은 원문.
            return sev
    return "major"


def _classify_role(text: str) -> str:
    """결함 설명 → 책임 역할(키워드 결정론·LLM 0). 미매치는 DEV(구현 기본)."""
    low = text.lower()
    for role in _ROLE_CANON:
        if any(kw in low for kw in _ROLE_KEYWORDS[role]):
            return role
    return "DEV"


def parse_audit_report(path: Union[Path, str]) -> list[Defect]:
    """(i) §29 감사 리포트(사람 Opus 세션 산출)를 파일 경로로 읽어 결함을 추출.

    결함 섹션(헤더 ⊇ "결함"/"defect"/"블로커"/"finding")의 불릿 항목을 Defect 로 파싱한다.
    각 항목에서 심각도(✅/🟡/❌·blocker/major/minor·🔴)와 책임 역할(키워드)을 결정론으로 분류.

    Opus 세션 *자체*는 이 함수 밖(사람·대화형·§7). 이 함수는 그 산출물을 *입력*으로만 받는다.
    """
    text = Path(path).read_text(encoding="utf-8")
    defects: list[Defect] = []
    in_defect_section = False
    for line in text.splitlines():
        if _is_section_header(line):
            in_defect_section = bool(_DEFECT_HEADER.match(line))
            continue
        if not in_defect_section:
            continue
        m = _DEFECT_ITEM.match(line)
        if not m:
            continue
        body = m.group("body").strip()
        defects.append(
            Defect(
                description=body,
                severity=_classify_severity(body),
                role=_classify_role(body),
            )
        )
    return defects


def _is_section_header(line: str) -> bool:
    """마크다운 ATX 헤더(`#`~`######` + 공백) 줄인지."""
    s = line.lstrip()
    if not s.startswith("#"):
        return False
    n = len(s) - len(s.lstrip("#"))
    return 1 <= n <= 6 and s[n : n + 1] == " "


def _slugify(text: str, *, max_len: int = 48) -> str:
    """결함 설명 → 파일 슬러그(ASCII 영숫자+하이픈·한글은 제거되므로 폴백 보장)."""
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    slug = ascii_only[:max_len].strip("-")
    return slug or "lesson"


_PRIORITY_BY_SEVERITY: dict[Severity, str] = {
    "blocker": "high",
    "major": "med",
    "minor": "low",
}


def defect_to_handoff(
    defect: Defect,
    vault_dir: Union[Path, str],
    *,
    cycle: int = 0,
    index: int = 0,
    from_role: str = "QUALITY",
) -> tuple[Path, HandoffBrief]:
    """(ii) defect → §27.1 핸드오프 브리프 → ``<vault>/specs/<task_id>.md`` 생성.

    책임 에이전트(defect.role) 라우팅 + ``authority.decide`` 로 §20 가역성/우선순위 부여
    (reversal_cost/scope 가 가역성·priority 를 결정론으로 산출). 반환 (경로, 브리프).
    """
    task_id = f"qa-fix-c{cycle}-{index:02d}-{_slugify(defect.description)}"
    tier, inputs, reason = authority.decide(defect.description, {})
    reversibility = "one-way" if inputs.get("reversibility") == "one_way" else "two-way"
    # 우선순위: 심각도 우선, 단 Escalate(비가역/owner-reserved/고비용)면 high 로 격상.
    priority = _PRIORITY_BY_SEVERITY[defect.severity]
    if tier is authority.Tier.ESCALATE:
        priority = "high"

    brief = HandoffBrief(
        task_id=task_id,
        from_=from_role,
        to=defect.role,
        objective=f"[품질결함 수정] {defect.description}",
        context_refs=[],
        acceptance_criteria=[
            "정밀검수가 지적한 결함이 재현되지 않음",
            "재발방지 교훈이 vault/quality/ 에 적재됨",
        ],
        constraints=[f"가역성 {reversibility}", f"권한판정 {tier.value} ({reason})"],
        reversibility=reversibility,
        confidence=float(inputs.get("confidence", 0.55)),
        priority=priority,
        artifacts_expected=[f"vault/specs/{task_id}.md"],
    )
    return write_brief(brief, vault_dir), brief


# ── §21.4④ 교훈 writer (Quality Lessons KB · ADR형) ────────────────────────────
_LESSON_TEMPLATE_REL = Path("_templates") / "quality-lesson.md"


def _load_lesson_template(vault_dir: Path) -> str | None:
    """``<vault>/_templates/quality-lesson.md`` 가 있으면 그 내용(헤더 보존용). 없으면 None."""
    tmpl = vault_dir / _LESSON_TEMPLATE_REL
    if tmpl.is_file():
        return tmpl.read_text(encoding="utf-8")
    return None


def render_lesson(
    *,
    defect: Defect,
    root_cause: str,
    resolution: str,
    prevention: str,
    source: str,
    handoff_ref: str = "",
    checklist: list[str] | None = None,
    on_date: str | None = None,
) -> str:
    """§21.4④ 교훈(ADR형) 마크다운 렌더 — 결함 패턴·원인·해결·재발방지·검수 체크리스트.

    템플릿(quality-lesson.md)과 같은 섹션 구조. 결정론(입력만 의존·날짜 주입 가능).
    """
    on_date = on_date or date.today().isoformat()
    checklist = checklist or [f"이 패턴({_slugify(defect.description)}) 재현 점검"]
    title = defect.description.strip().splitlines()[0][:80]
    handoff_line = f"\n## 핸드오프\n{handoff_ref}\n" if handoff_ref else ""
    check_lines = "\n".join(f"- [ ] {c}" for c in checklist)
    return (
        f"# Quality Lesson: {title}\n"
        f"- Status: captured\n"
        f"- Date: {on_date}\n"
        f"- Severity: {defect.severity}\n"
        f"- Owner-role: {defect.role}\n"
        f"- Source: {source}\n"
        f"{handoff_line}"
        f"\n## 결함 패턴 (Defect)\n{defect.description}\n"
        f"\n## 원인 (Root Cause)\n{root_cause}\n"
        f"\n## 해결 (Resolution)\n{resolution}\n"
        f"\n## 재발방지책 (Prevention)\n{prevention}\n"
        f"\n## 검수 체크리스트 (Review Checklist)\n{check_lines}\n"
    )


def write_lesson(
    store: Any,
    *,
    defect: Defect,
    root_cause: str,
    resolution: str,
    prevention: str,
    source: str,
    vault_dir: Union[Path, str],
    handoff_ref: str = "",
    checklist: list[str] | None = None,
    on_date: str | None = None,
    chunk_id: int = 0,
) -> tuple[Path, int]:
    """(iii) 교훈을 ``<vault>/quality/<slug>.md`` 로 기록 + Qdrant 색인.

    ``orch.knowledge_lifecycle.capture_and_index`` 로 Quality Lessons KB 에 trusted
    provenance 와 함께 증분 색인(차기 RPI 가 search_hybrid 로 회수 — §21.4⑤). store 는
    주입(duck-typed: upsert/search_hybrid). 반환 (교훈 경로, 업서트 포인트 수).
    """
    vault_dir = Path(vault_dir)
    # 템플릿 존재 확인(없으면 인라인 렌더로 폴백·게이트 비차단). 헤더 호환만 보장.
    _ = _load_lesson_template(vault_dir)
    text = render_lesson(
        defect=defect,
        root_cause=root_cause,
        resolution=resolution,
        prevention=prevention,
        source=source,
        handoff_ref=handoff_ref,
        checklist=checklist,
        on_date=on_date,
    )
    slug = _slugify(defect.description)
    out = vault_dir / "quality" / f"{slug}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    # 내부 산출물 = trusted(§18.1). source_url 불요. capture_and_index → Qdrant.
    n = capture_and_index(
        store,
        text=text,
        source=str(out),
        provenance=ArtifactProvenance(provenance="trusted", author="QUALITY"),
        chunk_id=chunk_id,
    )
    return out, n


# ── §21.4⑤ RPI 선조회 게이트 (차기 Research 가 Quality Lessons KB 회수) ─────────
class QualityLesson(BaseModel):
    """RPI 선조회로 회수한 교훈 한 건(검색 결과 투영)."""

    text: str
    source: str
    score: float


def prefetch_quality_lessons(
    query: str, store: Any, top_n: int = 5
) -> list[QualityLesson]:
    """(iv) §21.4⑤ RPI 선조회: 차기 Research 단계가 Quality Lessons KB 를 회수.

    ``store.search_hybrid`` 로 교훈을 검색해 같은 결함 재발을 방지(검수 체크리스트가 사이클
    마다 진화). store 는 주입(duck-typed). 회수된 교훈을 QualityLesson 목록으로 투영.
    """
    return [
        QualityLesson(text=c.text, source=c.source, score=float(s))
        for c, s in store.search_hybrid(query, top_n=top_n)
    ]


def lesson_is_retrievable(store: Any, unique_token: str, top_n: int = 20) -> bool:
    """교훈이 실제로 회수되는지(searchable-after-emit 위임 — §18.2)."""
    return is_searchable(store, unique_token, top_n=top_n)


# ── §21.4③ 재검수 라운드캡 루프(termination P0 · 노드 외부 정수 카운터) ─────────
class ReviewOutcome(BaseModel):
    """재검수 루프 종료 결과.

    ``status``:
      - ``passed``  — 재검수 통과(결함 0). ``rounds`` = 소요 라운드.
      - ``escalate``— 라운드 상한 도달(결함 잔존). Owner 에스컬레이션/범위축소 분기.
    """

    status: Literal["passed", "escalate"]
    rounds: int
    remaining: list[Defect] = Field(default_factory=list)
    note: str = ""


def run_review_loop(
    review_fn: Any,
    *,
    round_cap: int = ROUND_CAP,
) -> ReviewOutcome:
    """§21.4③ 재검수 루프 — *카운터 구동* 종료(합의/조건 종료 금지·termination P0).

    ``review_fn(round_index) -> list[Defect]`` 를 최대 ``round_cap`` 회 호출한다. 결함이
    0 이면 즉시 ``passed``. 상한까지 결함이 남으면 ``escalate``(Owner 에스컬레이션 또는
    범위축소·§21.4③). 정수 카운터(``for`` 유한 범위)라 무한 루프 구조적 0 — review_fn 이
    항상 실패해도 정확히 ``round_cap`` 회 후 종료한다.

    Args:
        review_fn: 라운드별 재검수 호출(0-기반 인덱스 → 잔존 결함 목록). 실제 정밀검수는
            외부 사람 Opus 세션이며, 이 함수는 그 결과(결함 목록)만 카운트한다(§7 HARD).
        round_cap: 재검수 상한(기본 ROUND_CAP=3).

    Returns:
        ReviewOutcome — passed(통과) | escalate(상한 도달).
    """
    if round_cap < 1:
        raise ValueError("round_cap 은 1 이상이어야 한다(최소 1회 검수)")
    remaining: list[Defect] = []
    for i in range(round_cap):  # ★유한 범위 — 무한 루프 불가(termination P0)
        remaining = list(review_fn(i))
        if not remaining:
            return ReviewOutcome(
                status="passed",
                rounds=i + 1,
                remaining=[],
                note="재검수 통과(결함 0)",
            )
    return ReviewOutcome(
        status="escalate",
        rounds=round_cap,
        remaining=remaining,
        note=(
            f"라운드 상한({round_cap}) 도달·결함 {len(remaining)}건 잔존 → "
            "Owner 에스컬레이션 또는 범위축소(§21.4③)"
        ),
    )
