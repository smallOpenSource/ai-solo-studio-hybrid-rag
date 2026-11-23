"""Schema and contract tests for benchmark case files.

Verifies:
- JSONL parsing succeeds for all three case files
- Required keys exist in every record
- tools follow OpenAI function-calling format
- expected.name matches one of the tool names in the same record
- Record counts: toolcall_ko=12, toolcall_en=12, multistep=4
- Distractor coverage: >=8 records in single-turn files have len(tools)>=2
- Argument coverage: >=8 records in single-turn files have >=2 required args in expected
- Multistep: exactly 3 steps per record
- Multistep carry-forward: >=1 key arg value in step 2/3 matches a value from the
  preceding step's tool_result (variable propagation check)

No network access required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CASES_DIR = Path(__file__).parent.parent / "cases"


def _load_jsonl(filename: str) -> list[dict[str, Any]]:
    path = CASES_DIR / filename
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                pytest.fail(f"{filename}:{lineno} — JSON parse error: {exc}")
    return records


def _tool_names(record: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in record["tools"]}


def _required_args_count(record: dict[str, Any]) -> int:
    """Count required parameters across all tools (union — distractor-agnostic)."""
    # We want to know how many args are *expected* by the ground-truth tool.
    expected_name = record["expected"]["name"]
    for tool in record["tools"]:
        if tool["function"]["name"] == expected_name:
            return len(tool["function"]["parameters"].get("required", []))
    return 0


def _flatten_values(obj: Any) -> set[str]:
    """Recursively collect all leaf string/numeric values from a dict/list."""
    result: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            result |= _flatten_values(v)
    elif isinstance(obj, list):
        for item in obj:
            result |= _flatten_values(item)
    elif obj is not None:
        result.add(str(obj))
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ko_records() -> list[dict[str, Any]]:
    return _load_jsonl("toolcall_ko.jsonl")


@pytest.fixture(scope="module")
def en_records() -> list[dict[str, Any]]:
    return _load_jsonl("toolcall_en.jsonl")


@pytest.fixture(scope="module")
def ms_records() -> list[dict[str, Any]]:
    return _load_jsonl("multistep.jsonl")


# ---------------------------------------------------------------------------
# Single-turn: toolcall_ko.jsonl
# ---------------------------------------------------------------------------


class TestToolcallKo:
    def test_count(self, ko_records: list[dict[str, Any]]) -> None:
        assert len(ko_records) == 12, f"Expected 12 records, got {len(ko_records)}"

    def test_ids(self, ko_records: list[dict[str, Any]]) -> None:
        ids = [r["id"] for r in ko_records]
        expected = [f"ko-{i:02d}" for i in range(1, 13)]
        assert ids == expected, f"ID mismatch: {ids}"

    def test_required_keys(self, ko_records: list[dict[str, Any]]) -> None:
        for r in ko_records:
            for key in ("id", "lang", "prompt", "tools", "expected"):
                assert key in r, f"[{r.get('id')}] missing key '{key}'"

    def test_lang_field(self, ko_records: list[dict[str, Any]]) -> None:
        for r in ko_records:
            assert r["lang"] == "ko", (
                f"[{r['id']}] lang should be 'ko', got {r['lang']!r}"
            )

    def test_tools_openai_format(self, ko_records: list[dict[str, Any]]) -> None:
        for r in ko_records:
            assert isinstance(r["tools"], list) and len(r["tools"]) >= 1, (
                f"[{r['id']}] tools must be a non-empty list"
            )
            for tool in r["tools"]:
                assert tool.get("type") == "function", (
                    f"[{r['id']}] tool.type must be 'function'"
                )
                fn = tool.get("function", {})
                assert "name" in fn, f"[{r['id']}] tool.function.name missing"
                assert "parameters" in fn, (
                    f"[{r['id']}] tool.function.parameters missing"
                )
                params = fn["parameters"]
                assert "required" in params, (
                    f"[{r['id']}] tool.function.parameters.required missing"
                )
                assert isinstance(params["required"], list), (
                    f"[{r['id']}] parameters.required must be a list"
                )

    def test_expected_format(self, ko_records: list[dict[str, Any]]) -> None:
        for r in ko_records:
            exp = r.get("expected", {})
            assert "name" in exp, f"[{r['id']}] expected.name missing"
            assert "args" in exp, f"[{r['id']}] expected.args missing"

    def test_expected_name_in_tools(self, ko_records: list[dict[str, Any]]) -> None:
        for r in ko_records:
            assert r["expected"]["name"] in _tool_names(r), (
                f"[{r['id']}] expected.name '{r['expected']['name']}' not in tools {_tool_names(r)}"
            )

    def test_distractor_coverage(self, ko_records: list[dict[str, Any]]) -> None:
        distractor_count = sum(1 for r in ko_records if len(r["tools"]) >= 2)
        assert distractor_count >= 8, (
            f"Expected >=8 records with distractor tools, got {distractor_count}"
        )

    def test_multi_arg_coverage(self, ko_records: list[dict[str, Any]]) -> None:
        multi_arg_count = sum(1 for r in ko_records if _required_args_count(r) >= 2)
        assert multi_arg_count >= 8, (
            f"Expected >=8 records with >=2 required args, got {multi_arg_count}"
        )


# ---------------------------------------------------------------------------
# Single-turn: toolcall_en.jsonl
# ---------------------------------------------------------------------------


class TestToolcallEn:
    def test_count(self, en_records: list[dict[str, Any]]) -> None:
        assert len(en_records) == 12, f"Expected 12 records, got {len(en_records)}"

    def test_ids(self, en_records: list[dict[str, Any]]) -> None:
        ids = [r["id"] for r in en_records]
        expected = [f"en-{i:02d}" for i in range(1, 13)]
        assert ids == expected, f"ID mismatch: {ids}"

    def test_required_keys(self, en_records: list[dict[str, Any]]) -> None:
        for r in en_records:
            for key in ("id", "lang", "prompt", "tools", "expected"):
                assert key in r, f"[{r.get('id')}] missing key '{key}'"

    def test_lang_field(self, en_records: list[dict[str, Any]]) -> None:
        for r in en_records:
            assert r["lang"] == "en", (
                f"[{r['id']}] lang should be 'en', got {r['lang']!r}"
            )

    def test_tools_openai_format(self, en_records: list[dict[str, Any]]) -> None:
        for r in en_records:
            assert isinstance(r["tools"], list) and len(r["tools"]) >= 1, (
                f"[{r['id']}] tools must be a non-empty list"
            )
            for tool in r["tools"]:
                assert tool.get("type") == "function", (
                    f"[{r['id']}] tool.type must be 'function'"
                )
                fn = tool.get("function", {})
                assert "name" in fn, f"[{r['id']}] tool.function.name missing"
                assert "parameters" in fn, (
                    f"[{r['id']}] tool.function.parameters missing"
                )
                params = fn["parameters"]
                assert "required" in params, (
                    f"[{r['id']}] tool.function.parameters.required missing"
                )
                assert isinstance(params["required"], list), (
                    f"[{r['id']}] parameters.required must be a list"
                )

    def test_expected_format(self, en_records: list[dict[str, Any]]) -> None:
        for r in en_records:
            exp = r.get("expected", {})
            assert "name" in exp, f"[{r['id']}] expected.name missing"
            assert "args" in exp, f"[{r['id']}] expected.args missing"

    def test_expected_name_in_tools(self, en_records: list[dict[str, Any]]) -> None:
        for r in en_records:
            assert r["expected"]["name"] in _tool_names(r), (
                f"[{r['id']}] expected.name '{r['expected']['name']}' not in tools {_tool_names(r)}"
            )

    def test_distractor_coverage(self, en_records: list[dict[str, Any]]) -> None:
        distractor_count = sum(1 for r in en_records if len(r["tools"]) >= 2)
        assert distractor_count >= 8, (
            f"Expected >=8 records with distractor tools, got {distractor_count}"
        )

    def test_multi_arg_coverage(self, en_records: list[dict[str, Any]]) -> None:
        multi_arg_count = sum(1 for r in en_records if _required_args_count(r) >= 2)
        assert multi_arg_count >= 8, (
            f"Expected >=8 records with >=2 required args, got {multi_arg_count}"
        )


# ---------------------------------------------------------------------------
# Multistep: multistep.jsonl
# ---------------------------------------------------------------------------


class TestMultistep:
    def test_count(self, ms_records: list[dict[str, Any]]) -> None:
        assert len(ms_records) == 4, f"Expected 4 records, got {len(ms_records)}"

    def test_ids(self, ms_records: list[dict[str, Any]]) -> None:
        ids = [r["id"] for r in ms_records]
        expected = [f"ms-{i:02d}" for i in range(1, 5)]
        assert ids == expected, f"ID mismatch: {ids}"

    def test_required_keys(self, ms_records: list[dict[str, Any]]) -> None:
        for r in ms_records:
            for key in ("id", "lang", "goal", "tools", "steps"):
                assert key in r, f"[{r.get('id')}] missing key '{key}'"

    def test_lang_distribution(self, ms_records: list[dict[str, Any]]) -> None:
        ko_count = sum(1 for r in ms_records if r["lang"] == "ko")
        en_count = sum(1 for r in ms_records if r["lang"] == "en")
        assert ko_count == 2, f"Expected 2 ko multistep records, got {ko_count}"
        assert en_count == 2, f"Expected 2 en multistep records, got {en_count}"

    def test_exactly_three_steps(self, ms_records: list[dict[str, Any]]) -> None:
        for r in ms_records:
            assert len(r["steps"]) == 3, (
                f"[{r['id']}] expected exactly 3 steps, got {len(r['steps'])}"
            )

    def test_step_required_keys(self, ms_records: list[dict[str, Any]]) -> None:
        for r in ms_records:
            for i, step in enumerate(r["steps"]):
                assert "expected" in step, (
                    f"[{r['id']}] step {i + 1} missing 'expected'"
                )
                assert "tool_result" in step, (
                    f"[{r['id']}] step {i + 1} missing 'tool_result'"
                )
                exp = step["expected"]
                assert "name" in exp, f"[{r['id']}] step {i + 1} expected.name missing"
                assert "args" in exp, f"[{r['id']}] step {i + 1} expected.args missing"

    def test_step_expected_name_in_tools(
        self, ms_records: list[dict[str, Any]]
    ) -> None:
        for r in ms_records:
            tool_names = _tool_names(r)
            for i, step in enumerate(r["steps"]):
                name = step["expected"]["name"]
                assert name in tool_names, (
                    f"[{r['id']}] step {i + 1} expected.name '{name}' not in tools {tool_names}"
                )

    def test_tools_openai_format(self, ms_records: list[dict[str, Any]]) -> None:
        for r in ms_records:
            assert isinstance(r["tools"], list) and len(r["tools"]) >= 1, (
                f"[{r['id']}] tools must be a non-empty list"
            )
            for tool in r["tools"]:
                assert tool.get("type") == "function", (
                    f"[{r['id']}] tool.type must be 'function'"
                )
                fn = tool.get("function", {})
                assert "name" in fn, f"[{r['id']}] tool.function.name missing"
                assert "parameters" in fn, (
                    f"[{r['id']}] tool.function.parameters missing"
                )
                params = fn["parameters"]
                assert "required" in params, (
                    f"[{r['id']}] tool.function.parameters.required missing"
                )

    def test_carry_forward_step2(self, ms_records: list[dict[str, Any]]) -> None:
        """Step 2 expected.args must have >=1 concrete value that appears in step 1 tool_result."""
        for r in ms_records:
            step1_result_values = _flatten_values(r["steps"][0]["tool_result"])
            step2_args = r["steps"][1]["expected"]["args"]
            # Collect non-null concrete arg values from step 2
            step2_concrete = {str(v) for v in step2_args.values() if v is not None}
            overlap = step2_concrete & step1_result_values
            assert len(overlap) >= 1, (
                f"[{r['id']}] step 2 expected.args has no value carried forward "
                f"from step 1 tool_result.\n"
                f"  step2 concrete args: {step2_concrete}\n"
                f"  step1 result values: {step1_result_values}"
            )

    def test_carry_forward_step3(self, ms_records: list[dict[str, Any]]) -> None:
        """Step 3 expected.args must have >=1 concrete value that appears in step 2 tool_result."""
        for r in ms_records:
            step2_result_values = _flatten_values(r["steps"][1]["tool_result"])
            step3_args = r["steps"][2]["expected"]["args"]
            step3_concrete = {str(v) for v in step3_args.values() if v is not None}
            overlap = step3_concrete & step2_result_values
            assert len(overlap) >= 1, (
                f"[{r['id']}] step 3 expected.args has no value carried forward "
                f"from step 2 tool_result.\n"
                f"  step3 concrete args: {step3_concrete}\n"
                f"  step2 result values: {step2_result_values}"
            )
