"""P5b B7 — §21.4 정밀검수 *배관* e2e (audit 신호 emit + 폐루프·fake embedder).

검증(falsifiable·green-theater 차단):
  (a) audit 신호 emit 경로: ``make_claude_signal(trigger="audit")`` → ``emit_text_message``
      (완성 사이클 run_id 컨텍스트·신규-2) → TEXT_MESSAGE 프레임(신규 이벤트 타입 0·동결
      events.ts 준수). 접두사 "🔍 [정밀검수 요청]" BLUF.
  (b) 배관 e2e: 샘플 §29 감사 리포트(vault/quality/sample-audit.md·결함 1건) →
      ``parse_audit_report`` → ``defect_to_handoff``(vault/specs 실파일) → ``write_lesson``
      (vault/quality 실파일) → Qdrant 색인(fake embedder + ``:memory:``) → ``lesson_is_retrievable``
      회수. 상태플래그 아닌 실 search hit.
  (c) 차기 RPI(``prefetch_quality_lessons``)가 이 교훈을 회수(누적 개선 입증).
  (d) §7 HARD: 정밀검수 *자체* = 사람 Opus 세션(자동화 0). 본 task = *배관*(리포트=파일 입력).
      quality_loop.py 의 §7 HARD 정적 가드는 test_quality_loop 가 강제(여기선 입력 샘플 사용).

env: ``/opt/conda/envs/orch/bin/python`` · ``PYTHONPATH=/opt/studio``.
qdrant 미설치(orch env) → 폐루프 e2e 는 ``importorskip`` graceful skip(파싱/handoff/신호 emit/
교훈 writer 는 무조건 실행·실 회수는 knowledge env 에서 닫힘·P4c 선례).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orch.handoff import HandoffBrief, parse_brief  # noqa: E402
from orch.outbound import OutboundEmitter  # noqa: E402
from orch.quality_loop import (  # noqa: E402
    defect_to_handoff,
    parse_audit_report,
    prefetch_quality_lessons,
    write_lesson,
)
from orch.spec_queue import make_claude_signal  # noqa: E402
from orch.tests.conftest import FakeEmbedder  # noqa: E402

# 실 샘플 감사 리포트(B7 입력·결함 ≥1·git 영속). 사람 Opus 세션 산출의 *샘플*.
_SAMPLE_AUDIT = _REPO / "vault" / "quality" / "sample-audit.md"


def _audit_brief() -> HandoffBrief:
    return HandoffBrief(
        task_id="p5-slide-puzzle",
        from_="DEV",
        to="Claude Code",
        objective="단일화면 HTML5 슬라이딩 타일 퍼즐 구현 — games/slide-puzzle/",
        context_refs=[],
        acceptance_criteria=["state_shape: tiles,moves,solved,size"],
        constraints=["vanilla JS"],
        reversibility="two-way",
        confidence=0.8,
        priority="high",
        artifacts_expected=["games/slide-puzzle/index.html"],
    )


def _memory_store():
    """``:memory:`` KnowledgeStore + fake embedder(qdrant 필요·없으면 skip·P4c 선례)."""
    pytest.importorskip("qdrant_client")
    from knowledge.store import KnowledgeStore

    s = KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())
    s.ensure_collection()
    return s


# ── (a) audit 신호 emit 경로 — make_claude_signal → emit_text_message ──────────
def test_audit_signal_emits_text_message_with_run_id():
    """audit 신호가 *완성 사이클 run_id 컨텍스트*에서 TEXT_MESSAGE 로 방출됨(신규-2).

    make_claude_signal("audit") 출력(문자열)이 emit_text_message(content=...) 인자로
    그대로 들어가 TEXT_MESSAGE 프레임을 만든다 — 신규 이벤트 타입 0(동결 events.ts 준수).
    """
    brief = _audit_brief()
    signal = make_claude_signal("audit", brief.task_id, brief)
    # audit 접두사 + BLUF 술어(직원 발화와 구분).
    assert signal.startswith("🔍 [정밀검수 요청]")
    assert brief.objective in signal
    assert "vault/specs/p5-slide-puzzle.md" in signal

    # 완성된 제품 사이클의 run_id 컨텍스트(reviewer 배관이 그 run_id 로 emit·신규-2).
    completed_run_id = "cyc-p5-slide-puzzle-001"
    emitter = OutboundEmitter()
    frame = emitter.emit_text_message(run_id=completed_run_id, content=signal)

    # TEXT_MESSAGE 프레임(신규 이벤트 타입 0·동결 events.ts).
    assert frame.payload["type"] == "TEXT_MESSAGE"
    assert frame.payload["content"] == signal
    assert frame.payload["run_id"] == completed_run_id
    # 단조 seq(emitter 계약).
    assert frame.seq == 1


def test_audit_signal_distinct_from_employee_speech():
    """audit 신호 접두사가 impl/infra 와 구별(직원 발화 오인 방지·결정론)."""
    brief = _audit_brief()
    audit = make_claude_signal("audit", brief.task_id, brief)
    impl = make_claude_signal("impl", brief.task_id, brief)
    infra = make_claude_signal("infra", brief.task_id, brief)
    assert audit != impl != infra
    assert "🔍" in audit and "🔧" in impl and "⚙️" in infra
    # 결정론(재호출 동일).
    assert audit == make_claude_signal("audit", brief.task_id, brief)


# ── (b)+(c) 배관 e2e: 샘플 리포트 → handoff → 교훈 → Qdrant → RPI 회수 ─────────
def test_audit_pipeline_closed_loop_with_sample_report(tmp_path: Path):
    """★폐루프(falsifiable): 샘플 §29 리포트 → 파싱 → handoff 실파일 → 교훈 실파일 →
    Qdrant 색인(fake embedder+:memory:) → 차기 RPI 가 그 교훈을 *실제* 회수.

    qdrant 없으면(orch env) skip — 파싱/handoff/교훈 writer 는 그 전에 실행돼 배관 입증.
    """
    # 샘플 리포트(git 영속 입력·사람 Opus 산출 샘플)에서 결함 추출.
    assert _SAMPLE_AUDIT.is_file(), (
        "샘플 감사 리포트(B7 입력)가 vault/quality 에 있어야 함"
    )
    defects = parse_audit_report(_SAMPLE_AUDIT)
    assert defects, "샘플 리포트에서 결함 ≥1 추출(폐루프 의미)"
    target = defects[0]

    # defect → handoff 실파일(vault/specs·§27.1 라운드트립).
    spec_path, brief = defect_to_handoff(target, tmp_path, cycle=0, index=0)
    assert spec_path.is_file() and spec_path.parent.name == "specs"
    reparsed = parse_brief(spec_path.read_text(encoding="utf-8"))
    assert reparsed.task_id == brief.task_id

    # 여기서부터 Qdrant 필요 — 없으면 graceful skip(배관 앞단은 위에서 입증됨).
    store = _memory_store()

    token = "p5audpipetoken7"  # 회수 falsifiability 용 고유 토큰
    lesson_path, n = write_lesson(
        store,
        defect=target,
        root_cause=f"셔플 결정론 회귀 가드 누락 {token}",
        resolution="시드 재현 회귀 단위테스트 추가",
        prevention="PRNG 교체 시 보드 재현성 회귀게이트",
        source=str(_SAMPLE_AUDIT),
        vault_dir=tmp_path,
        handoff_ref=str(spec_path),
    )
    assert lesson_path.is_file() and lesson_path.parent.name == "quality"
    assert n == 1  # 실 1포인트 업서트

    # (c) 차기 RPI 선조회가 그 교훈을 실제 회수(상태플래그 아님).
    lessons = prefetch_quality_lessons(token, store, top_n=10)
    assert any(token in lz.text for lz in lessons), "차기 RPI 가 교훈을 회수해야 함"
    # 대조: 색인 안 된 토큰은 회수 안 됨(거짓 양성 차단).
    from orch import quality_loop

    assert quality_loop.lesson_is_retrievable(store, token) is True
    assert quality_loop.lesson_is_retrievable(store, "absentp5token404") is False


# ── (d) §7 HARD: spec_queue 신호는 *알림 텍스트*만(자동 호출 객체 아님) ─────────
def test_audit_signal_is_text_not_autocall():
    """make_claude_signal 은 문자열만 반환(Opus/Claude Code 자동 호출 표면 0·§7 HARD).

    spec_queue.py 의 §7.1 HARD 정적 grep 가드는 test_spec_queue 가 강제. 여기선 audit
    신호가 *실행* 객체가 아니라 *알림 문자열*임을 타입으로 재확인(emit 인자 정합).
    """
    brief = _audit_brief()
    sig = make_claude_signal("audit", brief.task_id, brief)
    assert isinstance(sig, str)
    assert not callable(sig)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
