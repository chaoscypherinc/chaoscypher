# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DashboardService — aggregation of six sibling summaries."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS
from chaoscypher_cortex.features.dashboard.models import DashboardResponse
from chaoscypher_cortex.features.dashboard.service import DashboardService
from chaoscypher_cortex.shared.models.summaries import (
    LLMStatsResponse,
    QueueStatsResponse,
)


# ---------------------------------------------------------------------------
# Realistic fixture values — shapes lifted from features/dashboard/api.py
# (the existing inline asyncio.gather) and sibling service return types.
# ---------------------------------------------------------------------------

_COUNTS_DICT: dict[str, int] = {
    "knowledge_nodes": 42,
    "links": 18,
    "templates": 7,
    "workflows": 3,
    "lenses": 2,
    "sources": 5,
}

_AWAITING_COUNT: int = 3

_LLM_STATS = LLMStatsResponse(
    data={
        "queue_depth": 2,
        "semaphore_available": 1,
        "total_cost_usd": 0.0123,
    }
)

_QUEUE_STATS = QueueStatsResponse(
    queues=[
        {"queue": "llm", "queued": 1, "running": 0},
        {"queue": "operations", "queued": 3, "running": 2},
    ],
    note="Queue configuration managed in worker/config.py",
)

_WORKFLOW_STATS_DICT: dict[str, object] = {
    "total_workflows": 5,
    "active_workflows": 3,
    "inactive_workflows": 2,
    "total_executions": 120,
    "successful_executions": 110,
    "failed_executions": 8,
    "cancelled_executions": 2,
    "success_rate": 91.67,
}

_PAUSE_STATUS_DICT: dict[str, object] = {
    "paused": False,
    "paused_at": None,
    "reason": None,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_service(
    *,
    counts_dict: dict[str, int] = _COUNTS_DICT,
    awaiting_count: int = _AWAITING_COUNT,
    llm_stats: LLMStatsResponse = _LLM_STATS,
    queue_stats: QueueStatsResponse = _QUEUE_STATS,
    workflow_dict: dict[str, object] = _WORKFLOW_STATS_DICT,
    pause_dict: dict[str, object] = _PAUSE_STATUS_DICT,
    database_name: str = "test_db",
) -> DashboardService:
    """Build a DashboardService with all six sibling mocks."""
    counts_service = MagicMock()
    counts_service.get_counts.return_value = counts_dict

    llm_service = MagicMock()
    llm_service.get_stats = AsyncMock(return_value=llm_stats)

    queue_service = MagicMock()
    queue_service.get_all_stats = AsyncMock(return_value=queue_stats)

    workflow_service = MagicMock()
    workflow_service.get_global_stats.return_value = workflow_dict

    pause_service = MagicMock()
    pause_service.get_system_status = AsyncMock(return_value=pause_dict)

    source_recovery = MagicMock()
    source_recovery.count_awaiting_confirmation.return_value = awaiting_count

    return DashboardService(
        counts_service=counts_service,
        llm_service=llm_service,
        queue_service=queue_service,
        workflow_service=workflow_service,
        pause_service=pause_service,
        source_recovery=source_recovery,
        database_name=database_name,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_dashboard_aggregates_six_siblings() -> None:
    """Happy path: all six sibling services return, DashboardResponse is built."""
    service = _make_service()
    response = await service.get_dashboard()

    assert isinstance(response, DashboardResponse)

    # Each sibling was invoked exactly once.
    service._counts.get_counts.assert_called_once_with(system_template_ids=SYSTEM_TEMPLATE_IDS)
    service._llm.get_stats.assert_awaited_once()
    service._queue.get_all_stats.assert_awaited_once()
    service._workflow.get_global_stats.assert_called_once_with()
    service._pause.get_system_status.assert_awaited_once()
    service._source_recovery.count_awaiting_confirmation.assert_called_once_with("test_db")

    # Payload is composed from the six siblings.
    assert response.counts.knowledge_nodes == 42
    assert response.counts.sources == 5
    assert response.counts.awaiting_confirmation == _AWAITING_COUNT
    assert response.llm is _LLM_STATS
    assert response.queue is _QUEUE_STATS
    assert response.workflows.total_workflows == 5
    assert response.workflows.success_rate == 91.67
    assert response.processing.paused is False
    assert response.processing.reason is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_awaiting_confirmation_zero_when_none_parked() -> None:
    """awaiting_confirmation is 0 when no sources are parked."""
    service = _make_service(awaiting_count=0)
    response = await service.get_dashboard()
    assert response.counts.awaiting_confirmation == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_sibling_failure_propagates() -> None:
    """If a sibling raises, get_dashboard propagates the exception."""
    counts_service = MagicMock()
    counts_service.get_counts.side_effect = RuntimeError("counts broke")

    llm_service = MagicMock()
    llm_service.get_stats = AsyncMock(return_value=_LLM_STATS)
    queue_service = MagicMock()
    queue_service.get_all_stats = AsyncMock(return_value=_QUEUE_STATS)
    workflow_service = MagicMock()
    workflow_service.get_global_stats = MagicMock(return_value=_WORKFLOW_STATS_DICT)
    pause_service = MagicMock()
    pause_service.get_system_status = AsyncMock(return_value=_PAUSE_STATUS_DICT)
    source_recovery = MagicMock()
    source_recovery.count_awaiting_confirmation.return_value = 0

    service = DashboardService(
        counts_service=counts_service,
        llm_service=llm_service,
        queue_service=queue_service,
        workflow_service=workflow_service,
        pause_service=pause_service,
        source_recovery=source_recovery,
    )

    with pytest.raises(RuntimeError, match="counts broke"):
        await service.get_dashboard()
