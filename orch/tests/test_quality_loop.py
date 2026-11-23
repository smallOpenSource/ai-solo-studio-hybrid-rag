"""SR/renewal P4c — 연속 품질 루프(§21.4) *배관* e2e + §7 HARD + 라운드캡 단위.

검증 철학(falsifiable·green-theater 차단):
  - 폐루프는 **실 아티팩트**로만 닫힌다 — fixture §29 감사 리포트 → 파싱 → 실 handoff
    브리프(vault/specs/) → 실 교훈(vault/quality/) → Qdrant 색인 → **차기 RPI 가 그
    교훈을 실제 회수**(상태플래그 아님·실제 search hit). vault 쓰기는 ``tmp_path`` 격리.
  - Qdrant 색인/회수는 **fake embedder(토큰해시→고정벡터·결정론) + ``:memory:``** 로만
    — 실 bge(DEGRADED) 미접촉. ``importorskip("qdrant_client")``: qdrant 없는 orch env
    에선 graceful skip(파싱/handoff/교훈 writer/라운드캡 단위는 무조건 실행). 실 회수는
    knowledge env(qdrant 보유)에서 *실제로* 닫힘 — slow/OBSERVE 가 아니라 결정론 e2e.
  - human-trigger(①정밀검수) = hook 모킹(fixture 리포트 주입). Opus 세션은 코드 밖(§7).

env: ``/opt/conda/envs/orch/bin/python`` · ``PYTHONPATH=/opt/studio``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orch import quality_loop  # noqa: E402
from orch.quality_loop import (  # noqa: E402
    Defect,
    defect_to_handoff,
    parse_audit_report,
    prefetch_quality_lessons,
    run_review_loop,
    write_lesson,
)
from orch.tests.conftest import FakeEmbedder  # noqa: E402

# ── fixture §29 감사 리포트(사람 Opus 세션 산출 모킹·defect ≥1) ────────────────
_AUDIT_REPORT = """# 체크포인트 보고 — Phase 5 · 2026-06-22
BLUF: at-risk · 클리커 게임 정밀검수에서 결함 검출 · 전체 완료율 ~80%.

## 1. 진척
현재 Phase 5(캡스톤) / 정밀검수 1차 / 결함 라우팅 대기.

## 2. §12 수용 기준 (근거 필수)
| 기준 | 상태 | 근거 |
|---|---|---|
| 코어 루프 | 🟡 | balance 미세조정 필요 |

## 5. 결함 (Defects)
- 🔴 [blocker] 업그레이드 비용 곡선이 코드에서 음수로 떨어져 게임이 깨짐(구현 버그)
- 🟡 major: 리서치 근거(citation) 없이 밸런스 수치를 주장함 — 출처 보강 필요
- minor 아트 에셋 색 팔레트가 §28 가이드와 불일치(visual 톤)

## 6. 다음 단계
defect 라우팅 → 재구현 → 재검수.
"""


@pytest.fixture
def audit_report(tmp_path: Path) -> Path:
    """fixture §29 감사 리포트를 파일로 떨군다(human-trigger hook 모킹 = 파일경로 주입)."""
    p = tmp_path / "audit-0001.md"
    p.write_text(_AUDIT_REPORT, encoding="utf-8")
    return p


def _memory_store():
    """``:memory:`` KnowledgeStore + fake embedder(qdrant 필요·없으면 skip)."""
    pytest.importorskip("qdrant_client")
    from knowledge.store import KnowledgeStore

    s = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
    s.ensure_collection()
    return s


def _seed_template(tmp_path: Path) -> Path:
    """tmp vault 에 quality-lesson 템플릿을 복사(write_lesson 헤더 호환 확인용)."""
    src = _REPO / "vault" / "_templates" / "quality-lesson.md"
    dst = tmp_path / "_templates" / "quality-lesson.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


# ── (i) §29 파서 ───────────────────────────────────────────────────────────────
def test_parse_audit_report_extracts_defects(audit_report: Path):
    defects = parse_audit_report(audit_report)
    assert len(defects) == 3
    # 심각도 분류(🔴→blocker · 🟡 major · minor).
    sev = [d.severity for d in defects]
    assert sev == ["blocker", "major", "minor"]
    # 책임 역할 분류(구현 버그→DEV · citation/출처→RESEARCH · visual→ART).
    roles = [d.role for d in defects]
    assert roles == ["DEV", "RESEARCH", "ART"]


def test_parse_ignores_non_defect_sections(audit_report: Path):
    """BLUF·진척·수용기준 표의 불릿이 결함으로 새지 않음(결함 섹션만)."""
    defects = parse_audit_report(audit_report)
    bodies = " ".join(d.description for d in defects)
    assert "다음 단계" not in bodies and "balance 미세조정" not in bodies


# ── (ii) defect → handoff (vault/specs 실파일) ─────────────────────────────────
def test_defect_to_handoff_writes_spec(tmp_path: Path):
    d = Defect(description="업그레이드 비용 곡선 버그", severity="blocker", role="DEV")
    path, brief = defect_to_handoff(d, tmp_path, cycle=1, index=0)
    assert path.is_file()  # 실파일 존재(상태플래그 아님)
    assert path.parent.name == "specs"
    assert brief.to == "DEV"
    assert brief.priority == "high"  # blocker → high
    # 라운드트립: 기록된 파일이 §27.1 frontmatter 로 다시 파싱됨.
    from orch.handoff import parse_brief

    reparsed = parse_brief(path.read_text(encoding="utf-8"))
    assert reparsed.task_id == brief.task_id and reparsed.to == "DEV"


def test_defect_to_handoff_reversibility_from_authority(tmp_path: Path):
    """§20: '삭제'(비가역) 결함은 one-way + high 로 격상(authority.decide 위임)."""
    d = Defect(
        description="저장 데이터 영구 삭제 로직 결함", severity="minor", role="DEV"
    )
    _path, brief = defect_to_handoff(d, tmp_path)
    assert brief.reversibility == "one-way"
    assert brief.priority == "high"  # Escalate tier → high (minor 라도 격상)


# ── (iii)+(iv)+(v) 폐루프: 교훈 적재 → Qdrant 색인 → 차기 RPI 실회수 ────────────
def test_quality_loop_closed_loop_real_artifacts(tmp_path: Path):
    """★폐루프(falsifiable): fixture 리포트 → 파싱 → handoff 실파일 → 교훈 실파일 →
    Qdrant 색인(fake embedder+:memory:) → **차기 RPI 가 그 교훈을 실제 회수**.

    상태플래그가 아니라 *실제* search_hybrid hit 로 닫는다. qdrant 없으면 skip.
    """
    store = _memory_store()  # importorskip — qdrant 없는 orch env 는 여기서 skip
    _seed_template(tmp_path)

    # human-trigger 모킹: fixture §29 리포트를 파일로 주입(Opus 세션은 코드 밖·§7).
    report = tmp_path / "audit.md"
    report.write_text(_AUDIT_REPORT, encoding="utf-8")

    # (i) 파싱
    defects = parse_audit_report(report)
    assert defects, "결함이 추출되어야 폐루프가 의미 있음"
    blocker = next(d for d in defects if d.severity == "blocker")

    # (ii) defect → handoff 실파일
    spec_path, brief = defect_to_handoff(blocker, tmp_path, cycle=0, index=0)
    assert spec_path.is_file()

    # (iii) 교훈 writer → vault/quality 실파일 + (iv) Qdrant 색인
    token = "zqxcurvebugtoken9"  # 회수 falsifiability 용 고유 토큰
    lesson_path, n = write_lesson(
        store,
        defect=blocker,
        root_cause=f"비용 누산기 부호 오류 {token}",
        resolution="누산을 양수로 클램프 + 단위테스트 추가",
        prevention="비용 곡선 단조성 단위테스트를 회귀게이트에 추가",
        source=str(report),
        vault_dir=tmp_path,
        handoff_ref=str(spec_path),
    )
    assert lesson_path.is_file() and lesson_path.parent.name == "quality"
    assert n == 1  # 실제 1 포인트 업서트

    # (v) 차기 RPI 선조회가 그 교훈을 **실제로 회수**(상태플래그 아님).
    lessons = prefetch_quality_lessons(token, store, top_n=10)
    assert any(token in lz.text for lz in lessons), "차기 RPI 가 교훈을 회수해야 함"
    # 대조: 색인 안 된 토큰은 회수 안 됨(거짓 양성 차단).
    assert quality_loop.lesson_is_retrievable(store, token) is True
    assert quality_loop.lesson_is_retrievable(store, "absent404zztoken") is False
    # 교훈 본문이 ADR형 섹션을 포함(템플릿 구조).
    body = lesson_path.read_text(encoding="utf-8")
    for header in (
        "## 결함 패턴",
        "## 원인",
        "## 해결",
        "## 재발방지책",
        "## 검수 체크리스트",
    ):
        assert header in body


# ── (vi) §7 HARD: quality_loop.py 가 Opus 를 자동호출하지 않음(정적 grep) ───────
def test_quality_loop_no_opus_autocall_static_guard():
    """§7 HARD: 소스에 ``-p ``/``subprocess.*claude``/``anthropic``/``ANTHROPIC_API_KEY``
    부재(정적). Opus 정밀검수는 외부 사람 세션 — 코드는 배관만(DR-3)."""
    src = (_REPO / "orch" / "quality_loop.py").read_text(encoding="utf-8")
    low = src.lower()
    # 벤더 클라이언트/키 표면 부재(자동 LLM 호출 금지).
    assert "anthropic" not in low, "anthropic 자동호출 금지(§7)"
    assert "anthropic_api_key" not in low
    for banned in ("import anthropic", "from anthropic"):
        assert banned not in low
    # headless -p 플래그·claude CLI 호출 표면 부재(공백 둘러싼 토큰으로 좁게).
    assert " -p " not in src, "headless -p 자동호출 금지(§7)"
    assert "claude" not in low, "claude CLI 자동호출 금지(§7)"
    # subprocess 로 외부 모델을 부르지 않음.
    assert "subprocess" not in low, "subprocess 자동호출 표면 금지(§7)"
    # Agent SDK 자동호출 표면 부재.
    assert "agent sdk" not in low


# ── (vii) 라운드캡3: 항상 실패 → 정확히 3회 후 escalate(termination P0·무한0) ───
def test_review_loop_round_cap_escalates():
    calls: list[int] = []
    persistent = [Defect(description="잔존 결함", severity="major", role="DEV")]

    def always_fail(round_index: int):
        calls.append(round_index)
        return persistent  # 매 라운드 결함 잔존(절대 통과 안 함)

    outcome = run_review_loop(always_fail, round_cap=3)
    assert outcome.status == "escalate"
    assert outcome.rounds == 3
    assert calls == [0, 1, 2]  # 정확히 3회·무한 루프 0
    assert len(outcome.remaining) == 1
    assert "에스컬레이션" in outcome.note or "범위축소" in outcome.note


def test_review_loop_passes_when_clean():
    """결함 0 라운드에서 즉시 passed(상한 전 종료)."""
    seq = [
        [Defect(description="r0 결함", severity="minor", role="DEV")],
        [],  # 2라운드에 결함 0
    ]

    def review(i: int):
        return seq[i]

    outcome = run_review_loop(review, round_cap=3)
    assert outcome.status == "passed"
    assert outcome.rounds == 2 and outcome.remaining == []


def test_review_loop_rejects_zero_cap():
    with pytest.raises(ValueError):
        run_review_loop(lambda i: [], round_cap=0)
