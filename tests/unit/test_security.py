from databridge.security import redact_headers


def test_sensitive_headers_are_masked():
    headers = {
        "Authorization": "Bearer supersecrettoken",
        "x-api-key": "myapikey1234",
        "x-token": "tok",
        "cookie": "session=abc",
        "set-cookie": "id=xyz",
        "proxy-authorization": "Basic dXNlcjpwYXNz",
        "Content-Type": "application/json",
        "X-Request-ID": "abc-123",
    }
    result = redact_headers(headers)
    assert result["Authorization"] == "Bear...[REDACTED]"
    assert result["x-api-key"] == "myap...[REDACTED]"
    assert result["cookie"] == "sess...[REDACTED]"
    assert result["set-cookie"] == "id=x...[REDACTED]"
    assert result["proxy-authorization"] == "Basi...[REDACTED]"
    # Non-sensitive headers are unchanged
    assert result["Content-Type"] == "application/json"
    assert result["X-Request-ID"] == "abc-123"


def test_short_sensitive_value_fully_redacted():
    result = redact_headers({"Authorization": "abc"})
    assert result["Authorization"] == "[REDACTED]"


def test_returns_copy_not_mutating_original():
    original = {"Authorization": "Bearer token"}
    result = redact_headers(original)
    assert original["Authorization"] == "Bearer token"
    assert result["Authorization"] != "Bearer token"


def test_empty_headers():
    assert redact_headers({}) == {}


def test_case_insensitive_matching():
    result = redact_headers({"AUTHORIZATION": "Bearer secret"})
    assert result["AUTHORIZATION"] == "Bear...[REDACTED]"
