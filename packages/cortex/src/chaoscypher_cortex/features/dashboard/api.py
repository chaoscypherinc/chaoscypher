# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Dashboard API endpoint — thin wrapper over DashboardService."""

from typing import Annotated

from fastapi import APIRouter, Depends

from chaoscypher_core.services.graph.engine.stats import CountsService
from chaoscypher_core.services.sources.recovery import SourceRecovery
from chaoscypher_core.services.workflows.management import (
    WorkflowService as EngineWorkflowService,
)
from chaoscypher_cortex.features.counts.api import get_counts_service
from chaoscypher_cortex.features.dashboard.models import DashboardResponse
from chaoscypher_cortex.features.dashboard.service import DashboardService
from chaoscypher_cortex.features.llm.api import get_llm_service
from chaoscypher_cortex.features.llm.service import LLMService
from chaoscypher_cortex.features.pause.api import get_pause_service
from chaoscypher_cortex.features.pause.service import PauseService
from chaoscypher_cortex.features.queue.api import get_queue_service
from chaoscypher_cortex.features.queue.service import QueueService
from chaoscypher_cortex.features.workflows.api import get_engine_workflow_service
from chaoscypher_cortex.shared.api.responses import (
    COMMON_ERROR_RESPONSES,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def get_source_recovery_for_dashboard() -> tuple[SourceRecovery, str]:
    """Build a minimal ``SourceRecovery`` and resolve ``current_database`` once.

    Returns both the ``SourceRecovery`` instance and the resolved
    ``database_name`` so ``get_dashboard_service`` can use the *same*
    settings snapshot for both — preventing the adapter and the
    ``DashboardService.database_name`` from diverging under a future
    settings reload. Queue client and recovery settings are supplied but
    only the adapter is exercised by the count call.
    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.queue import queue_client

    settings = get_settings()
    database_name = settings.current_database
    adapter = get_sqlite_adapter(database_name=database_name)
    recovery = SourceRecovery(
        adapter=adapter,
        queue_client=queue_client,
        stalled_threshold_seconds=settings.source_recovery.stalled_threshold_seconds,
        max_recovery_attempts=settings.source_recovery.max_recovery_attempts,
        recovery_warn_threshold=settings.source_recovery.recovery_warn_threshold,
    )
    return recovery, database_name


def get_dashboard_service(
    counts_service: Annotated[CountsService, Depends(get_counts_service)],
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    pause_service: Annotated[PauseService, Depends(get_pause_service)],
    recovery_and_db: Annotated[
        tuple[SourceRecovery, str], Depends(get_source_recovery_for_dashboard)
    ],
) -> DashboardService:
    """Assemble a ``DashboardService`` from the six sibling factories.

    The sibling-factory imports above are the only place in Cortex where
    a slice imports from other slices' ``api.py`` modules — documented
    in ADR-0002 as the one allowed exception for aggregator slices.

    ``database_name`` comes from the same ``get_settings()`` call that
    built the adapter inside ``get_source_recovery_for_dashboard`` — a
    single resolved value threads through so the adapter and service
    cannot diverge under a future hot-reload.
    """
    source_recovery, database_name = recovery_and_db
    return DashboardService(
        counts_service=counts_service,
        llm_service=llm_service,
        queue_service=queue_service,
        workflow_service=workflow_service,
        pause_service=pause_service,
        source_recovery=source_recovery,
        database_name=database_name,
    )


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_dashboard(
    _: CurrentUsername,
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> DashboardResponse:
    """Aggregated live-status snapshot for the UI dashboard polling loop."""
    return await service.get_dashboard()
