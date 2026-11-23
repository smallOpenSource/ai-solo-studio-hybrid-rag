"""P2-core — 핸드오프 브리프(§27.1) round-trip · §18.5 DoD 게이트 단위.

결정론·네트워크 0(순수 스키마 + 파일 I/O). orch env(pydantic 2 · pyyaml).
스펙: SR/renewal/AI_GAME_STUDIO_IMPLEMENTATION_SPEC.md §18.3/§18.5/§27.1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orch.handoff import (  # noqa: E402
    DoDResult,
    HandoffBrief,
    check_dod,
    parse_brief,
    render_brief,
    write_brief,
)

# §27.1 스펙 YAML 블록의 예시값(리터럴).
_SAMPLE_KW = dict(
    task_id="g1-gdd",
    from_="CEO",
    to="RESEARCH",
    objective="첫 게임 GDD 작성",
    context_refs=[
        "vault/game-design/concept.md",
        "qdrant:knowledge:캐주얼게임_코어루프",
    ],
    acceptance_criteria=[
        "장르 확정(클리커/퍼즐/아케이드 중 1)",
        "코어 루프 1문단 + 승리/실패 조건",
    ],
    constraints=["단일 화면", "로컬 점수", "외부 의존 없음"],
    reversibility="two-way",
    confidence=0.80,
    priority="high",
    artifacts_expected=["vault/game-design/gdd.md"],
)


def _sample() -> HandoffBrief:
    return HandoffBrief(**_SAMPLE_KW)


# ── §27.1 필드 정본(11) ───────────────────────────────────────────────────
def test_all_11_fields_present():
    b = _sample()
    for field in (
        "task_id",
        "from_",
        "to",
        "objective",
        "context_refs",
        "acceptance_criteria",
        "constraints",
        "reversibility",
        "confidence",
        "priority",
        "artifacts_expected",
    ):
        assert hasattr(b, field), field
    assert b.from_ == "CEO" and b.to == "RESEARCH"


def test_from_alias_and_field_name_both_accepted():
    b1 = HandoffBrief(**_SAMPLE_KW)  # 필드명 from_
    kw = {k: v for k, v in _SAMPLE_KW.items() if k != "from_"}
    b2 = HandoffBrief(**kw, **{"from": "CEO"})  # alias "from"
    assert b1 == b2


# ── 필드 검증(계약·§27 모호성 제거) ──────────────────────────────────────
@pytest.mark.parametrize("bad", [1.5, -0.1, 2.0])
def test_confidence_out_of_range_rejected(bad):
    with pytest.raises(ValidationError):
        HandoffBrief(**{**_SAMPLE_KW, "confidence": bad})


def test_reversibility_invalid_rejected():
    with pytest.raises(ValidationError):
        HandoffBrief(**{**_SAMPLE_KW, "reversibility": "maybe"})


def test_priority_invalid_rejected():
    with pytest.raises(ValidationError):
        HandoffBrief(**{**_SAMPLE_KW, "priority": "urgent"})


def test_defaults_for_optional_fields():
    b = HandoffBrief(task_id="t", from_="A", to="B", objective="o", confidence=0.5)
    assert b.reversibility == "two-way" and b.priority == "med"
    assert b.context_refs == []
    assert b.acceptance_criteria == []
    assert b.constraints == []
    assert b.artifacts_expected == []


# ── §18.3 render + round-trip ─────────────────────────────────────────────
def test_render_frontmatter_uses_alias_from_key():
    out = render_brief(_sample())
    assert out.startswith("---\n")
    assert "from: CEO" in out  # 정본 키는 alias 'from'(필드명 from_ 아님)
    assert "from_:" not in out


def test_render_has_18_3_body_sections():
    out = render_brief(_sample())
    for header in (
        "## 목적(1줄)",
        "## 결론/권고",
        "## 수용 기준",
        "## 제약·미해결",
        "## 참조",
    ):
        assert header in out


def test_roundtrip_equal_all_fields():
    b = _sample()
    assert parse_brief(render_brief(b)) == b


def test_roundtrip_preserves_lists_and_unicode():
    b = _sample()
    p = parse_brief(render_brief(b))
    assert p.context_refs == b.context_refs
    assert p.acceptance_criteria == b.acceptance_criteria
    assert p.constraints == b.constraints
    assert p.artifacts_expected == b.artifacts_expected
    assert p.from_ == "CEO" and p.objective == "첫 게임 GDD 작성"


def test_parse_rejects_missing_frontmatter():
    with pytest.raises(ValueError):
        parse_brief("frontmatter 없는 본문")


def test_parse_rejects_unterminated_frontmatter():
    with pytest.raises(ValueError):
        parse_brief("---\ntask_id: x\n본문에 종료 구분자 없음")


# ── write_brief (§27.1 저장 위치) ─────────────────────────────────────────
def test_write_brief_path_and_file_roundtrip(tmp_path):
    b = _sample()
    out = write_brief(b, tmp_path)
    assert out == tmp_path / "specs" / "g1-gdd.md"
    assert out.exists()
    assert parse_brief(out.read_text(encoding="utf-8")) == b


# ── §18.5 DoD 게이트 ──────────────────────────────────────────────────────
def _four_files(tmp_path) -> dict:
    files = {}
    for name in ("artifact", "research_evidence", "worklog", "handoff"):
        p = tmp_path / f"{name}.md"
        p.write_text("x", encoding="utf-8")
        files[name] = p
    return files


def test_dod_passes_when_all_present(tmp_path):
    f = _four_files(tmp_path)
    res = check_dod(f["artifact"], f["research_evidence"], f["worklog"], f["handoff"])
    assert isinstance(res, DoDResult)
    assert res.ok is True and res.missing == []


@pytest.mark.parametrize(
    "drop", ["artifact", "research_evidence", "worklog", "handoff"]
)
def test_dod_rejects_when_one_missing(tmp_path, drop):
    f = _four_files(tmp_path)
    f[drop] = None
    res = check_dod(f["artifact"], f["research_evidence"], f["worklog"], f["handoff"])
    assert res.ok is False
    assert drop in res.missing


def test_dod_rejects_nonexistent_path(tmp_path):
    f = _four_files(tmp_path)
    res = check_dod(
        f["artifact"], f["research_evidence"], f["worklog"], tmp_path / "nope.md"
    )
    assert res.ok is False and "handoff" in res.missing


def test_dod_rejects_empty_string_path(tmp_path):
    # "" 는 Path(".")(디렉토리)이므로 거짓 통과하면 안 됨(critic MINOR 하드닝).
    f = _four_files(tmp_path)
    res = check_dod("", f["research_evidence"], f["worklog"], f["handoff"])
    assert res.ok is False and "artifact" in res.missing


def test_dod_rejects_directory_not_file(tmp_path):
    # 디렉토리는 아티팩트(파일)로 인정하지 않음(critic MINOR · is_file 검사).
    f = _four_files(tmp_path)
    res = check_dod(tmp_path, f["research_evidence"], f["worklog"], f["handoff"])
    assert res.ok is False and "artifact" in res.missing
