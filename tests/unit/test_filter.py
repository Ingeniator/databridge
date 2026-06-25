"""Tests for _query_to_sql filter expression translation (adapters.py)."""
import pytest

from databridge.adapters import _query_to_sql


# ── Empty / blank ─────────────────────────────────────────────────────────────

def test_empty_string_returns_none():
    assert _query_to_sql("", "message") is None


def test_whitespace_only_returns_none():
    assert _query_to_sql("   ", "message") is None


# ── Single operator — equality / inequality ───────────────────────────────────

def test_eq_maps_to_sql_equals():
    result = _query_to_sql("status == 'ok'", "message")
    assert result == "status = 'ok'"


def test_ne_operator():
    result = _query_to_sql("status != 'error'", "message")
    assert result == "status != 'error'"


# ── Single operator — comparisons ─────────────────────────────────────────────

def test_gte_operator():
    result = _query_to_sql("level >= 'WARNING'", "message")
    assert result == "level >= 'WARNING'"


def test_lte_operator():
    result = _query_to_sql("level <= 'INFO'", "message")
    assert result == "level <= 'INFO'"


def test_gt_operator():
    result = _query_to_sql("count > '10'", "message")
    assert result == "count > '10'"


def test_lt_operator():
    result = _query_to_sql("count < '100'", "message")
    assert result == "count < '100'"


# ── contains operator ─────────────────────────────────────────────────────────

def test_contains_produces_positioncaseinsensitive():
    result = _query_to_sql("message contains 'hello'", "message")
    assert result == "positionCaseInsensitive(toString(message), 'hello') > 0"


def test_contains_uppercase():
    result = _query_to_sql("message CONTAINS 'Hello'", "msg")
    assert result == "positionCaseInsensitive(toString(message), 'Hello') > 0"


def test_contains_mixed_case():
    result = _query_to_sql("body Contains 'text'", "body")
    assert result == "positionCaseInsensitive(toString(body), 'text') > 0"


# ── Compound AND ──────────────────────────────────────────────────────────────

def test_two_rules_and():
    result = _query_to_sql("a == 'x' AND b == 'y'", "message")
    assert result == "a = 'x' AND b = 'y'"


def test_two_rules_and_lowercase():
    result = _query_to_sql("a == 'x' and b == 'y'", "message")
    assert result == "a = 'x' and b = 'y'"


def test_and_with_mixed_operators():
    result = _query_to_sql("level >= 'WARNING' AND status != 'ok'", "message")
    assert result == "level >= 'WARNING' AND status != 'ok'"


def test_and_with_contains():
    result = _query_to_sql("status == 'ok' AND message contains 'error'", "message")
    assert result == "status = 'ok' AND positionCaseInsensitive(toString(message), 'error') > 0"


# ── Compound OR ───────────────────────────────────────────────────────────────

def test_two_rules_or():
    result = _query_to_sql("a == 'x' OR b == 'y'", "message")
    assert result == "a = 'x' OR b = 'y'"


def test_two_rules_or_lowercase():
    result = _query_to_sql("a == 'x' or b == 'y'", "message")
    assert result == "a = 'x' or b = 'y'"


def test_or_with_mixed_operators():
    result = _query_to_sql("level > 'DEBUG' OR level < 'CRITICAL'", "message")
    assert result == "level > 'DEBUG' OR level < 'CRITICAL'"


def test_or_with_contains():
    result = _query_to_sql("message contains 'fail' OR status != 'ok'", "log")
    assert result == "positionCaseInsensitive(toString(message), 'fail') > 0 OR status != 'ok'"


# ── Three-rule compound ───────────────────────────────────────────────────────

def test_three_rules_all_and():
    result = _query_to_sql("a == 'x' AND b >= 'y' AND c <= 'z'", "message")
    assert result == "a = 'x' AND b >= 'y' AND c <= 'z'"


def test_three_rules_and_then_or():
    result = _query_to_sql("a == 'x' AND b != 'y' OR c > '0'", "message")
    assert result == "a = 'x' AND b != 'y' OR c > '0'"


def test_three_rules_with_contains():
    result = _query_to_sql("a == 'x' AND b contains 'hi' OR c < '99'", "message")
    assert result == "a = 'x' AND positionCaseInsensitive(toString(b), 'hi') > 0 OR c < '99'"


# ── Operator mapping completeness ─────────────────────────────────────────────

@pytest.mark.parametrize("op,expected_sql_op", [
    ("==", "="),
    ("!=", "!="),
    (">=", ">="),
    ("<=", "<="),
    (">", ">"),
    ("<", "<"),
])
def test_all_operators_map_correctly(op, expected_sql_op):
    result = _query_to_sql(f"field {op} 'value'", "message")
    assert result == f"field {expected_sql_op} 'value'"


# ── Fallback — unstructured full-text search ──────────────────────────────────

def test_unstructured_plain_text_falls_back():
    result = _query_to_sql("hello world", "message")
    assert result == "positionCaseInsensitive(toString(message), 'hello world') > 0"


def test_single_equals_not_double_falls_back():
    # single `=` doesn't match the structured pattern → full-text fallback
    result = _query_to_sql("status = 'ok'", "message")
    assert result == "positionCaseInsensitive(toString(message), 'status = \\'ok\\'') > 0"


def test_fallback_uses_given_search_column():
    result = _query_to_sql("some query text", "log_line")
    assert "toString(log_line)" in result


def test_fallback_wraps_entire_expression():
    result = _query_to_sql("foo bar baz", "msg")
    assert result == "positionCaseInsensitive(toString(msg), 'foo bar baz') > 0"


def test_partial_match_falls_back_to_full_text():
    # first token matches but second doesn't → handled by fallback at first bad token
    result = _query_to_sql("status == 'ok' AND raw text here", "message")
    assert "positionCaseInsensitive" in result


# ── Whitespace tolerance ──────────────────────────────────────────────────────

def test_extra_spaces_around_operator():
    result = _query_to_sql("field  ==  'val'", "message")
    assert result == "field = 'val'"


def test_leading_and_trailing_whitespace_trimmed():
    result = _query_to_sql("  status == 'ok'  ", "message")
    assert result == "status = 'ok'"


# ── Value content edge cases ──────────────────────────────────────────────────

def test_empty_string_value():
    result = _query_to_sql("status == ''", "message")
    assert result == "status = ''"


def test_value_with_spaces():
    result = _query_to_sql("name == 'John Doe'", "message")
    assert result == "name = 'John Doe'"


def test_value_with_escaped_quote():
    result = _query_to_sql(r"msg contains 'it\'s'", "message")
    assert "positionCaseInsensitive(toString(msg)," in result
    assert r"it\'s" in result


def test_numeric_string_value():
    result = _query_to_sql("code == '404'", "message")
    assert result == "code = '404'"


def test_value_with_special_chars():
    result = _query_to_sql("path == '/api/v1/health'", "message")
    assert result == "path = '/api/v1/health'"


# ── Field name edge cases ─────────────────────────────────────────────────────

def test_alphanumeric_field_name():
    result = _query_to_sql("field123 == 'val'", "message")
    assert result == "field123 = 'val'"


def test_underscore_field_name():
    result = _query_to_sql("user_id == 'abc'", "message")
    assert result == "user_id = 'abc'"


def test_search_column_propagated_to_fallback():
    result = _query_to_sql("not a rule", "custom_col")
    assert "toString(custom_col)" in result


# ── Return type ───────────────────────────────────────────────────────────────

def test_returns_string_for_valid_expression():
    result = _query_to_sql("field == 'val'", "message")
    assert isinstance(result, str)


def test_returns_none_for_empty():
    assert _query_to_sql("", "col") is None
