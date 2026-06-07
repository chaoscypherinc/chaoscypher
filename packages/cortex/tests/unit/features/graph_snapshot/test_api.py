# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for graph_snapshot API endpoints.

Calls endpoint functions directly with mocked deps — same pattern as
``tests/unit/features/pause/test_pause_api.py``.  No TestClient needed;
no Valkey needed.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
)
from chaoscypher_cortex.features.graph_snapshot.api import (
    get_snapshot,
    refresh_snapshot,
)
from chaoscypher_cortex.features.graph_snapshot.service import GraphSnapshotFeatureService
from chaoscypher_cortex.shared.kernel import BulkResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_USER = "testuser"

_FAKE_SETTINGS = MagicMock()
_FAKE_SETTINGS.current_database = "default"
_FAKE_SETTINGS.priorities.background = 5
# Real numeric values so timedelta(seconds=...) and `>` comparisons work
# against MagicMock attribute access. Mirrors GraphSnapshotSettings defaults.
_FAKE_SETTINGS.graph_snapshot.staleness_threshold_seconds = 3600
_FAKE_SETTINGS.graph_snapshot.count_drift_threshold = 0.10


def _make_breakdown() -> GraphBreakdown:
    """Return a minimal valid GraphBreakdown fixture."""
    return GraphBreakdown(
        database_name="default",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        stats=GraphStats(total_nodes=10, total_edges=5, total_sources=2),
        sources=[],
    )


# ---------------------------------------------------------------------------
# Test 1: GET returns 204 when no snapshot exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_204_when_no_snapshot() -> None:
    """GET /api/v1/graph/snapshot returns 204 when no snapshot has been built."""
    svc = MagicMock(spec=GraphSnapshotFeatureService)
    svc.get_staleness_info.return_value = None

    with patch(
        "chaoscypher_cortex.features.graph_snapshot.api.queue_client.enqueue_task",
        new_callable=AsyncMock,
        return_value="task-init",
    ):
        result = await get_snapshot(
            _=_FAKE_USER,
            svc=svc,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, Response)
    assert result.status_code == 204
    svc.get_staleness_info.assert_called_once_with("default")


# ---------------------------------------------------------------------------
# Test 2: GET returns 200 with GraphBreakdown when snapshot exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_snapshot_when_available() -> None:
    """GET /api/v1/graph/snapshot returns 200 with GraphBreakdown body."""
    breakdown = _make_breakdown()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    staleness_info = MagicMock()
    staleness_info.generated_at = now  # fresh — no staleness
    staleness_info.node_count = 10

    svc = MagicMock(spec=GraphSnapshotFeatureService)
    svc.get_staleness_info.return_value = staleness_info
    svc.get_live_node_count.return_value = 10
    svc.get_current.return_value = breakdown

    with patch(
        "chaoscypher_cortex.features.graph_snapshot.api.datetime",
    ) as mock_dt:
        mock_dt.now.return_value = now

        result = await get_snapshot(
            _=_FAKE_USER,
            svc=svc,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, GraphBreakdown)
    assert result.database_name == "default"
    assert result.stats.total_nodes == 10
    svc.get_current.assert_called_once_with("default")


# ---------------------------------------------------------------------------
# Test 3: POST /refresh returns 202 with task_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_202_with_task_id() -> None:
    """POST /api/v1/graph/snapshot/refresh returns 202 with a task_id."""
    fake_task_id = "task-abc-123"

    with patch(
        "chaoscypher_cortex.features.graph_snapshot.api.queue_client.enqueue_task",
        new_callable=AsyncMock,
        return_value=fake_task_id,
    ) as mock_enqueue:
        result = await refresh_snapshot(
            _=_FAKE_USER,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, BulkResponse)
    assert result.task_id == fake_task_id
    assert result.status == "queued"
    assert "queued" in result.message.lower()

    mock_enqueue.assert_awaited_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["operation"] == "build_graph_snapshot"
    assert call_kwargs["data"] == {"database_name": "default"}
