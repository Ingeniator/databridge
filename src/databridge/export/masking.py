from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from databridge.export.extraction import _decode_and_index
from databridge.export.models import MaskingAction, MaskingRule

_PII_NAME_PATTERNS = (
    "email", "phone", "ssn", "password", "ip", "user_id",
    "token", "secret", "card",
)


def _apply_at_path(container: Any, parts: list[str], action: MaskingAction) -> tuple[Any, bool]:
    """Recursively apply `action` at the dotted path `parts` inside `container`.

    The single-segment descend step (transparently parsing JSON-encoded string
    containers, and indexing into lists when a path segment is a plain digit
    -- e.g. "items.0.email") is shared with field extraction's read-only
    traversal via `extraction._decode_and_index`, since `_infer_schema_nested`'s
    PII discovery (adapters.py) surfaces dotted paths through stringified JSON
    that a plain dict-only walk would miss, and field extraction has the same
    problem reaching into enveloped JSON fields. This wrapper re-serializes the
    mutated node back to a string when the container was originally one.
    Returns (possibly-updated container, whether a value was actually found and
    the action applied).
    """
    reparse = isinstance(container, str)
    node, idx_or_key, found = _decode_and_index(container, parts[0])
    if not found:
        return container, False

    if len(parts) == 1:
        applied = _apply_action(node, idx_or_key, action)
    else:
        node[idx_or_key], applied = _apply_at_path(node[idx_or_key], parts[1:], action)

    return (json.dumps(node, ensure_ascii=False) if reparse else node), applied


def _apply_action(node: dict | list, key: Any, action: MaskingAction) -> bool:
    value = node[key]
    if action == MaskingAction.mask:
        node[key] = "***"
    elif action == MaskingAction.hash:
        node[key] = hashlib.sha256(str(value).encode()).hexdigest()
    elif action == MaskingAction.drop:
        del node[key]
    elif action == MaskingAction.redact:
        node[key] = "[REDACTED]"
    return True


def apply_masking(record: dict, rules: list[MaskingRule]) -> dict:
    result = deepcopy(record)
    for rule in rules:
        parts = rule.field_path.split(".")
        result, _ = _apply_at_path(result, parts, rule.action)
    return result


def pii_candidate_fields(schema_fields: dict) -> list[str]:
    candidates: list[str] = []
    for field_name in schema_fields:
        lower = field_name.lower()
        if any(pattern in lower for pattern in _PII_NAME_PATTERNS):
            candidates.append(field_name)
    return candidates
