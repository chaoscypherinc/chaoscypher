# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue Infrastructure.

Valkey-based queue system for background task processing.

Provides async task queue integration using sorted sets for running
background operations. The system manages per-queue concurrency via asyncio
semaphores: LLM queue (1 concurrent task) for API-limited operations, and
Operations queue (8 concurrent tasks) for CPU/IO work.

Components:
- QueueClient: Main queue client facade with task submission and monitoring
- queue_client: Singleton instance for application-wide queue access
- QueueUnavailableError: Exception raised when queue server is unavailable
- QueueWorker: Async worker loop with per-queue pollers and health publishing
- classify_error: Utility for categorizing task execution errors
- QueueMonitor: Monitor queue statistics and token usage

Architecture:
- Uses delegation pattern with QueueMonitor (stats)
- Supports multiple named queues with separate handler registries
- Automatic connection pooling and reconnection handling
- Job result storage with configurable expiration

Example:
    from chaoscypher_core.constants import QUEUE_LLM
    from chaoscypher_core.queue import queue_client

    # Enqueue a task with priority
    job_id = await queue_client.enqueue_task(
        queue=QUEUE_LLM,
        operation="chat_completion",
        data={"messages": [...]},
        priority=100,  # Higher = higher priority (ZPOPMAX convention)
    )

    # Check job status
    status = await queue_client.get_task(job_id)

"""

# Client and main exports
from chaoscypher_core.queue.client import (
    QueueClient,
    QueueUnavailableError,
    queue_client,
)

# Monitoring
from chaoscypher_core.queue.monitor import QueueMonitor

# Pub/sub helpers
from chaoscypher_core.queue.pubsub import (
    publish_chat_event,
    subscribe_chat_events,
)

# Task execution
from chaoscypher_core.queue.service import classify_error

# Utilities
from chaoscypher_core.queue.utils import decode_bytes

# Worker
from chaoscypher_core.queue.worker import QueueWorker


__all__ = [
    # Client
    "QueueClient",
    # Monitoring
    "QueueMonitor",
    "QueueUnavailableError",
    # Worker
    "QueueWorker",
    # Task execution
    "classify_error",
    # Utilities
    "decode_bytes",
    # Pub/sub
    "publish_chat_event",
    "queue_client",
    "subscribe_chat_events",
]
