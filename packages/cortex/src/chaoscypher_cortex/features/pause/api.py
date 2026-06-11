# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pause / Resume API Endpoints.

Endpoints are split across two routers so they can be mounted at
different prefixes (``/sources`` and ``/system/processing``).

Per-source (mounted at /sources):
    POST   /{source_id}/pause
    POST   /{source_id}/resume
    POST   /pause           (bulk — body: source_ids)
    POST   /resume          (bulk — body: source_ids)

System-wide (mounted at /system/processing):
    POST   /pause
    POST   /resume
    GET    /status
    GET    /events
    DELETE /events
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from chaoscypher_core.app_config import get_settings
from chaoscypher_cortex.features.pause.models import (
    BulkPauseActionResponse,
    BulkPauseRequest,
    BulkResumeRequest,
    PauseSourceRequest,
    PauseSystemRequest,
    SourcePauseActionResponse,
    SystemEventResponse,
    SystemEventsClearResponse,
    SystemPauseActionResponse,
    SystemPauseStatusResponse,
)
from chaoscypher_cortex.features.pause.repository import PauseRepository
from chaoscypher_cortex.features.pause.service import PauseService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


sources_router = APIRouter()
system_router = APIRouter()


def get_pause_service() -> PauseService:
    """Build a PauseService for the current request.

    Uses the shared SqliteAdapter (singleton keyed by database name)
    and constructs a SourceRecovery on demand with the live
    queue client. Called per-request via FastAPI's Depends.
    """
    from chaoscypher_core.database.adapter_factory import (
        get_sqlite_adapter,
    )
    from chaoscypher_core.queue import queue_client
    from chaoscypher_core.services.sources.recovery import SourceRecovery

    settings = get_settings()
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    repository = PauseRepository(adapter=adapter)
    source_recovery = SourceRecovery(
        adapter=adapter,
        queue_client=queue_client,
        stalled_threshold_seconds=settings.source_recovery.stalled_threshold_seconds,
        max_recovery_attempts=settings.source_recovery.max_recovery_attempts,
        recovery_warn_threshold=settings.source_recovery.recovery_warn_threshold,
    )
    return PauseService(repository=repository, source_recovery=source_recovery)


# ---------------------------------------------------------------------------
# Per-source endpoints (mounted at /sources)
# ---------------------------------------------------------------------------
#
# The literal paths (/pause, /resume) are declared BEFORE the
# parameterized paths so FastAPI matches them first when both are
# registered on the same router. (FastAPI matches routes in
# declaration order within a router.)
# ---------------------------------------------------------------------------


@sources_router.post(
    "/pause",
    response_model=BulkPauseActionResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def bulk_pause_endpoint(
    _: CurrentUsername,
    request: BulkPauseRequest,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> BulkPauseActionResponse:
    """Pause multiple sources at once."""
    count = await service.pause_sources(
        source_ids=request.source_ids,
        database_name=get_settings().current_database,
        reason=request.reason,
    )
    return BulkPauseActionResponse(count=count)


@sources_router.post(
    "/resume",
    response_model=BulkPauseActionResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def bulk_resume_endpoint(
    _: CurrentUsername,
    request: BulkResumeRequest,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> BulkPauseActionResponse:
    """Resume multiple sources at once."""
    count = await service.resume_sources(
        source_ids=request.source_ids,
        database_name=get_settings().current_database,
    )
    return BulkPauseActionResponse(count=count)


@sources_router.post(
    "/{source_id}/pause",
    response_model=SourcePauseActionResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def pause_source_endpoint(
    source_id: str,
    _: CurrentUsername,
    request: PauseSourceRequest,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> SourcePauseActionResponse:
    """Pause a single source."""
    await service.pause_source(
        source_id=source_id,
        database_name=get_settings().current_database,
        reason=request.reason,
    )
    return SourcePauseActionResponse(source_id=source_id, paused=True)


@sources_router.post(
    "/{source_id}/resume",
    response_model=SourcePauseActionResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def resume_source_endpoint(
    source_id: str,
    _: CurrentUsername,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> SourcePauseActionResponse:
    """Resume a single source; immediately triggers recovery."""
    await service.resume_source(
        source_id=source_id,
        database_name=get_settings().current_database,
    )
    return SourcePauseActionResponse(source_id=source_id, paused=False)


# ---------------------------------------------------------------------------
# System-wide endpoints (mounted at /system/processing)
# ---------------------------------------------------------------------------


@system_router.post(
    "/pause",
    response_model=SystemPauseActionResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def system_pause_endpoint(
    _: CurrentUsername,
    request: PauseSystemRequest,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> SystemPauseActionResponse:
    """Pause all source processing system-wide."""
    await service.pause_system(reason=request.reason)
    return SystemPauseActionResponse(paused=True)


@system_router.post(
    "/resume",
    response_model=SystemPauseActionResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def system_resume_endpoint(
    _: CurrentUsername,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> SystemPauseActionResponse:
    """Resume system-wide processing."""
    await service.resume_system()
    return SystemPauseActionResponse(paused=False)


@system_router.get(
    "/status",
    response_model=SystemPauseStatusResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def system_status_endpoint(
    _: CurrentUsername,
    service: Annotated[PauseService, Depends(get_pause_service)],
) -> SystemPauseStatusResponse:
    """Get current system-wide pause state."""
    status_data = await service.get_system_status()
    return SystemPauseStatusResponse(**status_data)


@system_router.get(
    "/events",
    response_model=list[SystemEventResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_system_events(
    _: CurrentUsername,
    event_type: str | None = None,
    limit: int = 50,
) -> list[SystemEventResponse]:
    """List recent system events (audit trail).

    Filterable by type: pause, resume, health_change, task_failed, recovery.
    """
    from chaoscypher_core.database.adapter_factory import (
        get_sqlite_adapter,
    )

    settings = get_settings()
    # Clamp to the shared pagination ceiling so ?limit=999999999 can't try to
    # materialize an unbounded result set (self-inflicted DoS guard).
    capped_limit = min(max(limit, 1), settings.pagination.max_page_size)
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    events = adapter.list_system_events(event_type=event_type, limit=capped_limit)
    return [SystemEventResponse(**event) for event in events]


@system_router.delete(
    "/events",
    response_model=SystemEventsClearResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def clear_system_events(
    _: CurrentUsername,
) -> SystemEventsClearResponse:
    """Delete all system events."""
    from chaoscypher_core.database.adapter_factory import (
        get_sqlite_adapter,
    )

    settings = get_settings()
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    deleted = adapter.clear_system_events()
    return SystemEventsClearResponse(deleted=deleted)
