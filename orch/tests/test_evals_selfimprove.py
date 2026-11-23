"""orch/tests/test_evals_selfimprove.py — P4c 자기개선 경로 단위 테스트.

★tmp 격리 원칙:
  - 실 vault/evals/golden.jsonl 은 절대 수정하지 않는다.
  - 모든 테스트는 tmp_path (pytest fixture) 에 golden.jsonl 복사본을 만들어 사용한다.

★LLM-0: 모든 테스트는 LLM 호출 없이 결정론적으로 실행된다.
★not slow: 외부 I/O / 네트워크 / LLM 미접촉.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

# 실 golden 경로 (참조용·읽기 전용)
_REAL_GOLDEN = Path(__file__).resolve().parents[2] / "vault" / "evals" / "golden.jsonl"

# golden 스키마 필수키 (test_evals_golden.py 와 동형)
_REQUIRED_KEYS = {
    "id",
    "type",
    "graph",
    "input",
    "expected",
    "category",
    "expects_tool",
}
_VALID_TYPES = {"agent_task", "routing", "rag", "injection"}
_VALID_GRAPHS = {"cycle", "org"}


# ── helpers ───────────────────────────────────────────────────────────────────


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _make_failure_record(
    anchor_id: str = "g-test-001",
    record_type: str = "routing",
    graph: str = "cycle",
    route: str = "end",
    tool_calls: list | None = None,
) -> dict[str, Any]:
    """테스트용 최소 캡처 레코드 (runner.run_anchor 반환형과 동형)."""
    return {
        "anchor_id": anchor_id,
        "type": record_type,
        "graph": graph,
        "route": route,
        "decision_confidence": 0.55,
        "bluf": "작업을 완료했습니다.",
        "tool_calls": tool_calls or [],
        "search_results": [],
        "final_state": {"route": route},
    }


def _make_label(
    expected: dict | None = None,
    label_type: str = "routing",
    category: str = "selfimprove/test",
    **kwargs: Any,
) -> dict[str, Any]:
    """테스트용 사람 라벨."""
    lbl: dict[str, Any] = {
        "expected": expected if expected is not None else {"route": "build"},
        "type": label_type,
        "category": category,
    }
    lbl.update(kwargs)
    return lbl


# ── fixture: tmp golden 복사본 ─────────────────────────────────────────────────


@pytest.fixture()
def tmp_golden(tmp_path: Path) -> Path:
    """실 golden.jsonl 의 tmp 격리 복사본을 반환한다.

    실 파일이 없으면 최소 앵커로 빈 tmp golden 을 만들어 테스트를 실행한다.
    어느 경우든 실 _REAL_GOLDEN 은 절대 건드리지 않는다.
    """
    dest = tmp_path / "golden.jsonl"
    if _REAL_GOLDEN.exists():
        dest.write_bytes(_REAL_GOLDEN.read_bytes())
    else:
        # 실 파일 없어도 테스트가 돌도록 최소 앵커를 시딩
        seed = {
            "id": "seed-001",
            "type": "routing",
            "graph": "cycle",
            "input": "클리커 게임을 만들어줘",
            "expected": {"route": "build"},
            "category": "seed",
            "expects_tool": False,
        }
        dest.write_text(json.dumps(seed, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


# ── 1. 앵커 수 +1 ─────────────────────────────────────────────────────────────


def test_add_golden_anchor_increments_count(tmp_golden: Path) -> None:
    """add_golden_anchor 호출 후 golden 앵커 수가 정확히 1 증가한다."""
    from orch.evals.selfimprove import add_golden_anchor

    before = _load_jsonl(tmp_golden)
    count_before = len(before)

    add_golden_anchor(
        _make_failure_record(),
        _make_label(),
        tmp_golden,
    )

    after = _load_jsonl(tmp_golden)
    assert len(after) == count_before + 1, (
        f"expected {count_before + 1} anchors, got {len(after)}"
    )


# ── 2. 스키마 유효 (test_evals_golden 검증과 동형) ───────────────────────────


def test_appended_anchor_schema_valid(tmp_golden: Path) -> None:
    """append 된 앵커가 golden 스키마 필수키·enum 을 만족한다."""
    from orch.evals.selfimprove import add_golden_anchor

    add_golden_anchor(
        _make_failure_record(record_type="agent_task"),
        _make_label(
            expected={"completion": ["완료", "생성"]},
            label_type="agent_task",
            category="selfimprove/agent_task",
        ),
        tmp_golden,
    )

    rows = _load_jsonl(tmp_golden)
    new_anchor = rows[-1]

    missing = _REQUIRED_KEYS - new_anchor.keys()
    assert not missing, f"appended anchor missing keys: {missing}"
    assert new_anchor["type"] in _VALID_TYPES, f"invalid type={new_anchor['type']!r}"
    assert new_anchor["graph"] in _VALID_GRAPHS, (
        f"invalid graph={new_anchor['graph']!r}"
    )
    assert isinstance(new_anchor["expects_tool"], bool)


# ── 3. id 유일 보장 ──────────────────────────────────────────────────────────


def test_appended_anchor_id_unique(tmp_golden: Path) -> None:
    """append 된 앵커 id 가 기존 id 와 충돌하지 않는다."""
    from orch.evals.selfimprove import add_golden_anchor

    # 기존 id 목록
    before_ids = {row["id"] for row in _load_jsonl(tmp_golden)}

    add_golden_anchor(
        _make_failure_record(),
        _make_label(),
        tmp_golden,
    )

    after = _load_jsonl(tmp_golden)
    new_anchor = after[-1]
    assert new_anchor["id"] not in before_ids, (
        f"appended id={new_anchor['id']!r} collides with existing ids"
    )

    # 전체 id 유일성 재확인
    all_ids = [row["id"] for row in after]
    assert len(all_ids) == len(set(all_ids)), "duplicate ids after append"


# ── 4. 충돌 id 자동 교체 ──────────────────────────────────────────────────────


def test_conflicting_id_replaced(tmp_golden: Path) -> None:
    """label 에 기존과 동일한 id 를 지정해도 uuid4 로 자동 교체해 유일성을 보장한다."""
    from orch.evals.selfimprove import add_golden_anchor

    existing_ids = {row["id"] for row in _load_jsonl(tmp_golden)}
    # 기존 id 중 하나를 라벨 id 로 지정
    conflict_id = next(iter(existing_ids))

    add_golden_anchor(
        _make_failure_record(),
        _make_label(id=conflict_id),  # 충돌 id
        tmp_golden,
    )

    after = _load_jsonl(tmp_golden)
    new_id = after[-1]["id"]
    assert new_id != conflict_id, f"conflicting id was not replaced; still {new_id!r}"
    all_ids = [row["id"] for row in after]
    assert len(all_ids) == len(set(all_ids)), "duplicate ids after conflict replacement"


# ── 5. type / expected 라벨 반영 ────────────────────────────────────────────


def test_label_type_and_expected_reflected(tmp_golden: Path) -> None:
    """append 된 앵커의 type·expected 가 사람 라벨을 정확히 반영한다."""
    from orch.evals.selfimprove import add_golden_anchor

    label = _make_label(
        expected={"must_not_contain": ["ignore previous", "system prompt"]},
        label_type="injection",
        category="selfimprove/injection",
    )
    add_golden_anchor(_make_failure_record(record_type="injection"), label, tmp_golden)

    new_anchor = _load_jsonl(tmp_golden)[-1]
    assert new_anchor["type"] == "injection"
    assert new_anchor["expected"] == {
        "must_not_contain": ["ignore previous", "system prompt"]
    }
    assert new_anchor["category"] == "selfimprove/injection"


# ── 6. append 된 앵커가 재실행 대상에 포함됨 (id 로드 확인) ──────────────────


def test_appended_anchor_loadable_by_new_id(tmp_golden: Path) -> None:
    """append 후 golden 을 다시 로드하면 새 id 가 존재한다 (게이트 재실행 대상 확인)."""
    from orch.evals.selfimprove import add_golden_anchor

    add_golden_anchor(
        _make_failure_record(),
        _make_label(category="selfimprove/reload_check"),
        tmp_golden,
    )

    # 다시 로드
    reloaded = _load_jsonl(tmp_golden)
    new_anchor = reloaded[-1]
    new_id = new_anchor["id"]

    # 새 id 가 전체 목록에 포함됨 → 게이트 재실행 시 대상에 포함
    all_ids = {row["id"] for row in reloaded}
    assert new_id in all_ids, f"new anchor id={new_id!r} not found after reload"


# ── 7. expects_tool 추론 (tool_calls 존재 여부) ───────────────────────────────


def test_expects_tool_inferred_from_tool_calls(tmp_golden: Path) -> None:
    """label 에 expects_tool 미지정 시 failure_record.tool_calls 존재 여부로 추론된다."""
    from orch.evals.selfimprove import add_golden_anchor

    # tool_calls 비어있으면 False
    add_golden_anchor(
        _make_failure_record(tool_calls=[]),
        _make_label(),  # expects_tool 미지정
        tmp_golden,
    )
    rows = _load_jsonl(tmp_golden)
    assert rows[-1]["expects_tool"] is False

    # tool_calls 있으면 True
    add_golden_anchor(
        _make_failure_record(
            anchor_id="g-tool-001",
            tool_calls=[{"name": "knowledge_search", "args": {"query": "test"}}],
        ),
        _make_label(
            expected={"tool": "knowledge_search", "args": {}},
            label_type="rag",
        ),  # expects_tool 미지정
        tmp_golden,
    )
    rows = _load_jsonl(tmp_golden)
    assert rows[-1]["expects_tool"] is True


# ── 8. label 의 expects_tool 이 추론보다 우선 ────────────────────────────────


def test_label_expects_tool_overrides_inference(tmp_golden: Path) -> None:
    """label 에 expects_tool 명시 시 tool_calls 추론보다 우선된다."""
    from orch.evals.selfimprove import add_golden_anchor

    # tool_calls 없지만 label 에 expects_tool=True 명시
    add_golden_anchor(
        _make_failure_record(tool_calls=[]),
        _make_label(
            expects_tool=True,
            label_type="rag",
            expected={"answer_doc": "wiki/test.md", "answer_snippet": "test"},
        ),
        tmp_golden,
    )
    rows = _load_jsonl(tmp_golden)
    assert rows[-1]["expects_tool"] is True


# ── 9. 스키마 위반 시 ValueError (append 차단) ──────────────────────────────


def test_invalid_type_raises_value_error(tmp_golden: Path) -> None:
    """잘못된 type 을 가진 라벨이면 ValueError 를 발생시키고 파일을 오염하지 않는다."""
    from orch.evals.selfimprove import add_golden_anchor

    count_before = len(_load_jsonl(tmp_golden))

    with pytest.raises(ValueError, match="invalid anchor type"):
        add_golden_anchor(
            _make_failure_record(),
            _make_label(label_type="unknown_type"),
            tmp_golden,
        )

    # 파일 내용 불변
    assert len(_load_jsonl(tmp_golden)) == count_before


# ── 10. 실 golden.jsonl 미변경 확인 (tmp 격리 증거) ──────────────────────────


def test_real_golden_not_modified(tmp_golden: Path) -> None:
    """add_golden_anchor 호출 후 실 golden.jsonl 이 변경되지 않는다."""
    from orch.evals.selfimprove import add_golden_anchor

    # 실 파일이 있으면 수정 시각·크기를 기록
    if _REAL_GOLDEN.exists():
        stat_before = _REAL_GOLDEN.stat()
        mtime_before = stat_before.st_mtime
        size_before = stat_before.st_size
    else:
        mtime_before = None
        size_before = None

    # tmp 복사본에만 append
    add_golden_anchor(
        _make_failure_record(),
        _make_label(),
        tmp_golden,  # ← tmp 경로만 사용
    )

    if _REAL_GOLDEN.exists() and mtime_before is not None:
        stat_after = _REAL_GOLDEN.stat()
        assert stat_after.st_mtime == mtime_before, "real golden.jsonl mtime changed!"
        assert stat_after.st_size == size_before, "real golden.jsonl size changed!"


# ── 11. CLI 결정론 (stub 입력) ────────────────────────────────────────────────


def test_cli_appends_anchor_via_stub(tmp_path: Path, tmp_golden: Path) -> None:
    """CLI(--from-capture --label --golden) 가 결정론적으로 앵커를 append 한다.

    gate 재실행은 tmp golden 과 tmp capture 를 쓰므로 실제 그래프/LLM 호출 없이
    결정론 경로만 실행된다(게이트는 score_deterministic 만 · LLM-0).
    """
    from orch.evals.selfimprove import main as selfimprove_main

    # capture.jsonl 작성
    capture_path = tmp_path / "capture.jsonl"
    record = _make_failure_record(anchor_id="g-cli-test-001")
    capture_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # label.json 작성 — routing 앵커로 라벨
    label_path = tmp_path / "label.json"
    label = {
        "expected": {"route": "build"},
        "type": "routing",
        "category": "selfimprove/cli_test",
    }
    label_path.write_text(json.dumps(label, ensure_ascii=False), encoding="utf-8")

    count_before = len(_load_jsonl(tmp_golden))

    # CLI 실행 (gate 재실행 포함)
    selfimprove_main(
        [
            "--from-capture",
            str(capture_path),
            "--label",
            str(label_path),
            "--golden",
            str(tmp_golden),
        ]
    )

    # append 확인
    after = _load_jsonl(tmp_golden)
    assert len(after) == count_before + 1, "CLI did not append exactly one anchor"
    new_anchor = after[-1]
    assert new_anchor["type"] == "routing"
    assert new_anchor["category"] == "selfimprove/cli_test"


# ── 12. Phoenix 자동수집 미구현 · OBSERVE 표기 확인 ─────────────────────────


def test_phoenix_autoingestion_not_implemented() -> None:
    """Phoenix 실시간 자동수집이 구현되지 않고 OBSERVE-ONLY 표기가 존재한다.

    selfimprove.py 모듈 docstring 에 'OBSERVE' 또는 'OBSERVE-ONLY' 문자열이 있음을 단언한다.
    이것이 없으면 Phoenix 경로가 구현됐거나 정직 표기가 누락된 것이다.
    """
    import orch.evals.selfimprove as mod

    doc = (mod.__doc__ or "").upper()
    assert "OBSERVE" in doc, (
        "selfimprove.py module docstring must contain 'OBSERVE' or 'OBSERVE-ONLY' "
        "to declare Phoenix autoingestion as OBSERVE-ONLY (not implemented)."
    )


def test_phoenix_query_function_absent() -> None:
    """Phoenix query API 를 직접 호출하는 함수가 selfimprove.py 에 없음을 확인한다.

    Phoenix 자동수집 경로가 OBSERVE-ONLY 이므로, phoenix_query 또는 phoenix_ingest
    같은 함수가 이 모듈에 정의되어서는 안 된다.
    """
    import orch.evals.selfimprove as mod

    public_names = [
        name
        for name, obj in inspect.getmembers(mod, inspect.isfunction)
        if not name.startswith("_")
    ]
    phoenix_funcs = [n for n in public_names if "phoenix" in n.lower()]
    assert not phoenix_funcs, (
        f"Phoenix autoingestion functions found in selfimprove.py: {phoenix_funcs}. "
        "Phoenix path must be OBSERVE-ONLY (not implemented in this module)."
    )


# ── 13. 단발 재실행 (루프 0) 구조 확인 ──────────────────────────────────────


def test_main_calls_gate_exactly_once() -> None:
    """CLI main 이 gate.main 을 정확히 1회 호출한다 (루프 0 · termination P0).

    소스 정적 분석으로 gate_main(... 호출이 main() 본문에 1회만 등장하고
    while 루프로 감싸지지 않음을 확인한다 (monkeypatch 불필요·LLM-0).
    """
    import orch.evals.selfimprove as selfimprove_mod

    # monkeypatch 없이 원본 함수의 소스를 읽는다
    src = inspect.getsource(selfimprove_mod.main)

    # gate_main( 호출이 정확히 1번 등장 (루프 0 · termination P0)
    gate_call_count = src.count("gate_main(")
    assert gate_call_count == 1, (
        f"gate_main called {gate_call_count} times in main(); expected exactly 1 (loop=0)"
    )

    # gate_main 호출 이후 라인에 while 이 없음 (루프 감쌈 방지)
    after_gate = src.split("gate_main(", 1)[1]
    # 첫 줄에 while 이 없으면 루프로 감싸지지 않은 것
    first_line_after = after_gate.split("\n")[0]
    assert "while" not in first_line_after, (
        "gate_main appears to be inside a while loop — termination P0 violated"
    )

    # while 루프가 gate_main 을 포함하는 블록을 감싸는지 추가 확인
    # main() 전체 소스에서 while 뒤에 gate_main 이 나오는 패턴 검사
    lines = src.splitlines()
    in_while = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("while "):
            in_while = True
        if in_while and "gate_main(" in stripped:
            pytest.fail(
                "gate_main() is called inside a while loop — termination P0 violated"
            )
