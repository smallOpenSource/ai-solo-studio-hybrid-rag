"""SR/renewal P2-core — 무손실 전파 최소셋: 핸드오프 브리프 + vault writer + DoD 게이트.

스펙 권위(renewal-spec-is-authority · SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md):
  - §27.1 핸드오프 브리프 — **필드 정본**(저장: ``vault/specs/<task_id>.md`` 의
    YAML frontmatter + 본문).
  - §18.3 본문 섹션 템플릿(목적 / 결론·권고 / 수용 기준 / 제약·미해결 / 참조).
  - §18.5 지식 적재 의무(Definition of Done) — 3종 적재 + 핸드오프 브리프.

이 모듈은 orch 코어 그래프(graph.py / org_graph.py / outbound.py)와 **독립한 신규 표면**
이다(동결셋 무관 · 코어 0수정). 자유 형식 전달(telephone game) 금지(§18.2 Blackboard)
— 단계 간 인계는 이 구조화 브리프로만 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

# §20 가역성 · §27.1 priority 허용값(리터럴 — 계약상 모호성 제거 §27).
Reversibility = Literal["two-way", "one-way"]
Priority = Literal["low", "med", "high"]

# §18.3 본문 섹션 템플릿 — 핸드오프 브리프 본문(요약 재전달 금지·링크/근거 동반).
_BODY_SECTIONS: tuple[str, ...] = (
    "## 목적(1줄)",
    "## 결론/권고",
    "## 수용 기준",
    "## 제약·미해결",
    "## 참조",
)

# §27.1 필드 정본 — frontmatter 직렬화 키 순서(스펙 YAML 블록 순서와 1:1).
_FIELD_ORDER: tuple[str, ...] = (
    "task_id",
    "from",
    "to",
    "objective",
    "context_refs",
    "acceptance_criteria",
    "constraints",
    "reversibility",
    "confidence",
    "priority",
    "artifacts_expected",
)

_PathLike = Union[Path, str, None]


class HandoffBrief(BaseModel):
    """§27.1 핸드오프 브리프 — 단계 간 전달 단위(필드 정본).

    필드는 스펙 §27.1 YAML 블록과 1:1 리터럴이다. ``from`` 은 파이썬 예약어라
    ``from_`` 으로 두고 alias("from")로 직렬화/파싱한다(``populate_by_name`` 로
    두 이름 모두 허용). 저장 위치는 ``vault/specs/<task_id>.md``.
    """

    # §27.1 11필드가 정본. §18.3 예시 frontmatter 의 부가 키(type/date/sources 등)는
    # 관용적으로 무시(pydantic extra="ignore" 기본) — 라운드트립은 정본 11필드로 보존.
    model_config = ConfigDict(populate_by_name=True)

    task_id: str
    from_: str = Field(alias="from")
    to: str
    objective: str
    context_refs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    reversibility: Reversibility = "two-way"
    confidence: float = Field(ge=0.0, le=1.0)
    priority: Priority = "med"
    artifacts_expected: list[str] = Field(default_factory=list)

    def frontmatter(self) -> dict:
        """§27.1 필드를 정본 순서의 dict 로 반환(YAML 키 ``from`` 포함)."""
        data = self.model_dump(by_alias=True)
        return {key: data[key] for key in _FIELD_ORDER}


def render_brief(brief: HandoffBrief) -> str:
    """핸드오프 브리프를 마크다운으로 렌더 — §27.1 frontmatter + §18.3 본문 섹션.

    frontmatter 는 결정론적(키 순서 고정 · unicode 보존 · flow 스타일 비활성).
    본문은 §18.3 5개 섹션 스캐폴드를 둔다(작성자가 채움).
    """
    fm = yaml.safe_dump(
        brief.frontmatter(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    body = "\n".join(f"{header}\n" for header in _BODY_SECTIONS)
    return f"---\n{fm}---\n\n{body}"


def parse_brief(text: str) -> HandoffBrief:
    """렌더된 마크다운(또는 ``vault/specs/*.md`` 내용)의 frontmatter 를 복원.

    ``render_brief`` 의 역연산 — ``parse_brief(render_brief(b)) == b`` (라운드트립).
    """
    return HandoffBrief.model_validate(_extract_frontmatter(text))


def _extract_frontmatter(text: str) -> dict:
    """선두 ``---`` … ``---`` 블록을 YAML 매핑으로 로드. 없으면 ``ValueError``."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter 시작 구분자 '---' 없음")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter 종료 구분자 '---' 없음")
    data = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter 가 매핑(dict)이 아님")
    return data


def write_brief(brief: HandoffBrief, vault_dir: Union[Path, str]) -> Path:
    """브리프를 ``<vault_dir>/specs/<task_id>.md`` 로 기록(§27.1 저장 위치). 경로 반환."""
    out = Path(vault_dir) / "specs" / f"{brief.task_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_brief(brief), encoding="utf-8")
    return out


# ── §18.5 지식 적재 의무 (Definition of Done) ──────────────────────────────────
# '완료'의 정의 = ① 산출물 아티팩트 ② 리서치 근거(스냅샷) ③ Work Log + 핸드오프 브리프.
# 하나라도 누락 시 done 아님(QA/Dev Lead·LIBRARIAN 반려 · §20.2 자율 근거 미충족).
_DOD_COMPONENTS: tuple[str, ...] = (
    "artifact",
    "research_evidence",
    "worklog",
    "handoff",
)


class DoDResult(BaseModel):
    """DoD 게이트 결과. ``ok`` 이면 4구성 전부 적재됨, 아니면 ``missing`` 에 누락 이름."""

    ok: bool
    missing: list[str] = Field(default_factory=list)


def _present(item: _PathLike) -> bool:
    """구성요소가 실제로 적재됐는지 — ``None``/빈 경로 아님 + 실제 **파일** 존재.

    ``is_file()`` 사용(``exists()`` 아님): 디렉토리·빈 문자열("")은 미충족으로 본다
    (``Path("")`` 는 ``"."`` 디렉토리라 ``is_file()`` False — DoD 게이트가 거짓 통과
    하지 않도록). §18.5 의 적재 구성은 vault 마크다운 **파일**이다.
    """
    return item is not None and Path(item).is_file()


def check_dod(
    artifact: _PathLike,
    research_evidence: _PathLike,
    worklog: _PathLike,
    handoff: _PathLike,
) -> DoDResult:
    """§18.5 Definition of Done 게이트.

    4구성(산출물 아티팩트 · 리서치 근거 · Work Log · 핸드오프 브리프) 중 하나라도
    누락이면 ``ok=False`` 와 누락 이름 목록을 돌려준다(반려).
    """
    given = {
        "artifact": artifact,
        "research_evidence": research_evidence,
        "worklog": worklog,
        "handoff": handoff,
    }
    missing = [name for name in _DOD_COMPONENTS if not _present(given[name])]
    return DoDResult(ok=not missing, missing=missing)
