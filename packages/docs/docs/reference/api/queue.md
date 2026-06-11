---
title: Queue API
description: Manage Chaos Cypher background tasks — create, cancel, and monitor queue tasks across the LLM lane (1 concurrent) and Operations lane (8 concurrent).
---

# Queue

Manage background tasks, monitor queue health, and view processing statistics. Chaos Cypher uses a Valkey-backed queue with two processing lanes: **LLM** (1 concurrent) for AI operations and **Operations** (8 concurrent) for source processing, exports, and workflows.

**Base path:** `/api/v1/queue`

---

## Tasks

### Create Task

```
POST /api/v1/queue/tasks
```

Queues a new background task for processing.

#### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `queue` | string | Yes | -- | Queue name (e.g., `"operations"`, `"llm"`) |
| `operation` | string | Yes | -- | Operation name (e.g., `"import_ccx"`, `"chat_completion"`) |
| `data` | object | Yes | -- | Operation-specific payload |
| `priority` | integer | No | `50` | Task priority (0-100, higher = higher priority — ZPOPMAX) |
| `metadata` | object | No | `{}` | Arbitrary metadata for filtering and tracking |

```json
{
  "queue": "operations",
  "operation": "import_ccx",
  "data": {
    "source_id": "src-uuid-1",
    "file_path": "/data/uploads/graph.ccx"
  },
  "priority": 50,
  "metadata": {
    "source_id": "src-uuid-1",
    "user_initiated": true
  }
}
```

#### Response

**Status:** `201 Created`

```json
{
  "task_id": "task-abc123def456"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `503` | Queue service unavailable |

#### curl Example

```bash
curl -s -X POST http://localhost/api/v1/queue/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "queue": "operations",
    "operation": "import_ccx",
    "data": {"source_id": "src-uuid-1"},
    "priority": 50,
    "metadata": {"source_id": "src-uuid-1"}
  }'
```

---

### List Tasks

```
GET /api/v1/queue/tasks
```

Returns recent tasks across all queues or filtered by specific queues. Supports pagination.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | integer | No | `1` | 1-based page number (>= 1) |
| `page_size` | integer | No | `50` | Items per page (>= 1, clamped to the server max of `1000`) |
| `queues` | string | No | _none_ | Comma-separated queue names to filter by (e.g., `"operations,llm"`) |

#### Response

**Status:** `200 OK`

```json
{
  "data": [
    {
      "task_id": "task-abc123def456",
      "queue": "operations",
      "operation": "import_ccx",
      "status": "completed",
      "priority": 50,
      "data": {"source_id": "src-uuid-1"},
      "metadata": {"source_id": "src-uuid-1"},
      "attempts": 1,
      "created_at": "2026-03-09T14:30:00.000000",
      "started_at": "2026-03-09T14:30:01.000000",
      "completed_at": "2026-03-09T14:30:15.000000"
    },
    {
      "task_id": "task-xyz789ghi012",
      "queue": "llm",
      "operation": "chat_completion",
      "status": "running",
      "priority": 10,
      "data": {"chat_id": "chat-uuid-1"},
      "metadata": {},
      "attempts": 1,
      "created_at": "2026-03-09T14:31:00.000000",
      "started_at": "2026-03-09T14:31:02.000000",
      "completed_at": null
    }
  ],
  "pagination": {
    "total": 2,
    "page": 1,
    "page_size": 50,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  },
  "total_in_queue": 5,
  "queues": null
}
```

#### curl Example

```bash
# List all recent tasks
curl -s http://localhost/api/v1/queue/tasks

# With pagination
curl -s "http://localhost/api/v1/queue/tasks?page=1&page_size=10"

# Filter by queue
curl -s "http://localhost/api/v1/queue/tasks?queues=operations"

# Filter by multiple queues
curl -s "http://localhost/api/v1/queue/tasks?queues=operations,llm"
```

---

### Get Task

```
GET /api/v1/queue/tasks/{task_id}
```

Returns full details for a single task including status, data, attempts, and timestamps.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Task ID |

#### Response

**Status:** `200 OK`

Returns a single task object (same schema as the items in [List Tasks](#list-tasks)), plus `error` (public-safe error message for failed tasks, `null` otherwise) and `error_type` (short error classification such as `ValidationError` or `TimeoutError`, `null` otherwise).

#### Errors

| Status | Description |
|--------|-------------|
| `404` | Task not found |
| `503` | Queue service unavailable |

#### curl Example

```bash
curl -s http://localhost/api/v1/queue/tasks/task-abc123def456
```

---

### Get Task Result

```
GET /api/v1/queue/tasks/{task_id}/result
```

Returns the result data for a completed task. Results may expire after a configured retention period (`TimeoutSettings.operations_result_ttl` / `llm_result_ttl` — 2h and 1h respectively by default). Failed tasks have no result body; their post-mortem metadata is exposed via [Get Task](#get-task) and retained for `TimeoutSettings.failed_result_ttl` (14 days by default).

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Task ID |

#### Response

**Status:** `200 OK`

```json
{
  "result": {
    "entities_created": 42,
    "relationships_created": 18,
    "processing_time_ms": 14320
  }
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `404` | Result not found or expired |
| `503` | Queue service unavailable |

#### curl Example

```bash
curl -s http://localhost/api/v1/queue/tasks/task-abc123def456/result
```

---

### Cancel Task

```
DELETE /api/v1/queue/tasks/{task_id}
```

Cancels a single task. Both queued and running tasks can be cancelled. Running tasks are cancelled cooperatively — a cancellation flag is set in Valkey that the worker checks between processing batches.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Task ID |

#### Response

**Status:** `200 OK`

```json
{
  "status": "cancelled"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `400` | Task cannot be cancelled (already completed or failed) |
| `404` | Task not found |
| `503` | Queue service unavailable |

#### curl Example

```bash
curl -s -X DELETE http://localhost/api/v1/queue/tasks/task-abc123def456
```

---

### Retry Task

```
POST /api/v1/queue/tasks/{task_id}/retry
```

Re-enqueues a failed task with the same parameters. Creates a new task with a new ID.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Task ID of the failed task |

#### Response

**Status:** `200 OK`

```json
{
  "new_task_id": "task-new789xyz012",
  "original_task_id": "task-abc123def456"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `400` | Task is not in failed status |
| `404` | Task not found |
| `503` | Queue service unavailable |

#### curl Example

```bash
curl -s -X POST http://localhost/api/v1/queue/tasks/task-abc123def456/retry
```

---

### Cancel All Tasks

```
DELETE /api/v1/queue/tasks
```

Cancels all active (queued + running) tasks. Optionally filtered by queue name.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `queue` | string | No | _none_ | Queue name filter. Omit to cancel across all queues. |

#### Response

**Status:** `200 OK`

```json
{
  "cancelled": 12,
  "queue": null
}
```

With queue filter:

```json
{
  "cancelled": 3,
  "queue": "operations"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `503` | Queue service unavailable |

:::warning

This permanently cancels all active tasks. Use with caution.

:::

#### curl Example

```bash
# Cancel all tasks across all queues
curl -s -X DELETE http://localhost/api/v1/queue/tasks

# Cancel all tasks in a specific queue
curl -s -X DELETE "http://localhost/api/v1/queue/tasks?queue=operations"
```

---

### Cancel Tasks (Batch or by Metadata)

```
POST /api/v1/queue/tasks/cancel
```

Cancels tasks using one of two modes: **batch** (by task IDs) or **metadata** (by matching key-value pairs). Batch mode is preferred for performance.

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_ids` | string[] | No | List of task IDs to cancel (batch mode) |
| `metadata` | object | No | Metadata key-value pairs to match (metadata mode) |
| `queue` | string | No | Queue name filter (metadata mode only) |

:::note

You must provide either `task_ids` or `metadata`, but not both. Batch mode is preferred to avoid SCAN overhead.

:::

**Batch mode request:**

```json
{
  "task_ids": ["task-abc123", "task-def456", "task-ghi789"]
}
```

**Metadata mode request:**

```json
{
  "metadata": {"source_id": "src-uuid-1"},
  "queue": "operations"
}
```

#### Response (Batch Mode)

**Status:** `200 OK`

```json
{
  "cancelled_count": 2,
  "requested_count": 3,
  "failed": [
    {
      "task_id": "task-ghi789",
      "reason": "Task is currently running"
    }
  ]
}
```

#### Response (Metadata Mode)

**Status:** `200 OK`

```json
{
  "cancelled": 4
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `400` | Must provide either `task_ids` or `metadata` |
| `503` | Queue service unavailable |

#### curl Example

```bash
# Batch cancel by task IDs
curl -s -X POST http://localhost/api/v1/queue/tasks/cancel \
  -H "Content-Type: application/json" \
  -d '{"task_ids": ["task-abc123", "task-def456"]}'

# Cancel by metadata
curl -s -X POST http://localhost/api/v1/queue/tasks/cancel \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {"source_id": "src-uuid-1"},
    "queue": "operations"
  }'
```

---

### Clear History

```
DELETE /api/v1/queue/tasks/history
```

Permanently removes completed, failed, and cancelled tasks from history. Does not affect queued or running tasks.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `queue` | string | No | _none_ | Queue name filter. Omit to clear across all queues. |
| `older_than_hours` | integer | No | `0` | Clear only tasks older than this many hours. `0` clears all history. Max: `8760` (1 year). |

#### Response

**Status:** `200 OK`

```json
{
  "cleared": 156,
  "queue": null
}
```

With filters:

```json
{
  "cleared": 42,
  "queue": "operations"
}
```

#### Errors

| Status | Description |
|--------|-------------|
| `503` | Queue service unavailable |

:::warning

This permanently removes task history and cannot be undone.

:::

#### curl Example

```bash
# Clear all task history
curl -s -X DELETE http://localhost/api/v1/queue/tasks/history

# Clear history for a specific queue
curl -s -X DELETE "http://localhost/api/v1/queue/tasks/history?queue=operations"

# Clear tasks older than 24 hours
curl -s -X DELETE "http://localhost/api/v1/queue/tasks/history?older_than_hours=24"

# Combine filters
curl -s -X DELETE "http://localhost/api/v1/queue/tasks/history?queue=llm&older_than_hours=48"
```

---

## Statistics

### Get All Queue Stats

```
GET /api/v1/queue/stats
```

Returns statistics for all known queues.

#### Response

**Status:** `200 OK`

```json
{
  "queues": [
    {
      "queue": "llm",
      "queued": 2,
      "running": 1,
      "completed_recent": 0,
      "failed_recent": 0,
      "workers": 1
    },
    {
      "queue": "operations",
      "queued": 0,
      "running": 4,
      "completed_recent": 0,
      "failed_recent": 0,
      "workers": 1
    }
  ],
  "note": "Queue configuration managed in worker/config.py"
}
```

:::note

`completed_recent` / `failed_recent` are placeholders that are currently always `0` (reserved for future windowed stats). `workers` is `1` when a worker health key is present for the queue, else `0`.

:::

If the queue service is unavailable, the response returns an empty list with a note:

```json
{
  "queues": [],
  "note": "Queue service unavailable"
}
```

#### curl Example

```bash
curl -s http://localhost/api/v1/queue/stats
```

---

### Get Per-Queue Stats

```
GET /api/v1/queue/stats/{queue_name}
```

Returns statistics for a single queue.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `queue_name` | string | Yes | Queue name (e.g., `"llm"`, `"operations"`) |

#### Response

**Status:** `200 OK`

```json
{
  "queue": "operations",
  "queued": 0,
  "running": 4,
  "completed_recent": 0,
  "failed_recent": 0,
  "workers": 1
}
```

As with [Get All Queue Stats](#get-all-queue-stats), `completed_recent` / `failed_recent` are placeholders that are currently always `0`, and `workers` is `1` when a worker health key is present for the queue, else `0`.

#### Errors

| Status | Description |
|--------|-------------|
| `503` | Queue service unavailable |

#### curl Example

```bash
curl -s http://localhost/api/v1/queue/stats/operations
```

---

## Health

### Health Check

```
GET /api/v1/queue/health
```

Returns the health status of the queue system, including Valkey connectivity and worker configuration.

#### Response

**Status:** `200 OK`

```json
{
  "status": "healthy",
  "enabled": true,
  "connected": true,
  "system": "valkey",
  "note": "Workers run in separate container. See worker/config.py for concurrency settings."
}
```

When the queue is unavailable:

```json
{
  "status": "unavailable",
  "enabled": true,
  "connected": false,
  "system": "valkey",
  "note": "Workers run in separate container. See worker/config.py for concurrency settings."
}
```

#### curl Example

```bash
curl -s http://localhost/api/v1/queue/health
```

---

## Maintenance

### Reconcile Queue

```
POST /api/v1/queue/reconcile
```

Triggers an immediate queue reconciliation pass. This self-healing admin endpoint inspects running sets across the specified queue (or all queues if omitted), removes orphan task IDs that have no backing hash, and requeues or fails tasks that were abandoned by crashed workers.

#### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `queue` | string | No | `null` | Queue name to reconcile. Omit or set to `null` to reconcile all queues. |

```json
{
  "queue": "operations"
}
```

#### Response

**Status:** `200 OK`

```json
{
  "recovered_orphans": 3,
  "recovered_crashed": 1,
  "failed_unrecoverable": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `recovered_orphans` | integer | Task IDs found in the running set with no backing hash — removed |
| `recovered_crashed` | integer | Tasks abandoned by a crashed worker that were requeued |
| `failed_unrecoverable` | integer | Abandoned tasks that exhausted retries or had `retry_on_crash=false` — marked failed |

#### Errors

| Status | Description |
|--------|-------------|
| `503` | Queue service unavailable |

#### curl Example

```bash
# Reconcile all queues
curl -s -X POST http://localhost/api/v1/queue/reconcile \
  -H "Content-Type: application/json" \
  -d '{}'

# Reconcile a specific queue
curl -s -X POST http://localhost/api/v1/queue/reconcile \
  -H "Content-Type: application/json" \
  -d '{"queue": "operations"}'
```

---

## Task Lifecycle

A task progresses through the following statuses:

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    running --> completed
    running --> failed
    failed --> queued: retry

    classDef default fill:#12121e,stroke:#7b2ff7,color:#e0e0f0
    class queued,running,completed,failed default
```

| Status | Description |
|--------|-------------|
| `queued` | Task is waiting to be picked up by a worker |
| `running` | Task is currently being processed |
| `completed` | Task finished successfully (result available) |
| `failed` | Task encountered an error (can be retried) |
| `cancelled` | Task was cancelled before completion |

### Example: Submit and Track a Task

**1. Create the task:**

```bash
curl -s -X POST http://localhost/api/v1/queue/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "queue": "operations",
    "operation": "import_ccx",
    "data": {"source_id": "src-uuid-1"},
    "metadata": {"source_id": "src-uuid-1"}
  }'
```

```json
{"task_id": "task-abc123def456"}
```

**2. Poll for status** (returns the full task object -- see [Get Task](#get-task)):

```bash
curl -s http://localhost/api/v1/queue/tasks/task-abc123def456
```

**3. Retrieve the result once completed** (see [Get Task Result](#get-task-result)):

```bash
curl -s http://localhost/api/v1/queue/tasks/task-abc123def456/result
```

**4. If the task failed, retry it:**

```bash
curl -s -X POST http://localhost/api/v1/queue/tasks/task-abc123def456/retry
```

```json
{
  "new_task_id": "task-new789xyz012",
  "original_task_id": "task-abc123def456"
}
```

---

## Response Models Reference

### QueueTaskRequest

Request body for creating a new task.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `queue` | string | Yes | -- | Target queue name |
| `operation` | string | Yes | -- | Operation to perform |
| `data` | object | Yes | -- | Operation-specific payload |
| `priority` | integer | No | `50` | Priority (0-100) |
| `metadata` | object | No | `{}` | Arbitrary metadata |

### QueueTaskResponse

Returned when a task is created.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique task identifier |

### TaskListResponse

Returned by the list tasks endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `data` | object[] | List of task detail objects |
| `pagination` | PaginationInfo | Pagination metadata |
| `total_in_queue` | integer | Active tasks across queues (queued + running) |
| `queues` | string[] or null | Queue filter applied, or `null` if unfiltered |

### PaginationInfo

Pagination metadata (`PaginationMetadata`) included in list responses.

| Field | Type | Description |
|-------|------|-------------|
| `total` | integer | Total items in the list |
| `page` | integer | Current page number (1-based) |
| `page_size` | integer | Items per page |
| `total_pages` | integer | Total number of pages |
| `has_next` | boolean | Whether a next page exists |
| `has_prev` | boolean | Whether a previous page exists |

### TaskResultResponse

Returned by the get result endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `result` | any | Task result data (structure varies by operation) |

### CancelTaskResponse

Returned when a single task is cancelled.

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Cancellation status (e.g., `"cancelled"`) |

### RetryTaskResponse

Returned when a failed task is retried.

| Field | Type | Description |
|-------|------|-------------|
| `new_task_id` | string | ID of the newly created task |
| `original_task_id` | string | ID of the original failed task |

### CancelAllResponse

Returned when all tasks are cancelled.

| Field | Type | Description |
|-------|------|-------------|
| `cancelled` | integer | Number of tasks cancelled |
| `queue` | string or null | Queue filter applied, or `null` if all queues |

### CancelBatchResponse

Returned by batch cancel (via task IDs).

| Field | Type | Description |
|-------|------|-------------|
| `cancelled_count` | integer | Number of tasks successfully cancelled |
| `requested_count` | integer | Number of task IDs requested |
| `failed` | object[] | List of tasks that could not be cancelled (with `task_id` and `reason`) |

### CancelByMetadataResponse

Returned by metadata-based cancel.

| Field | Type | Description |
|-------|------|-------------|
| `cancelled` | integer | Number of tasks cancelled |

### ClearHistoryResponse

Returned when task history is cleared.

| Field | Type | Description |
|-------|------|-------------|
| `cleared` | integer | Number of history entries removed |
| `queue` | string or null | Queue filter applied, or `null` if all queues |

### QueueStatsResponse

Returned by the all-queues stats endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `queues` | object[] | List of per-queue statistics |
| `note` | string or null | Informational note about configuration |

### QueueHealthResponse

Returned by the health check endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Health status (`"healthy"` or `"unavailable"`) |
| `enabled` | boolean | Whether the queue system is enabled |
| `connected` | boolean | Whether Valkey is connected |
| `system` | string | Queue backend system (e.g., `"valkey"`) |
| `note` | string or null | Informational note about worker configuration |
