# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue Models.

Pydantic DTOs for queue operations.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chaoscypher_core import policy
from chaoscypher_cortex.shared.api.models import PaginationMetadata
from chaoscypher_cortex.shared.models.summaries import QueueStatsResponse


__all__ = [
    "CancelAllResponse",
    "CancelBatchResponse",
    "CancelByMetadataResponse",
    "CancelTaskResponse",
    "CancelTasksRequest",
    "ClearHistoryResponse",
    "QueueHealthResponse",
    "QueueStatsByName",
    "QueueStatsResponse",
    "QueueTaskRequest",
    "QueueTaskResponse",
    "ReconcileRequest",
    "ReconcileResponse",
    "RetryTaskResponse",
    "TaskDetailResponse",
    "TaskListResponse",
    "TaskResultResponse",
]


class QueueTaskRequest(BaseModel):
    """Request to queue a new task."""

    queue: str = Field(max_length=policy.QUEUE_NAME_MAX_LENGTH)
    operation: str = Field(max_length=policy.OPERATION_NAME_MAX_LENGTH)
    data: dict[str, Any]
    priority: int = Field(default=50, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueueTaskResponse(BaseModel):
    """Response from queueing a task."""

    task_id: str


class TaskListResponse(BaseModel):
    """Paginated list of tasks (canonical {data, pagination} envelope).

    ``total_in_queue`` is a sibling of the pagination block, not a
    pagination metric: it counts active tasks (queued + running) across
    every matched queue regardless of which page is shown. The UI uses
    it for the "N tasks in queue" indicator.
    """

    data: list[dict[str, Any]]
    pagination: PaginationMetadata
    total_in_queue: int = 0
    queues: list[str] | None = None


class TaskResultResponse(BaseModel):
    """Task result response."""

    result: Any


class TaskDetailResponse(BaseModel):
    """Full task record returned by GET /queue/tasks/{task_id}.

    Shape mirrors ``QueueClient._decode_record`` — a decoded Valkey hash
    of the ``queue:task:{id}`` record. Timestamps are ISO-8601 strings
    (the client stores them as-is from Valkey, does not parse to
    ``datetime``). Optional fields are only populated once the task has
    reached the corresponding lifecycle stage.
    """

    task_id: str = Field(description="Unique task identifier.")
    queue: str = Field(description="Queue name (e.g. 'llm', 'operations').")
    operation: str = Field(description="Operation name registered on the queue.")
    status: str = Field(
        description="Current lifecycle status: queued | running | completed | failed | cancelled.",
    )
    priority: int = Field(description="Effective dispatch priority (lower = sooner).")
    created_at: str = Field(description="ISO-8601 UTC timestamp when the task was enqueued.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Caller-supplied metadata used for filtering and cancellation-by-query.",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Operation-specific input payload (opaque to the queue layer).",
    )
    attempts: int = Field(default=0, description="Number of times this task has been attempted.")
    started_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp when a worker first picked up the task.",
    )
    completed_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp when the task reached a terminal state.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Public-safe error message (the client redacts raw exception text to 'Task failed')."
        ),
    )
    error_type: str | None = Field(
        default=None,
        description="Short error classification (e.g. 'ValidationError', 'TimeoutError').",
    )


class QueueStatsByName(BaseModel):
    """Per-queue statistics returned by GET /queue/stats/{queue_name}.

    Shape comes from ``QueueMonitor.get_queue_stats`` — queued/running
    are live counts of the pending sorted set and running set;
    ``workers`` is 1 when the health key is present, otherwise 0. The
    ``completed_recent`` / ``failed_recent`` counters are currently
    always 0 (placeholder for future windowed stats).
    """

    queue: str = Field(description="Queue name.")
    queued: int = Field(description="Tasks currently waiting in the pending sorted set.")
    running: int = Field(description="Tasks currently being processed by a worker.")
    completed_recent: int = Field(
        default=0,
        description="Tasks completed in the recent window (placeholder — always 0 today).",
    )
    failed_recent: int = Field(
        default=0,
        description="Tasks failed in the recent window (placeholder — always 0 today).",
    )
    workers: int = Field(
        default=0,
        description="1 if a worker health key is present for this queue, else 0.",
    )


class CancelTaskResponse(BaseModel):
    """Cancel task response."""

    status: str


class RetryTaskResponse(BaseModel):
    """Retry task response."""

    new_task_id: str
    original_task_id: str


class CancelTasksRequest(BaseModel):
    """Typed request body for POST /queue/tasks/cancel.

    Either ``task_ids`` or ``metadata`` must be provided (not both empty).
    ``extra='forbid'`` rejects unknown keys at validation time.
    """

    model_config = ConfigDict(extra="forbid")

    task_ids: list[str] | None = Field(
        default=None,
        description="Specific task IDs to cancel (batch mode). Preferred — avoids SCAN deadlocks.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Metadata key-value pairs to match (metadata mode). e.g. "
            "{'chat_id': 'abc', 'user_id': 42}."
        ),
    )
    queue: str | None = Field(
        default=None,
        description="Optional queue name filter (only applies to metadata mode).",
    )


class CancelByMetadataResponse(BaseModel):
    """Cancel by metadata response."""

    cancelled: int


class CancelBatchResponse(BaseModel):
    """Cancel batch response."""

    cancelled_count: int
    requested_count: int
    failed: list[dict[str, Any]]


class CancelAllResponse(BaseModel):
    """Cancel all tasks response."""

    cancelled: int
    queue: str | None


class ClearHistoryResponse(BaseModel):
    """Clear task history response."""

    cleared: int
    queue: str | None


class QueueHealthResponse(BaseModel):
    """Queue health response."""

    status: str
    enabled: bool
    connected: bool
    system: str
    note: str | None = None


class ReconcileRequest(BaseModel):
    """Body for POST /api/v1/queue/reconcile.

    Triggers an immediate reconciliation pass across the specified
    queue (or all queues if omitted).
    """

    queue: str | None = Field(
        default=None,
        description="Queue name to reconcile, or null/omitted for all queues.",
    )


class ReconcileResponse(BaseModel):
    """Response from POST /api/v1/queue/reconcile."""

    recovered_orphans: int = Field(description="Tasks with ID in running set but no backing hash.")
    recovered_crashed: int = Field(
        description="Tasks abandoned by a crashed worker that were requeued."
    )
    failed_unrecoverable: int = Field(
        description="Tasks abandoned that exhausted retries or had retry_on_crash=False.",
    )
