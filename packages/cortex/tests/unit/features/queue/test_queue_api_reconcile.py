# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the POST /api/v1/queue/reconcile endpoint handler.

Calls the endpoint function directly with a mocked service (matching
the existing cortex test pattern) rather than spinning up a TestClient.
"""

from unittest.mock import AsyncMock

import pytest

from chaoscypher_cortex.features.queue.api import reconcile_queue_endpoint
from chaoscypher_cortex.features.queue.models import (
    ReconcileRequest,
    ReconcileResponse,
)


@pytest.mark.asyncio
async def test_reconcile_endpoint_calls_service_with_queue_name() -> None:
    """Passing `queue='llm'` forwards to force_reconcile(queue_name='llm')."""
    service = AsyncMock()
    service.force_reconcile = AsyncMock(
        return_value={
            "recovered_orphans": 1,
            "recovered_crashed": 0,
            "failed_unrecoverable": 0,
        }
    )

    result = await reconcile_queue_endpoint(
        _="test-user",  # type: ignore[arg-type]
        request=ReconcileRequest(queue="llm"),
        queue_service=service,
    )

    assert isinstance(result, ReconcileResponse)
    assert result.recovered_orphans == 1
    assert result.recovered_crashed == 0
    assert result.failed_unrecoverable == 0
    service.force_reconcile.assert_awaited_once_with(queue_name="llm")


@pytest.mark.asyncio
async def test_reconcile_endpoint_without_queue_reconciles_all() -> None:
    """Omitting `queue` forwards force_reconcile(queue_name=None)."""
    service = AsyncMock()
    service.force_reconcile = AsyncMock(
        return_value={
            "recovered_orphans": 2,
            "recovered_crashed": 1,
            "failed_unrecoverable": 0,
        }
    )

    result = await reconcile_queue_endpoint(
        _="test-user",  # type: ignore[arg-type]
        request=ReconcileRequest(queue=None),
        queue_service=service,
    )

    assert result.recovered_orphans == 2
    assert result.recovered_crashed == 1
    service.force_reconcile.assert_awaited_once_with(queue_name=None)


@pytest.mark.asyncio
async def test_reconcile_endpoint_returns_all_zero_when_service_reports_nothing() -> None:
    """When the service returns zero counters, the response reflects that."""
    service = AsyncMock()
    service.force_reconcile = AsyncMock(
        return_value={
            "recovered_orphans": 0,
            "recovered_crashed": 0,
            "failed_unrecoverable": 0,
        }
    )

    result = await reconcile_queue_endpoint(
        _="test-user",  # type: ignore[arg-type]
        request=ReconcileRequest(),
        queue_service=service,
    )

    assert result.recovered_orphans == 0
    assert result.recovered_crashed == 0
    assert result.failed_unrecoverable == 0
