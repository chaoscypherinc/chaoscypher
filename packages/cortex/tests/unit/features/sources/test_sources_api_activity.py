# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for last_activity_at + recovery_attempts in source response models.

The two resumability observability fields must flow all the way from the
SourceRow column → SqliteAdapter list_sources/get_source projection →
engine service → Cortex SourcesService → SourceResponse. These tests
pin the Pydantic model and a small round-trip through the response
model's from_attributes handling.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chaoscypher_cortex.features.sources.models import SourceResponse, SourceSummaryResponse


def _base_payload() -> dict:
    """Minimum payload to satisfy the required SourceResponse fields."""
    return {
        "id": "s-1",
        "database_name": "default",
        "filename": "test.pdf",
        "status": "indexing",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def test_source_response_accepts_activity_fields() -> None:
    """The Pydantic response model accepts both resumability fields."""
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)
    payload = _base_payload()
    payload["last_activity_at"] = now
    payload["recovery_attempts"] = 3
    source = SourceResponse(**payload)
    assert source.last_activity_at == now
    assert source.recovery_attempts == 3


def test_source_response_defaults_for_activity_fields() -> None:
    """When omitted, last_activity_at is None and recovery_attempts is 0."""
    source = SourceResponse(**_base_payload())
    assert source.last_activity_at is None
    assert source.recovery_attempts == 0


def test_source_response_round_trip_from_attributes() -> None:
    """from_attributes=True lets the model hydrate from an object with matching attributes.

    This is how FastAPI serializes repo rows.
    """
    now = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)
    row = SimpleNamespace(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="extracting",
        enabled=True,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        recovery_attempts=2,
    )
    source = SourceResponse.model_validate(row, from_attributes=True)
    assert source.last_activity_at == now
    assert source.recovery_attempts == 2


def test_source_response_recovery_attempts_is_int() -> None:
    """Pydantic rejects non-int values for the counter."""
    payload = _base_payload()
    payload["recovery_attempts"] = "not-an-int"
    with pytest.raises(ValidationError):
        SourceResponse(**payload)


# ---------------------------------------------------------------------------
# SourceSummaryResponse (list view) must include recovery_attempts
# ---------------------------------------------------------------------------


def _summary_payload() -> dict:
    """Minimum payload to satisfy the required SourceSummaryResponse fields."""
    return {
        "id": "s-1",
        "database_name": "default",
        "filename": "test.pdf",
        "status": "indexing",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def test_source_summary_response_includes_recovery_attempts_default() -> None:
    """SourceSummaryResponse defaults recovery_attempts to 0 when not supplied.

    The D1 badge in SourceStatusCell renders only when recovery_attempts > 0.
    The field must be present in the list API response so the badge can work.
    """
    summary = SourceSummaryResponse(**_summary_payload())
    assert hasattr(summary, "recovery_attempts")
    assert summary.recovery_attempts == 0


def test_source_summary_response_accepts_recovery_attempts() -> None:
    """SourceSummaryResponse carries a non-zero recovery_attempts value through."""
    payload = _summary_payload()
    payload["recovery_attempts"] = 3
    summary = SourceSummaryResponse(**payload)
    assert summary.recovery_attempts == 3


def test_source_summary_response_recovery_attempts_flows_from_dict() -> None:
    """The field flows via **source_dict unpacking (the API construction pattern)."""
    now = datetime.now(UTC)
    source_dict = {
        "id": "s-2",
        "database_name": "default",
        "filename": "doc.pdf",
        "status": "extracted",
        "recovery_attempts": 2,
        "created_at": now,
        "updated_at": now,
    }
    summary = SourceSummaryResponse(**source_dict)
    assert summary.recovery_attempts == 2
