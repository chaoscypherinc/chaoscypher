# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for staleness-based snapshot auto-refresh in the GET endpoint.

Calls endpoint functions directly with mocked deps — same pattern as
``test_api.py``.  No TestClient needed; no Valkey needed.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

from chaoscypher_core.ports.storage_graph_snapshot import SnapshotStalenessInfo
from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
)
from chaoscypher_cortex.features.graph_snapshot.api import get_snapshot
from chaoscypher_cortex.features.graph_snapshot.service import GraphSnapshotFeatureService


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

_NOW = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)


def _make_breakdown() -> GraphBreakdown:
    """Return a minimal valid GraphBreakdown fixture."""
    return GraphBreakdown(
        database_name="default",
        generated_at=_NOW,
        stats=GraphStats(total_nodes=100, total_edges=50, total_sources=2),
        sources=[],
    )


def _make_staleness(*, generated_at: datetime, node_count: int = 100) -> SnapshotStalenessInfo:
    """Return a SnapshotStalenessInfo fixture."""
    return SnapshotStalenessInfo(
        generated_at=generated_at,
        node_count=node_count,
        edge_count=50,
    )


# ---------------------------------------------------------------------------
# Test 1: fresh snapshot — no enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_snapshot_does_not_enqueue_refresh() -> None:
    """GET snapshot with fresh data does NOT enqueue a rebuild.

    Staleness info: generated_at=now, node_count=100.
    Live count: 100.
    Expected: enqueue_task NOT called; response is the breakdown.
    """
    breakdown = _make_breakdown()
    staleness = _make_staleness(generated_at=_NOW, node_count=100)

    svc = MagicMock(spec=GraphSnapshotFeatureService)
    svc.get_staleness_info.return_value = staleness
    svc.get_live_node_count.return_value = 100
    svc.get_current.return_value = breakdown

    with (
        patch(
            "chaoscypher_cortex.features.graph_snapshot.api.queue_client.enqueue_task",
            new_callable=AsyncMock,
        ) as mock_enqueue,
        patch(
            "chaoscypher_cortex.features.graph_snapshot.api.datetime",
        ) as mock_dt,
    ):
        mock_dt.now.return_value = _NOW

        result = await get_snapshot(
            _=_FAKE_USER,
            svc=svc,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, GraphBreakdown)
    assert result.database_name == "default"
    mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: old snapshot — enqueue and still return stale data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_snapshot_enqueues_refresh_and_returns_stale_data() -> None:
    """GET snapshot with age > 1 hour enqueues a rebuild but returns stale data.

    Staleness info: generated_at=2 hours ago, node_count=100.
    Live count: 100 (no drift — age alone triggers stale).
    Expected: enqueue_task called exactly once with trigger="stale";
              response is the stale breakdown (not 204).
    """
    two_hours_ago = _NOW - timedelta(hours=2)
    breakdown = _make_breakdown()
    staleness = _make_staleness(generated_at=two_hours_ago, node_count=100)

    svc = MagicMock(spec=GraphSnapshotFeatureService)
    svc.get_staleness_info.return_value = staleness
    svc.get_live_node_count.return_value = 100
    svc.get_current.return_value = breakdown

    with (
        patch(
            "chaoscypher_cortex.features.graph_snapshot.api.queue_client.enqueue_task",
            new_callable=AsyncMock,
            return_value="task-xyz",
        ) as mock_enqueue,
        patch(
            "chaoscypher_cortex.features.graph_snapshot.api.datetime",
        ) as mock_dt,
    ):
        mock_dt.now.return_value = _NOW

        result = await get_snapshot(
            _=_FAKE_USER,
            svc=svc,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, GraphBreakdown)
    assert result.database_name == "default"
    mock_enqueue.assert_awaited_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["metadata"]["trigger"] == "stale"


# ---------------------------------------------------------------------------
# Test 3: count drift — enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_drift_enqueues_refresh() -> None:
    """GET snapshot with 20% node drift enqueues a rebuild.

    Staleness info: generated_at=now, node_count=100.
    Live count: 120 (20% drift > 10% threshold).
    Expected: enqueue_task called with trigger="stale".
    """
    breakdown = _make_breakdown()
    staleness = _make_staleness(generated_at=_NOW, node_count=100)

    svc = MagicMock(spec=GraphSnapshotFeatureService)
    svc.get_staleness_info.return_value = staleness
    svc.get_live_node_count.return_value = 120
    svc.get_current.return_value = breakdown

    with (
        patch(
            "chaoscypher_cortex.features.graph_snapshot.api.queue_client.enqueue_task",
            new_callable=AsyncMock,
            return_value="task-abc",
        ) as mock_enqueue,
        patch(
            "chaoscypher_cortex.features.graph_snapshot.api.datetime",
        ) as mock_dt,
    ):
        mock_dt.now.return_value = _NOW

        result = await get_snapshot(
            _=_FAKE_USER,
            svc=svc,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, GraphBreakdown)
    mock_enqueue.assert_awaited_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["metadata"]["trigger"] == "stale"


# ---------------------------------------------------------------------------
# Test 4: no snapshot — enqueue initial build and return 204
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_snapshot_enqueues_initial_build() -> None:
    """GET when no snapshot exists enqueues initial build and returns 204.

    Staleness info: None (no snapshot row).
    Expected: enqueue_task called with trigger="no_snapshot"; response is 204.
    """
    svc = MagicMock(spec=GraphSnapshotFeatureService)
    svc.get_staleness_info.return_value = None

    with patch(
        "chaoscypher_cortex.features.graph_snapshot.api.queue_client.enqueue_task",
        new_callable=AsyncMock,
        return_value="task-new",
    ) as mock_enqueue:
        result = await get_snapshot(
            _=_FAKE_USER,
            svc=svc,
            settings=_FAKE_SETTINGS,
        )

    assert isinstance(result, Response)
    assert result.status_code == 204
    mock_enqueue.assert_awaited_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["metadata"]["trigger"] == "no_snapshot"
