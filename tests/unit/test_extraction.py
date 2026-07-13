"""T003 — unit tests for resolve_field_path() and extract_field_value()."""
import json

from databridge.export.extraction import _MISSING, extract_field_value, resolve_field_path


class TestResolveFieldPath:
    def test_single_segment(self):
        assert resolve_field_path({"a": 1}, ["a"]) == 1

    def test_multi_segment_dotted_path(self):
        container = {"a": {"b": {"c": 42}}}
        assert resolve_field_path(container, ["a", "b", "c"]) == 42

    def test_descends_through_json_encoded_string_container(self):
        container = {"event_properties": json.dumps({"trace": {"span_id": "abc"}})}
        result = resolve_field_path(container, ["event_properties", "trace"])
        assert result == {"span_id": "abc"}

    def test_missing_segment_returns_missing_sentinel(self):
        assert resolve_field_path({"a": 1}, ["b"]) is _MISSING

    def test_non_dict_container_returns_missing_sentinel(self):
        assert resolve_field_path({"a": "not-a-dict"}, ["a", "b"]) is _MISSING

    def test_unparseable_string_container_returns_missing_sentinel(self):
        assert resolve_field_path("not json", ["a"]) is _MISSING

    def test_empty_parts_returns_container_itself(self):
        assert resolve_field_path({"a": 1}, []) == {"a": 1}

    def test_digit_segment_indexes_into_list(self):
        container = {"items": [{"email": "a@x.com"}, {"email": "b@x.com"}]}
        assert resolve_field_path(container, ["items", "1", "email"]) == "b@x.com"

    def test_digit_segment_out_of_range_returns_missing_sentinel(self):
        container = {"items": [{"email": "a@x.com"}]}
        assert resolve_field_path(container, ["items", "5", "email"]) is _MISSING

    def test_non_digit_segment_against_list_returns_missing_sentinel(self):
        container = {"items": [{"email": "a@x.com"}]}
        assert resolve_field_path(container, ["items", "email"]) is _MISSING


class TestExtractFieldValue:
    def test_native_dict_value_returned_unchanged(self):
        record = {"event_properties": {"trace": {"span_id": "abc"}}}
        assert extract_field_value(record, "event_properties.trace") == {"span_id": "abc"}

    def test_native_list_value_returned_unchanged(self):
        record = {"payload": [1, 2, 3]}
        assert extract_field_value(record, "payload") == [1, 2, 3]

    def test_json_encoded_string_dict_is_parsed(self):
        record = {"event_properties": json.dumps({"trace": json.dumps({"span_id": "abc"})})}
        assert extract_field_value(record, "event_properties.trace") == {"span_id": "abc"}

    def test_json_encoded_string_list_is_parsed(self):
        record = {"payload": json.dumps([1, 2, 3])}
        assert extract_field_value(record, "payload") == [1, 2, 3]

    def test_missing_field_returns_none(self):
        record = {"other": 1}
        assert extract_field_value(record, "event_properties.trace") is None

    def test_empty_string_value_returns_none(self):
        record = {"trace": ""}
        assert extract_field_value(record, "trace") is None

    def test_plain_non_json_string_returns_none(self):
        record = {"trace": "just a log line, not json"}
        assert extract_field_value(record, "trace") is None

    def test_json_encoded_bare_scalar_returns_none(self):
        record = {"trace": "123"}
        assert extract_field_value(record, "trace") is None

        record2 = {"trace": "true"}
        assert extract_field_value(record2, "trace") is None

    def test_nested_path_through_json_string_envelope(self):
        record = {"event_properties": json.dumps({"trace": {"span_id": "abc", "duration_ms": 42}})}
        assert extract_field_value(record, "event_properties.trace") == {
            "span_id": "abc",
            "duration_ms": 42,
        }
