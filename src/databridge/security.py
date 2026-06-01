_SENSITIVE_HEADERS = frozenset({
    "authorization", "x-api-key", "x-token",
    "cookie", "set-cookie", "proxy-authorization",
})
_REDACT_PREFIX_LEN = 4


def _redact_value(value: str) -> str:
    if len(value) <= _REDACT_PREFIX_LEN:
        return "[REDACTED]"
    return value[:_REDACT_PREFIX_LEN] + "...[REDACTED]"


def redact_headers(headers: dict) -> dict:
    """Return a copy of headers with sensitive values masked."""
    return {
        k: _redact_value(v) if k.lower() in _SENSITIVE_HEADERS else v
        for k, v in headers.items()
    }
