# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue API Endpoints.

POST   /api/v1/queue/tasks - Queue new task
GET    /api/v1/queue/tasks - List recent tasks
GET    /api/v1/queue/tasks/{task_id} - Get task details
GET    /api/v1/queue/tasks/{task_id}/result - Get task result
DELETE /api/v1/queue/tasks/{task_id} - Cancel task
POST   /api/v1/queue/tasks/{task_id}/retry - Retry failed task
DELETE /api/v1/queue/tasks - Cancel all tasks
POST   /api/v1/queue/tasks/cancel - Cancel tasks (batch or by metadata)
DELETE /api/v1/queue/tasks/history - Clear task history
GET    /api/v1/queue/stats - Get all queue stats
GET    /api/v1/queue/stats/{queue_name} - Get queue stats
GET    /api/v1/queue/health - Queue health check.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from chaoscypher_cortex.features.queue.models import (
    CancelAllResponse,
    CancelBatchResponse,
    CancelByMetadataResponse,
    CancelTaskResponse,
    CancelTasksRequest,
    ClearHistoryResponse,
    QueueHealthResponse,
    QueueStatsByName,
    QueueStatsResponse,
    QueueTaskRequest,
    QueueTaskResponse,
    ReconcileRequest,
    ReconcileResponse,
    RetryTaskResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskResultResponse,
)
from chaoscypher_cortex.features.queue.service import QueueService
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    RATE_LIMIT_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def get_queue_service() -> QueueService:
    """Get QueueService instance."""
    return QueueService()


@router.post(
    "/tasks",
    response_model=QueueTaskResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def queue_task(
    _: CurrentUsername,
    request: QueueTaskRequest,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> QueueTaskResponse:
    """Queue a new task.

    **Request Body:**
    - `queue`: Queue name (e.g., "operations", "llm")
    - `operation`: Operation name (e.g., "import_ccx", "chat_completion")
    - `data`: Operation-specific data
    - `priority`: Task priority (0-100, default: 50)
    - `metadata`: Optional metadata for filtering

    **Returns:**
    - task_id: Unique identifier for tracking

    **Errors:**
    - 503: Queue service unavailable
    """
    return await queue_service.enqueue_task(
        queue=request.queue,
        operation=request.operation,
        data=request.data,
        priority=request.priority,
        metadata=request.metadata,
    )


@router.get(
    "/tasks",
    response_model=TaskListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def list_tasks(
    _: CurrentUsername,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
    pagination: PageParams,
    queues: str | None = Query(None, description="Comma-separated queue names"),
) -> TaskListResponse:
    """List recent tasks across all queues or specific queues.

    **Query Parameters:**
    - `page`: 1-based page number (default: 1)
    - `page_size`: Items per page (default: 50, max: 1000)
    - `queues`: Filter by queue names (comma-separated)

    **Returns:**
    - Canonical ``{data, pagination}`` envelope plus ``total_in_queue``
      (active tasks across matched queues, used by the UI's
      "N tasks in queue" indicator).
    """
    page, page_size = pagination
    queue_list = queues.split(",") if queues else None
    return await queue_service.list_tasks(page=page, page_size=page_size, queues=queue_list)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskDetailResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_task(
    _: CurrentUsername,
    task_id: str,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> TaskDetailResponse:
    """Get task details.

    **Returns:**
    - Task status, data, attempts, timestamps, etc.

    **Errors:**
    - 404: Task not found
    - 503: Queue service unavailable
    """
    return TaskDetailResponse(**await queue_service.get_task(task_id))


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_task_result(
    _: CurrentUsername,
    task_id: str,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> TaskResultResponse:
    """Get task result (if completed).

    **Returns:**
    - Task result data

    **Errors:**
    - 404: Result not found or expired
    - 503: Queue service unavailable
    """
    return await queue_service.get_task_result(task_id)


@router.delete(
    "/tasks/history",
    response_model=ClearHistoryResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def clear_history(
    _: CurrentUsername,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
    queue: str | None = Query(None, description="Optional queue name filter"),
    older_than_hours: int = Query(
        0,
        ge=0,
        le=8760,
        description="Clear only tasks older than this many hours (0 = all)",
    ),
) -> ClearHistoryResponse:
    """Clear completed, failed, and cancelled tasks from history.

    **Query Parameters:**
    - `queue`: Optional queue name (None = all queues)
    - `older_than_hours`: Clear only tasks older than N hours (0 = all, max = 8760)

    **WARNING:**
    - This permanently removes task history
    - Use with caution!

    **Returns:**
    - Number of tasks cleared
    - Queue name (if filtered)

    **Errors:**
    - 503: Queue service unavailable
    """
    return await queue_service.clear_task_history(queue=queue, older_than_hours=older_than_hours)


@router.delete(
    "/tasks/{task_id}",
    response_model=CancelTaskResponse,
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
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> CancelTaskResponse:
    """Cancel a task.

    **Returns:**
    - Cancellation status

    **Errors:**
    - 400: Task cannot be cancelled (already completed/failed)
    - 404: Task not found
    - 503: Queue service unavailable
    """
    return await queue_service.cancel_task(task_id)


@router.post(
    "/tasks/{task_id}/retry",
    response_model=RetryTaskResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def retry_task(
    _: CurrentUsername,
    task_id: str,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> RetryTaskResponse:
    """Retry a failed task by re-enqueueing it with the same parameters.

    **Returns:**
    - New task ID
    - Original task ID

    **Errors:**
    - 400: Task is not in failed status
    - 404: Task not found
    - 503: Queue service unavailable
    """
    return await queue_service.retry_task(task_id)


@router.delete(
    "/tasks",
    response_model=CancelAllResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def delete_all_tasks(
    _: CurrentUsername,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
    queue: str | None = Query(None, description="Optional queue name filter"),
) -> CancelAllResponse:
    """Cancel all active tasks (bulk delete).

    **RESTful Design:**
    - DELETE /tasks removes all task resources

    **Query Parameters:**
    - `queue`: Optional queue name (None = all queues)

    **WARNING:**
    - This will cancel ALL active tasks
    - Use with caution!

    **Returns:**
    - Number of tasks cancelled
    - Queue name (if filtered)

    **Errors:**
    - 503: Queue service unavailable
    """
    return await queue_service.cancel_all_tasks(queue=queue)


@router.post(
    "/tasks/cancel",
    response_model=CancelBatchResponse | CancelByMetadataResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def cancel_tasks(
    _: CurrentUsername,
    request: CancelTasksRequest,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> CancelBatchResponse | CancelByMetadataResponse:
    """Cancel tasks by batch IDs or metadata (targeted bulk operation).

    **Request Body (``CancelTasksRequest``):**
    - `task_ids`: List of task IDs to cancel (batch mode)
    - `metadata`: Metadata key-value pairs to match (metadata mode)
    - `queue`: Optional queue name filter (metadata mode only)

    Unknown keys in the request body are rejected by the Pydantic model.

    **Modes:**
    1. **Batch**: Provide `task_ids` - cancels specific tasks (preferred)
    2. **Metadata**: Provide `metadata` - cancels tasks matching criteria

    **Returns:**
    - Number of tasks cancelled
    - Additional details based on mode

    **Note:**
    - Batch mode is preferred to avoid SCAN deadlocks
    - Frontend should provide exact list of task IDs when possible

    **Errors:**
    - 400: Must provide either task_ids or metadata
    - 422: Request body failed Pydantic validation (unknown key, wrong type)
    - 503: Queue service unavailable
    """
    if request.task_ids:
        return await queue_service.cancel_batch(request.task_ids)
    if request.metadata:
        return await queue_service.cancel_by_metadata(
            metadata=request.metadata, queue=request.queue
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ErrorDetail(
            code="VALIDATION_FAILED", message="Must provide either task_ids or metadata"
        ).model_dump(),
    )


@router.get(
    "/stats",
    response_model=QueueStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_all_stats(
    _: CurrentUsername,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> QueueStatsResponse:
    """Get statistics for all queues.

    **Returns:**
    - List of queue statistics (queued, running, completed, failed)
    - Note about worker configuration

    **Note:**
    - Configuration is managed in worker/config.py
    """
    return await queue_service.get_all_stats()


@router.get(
    "/stats/{queue_name}",
    response_model=QueueStatsByName,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_queue_stats(
    _: CurrentUsername,
    queue_name: str,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> QueueStatsByName:
    """Get queue statistics.

    **Returns:**
    - Queue statistics (queued, running, completed, failed)

    **Errors:**
    - 503: Queue service unavailable
    """
    return QueueStatsByName(**await queue_service.get_queue_stats(queue_name))


@router.get(
    "/health",
    response_model=QueueHealthResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_health(
    _: CurrentUsername,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> QueueHealthResponse:
    """Get queue system health status.

    **Returns:**
    - Health status (healthy/unavailable)
    - Connection status
    - System type (valkey)
    - Worker configuration note
    """
    return queue_service.get_health()


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def reconcile_queue_endpoint(
    _: CurrentUsername,
    request: ReconcileRequest,
    queue_service: Annotated[QueueService, Depends(get_queue_service)],
) -> ReconcileResponse:
    """Trigger an immediate queue reconciliation pass.

    Self-healing admin endpoint. Runs reconcile_queue across the
    specified queue (or all queues if omitted), removing orphan
    IDs from running sets and recovering (or failing) tasks abandoned
    by crashed workers.

    **Request Body:**
    - `queue`: Optional queue name. If omitted, reconciles all queues.

    **Returns:**
    - `recovered_orphans`: count of IDs with no backing hash removed
    - `recovered_crashed`: count of abandoned tasks requeued
    - `failed_unrecoverable`: count of abandoned tasks marked failed

    **Admin-only.**

    This endpoint supersedes the deleted `/api/v1/llm/health` stub.
    """
    stats = await queue_service.force_reconcile(queue_name=request.queue)
    return ReconcileResponse(**stats)
