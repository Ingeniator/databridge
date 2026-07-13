"""Field extraction: reduce a record to the value found at a single nested
field path, for jobs that opt into `field_extraction`. See
specs/changes/004-trace-extraction/data-model.md §2.
"""
from __future__ import annotations

import json
from typing import Any

_MISSING = object()


def _decode_and_index(node: Any, key: str) -> tuple[Any, Any, bool]:
    """Single-segment descent shared by masking's mutate-in-place walk and this
    module's read-only walk: transparently json.loads-es `node` if it's a
    string, then resolves `key` against the decoded node (dict key, or list
    index when `key` is a plain digit string). Returns
    (decoded_node, index_or_key, found) -- `decoded_node[index_or_key]` is the
    child when `found` is True; callers needing to re-serialize after a
    mutation must track whether `node` was originally a string themselves.
    """
    if isinstance(node, str):
        try:
            node = json.loads(node)
        except (json.JSONDecodeError, ValueError):
            return node, key, False
    if isinstance(node, list):
        if key.isdigit() and int(key) < len(node):
            return node, int(key), True
        return node, key, False
    if isinstance(node, dict):
        if key in node:
            return node, key, True
        return node, key, False
    return node, key, False


def resolve_field_path(container: Any, parts: list[str]) -> Any:
    """Read-only dotted-path descent: walks `parts` inside `container`,
    transparently json.loads-ing any string container encountered along the
    way, and indexing into lists when a path segment is a plain digit.
    Returns _MISSING if the path doesn't resolve.
    """
    node = container
    if not parts:
        return node

    decoded, idx_or_key, found = _decode_and_index(node, parts[0])
    if not found:
        return _MISSING

    child = decoded[idx_or_key]
    if len(parts) == 1:
        return child
    return resolve_field_path(child, parts[1:])


def extract_field_value(record: dict, field_path: str) -> Any | None:
    """Resolve field_path in record and return usable extracted content, or
    None if the field is missing or its value isn't usable (only dict/list
    values, native or JSON-string-encoded, count as usable).
    """
    resolved = resolve_field_path(record, field_path.split("."))
    if resolved is _MISSING:
        return None
    if isinstance(resolved, (dict, list)):
        return resolved
    if isinstance(resolved, str):
        try:
            parsed = json.loads(resolved)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, (dict, list)) else None
    return None
