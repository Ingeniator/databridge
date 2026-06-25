"""T016 — unit tests for apply_masking() and pii_candidate_fields()."""
import hashlib
import pytest
from databridge.export.masking import apply_masking, pii_candidate_fields
from databridge.export.models import MaskingAction, MaskingRule


def _rule(field_path: str, action: MaskingAction) -> MaskingRule:
    return MaskingRule(field_path=field_path, action=action)


class TestApplyMasking:
    def test_mask_replaces_with_stars(self):
        record = {"email": "user@example.com"}
        result = apply_masking(record, [_rule("email", MaskingAction.mask)])
        assert result["email"] == "***"

    def test_hash_replaces_with_sha256(self):
        record = {"user_id": "abc123"}
        result = apply_masking(record, [_rule("user_id", MaskingAction.hash)])
        expected = hashlib.sha256(b"abc123").hexdigest()
        assert result["user_id"] == expected

    def test_drop_removes_field(self):
        record = {"phone": "555-1234", "name": "Alice"}
        result = apply_masking(record, [_rule("phone", MaskingAction.drop)])
        assert "phone" not in result
        assert result["name"] == "Alice"

    def test_redact_replaces_with_label(self):
        record = {"ssn": "123-45-6789"}
        result = apply_masking(record, [_rule("ssn", MaskingAction.redact)])
        assert result["ssn"] == "[REDACTED]"

    def test_nested_dot_path_mask(self):
        record = {"payload": {"user_id": "u1", "status": "ok"}}
        result = apply_masking(record, [_rule("payload.user_id", MaskingAction.mask)])
        assert result["payload"]["user_id"] == "***"
        assert result["payload"]["status"] == "ok"

    def test_nested_dot_path_drop(self):
        record = {"meta": {"ip_address": "1.2.3.4", "region": "us"}}
        result = apply_masking(record, [_rule("meta.ip_address", MaskingAction.drop)])
        assert "ip_address" not in result["meta"]
        assert result["meta"]["region"] == "us"

    def test_missing_field_is_ignored(self):
        record = {"name": "Alice"}
        result = apply_masking(record, [_rule("nonexistent", MaskingAction.mask)])
        assert result == {"name": "Alice"}

    def test_original_record_not_mutated(self):
        record = {"email": "x@y.com"}
        original = dict(record)
        apply_masking(record, [_rule("email", MaskingAction.mask)])
        assert record == original

    def test_multiple_rules_applied_in_order(self):
        record = {"a": "1", "b": "2"}
        rules = [_rule("a", MaskingAction.mask), _rule("b", MaskingAction.redact)]
        result = apply_masking(record, rules)
        assert result["a"] == "***"
        assert result["b"] == "[REDACTED]"


class TestPiiCandidateFields:
    def test_returns_email_field(self):
        schema = {"email": {"type": "string"}, "name": {"type": "string"}}
        assert "email" in pii_candidate_fields(schema)
        assert "name" not in pii_candidate_fields(schema)

    def test_returns_phone_field(self):
        schema = {"phone_number": {"type": "string"}}
        assert "phone_number" in pii_candidate_fields(schema)

    def test_returns_user_id_field(self):
        schema = {"user_id": {"type": "string"}, "count": {"type": "int"}}
        assert "user_id" in pii_candidate_fields(schema)

    def test_returns_ip_address_field(self):
        schema = {"ip_address": {"type": "string"}}
        assert "ip_address" in pii_candidate_fields(schema)

    def test_empty_schema_returns_empty(self):
        assert pii_candidate_fields({}) == []

    def test_no_pii_fields_returns_empty(self):
        schema = {"timestamp": {"type": "string"}, "event": {"type": "string"}}
        assert pii_candidate_fields(schema) == []
