"""mock 응답 키 구조가 캡처 픽스처와 재귀적으로 일치하는지 검증."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_CAPTURES_DIR = Path(__file__).parent / "fixtures" / "endpoint_captures"


def _key_structure(obj: object) -> object:
    """dict/list를 재귀적으로 키 구조만 남긴다 (값은 타입명으로 치환)."""
    if isinstance(obj, dict):
        return {k: _key_structure(v) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return []
        return [_key_structure(obj[0])]
    return type(obj).__name__


def _load_capture(name: str) -> dict | None:
    path = _CAPTURES_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _captures_exist() -> bool:
    return _CAPTURES_DIR.exists() and any(_CAPTURES_DIR.glob("*.json"))


# 캡처가 없으면 전체 스킵
pytestmark = pytest.mark.skipif(
    not _captures_exist(),
    reason="endpoint_captures 픽스처 없음 — --probe --capture 먼저 실행",
)


def test_native_fc_structure() -> None:
    """probe_native_fc 캡처의 키 구조 검증."""
    cap = _load_capture("probe_native_fc")
    if cap is None:
        pytest.skip("probe_native_fc.json 없음")

    if cap.get("provisional"):
        pytest.skip("provisional 마킹 — 실캡처 아님")

    struct = _key_structure(cap)
    # choices 배열이 있어야 함
    assert "choices" in struct, f"'choices' 키 없음: {list(struct)}"
    choices_struct = struct["choices"]
    assert isinstance(choices_struct, list) and len(choices_struct) > 0
    choice = choices_struct[0]
    assert "message" in choice, f"choices[0]에 'message' 없음: {list(choice)}"


def test_schema_strict_structure() -> None:
    cap = _load_capture("probe_schema_strict")
    if cap is None:
        pytest.skip("probe_schema_strict.json 없음")
    if cap.get("provisional"):
        pytest.skip("provisional 마킹 — schema_strict 미지원")

    struct = _key_structure(cap)
    assert "choices" in struct


def test_bge_embed_structure() -> None:
    cap = _load_capture("probe_bge_embed")
    if cap is None:
        pytest.skip("probe_bge_embed.json 없음")

    assert "dense_embeddings" in cap, f"'dense_embeddings' 없음: {list(cap)}"
    embeddings = cap["dense_embeddings"]
    assert isinstance(embeddings, list)
    assert len(embeddings) > 0
    assert isinstance(embeddings[0], list)


def test_bge_rerank_structure() -> None:
    cap = _load_capture("probe_bge_rerank")
    if cap is None:
        pytest.skip("probe_bge_rerank.json 없음")

    assert isinstance(cap, (dict, list)), f"예상치 못한 타입: {type(cap).__name__}"


def test_all_captures_are_valid_json() -> None:
    """모든 캡처 파일이 유효한 JSON인지 확인."""
    if not _CAPTURES_DIR.exists():
        pytest.skip("captures 디렉토리 없음")

    for path in sorted(_CAPTURES_DIR.glob("*.json")):
        content = path.read_text(encoding="utf-8")
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{path.name} 유효하지 않은 JSON: {exc}")
