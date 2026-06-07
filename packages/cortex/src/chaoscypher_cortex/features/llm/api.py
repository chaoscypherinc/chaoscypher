# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM API Endpoints.

GET    /api/v1/llm/stats - Get LLM queue stats
DELETE /api/v1/llm/stats - Clear stats
GET    /api/v1/llm/tasks - List current LLM tasks
GET    /api/v1/llm/tasks/{task_id} - Get task status
DELETE /api/v1/llm/tasks/{task_id} - Cancel task
DELETE /api/v1/llm/tasks - Cancel all tasks (bulk)
GET    /api/v1/llm/health - Health check
DELETE /api/v1/llm/semaphore - Clear semaphore.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.llm_queue import get_llm_queue_service
from chaoscypher_cortex.features.llm.models import (
    CancelAllTasksResponse,
    ClearSemaphoreResponse,
    LLMStatsResponse,
    LLMTasksResponse,
    LLMTaskStatusResponse,
)
from chaoscypher_cortex.features.llm.service import LLMService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def get_llm_service() -> LLMService:
    """Get LLMService instance (VSA pattern).

    Uses singleton factories to get cached LLM queue service, avoiding
    expensive recreation of HTTP clients and connection pools.
    """
    # Get singleton LLM queue service (cached)
    llm_manager = get_llm_queue_service()

    return LLMService(llm_manager)


@router.get(
    "/stats",
    response_model=LLMStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_llm_queue_stats(
    _: CurrentUsername,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> LLMStatsResponse:
    """Get LLM queue stats.

    **Returns:**
    - Queued tasks count
    - Running tasks count
    - Completed tasks count
    - Failed tasks count
    - Average processing time
    - Other metrics

    **Errors:**
    - 503: LLM queue service unavailable
    """
    return await llm_service.get_stats()


@router.delete(
    "/stats",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def delete_llm_queue_stats(
    _: CurrentUsername,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    older_than_hours: int | None = Query(
        default=None, ge=0, le=8760, description="Clear tasks older than this many hours"
    ),
) -> Response:
    """Clear all LLM queue stats and old completed tasks.

    **RESTful Design:**
    - DELETE /stats removes the stats resource
    - Standard REST operation for clearing data

    **Query Parameters:**
    - `older_than_hours`: Clear tasks older than N hours (max: 8760)

    **Side Effects:**
    - Clears LLM queue stats
    - Clears old completed tasks
    - Clears workflow stats (if available)

    **Returns:**
    - 204 No Content on success

    **Errors:**
    - 503: LLM queue service unavailable
    """
    older_than_hours = (
        older_than_hours if older_than_hours is not None else settings.queue.stats_retention_hours
    )
    await llm_service.clear_stats(older_than_hours=older_than_hours)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/tasks",
    response_model=LLMTasksResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def list_current_tasks(
    _: CurrentUsername,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> LLMTasksResponse:
    """List currently queued and running LLM tasks.

    **Returns:**
    - List of active tasks with details

    **Errors:**
    - 503: LLM queue service unavailable
    """
    return await llm_service.list_current_tasks()


@router.get(
    "/tasks/{task_id}",
    response_model=LLMTaskStatusResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_task_status(
    _: CurrentUsername,
    task_id: str,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> LLMTaskStatusResponse:
    """Get status of a specific queued LLM task.

    **Returns:**
    - Task status details

    **Errors:**
    - 404: Task not found
    - 503: LLM queue service unavailable
    """
    return await llm_service.get_task_status(task_id)


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def cancel_task(
    _: CurrentUsername,
    task_id: str,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> Response:
    """Cancel a queued or running LLM task.

    **Returns:**
    - 204 No Content on success

    **Errors:**
    - 400: Task could not be cancelled (not found or already completed)
    - 503: LLM queue service unavailable
    """
    await llm_service.cancel_task(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/tasks",
    response_model=CancelAllTasksResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def delete_all_tasks(
    _: CurrentUsername,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> CancelAllTasksResponse:
    """Cancel all queued and running LLM tasks (bulk delete).

    **RESTful Design:**
    - DELETE /tasks is standard bulk delete operation

    **Returns:**
    - Cancellation result with count

    **Errors:**
    - 503: LLM queue service unavailable
    """
    return await llm_service.cancel_all_tasks()


@router.delete(
    "/semaphore",
    response_model=ClearSemaphoreResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def delete_semaphore(
    _: CurrentUsername,
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
) -> ClearSemaphoreResponse:
    """Clear all waiting tasks from the LLM semaphore queues.

    **RESTful Design:**
    - DELETE /semaphore removes the semaphore resource state

    **Use Case:**
    - When Valkey queues are cleared but semaphore still has orphaned waiters
    - Call this after clearing Valkey queues to fully reset system state

    **Returns:**
    - Counts of cleared high-priority and low-priority tasks

    **Errors:**
    - 503: LLM queue service unavailable
    """
    return await llm_service.clear_semaphore()
