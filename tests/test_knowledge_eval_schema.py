"""US-002 K0 — knowledge_eval.jsonl 스키마 검증.

오프라인 전용 (파일만 읽음, 네트워크·서버 접촉 0).
slow 마커 없음.

검증 항목:
- 항목 수 ≥ 20
- 필수 필드 존재: id, query, answer_doc, category, difficulty
- category 유효 태그: semantic | keyword
- difficulty 유효 태그: easy | medium | hard
- keyword 카테고리 항목 ≥ 6 (sparse 신호 측정용)
- id 중복 없음
- answer_doc이 wiki/ 로 시작
- answer_snippet 비어있지 않음
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

CASES_DIR = Path(__file__).parent.parent / "cases"
EVAL_FILE = CASES_DIR / "knowledge_eval.jsonl"

VALID_CATEGORIES = {"semantic", "keyword"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
REQUIRED_FIELDS = {"id", "query", "answer_doc", "category", "difficulty"}
MIN_ITEMS = 20
MIN_KEYWORD_ITEMS = 6


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def eval_records() -> list[dict[str, Any]]:
    """knowledge_eval.jsonl을 파싱해 레코드 목록 반환."""
    assert EVAL_FILE.exists(), f"평가셋 파일이 없음: {EVAL_FILE}"
    records: list[dict[str, Any]] = []
    with EVAL_FILE.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                pytest.fail(f"{EVAL_FILE.name}:{lineno} — JSON 파싱 오류: {exc}")
    return records


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------


class TestKnowledgeEvalSchema:
    """knowledge_eval.jsonl 스키마 및 수용기준 검증."""

    def test_file_exists(self) -> None:
        """평가셋 파일이 존재해야 한다."""
        assert EVAL_FILE.exists(), (
            "cases/knowledge_eval.jsonl 파일이 없음. K0 산출물 필요."
        )

    def test_minimum_item_count(self, eval_records: list[dict[str, Any]]) -> None:
        """항목 수 ≥ 20 (수용기준 US-002 #1)."""
        count = len(eval_records)
        assert count >= MIN_ITEMS, (
            f"평가셋 항목 수 {count}개 — 최소 {MIN_ITEMS}개 필요 (현재 부족: {MIN_ITEMS - count}개)"
        )

    def test_required_fields_present(self, eval_records: list[dict[str, Any]]) -> None:
        """각 항목에 필수 필드(id·query·answer_doc·category·difficulty)가 있어야 한다."""
        for record in eval_records:
            missing = REQUIRED_FIELDS - record.keys()
            assert not missing, f"[{record.get('id', '?')}] 필수 필드 누락: {missing}"

    def test_category_valid_tag(self, eval_records: list[dict[str, Any]]) -> None:
        """category는 'semantic' 또는 'keyword' 중 하나여야 한다."""
        for record in eval_records:
            cat = record.get("category", "")
            assert cat in VALID_CATEGORIES, (
                f"[{record.get('id')}] 유효하지 않은 category: {cat!r} "
                f"(허용값: {VALID_CATEGORIES})"
            )

    def test_difficulty_valid_tag(self, eval_records: list[dict[str, Any]]) -> None:
        """difficulty는 'easy', 'medium', 'hard' 중 하나여야 한다."""
        for record in eval_records:
            diff = record.get("difficulty", "")
            assert diff in VALID_DIFFICULTIES, (
                f"[{record.get('id')}] 유효하지 않은 difficulty: {diff!r} "
                f"(허용값: {VALID_DIFFICULTIES})"
            )

    def test_keyword_category_minimum_count(
        self, eval_records: list[dict[str, Any]]
    ) -> None:
        """keyword 카테고리 항목 ≥ 6 (sparse 신호 측정용, 수용기준 US-002 #2)."""
        keyword_count = sum(1 for r in eval_records if r.get("category") == "keyword")
        assert keyword_count >= MIN_KEYWORD_ITEMS, (
            f"keyword 카테고리 항목 {keyword_count}개 — "
            f"최소 {MIN_KEYWORD_ITEMS}개 필요 (sparse 신호 측정용)"
        )

    def test_no_duplicate_ids(self, eval_records: list[dict[str, Any]]) -> None:
        """id 중복이 없어야 한다."""
        ids = [r.get("id") for r in eval_records]
        seen: set[str] = set()
        duplicates: list[str] = []
        for id_val in ids:
            if id_val in seen:
                duplicates.append(str(id_val))
            seen.add(str(id_val))
        assert not duplicates, f"중복 id 발견: {duplicates}"

    def test_answer_doc_starts_with_wiki(
        self, eval_records: list[dict[str, Any]]
    ) -> None:
        """answer_doc은 'wiki/' 경로로 시작해야 한다 (코퍼스 내 파일)."""
        for record in eval_records:
            doc = record.get("answer_doc", "")
            assert doc.startswith("wiki/"), (
                f"[{record.get('id')}] answer_doc이 wiki/ 로 시작하지 않음: {doc!r}"
            )

    def test_query_is_nonempty_string(self, eval_records: list[dict[str, Any]]) -> None:
        """query는 비어있지 않은 문자열이어야 한다."""
        for record in eval_records:
            query = record.get("query", "")
            assert isinstance(query, str) and len(query.strip()) > 0, (
                f"[{record.get('id')}] query가 비어있거나 문자열이 아님: {query!r}"
            )

    def test_answer_snippet_is_nonempty_string(
        self, eval_records: list[dict[str, Any]]
    ) -> None:
        """answer_snippet은 비어있지 않은 문자열이어야 한다."""
        for record in eval_records:
            snippet = record.get("answer_snippet", "")
            assert isinstance(snippet, str) and len(snippet.strip()) > 0, (
                f"[{record.get('id')}] answer_snippet이 비어있거나 문자열이 아님"
            )

    def test_id_format(self, eval_records: list[dict[str, Any]]) -> None:
        """id는 'ke-' 접두사를 가진 문자열이어야 한다."""
        for record in eval_records:
            id_val = record.get("id", "")
            assert isinstance(id_val, str) and id_val.startswith("ke-"), (
                f"id 형식 불일치: {id_val!r} (기대: 'ke-NN' 형식)"
            )

    def test_difficulty_distribution(self, eval_records: list[dict[str, Any]]) -> None:
        """난이도 분포 — easy·medium·hard 각각 ≥ 1개 (분포 있음 확인)."""
        for diff in VALID_DIFFICULTIES:
            count = sum(1 for r in eval_records if r.get("difficulty") == diff)
            assert count >= 1, f"difficulty='{diff}' 항목이 없음 — 난이도 분포 필요"
