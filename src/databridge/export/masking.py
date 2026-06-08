from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from databridge.export.models import MaskingAction, MaskingRule

_PII_NAME_PATTERNS = (
    "email", "phone", "ssn", "password", "ip", "user_id",
    "token", "secret", "card",
)


def _get_path(obj: dict, parts: list[str]) -> tuple[dict, str] | None:
    """Navigate dot-path and return (parent_dict, final_key), or None if not found."""
    cur = obj
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return None
    return cur, parts[-1]


def apply_masking(record: dict, rules: list[MaskingRule]) -> dict:
    result = deepcopy(record)
    for rule in rules:
        parts = rule.field_path.split(".")
        location = _get_path(result, parts)
        if location is None:
            continue
        parent, key = location
        value = parent[key]
        if rule.action == MaskingAction.mask:
            parent[key] = "***"
        elif rule.action == MaskingAction.hash:
            parent[key] = hashlib.sha256(str(value).encode()).hexdigest()
        elif rule.action == MaskingAction.drop:
            del parent[key]
        elif rule.action == MaskingAction.redact:
            parent[key] = "[REDACTED]"
    return result


def pii_candidate_fields(schema_fields: dict) -> list[str]:
    candidates: list[str] = []
    for field_name in schema_fields:
        lower = field_name.lower()
        if any(pattern in lower for pattern in _PII_NAME_PATTERNS):
            candidates.append(field_name)
    return candidates
