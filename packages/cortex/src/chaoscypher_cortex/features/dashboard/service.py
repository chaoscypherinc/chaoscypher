# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DashboardService — aggregates summary data from sibling slices.

By design, this is the one Cortex slice that imports sibling services —
see ADR-0002. Sibling DTOs are imported from
``cortex.shared.models.summaries``, not from sibling slices.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS
from chaoscypher_cortex.features.dashboard.models import DashboardResponse
from chaoscypher_cortex.shared.models.summaries import (
    CountsResponse,
    GlobalWorkflowStatsResponse,
    SystemPauseStatusResponse,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.graph.engine.stats import CountsService
    from chaoscypher_core.services.sources.recovery import SourceRecovery
    from chaoscypher_core.services.workflows.management import (
        WorkflowService as EngineWorkflowService,
    )
    from chaoscypher_cortex.features.llm.service import LLMService
    from chaoscypher_cortex.features.pause.service import PauseService
    from chaoscypher_cortex.features.queue.service import QueueService


class DashboardService:
    """Aggregates dashboard data from six sibling services.

    Owns the ``asyncio.gather`` fan-out previously inlined in the
    dashboard API handler so the slice has a single responsibility:
    compose a ``DashboardResponse`` from the siblings' summary calls.
    Sync sibling methods are bridged onto the event loop via
    ``asyncio.to_thread`` to preserve the parallel-wall-time guarantee.
    """

    def __init__(
        self,
        counts_service: CountsService,
        llm_service: LLMService,
        queue_service: QueueService,
        workflow_service: EngineWorkflowService,
        pause_service: PauseService,
        source_recovery: SourceRecovery,
        database_name: str = "",
    ) -> None:
        """Initialize DashboardService with the six sibling services.

        Args:
            counts_service: Engine CountsService for resource counts.
            llm_service: Cortex LLMService for LLM queue stats.
            queue_service: Cortex QueueService for all-queue stats.
            workflow_service: Engine WorkflowService for global stats.
            pause_service: Cortex PauseService for system pause status.
            source_recovery: SourceRecovery for awaiting-confirmation count.
            database_name: Active database to query awaiting sources against.

        """
        self._counts = counts_service
        self._llm = llm_service
        self._queue = queue_service
        self._workflow = workflow_service
        self._pause = pause_service
        self._source_recovery = source_recovery
        self._database_name = database_name

    async def get_dashboard(self) -> DashboardResponse:
        """Fetch and aggregate the six sibling summaries in parallel.

        Returns:
            ``DashboardResponse`` composed of the six sibling payloads.

        Raises:
            Exception: Propagates any exception raised by a sibling —
                ``asyncio.gather`` surfaces the first failure so the
                caller sees the underlying error rather than a partial
                response.

        """
        (
            counts_dict,
            llm_stats,
            queue_stats,
            workflow_stats,
            processing_status,
            awaiting_count,
        ) = await asyncio.gather(
            asyncio.to_thread(self._counts.get_counts, system_template_ids=SYSTEM_TEMPLATE_IDS),
            self._llm.get_stats(),
            self._queue.get_all_stats(),
            asyncio.to_thread(self._workflow.get_global_stats),
            self._pause.get_system_status(),
            asyncio.to_thread(
                self._source_recovery.count_awaiting_confirmation,
                self._database_name,
            ),
        )

        return DashboardResponse(
            counts=CountsResponse(**counts_dict, awaiting_confirmation=awaiting_count),
            llm=llm_stats,
            queue=queue_stats,
            workflows=GlobalWorkflowStatsResponse(**workflow_stats),
            processing=SystemPauseStatusResponse(**processing_status),
        )
