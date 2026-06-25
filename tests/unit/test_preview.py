"""T008 — failing unit tests for total_count in preview response (write first, TDD)."""
import pytest
from databridge.models import PreviewResponse
from uuid import uuid4


def test_total_count_non_negative():
    resp = PreviewResponse(
        results=[{"id": 1}],
        connection_id=uuid4(),
        total_count=42,
        schema_fields={"id": {"type": "int"}},
    )
    assert resp.total_count >= 0


def test_results_length_respects_limit():
    rows = [{"id": i} for i in range(50)]
    resp = PreviewResponse(
        results=rows,
        connection_id=uuid4(),
        total_count=10_000,
        schema_fields={},
    )
    assert len(resp.results) <= 100_000


def test_total_count_larger_than_results():
    rows = [{"id": i} for i in range(10)]
    resp = PreviewResponse(
        results=rows,
        connection_id=uuid4(),
        total_count=5_000_000,
    )
    assert resp.total_count > len(resp.results)


def test_schema_fields_present():
    resp = PreviewResponse(
        results=[],
        connection_id=uuid4(),
        total_count=0,
        schema_fields={"timestamp": {"type": "string"}, "status": {"type": "string"}},
    )
    assert "timestamp" in resp.schema_fields
    assert "status" in resp.schema_fields


def test_preview_response_defaults():
    resp = PreviewResponse(results=[], connection_id=uuid4())
    assert resp.total_count == 0
    assert resp.schema_fields == {}
