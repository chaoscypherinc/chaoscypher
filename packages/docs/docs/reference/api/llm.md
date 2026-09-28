---
title: LLM Monitoring API
description: Monitor and manage the LLM task queue — view statistics, inspect active tasks, cancel operations, and diagnose stuck queues at /api/v1/llm.
---

# LLM Monitoring

Monitor and manage the LLM task queue -- view statistics, inspect running tasks, cancel operations, and diagnose stuck queues.

All endpoints are prefixed with `/api/v1/llm`.

---

## Statistics

### Get LLM Queue Stats

```
GET /api/v1/llm/stats
```

Returns current LLM queue statistics including queue depth, token usage, cost tracking, estimated completion times, and semaphore state.

```bash
curl http://localhost/api/v1/llm/stats
```

**Response** `200 OK` -- [LLMStatsResponse](#llmstatsresponse)

```json
{
  "data": {
    "queues": [
      {
        "queue": "llm",
        "queued": 3,
        "running": 1,
        "workers": 1,
        "max_depth": 100,
        "depth_percent": 4.0,
        "total_input_tokens": 15200,
        "total_output_tokens": 4800,
        "total_cost_usd": 0.032
      }
    ],
    "total_queued": 3,
    "total_cost_usd": 0.032,
    "total_input_tokens": 15200,
    "total_output_tokens": 4800,
    "total_tokens": 20000,
    "estimated_completion_time_seconds": 45,
    "estimated_completion_time_human": "45s",
    "estimated_completion_times_human": {
      "llm": "45s",
      "operations": "0s"
    },
    "semaphore_stats": {
      "max_concurrent": 1,
      "reserved_high_priority": 0,
      "active_count": 1,
      "active_high_priority": 0,
      "active_low_priority": 1,
      "waiting_high_priority": 0,
      "waiting_low_priority": 2,
      "total_high_priority": 42,
      "total_low_priority": 118,
      "avg_wait_time_high": 0.05,
      "avg_wait_time_low": 12.3
    }
  }
}
```

**Errors:**

| Status | Description |
|--------|-------------|
| `503`  | LLM queue service unavailable |

---

### Clear Stats

```
DELETE /api/v1/llm/stats
```

Reset the LLM queue's cumulative token and cost totals (what [Get LLM Queue Stats](#get-llm-queue-stats) reports as `total_*_tokens` / `total_cost_usd`), clear the recent-task history lists, and remove completed task records older than the cutoff from Valkey.

```bash
# Clear tasks older than 48 hours
curl -X DELETE "http://localhost/api/v1/llm/stats?older_than_hours=48"

# Clear with default (24 hours)
curl -X DELETE http://localhost/api/v1/llm/stats
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `older_than_hours` | integer | No | `24` | Remove completed tasks older than this many hours. Min: `0`, Max: `8760`. |

**Response** `204 No Content` -- empty body.

**Errors:**

| Status | Description |
|--------|-------------|
| `422`  | Validation error (e.g., `older_than_hours` out of range) |
| `503`  | LLM queue service unavailable |

---

## Tasks

### List LLM Tasks

```
GET /api/v1/llm/tasks
```

List currently queued and running LLM tasks. Completed and failed tasks are excluded.

```bash
curl http://localhost/api/v1/llm/tasks
```

**Response** `200 OK` -- [LLMTasksResponse](#llmtasksresponse)

```json
{
  "data": [
    {
      "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "queue": "llm",
      "operation": "chat_completion",
      "status": "running",
      "priority": 100,
      "created_at": "2026-03-09T14:30:00.000000+00:00",
      "started_at": "2026-03-09T14:30:01.500000+00:00",
      "metadata": {"source": "interactive_chat", "correlation_id": "…"},
      "data": {"messages": ["…"], "task_type": "CHAT"},
      "attempts": 1,
      "payload_version": 1
    },
    {
      "task_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "queue": "llm",
      "operation": "chat_background",
      "status": "queued",
      "priority": 50,
      "created_at": "2026-03-09T14:30:05.000000+00:00",
      "metadata": {},
      "data": {"messages": ["…"], "task_type": "CHAT"},
      "attempts": 0,
      "payload_version": 1
    }
  ]
}
```

`priority`, `attempts` and `payload_version` are integers; `metadata` and `data` are decoded objects (`data` is the task's full request payload). `started_at` is present once a task has started.

**Errors:**

| Status | Description |
|--------|-------------|
| `503`  | LLM queue service unavailable |

---

### Get Task Status

```
GET /api/v1/llm/tasks/{task_id}
```

Get the status of a specific LLM task by its ID.

```bash
curl http://localhost/api/v1/llm/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string (path) | **Yes** | UUID of the task to inspect |

**Response** `200 OK` -- [LLMTaskStatusResponse](#llmtaskstatusresponse)

```json
{
  "data": {
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "queue": "llm",
    "operation": "chat_completion",
    "status": "failed",
    "priority": 100,
    "created_at": "2026-03-09T14:30:00.000000+00:00",
    "started_at": "2026-03-09T14:30:01.500000+00:00",
    "completed_at": "2026-03-09T14:30:09.200000+00:00",
    "data": {"messages": ["…"], "task_type": "CHAT"},
    "metadata": {"source": "interactive_chat"},
    "attempts": 1,
    "payload_version": 1,
    "error": "Task failed",
    "error_type": "LLMServiceError"
  }
}
```

`completed_at`, `error` and `error_type` appear only on terminal tasks; `error` is always the fixed string `"Task failed"` (the detail is in `error_type` and the logs). There is no `result_ttl` field.

**Task statuses:** `queued`, `running`, `retried`, `completed`, `failed`, `cancelled`

**Errors:**

| Status | Description |
|--------|-------------|
| `404`  | Task not found |
| `503`  | LLM queue service unavailable |

---

### Cancel Task

```
DELETE /api/v1/llm/tasks/{task_id}
```

Cancel a specific queued or running LLM task.

```bash
curl -X DELETE http://localhost/api/v1/llm/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string (path) | **Yes** | UUID of the task to cancel |

**Response** `204 No Content` -- empty body.

**Errors:**

| Status | Description |
|--------|-------------|
| `400`  | Task not found. Cancelling a task that has already finished (`completed`, `failed`, `cancelled`) is a no-op and returns `204`. |
| `503`  | LLM queue service unavailable |

---

### Cancel All Tasks

```
DELETE /api/v1/llm/tasks
```

Bulk cancel all queued and running LLM tasks.

```bash
curl -X DELETE http://localhost/api/v1/llm/tasks
```

**Response** `200 OK` -- [CancelAllTasksResponse](#cancelalltasksresponse)

```json
{
  "data": {
    "cancelled": 5,
    "message": "Task cancellation requested for LLM queue"
  }
}
```

**Errors:**

| Status | Description |
|--------|-------------|
| `503`  | LLM queue service unavailable |

---

## Diagnostics

### Clear Semaphore

```
DELETE /api/v1/llm/semaphore
```

Clear all waiting tasks from the LLM priority semaphore queues. Use this when Valkey queues have been cleared but the semaphore still has orphaned waiters that will never complete.

:::warning

This can cause deadlock if workers are actively waiting. Only use when Valkey queues have been cleared and no workers are actively processing. If unsure, restart the backend instead.

:::

```bash
curl -X DELETE http://localhost/api/v1/llm/semaphore
```

**Response** `200 OK` -- [ClearSemaphoreResponse](#clearsemaphoreresponse)

```json
{
  "data": {
    "high_priority_cleared": 0,
    "low_priority_cleared": 3,
    "total_cleared": 3
  }
}
```

**Errors:**

| Status | Description |
|--------|-------------|
| `503`  | LLM queue service unavailable |

---

## Response Models

### LLMStatsResponse

| Field | Type | Description |
|-------|------|-------------|
| `data.queues` | array | Per-queue statistics (queue name, depth, workers, token usage, cost) |
| `data.total_queued` | integer | Total tasks waiting across all LLM queues |
| `data.total_cost_usd` | float | Cumulative estimated cost in USD |
| `data.total_input_tokens` | integer | Cumulative input tokens processed |
| `data.total_output_tokens` | integer | Cumulative output tokens generated |
| `data.total_tokens` | integer | Sum of input and output tokens |
| `data.estimated_completion_time_seconds` | integer | Estimated seconds to drain the LLM queue |
| `data.estimated_completion_time_human` | string | Human-readable estimate (e.g., `"2m 30s"`) |
| `data.estimated_completion_times_human` | object | Per-queue-type estimates (`llm`, `operations`) |
| `data.semaphore_stats` | object | Real-time semaphore state (active slots, waiting counts, averages) |

### LLMTasksResponse

| Field | Type | Description |
|-------|------|-------------|
| `data` | array | List of active task objects (queued and running only) |

### LLMTaskStatusResponse

| Field | Type | Description |
|-------|------|-------------|
| `data` | object | Full task record: `task_id`, `queue`, `operation`, `status`, `priority` (int), `created_at`, `metadata` (object), `data` (object — the request payload), `attempts` (int), `payload_version` (int); plus `started_at`, `completed_at`, `error`, `error_type` when set |

### CancelAllTasksResponse

| Field | Type | Description |
|-------|------|-------------|
| `data.cancelled` | integer | Number of tasks that were cancelled |
| `data.message` | string | Human-readable result message |

### ClearSemaphoreResponse

| Field | Type | Description |
|-------|------|-------------|
| `data.high_priority_cleared` | integer | Number of high-priority waiters cleared |
| `data.low_priority_cleared` | integer | Number of low-priority waiters cleared |
| `data.total_cleared` | integer | Total waiters cleared |
