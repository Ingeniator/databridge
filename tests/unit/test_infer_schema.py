from databridge.adapters import _infer_schema, _py_type


# ── _py_type ──────────────────────────────────────────────────────────────────

def test_py_type_primitives():
    assert _py_type(True)   == "bool"
    assert _py_type(False)  == "bool"
    assert _py_type(1)      == "int"
    assert _py_type(3.14)   == "float"
    assert _py_type("hi")   == "string"
    assert _py_type([])     == "list"
    assert _py_type({})     == "object"


def test_py_type_bool_before_int():
    # bool is a subclass of int in Python — must be checked first
    assert _py_type(True) == "bool"
    assert _py_type(1)    == "int"


# ── _infer_schema ─────────────────────────────────────────────────────────────

def test_empty_records_returns_empty_schema():
    assert _infer_schema([]) == {}


def test_flat_record():
    schema = _infer_schema([{"session_id": "s1", "cost": 0.012, "count": 5}])
    assert schema["session_id"] == {"type": "string", "example": "s1"}
    assert schema["cost"]       == {"type": "float",  "example": 0.012}
    assert schema["count"]      == {"type": "int",    "example": 5}


def test_nested_dict_uses_dot_notation():
    schema = _infer_schema([{"body": {"cost": 0.01, "model": "gpt-4"}}])
    assert "body.cost"  in schema
    assert "body.model" in schema
    assert "body"       not in schema   # intermediate dicts are not emitted


def test_json_string_value_is_parsed_and_nested():
    schema = _infer_schema([{"body": '{"cost": 0.01, "model": "gpt-4"}'}])
    assert "body.cost"  in schema
    assert "body.model" in schema
    assert "body"       not in schema


def test_non_json_string_stays_flat():
    schema = _infer_schema([{"body": "just a plain log line"}])
    assert schema["body"] == {"type": "string", "example": "just a plain log line"}


def test_malformed_json_string_stays_flat():
    schema = _infer_schema([{"body": "{not valid json"}])
    assert schema["body"]["type"] == "string"


def test_depth_limit_three_levels():
    # leaf at depth 3 (a.b.c) is included
    schema_3 = _infer_schema([{"a": {"b": {"c": "leaf"}}}])
    assert "a.b.c" in schema_3

    # leaf at depth 4 (a.b.c.d) is skipped — depth > 3 returns early
    schema_4 = _infer_schema([{"a": {"b": {"c": {"d": "too deep"}}}}])
    assert "a.b.c.d" not in schema_4


def test_underscore_keys_skipped():
    schema = _infer_schema([{"_private": "x", "public": "y"}])
    assert "_private" not in schema
    assert "public"   in schema


def test_list_value_type():
    schema = _infer_schema([{"tags": ["a", "b"]}])
    assert schema["tags"]["type"]    == "list"
    assert schema["tags"]["example"] is None


def test_first_seen_example_wins():
    records = [{"score": 0.9}, {"score": 0.1}]
    schema = _infer_schema(records)
    assert schema["score"]["example"] == 0.9   # first record's value kept


def test_multiple_records_merged():
    records = [
        {"session_id": "s1", "cost": 0.01},
        {"session_id": "s2", "model": "gpt-4"},
    ]
    schema = _infer_schema(records)
    assert "session_id" in schema
    assert "cost"       in schema
    assert "model"      in schema


def test_bool_field_type():
    schema = _infer_schema([{"is_cached": True}])
    assert schema["is_cached"]["type"] == "bool"
