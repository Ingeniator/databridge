from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from databridge.export.models import MaskingAction, MaskingRule

_PII_NAME_PATTERNS = (
    "email", "phone", "ssn", "password", "ip", "user_id",
    "token", "secret", "card",
)


def _apply_at_path(container: Any, parts: list[str], action: MaskingAction) -> tuple[Any, bool]:
    """Recursively apply `action` at the dotted path `parts` inside `container`.

    Transparently descends into JSON-encoded string values (parsing them, applying
    the action, then re-serializing back to a string) since `_infer_schema_nested`'s
    PII discovery (adapters.py) surfaces dotted paths through stringified JSON that a
    plain dict-only walk would miss -- without this, fields discovered that way would
    be silently un-maskable. A path segment that is a plain integer also indexes into
    a list, so a manually-configured path like "items.0.email" can reach fields nested
    inside array elements (`field_path` isn't restricted to auto-discovered candidates).
    Returns (possibly-updated container, whether a value was actually found and the
    action applied).
    """
    node = container
    reparse = isinstance(node, str)
    if reparse:
        try:
            node = json.loads(node)
        except (json.JSONDecodeError, ValueError):
            return container, False

    key = parts[0]
    if isinstance(node, list):
        if not key.isdigit() or int(key) >= len(node):
            return container, False
        idx = int(key)
        if len(parts) == 1:
            applied = _apply_action(node, idx, action)
        else:
            node[idx], applied = _apply_at_path(node[idx], parts[1:], action)
    elif isinstance(node, dict):
        if key not in node:
            return container, False
        if len(parts) == 1:
            applied = _apply_action(node, key, action)
        else:
            node[key], applied = _apply_at_path(node[key], parts[1:], action)
    else:
        return container, False

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
