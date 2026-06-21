"""Unit tests for export/webhook.py — render_payload and deliver_webhook."""
import pytest
import respx
import httpx

from databridge.export.webhook import deliver_webhook, render_payload


# ---------------------------------------------------------------------------
# render_payload
# ---------------------------------------------------------------------------

def test_render_payload_no_template_returns_context():
    ctx = {"job_id": "abc", "status": "done"}
    assert render_payload(None, ctx) is ctx


def test_render_payload_empty_string_returns_context():
    ctx = {"job_id": "abc"}
    assert render_payload("", ctx) is ctx


def test_render_payload_substitutes_known_placeholders():
    template = '{"id": "{{job_id}}", "status": "{{status}}"}'
    result = render_payload(template, {"job_id": "42", "status": "done"})
    assert result == {"id": "42", "status": "done"}


def test_render_payload_unknown_placeholder_becomes_empty_string():
    template = '{"x": "{{missing}}"}'
    result = render_payload(template, {})
    assert result == {"x": ""}


def test_render_payload_invalid_json_after_substitution_returns_context():
    ctx = {"val": "x"}
    # template renders to invalid JSON
    result = render_payload("not-json-{{val}}", ctx)
    assert result is ctx


# ---------------------------------------------------------------------------
# deliver_webhook
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_deliver_webhook_success():
    with respx.mock:
        respx.post("https://hook.example.com/notify").mock(
            return_value=httpx.Response(200)
        )
        await deliver_webhook("https://hook.example.com/notify", {"job_id": "1"})


@pytest.mark.anyio
async def test_deliver_webhook_http_error_does_not_raise():
    with respx.mock:
        respx.post("https://hook.example.com/notify").mock(
            return_value=httpx.Response(500)
        )
        # must not raise — fire-and-forget
        await deliver_webhook("https://hook.example.com/notify", {"job_id": "2"})


@pytest.mark.anyio
async def test_deliver_webhook_connect_error_does_not_raise():
    with respx.mock:
        respx.post("https://hook.example.com/notify").mock(
            side_effect=httpx.ConnectError("refused")
        )
        await deliver_webhook("https://hook.example.com/notify", {"job_id": "3"})


@pytest.mark.anyio
async def test_deliver_webhook_timeout_does_not_raise():
    with respx.mock:
        respx.post("https://hook.example.com/notify").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        await deliver_webhook("https://hook.example.com/notify", {"job_id": "4"})
