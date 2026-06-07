# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue Feature.

Async task queue monitoring and management.

This feature provides visibility and control over the Valkey-backed task
queues (LLM and Operations). Enables monitoring of queue depth,
task status, worker health, and job history. Supports queue inspection, task
cancellation, and statistics for performance analysis. Critical for debugging
long-running imports, extractions, and workflow executions.

Components:
- QueueService: Queue statistics, job lookup, and task management
- QueueTaskRequest: Pydantic request DTO for task operations
- QueueTaskResponse: Task status and result response DTO
- router: FastAPI endpoints for /api/v1/queue

Architecture:
Direct Valkey integration via valkey.asyncio connection. Service layer wraps
queue_client for queue inspection and task management. No repository layer
needed as queue is external queue state.

Example:
    from chaoscypher_cortex.features.queue import QueueService

    # Monitor and manage queue tasks
    service = QueueService(client_pool)
    stats = await service.get_queue_stats()
    job = await service.get_job_status(task_id)
    await service.cancel_task(task_id)

"""

from chaoscypher_cortex.features.queue.api import router
from chaoscypher_cortex.features.queue.models import QueueTaskRequest, QueueTaskResponse
from chaoscypher_cortex.features.queue.service import QueueService


__all__ = ["QueueService", "QueueTaskRequest", "QueueTaskResponse", "router"]
