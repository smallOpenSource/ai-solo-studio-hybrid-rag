"""채점기 단위 테스트 — 6 verdict × 한/영 ≥12 픽스처."""

from __future__ import annotations


from bench.scoring import lenient_extract_json, score


# ── lenient_extract_json 테스트 ────────────────────────────────────


def test_extract_plain_json() -> None:
    text = '{"name": "get_weather", "arguments": {"city": "서울"}}'
    result = lenient_extract_json(text)
    assert result == {"name": "get_weather", "arguments": {"city": "서울"}}


def test_extract_json_fence() -> None:
    text = '```json\n{"name": "search", "arguments": {"query": "hello"}}\n```'
    result = lenient_extract_json(text)
    assert result == {"name": "search", "arguments": {"query": "hello"}}


def test_extract_think_tag_removed() -> None:
    text = '<think>생각 중...</think>\n{"name": "calc", "arguments": {"x": 1}}'
    result = lenient_extract_json(text)
    assert result is not None
    assert result["name"] == "calc"


def test_extract_surrounding_text() -> None:
    text = '물론입니다! 다음과 같이 호출합니다:\n{"name": "fn", "arguments": {}}\n이상입니다.'
    result = lenient_extract_json(text)
    assert result is not None
    assert result["name"] == "fn"


def test_extract_returns_none_on_empty() -> None:
    assert lenient_extract_json("") is None


def test_extract_returns_none_no_json() -> None:
    assert lenient_extract_json("안녕하세요, JSON이 없습니다.") is None


def test_extract_fence_without_json_label() -> None:
    text = '```\n{"name": "do_thing", "arguments": {"val": 42}}\n```'
    result = lenient_extract_json(text)
    assert result is not None
    assert result["name"] == "do_thing"


# ── score 테스트 — 6 verdict ────────────────────────────────────


# correct — 한국어
def test_score_correct_ko() -> None:
    expected = {"name": "get_weather", "args": {"city": "서울"}}
    got = {"name": "get_weather", "args": {"city": "서울"}}
    result = score(expected, got)
    assert result["verdict"] == "correct"


# correct — 영어
def test_score_correct_en() -> None:
    expected = {"name": "search_files", "args": {"query": "hello", "limit": None}}
    got = {"name": "search_files", "args": {"query": "HELLO", "extra_key": None}}
    # extra_key는 expected에 없으므로 undefined_arg
    result = score(expected, got)
    assert result["verdict"] == "undefined_arg"


# correct — null 인자는 무엇이든 OK
def test_score_correct_null_arg() -> None:
    expected = {"name": "get_time", "args": {"timezone": None}}
    got = {"name": "get_time", "args": {"timezone": "UTC"}}
    result = score(expected, got)
    assert result["verdict"] == "correct"


# wrong_name — 한국어
def test_score_wrong_name_ko() -> None:
    expected = {"name": "날씨조회", "args": {}}
    got = {"name": "날씨검색", "args": {}}
    result = score(expected, got)
    assert result["verdict"] == "wrong_name"


# wrong_name — 영어
def test_score_wrong_name_en() -> None:
    expected = {"name": "get_weather", "args": {}}
    got = {"name": "fetch_weather", "args": {}}
    result = score(expected, got)
    assert result["verdict"] == "wrong_name"


# missing_required — 한국어
def test_score_missing_required_ko() -> None:
    expected = {
        "name": "send_email",
        "args": {"recipient": "test@example.com", "subject": "안녕"},
    }
    got = {"name": "send_email", "args": {"recipient": "test@example.com"}}
    result = score(expected, got)
    assert result["verdict"] == "missing_required"


# missing_required — 영어
def test_score_missing_required_en() -> None:
    expected = {"name": "create_file", "args": {"path": "/tmp/foo", "content": "bar"}}
    got = {"name": "create_file", "args": {"path": "/tmp/foo"}}
    result = score(expected, got)
    assert result["verdict"] == "missing_required"


# undefined_arg — 한국어
def test_score_undefined_arg_ko() -> None:
    expected = {"name": "조회", "args": {"id": "123"}}
    got = {"name": "조회", "args": {"id": "123", "비밀인자": "xyz"}}
    result = score(expected, got)
    assert result["verdict"] == "undefined_arg"


# undefined_arg — 영어
def test_score_undefined_arg_en() -> None:
    expected = {"name": "list_files", "args": {"path": "/tmp"}}
    got = {"name": "list_files", "args": {"path": "/tmp", "unknown": True}}
    result = score(expected, got)
    assert result["verdict"] == "undefined_arg"


# wrong_value — 한국어
def test_score_wrong_value_ko() -> None:
    expected = {"name": "예약", "args": {"날짜": "2026-07-01"}}
    got = {"name": "예약", "args": {"날짜": "2026-08-01"}}
    result = score(expected, got)
    assert result["verdict"] == "wrong_value"


# wrong_value — 영어
def test_score_wrong_value_en() -> None:
    expected = {"name": "set_volume", "args": {"level": 5}}
    got = {"name": "set_volume", "args": {"level": 10}}
    result = score(expected, got)
    assert result["verdict"] == "wrong_value"


# parse_fail — got=None
def test_score_parse_fail_none() -> None:
    expected = {"name": "anything", "args": {}}
    result = score(expected, None)
    assert result["verdict"] == "parse_fail"


# correct — 문자열 casefold 비교
def test_score_correct_casefold() -> None:
    expected = {"name": "get_weather", "args": {"city": "Seoul"}}
    got = {"name": "GET_WEATHER", "args": {"city": "  SEOUL  "}}
    result = score(expected, got)
    assert result["verdict"] == "correct"


# correct — 숫자 허용 오차
def test_score_correct_numeric_tolerance() -> None:
    expected = {"name": "compute", "args": {"value": 1.0}}
    got = {"name": "compute", "args": {"value": 1.0000000001}}
    result = score(expected, got)
    assert result["verdict"] == "correct"
