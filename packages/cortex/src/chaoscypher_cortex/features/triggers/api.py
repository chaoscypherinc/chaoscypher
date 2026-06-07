# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Triggers API Endpoints.

GET    /api/v1/triggers - List triggers
POST   /api/v1/triggers - Create trigger
GET    /api/v1/triggers/{id} - Get trigger
GET    /api/v1/triggers/{id}/stats - Aggregate execution statistics
PATCH  /api/v1/triggers/{id} - Update trigger
DELETE /api/v1/triggers/{id} - Delete trigger
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.factories import get_trigger_service as _make_trigger_service
from chaoscypher_cortex.features.triggers.models import (
    PaginatedTriggersResponse,
    TriggerCreate,
    TriggerResponse,
    TriggerSummaryResponse,
    TriggerUpdate,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
    paginate_list,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import TriggerDict
    from chaoscypher_core.services.workflows.triggers import TriggerService

# Create router
router = APIRouter()


# Dependency to get trigger service
def get_trigger_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TriggerService:
    """Get TriggerService instance using shared factory."""
    return _make_trigger_service(settings.current_database)


async def require_trigger_ownership(
    trigger_id: str,
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    _: CurrentUsername,
) -> TriggerDict:
    """Fetch the trigger, raising 404 if it does not exist.

    Single-user mode: the one local operator owns everything, so no
    per-user ACL check is needed beyond requiring authentication
    (enforced by the CurrentUsername dependency).

    Args:
        trigger_id: ID of the trigger being addressed.
        trigger_service: Injected TriggerService for lookup.
        _: CurrentUsername — auth-gate only, value unused.

    Returns:
        The trigger dict.

    Raises:
        HTTPException: 404 when the trigger does not exist.
    """
    trigger = trigger_service.get_trigger(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trigger not found")
    return trigger


# ============================================================================
# Trigger CRUD Endpoints
# ============================================================================


@router.get(
    "",
    response_model=PaginatedTriggersResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_triggers(
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    pagination: PageParams,
    _: CurrentUsername,
    event_source: str | None = Query(None, description="Filter by event source"),
    enabled: bool | None = Query(None, description="Filter by enabled flag"),
) -> PaginatedTriggersResponse:
    """List triggers with optional filters and pagination.

    Returns summary data (excludes filters/workflow_inputs).
    Use GET /triggers/{id} for full details.

    Single-user mode: the local operator owns everything; all triggers
    are visible without per-user scoping.
    """
    page, page_size = pagination
    all_triggers = trigger_service.list_triggers(
        event_source=event_source,
        enabled=enabled,
    )
    result = paginate_list(all_triggers, page, page_size)
    return PaginatedTriggersResponse(
        data=[TriggerSummaryResponse(**t) for t in result["data"]],
        pagination=result["pagination"],
    )


@router.post(
    "",
    response_model=TriggerResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def create_trigger(
    trigger_create: TriggerCreate,
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    _: CurrentUsername,
) -> TriggerDict | None:
    """Create a new trigger.

    - Single-user mode: the local operator owns everything.
    """
    # Convert Pydantic model to dict for engine service
    trigger_data = trigger_create.model_dump(exclude_unset=True)
    trigger_id = trigger_service.create_trigger(trigger_data)
    # Return the created trigger
    return trigger_service.get_trigger(trigger_id)


@router.get(
    "/{trigger_id}",
    response_model=TriggerResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_trigger(
    trigger_id: str,
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    _: CurrentUsername,
) -> TriggerDict:
    """Get trigger by ID.

    - Single-user mode: the local operator owns everything.

    Raises:
        HTTPException: 404 if the trigger does not exist.
    """
    from chaoscypher_cortex.shared.api.errors import raise_if_not_found

    return raise_if_not_found(trigger_service.get_trigger(trigger_id), "Trigger not found")


class TriggerStatsResponse(BaseModel):
    """Aggregate execution statistics for a single trigger."""

    total_executions: int
    successful_executions: int
    failed_executions: int
    success_rate: float
    average_duration_ms: int


@router.get(
    "/{trigger_id}/stats",
    response_model=TriggerStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_trigger_stats(
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    trigger: Annotated[TriggerDict, Depends(require_trigger_ownership)],
) -> TriggerStatsResponse:
    """Return aggregate execution statistics for a trigger.

    Computed from the persisted ``trigger_executions`` history. Per-execution
    duration is not yet persisted, so ``average_duration_ms`` is currently 0.
    """
    stats = trigger_service.get_trigger_stats(trigger["id"])
    return TriggerStatsResponse(**stats)


@router.patch(
    "/{trigger_id}",
    response_model=TriggerResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_trigger(
    trigger_id: str,
    trigger_update: TriggerUpdate,
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    _: CurrentUsername,
    trigger: Annotated[TriggerDict, Depends(require_trigger_ownership)],
) -> TriggerDict | None:
    """Update an existing trigger.

    - Single-user mode: the local operator owns everything.
    """
    # Trigger existence + ownership already verified by the dependency.
    del trigger  # dependency result not used directly; presence enforces ACL
    updates = trigger_update.model_dump(exclude_unset=True)
    trigger_service.update_trigger(trigger_id, updates)
    return trigger_service.get_trigger(trigger_id)


@router.delete(
    "/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_trigger(
    trigger_id: str,
    trigger_service: Annotated[TriggerService, Depends(get_trigger_service)],
    _: CurrentUsername,
    trigger: Annotated[TriggerDict, Depends(require_trigger_ownership)],
) -> None:
    """Delete a trigger.

    - Single-user mode: the local operator owns everything.
    """
    # Ownership verified by dependency.
    del trigger  # dependency result not used directly; presence enforces ACL
    trigger_service.delete_trigger(trigger_id)
