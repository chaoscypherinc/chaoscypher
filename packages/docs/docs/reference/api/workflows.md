---
title: Workflows API
description: Create and manage automation workflows — define multi-step pipelines with AI tools, execute workflows manually or via triggers, and retrieve execution history.
---

# Workflows API

Manage automation workflows -- define multi-step pipelines, configure steps with tools, execute workflows, and track execution history.

:::note[Triggers]

Trigger CRUD endpoints (`/api/v1/triggers`) are documented separately. This page covers the workflow-scoped trigger listing endpoint only.

:::

## Workflow CRUD

### List Workflows

```
GET /api/v1/workflows
```

Returns all workflows with optional filters. Paginated.

```bash
curl http://localhost/api/v1/workflows?is_active=true
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | `50` | Items per page (max 1000) |
| `category` | string | No | `null` | Filter by category |
| `is_system` | bool | No | `null` | Filter by system flag |
| `is_active` | bool | No | `null` | Filter by active flag |
| `expose_as_ai_tool` | bool | No | `null` | Filter by AI tool exposure |

**Response** `200 OK`

```json
{
  "data": [
    {
      "id": "wf_abc123",
      "database_name": "default",
      "name": "Auto-Extract Pipeline",
      "description": "Automatically extract entities from new uploads",
      "category": "extraction",
      "is_system": false,
      "is_active": true,
      "expose_as_ai_tool": false,
      "input_schema": {
        "type": "object",
        "properties": {
          "source_id": {"type": "string"}
        },
        "required": ["source_id"]
      },
      "output_schema": null,
      "allow_parallel_execution": true,
      "timeout_seconds": null,
      "max_retries": 0,
      "tags": ["extraction", "automation"],
      "icon": "robot",
      "version": "1",
      "created_by": null,
      "created_at": "2026-03-01T12:00:00",
      "updated_at": "2026-03-01T12:00:00",
      "last_executed_at": "2026-03-09T08:30:00"
    }
  ],
  "pagination": {
    "total": 1,
    "page": 1,
    "page_size": 50,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

### Create Workflow

```
POST /api/v1/workflows
```

```bash
curl -X POST http://localhost/api/v1/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Auto-Extract Pipeline",
    "description": "Automatically extract entities from new uploads",
    "category": "extraction",
    "expose_as_ai_tool": false,
    "input_schema": {
      "type": "object",
      "properties": {
        "source_id": {"type": "string"}
      },
      "required": ["source_id"]
    },
    "output_schema": null,
    "allow_parallel_execution": true,
    "timeout_seconds": null,
    "max_retries": 0,
    "tags": ["extraction"],
    "icon": "robot"
  }'
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Workflow name |
| `description` | string | No | `null` | Human-readable description |
| `category` | string | No | `null` | Grouping category |
| `expose_as_ai_tool` | bool | No | `false` | Expose workflow as an AI-callable tool |
| `input_schema` | object | Yes | -- | JSON Schema for workflow inputs |
| `output_schema` | object | No | `null` | JSON Schema for workflow outputs |
| `allow_parallel_execution` | bool | No | `true` | Allow concurrent executions |
| `timeout_seconds` | int | No | `null` | Maximum execution time |
| `max_retries` | int | No | `0` | Number of retries on failure |
| `tags` | string[] | No | `[]` | Searchable tags |
| `icon` | string | No | `null` | Icon identifier for UI |

**Response** `201 Created` -- returns the full `WorkflowResponse` object.

### Get Workflow

```
GET /api/v1/workflows/{workflow_id}
```

```bash
curl http://localhost/api/v1/workflows/wf_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `workflow_id` | string (path) | Yes | Workflow ID |

**Response** `200 OK` -- returns a single `WorkflowResponse`.

**Errors:** `404` if the workflow does not exist.

### Update Workflow

```
PATCH /api/v1/workflows/{workflow_id}
```

Partial update -- only include the fields you want to change. System workflows cannot be updated.

```bash
curl -X PATCH http://localhost/api/v1/workflows/wf_abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Renamed Pipeline",
    "is_active": false,
    "max_retries": 3
  }'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Workflow name |
| `description` | string | No | Description |
| `category` | string | No | Category |
| `is_active` | bool | No | Enable or disable the workflow |
| `expose_as_ai_tool` | bool | No | Expose as AI tool |
| `input_schema` | object | No | Input JSON Schema |
| `output_schema` | object | No | Output JSON Schema |
| `allow_parallel_execution` | bool | No | Allow concurrent executions |
| `timeout_seconds` | int | No | Timeout in seconds |
| `max_retries` | int | No | Retry count |
| `tags` | string[] | No | Tags (replaces existing) |
| `icon` | string | No | Icon identifier |

**Response** `200 OK` -- returns the updated `WorkflowResponse`.

**Errors:** `404` if the workflow does not exist.

### Delete Workflow

```
DELETE /api/v1/workflows/{workflow_id}
```

Permanently deletes a workflow and its steps. System workflows cannot be deleted.

```bash
curl -X DELETE http://localhost/api/v1/workflows/wf_abc123
```

**Response** `204 No Content`

**Errors:** `404` if the workflow does not exist.

### Duplicate Workflow

```
POST /api/v1/workflows/{workflow_id}/duplicate
```

Creates a copy of a workflow and all its steps. The new workflow name is `"{original_name} (imported)"` (numeric suffixes like `" (imported) (2)"` are appended on repeated duplication).

```bash
curl -X POST http://localhost/api/v1/workflows/wf_abc123/duplicate
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `workflow_id` | string (path) | Yes | Workflow ID to duplicate |

**Response** `201 Created`

```json
{
  "workflow_id": "wf_new456",
  "message": "Workflow 'Auto-Extract Pipeline (imported)' imported successfully with 1 steps.",
  "was_existing": false
}
```

**Errors:** `404` if the workflow does not exist.

---

## Workflow Steps

Steps define the tool invocations within a workflow. Steps execute as a
dependency **DAG**, not a strict sequence — `depends_on` governs execution
order, and independent steps run in parallel.

### Execution Model

- Steps with an empty `depends_on` start immediately and run **in parallel**.
- A step with dependencies runs only after **all** the steps listed in its
  `depends_on` have completed (AND-join). It runs exactly once, with every
  upstream result available.
- Sibling steps that share a parent fan out and run concurrently once that
  parent completes.
- If an upstream step fails hard, its dependent steps are skipped
  (fail-stop) while independent branches continue to run.
- `step_number` is display ordering only — `depends_on` governs execution.
  A workflow whose steps form a single chain via `depends_on` behaves
  exactly like a sequential pipeline.

### List Steps

```
GET /api/v1/workflows/{workflow_id}/steps
```

Returns all steps for a workflow sorted by `step_number`.

```bash
curl http://localhost/api/v1/workflows/wf_abc123/steps
```

**Response** `200 OK`

```json
[
  {
    "id": "step_001",
    "workflow_id": "wf_abc123",
    "step_number": 1,
    "name": "Extract Entities",
    "description": "Run entity extraction on the source",
    "tool_type": "system_tool",
    "tool_id": "extract_entities",
    "configuration": {
      "depth": "full",
      "domain": "auto"
    },
    "condition": null,
    "retry_on_failure": false,
    "timeout_seconds": 300,
    "depends_on": [],
    "continue_on_error": false,
    "thinking_mode": null,
    "created_at": "2026-03-01T12:00:00",
    "updated_at": "2026-03-01T12:00:00"
  }
]
```

### Create Step

```
POST /api/v1/workflows/{workflow_id}/steps
```

```bash
curl -X POST http://localhost/api/v1/workflows/wf_abc123/steps \
  -H "Content-Type: application/json" \
  -d '{
    "step_number": 1,
    "name": "Extract Entities",
    "description": "Run entity extraction on the source",
    "tool_type": "system_tool",
    "tool_id": "extract_entities",
    "configuration": {
      "depth": "full",
      "domain": "auto"
    },
    "condition": null,
    "retry_on_failure": false,
    "timeout_seconds": 300,
    "depends_on": [],
    "continue_on_error": false,
    "thinking_mode": null
  }'
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `step_number` | int | Yes | -- | Display ordering in the UI — execution order is governed by `depends_on` (see [Execution Model](#execution-model)) |
| `name` | string | Yes | -- | Step name |
| `description` | string | No | `null` | Description |
| `tool_type` | string | Yes | -- | `system_tool`, `user_tool`, or `workflow` |
| `tool_id` | string | Yes | -- | ID of the tool or sub-workflow to execute |
| `configuration` | object | Yes | -- | Parameter mappings passed to the tool |
| `condition` | object | No | `null` | Conditional execution rules |
| `retry_on_failure` | bool | No | `false` | Retry this step on failure |
| `timeout_seconds` | int | No | `null` | Per-step timeout |
| `depends_on` | string[] | No | `[]` | Step IDs that must **all** complete before this step runs (AND-join). Defines the execution DAG — see [Execution Model](#execution-model) |
| `continue_on_error` | bool | No | `false` | Continue workflow if step fails |
| `thinking_mode` | string | No | `null` | LLM thinking mode override |

**Response** `201 Created` -- returns the `WorkflowStepResponse`.

**Errors:** `404` workflow not found, `400` validation error.

### Get Step

```
GET /api/v1/workflows/{workflow_id}/steps/{step_id}
```

```bash
curl http://localhost/api/v1/workflows/wf_abc123/steps/step_001
```

**Response** `200 OK` -- returns a single `WorkflowStepResponse`.

**Errors:** `404` if the workflow or step does not exist.

### Update Step

```
PATCH /api/v1/workflows/{workflow_id}/steps/{step_id}
```

Partial update -- only include fields to change. System workflow steps cannot be modified.

```bash
curl -X PATCH http://localhost/api/v1/workflows/wf_abc123/steps/step_001 \
  -H "Content-Type: application/json" \
  -d '{
    "timeout_seconds": 600,
    "retry_on_failure": true
  }'
```

All fields from `WorkflowStepCreate` are accepted, all optional.

**Response** `200 OK` -- returns the updated `WorkflowStepResponse`.

**Errors:** `404` workflow or step not found, `400` validation error.

### Delete Step

```
DELETE /api/v1/workflows/{workflow_id}/steps/{step_id}
```

```bash
curl -X DELETE http://localhost/api/v1/workflows/wf_abc123/steps/step_001
```

**Response** `204 No Content`

**Errors:** `404` workflow or step not found.

### Reorder Steps

```
PUT /api/v1/workflows/{workflow_id}/steps/reorder
```

Updates `step_number` for all steps based on the provided ordering. The request must include every step ID belonging to the workflow.

```bash
curl -X PUT http://localhost/api/v1/workflows/wf_abc123/steps/reorder \
  -H "Content-Type: application/json" \
  -d '{
    "step_order": ["step_003", "step_001", "step_002"]
  }'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step_order` | string[] | Yes | Ordered list of all step IDs |

**Response** `200 OK` -- returns the reordered list of `WorkflowStepResponse` objects.

**Errors:** `400` if step IDs are missing or extra, `404` workflow not found.

---

## Export and Import

### Export Workflow

```
GET /api/v1/workflows/{workflow_id}/export
```

Exports a workflow with all its steps to a portable JSON format suitable for backup or sharing between databases.

```bash
curl http://localhost/api/v1/workflows/wf_abc123/export
```

**Response** `200 OK`

```json
{
  "data": {
    "version": "1.0",
    "exported_at": "2026-04-13T09:00:00.000000+00:00Z",
    "workflow": {
      "name": "Auto-Extract Pipeline",
      "description": "Automatically extract entities from new uploads",
      "category": "extraction",
      "input_schema": {"type": "object"},
      "output_schema": null,
      "tags": ["extraction"],
      "icon": "robot"
    },
    "steps": [
      {
        "step_number": 1,
        "name": "Extract Entities",
        "tool_type": "system_tool",
        "tool_id": "extract_entities",
        "configuration": {"depth": "full"}
      }
    ]
  },
  "message": "Workflow exported successfully"
}
```

**Errors:** `404` if the workflow does not exist.

### Import Workflow

```
POST /api/v1/workflows/import
```

Imports a workflow from previously exported JSON. Supports duplicate handling strategies.

```bash
curl -X POST http://localhost/api/v1/workflows/import \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_data": {
      "version": "1.0",
      "workflow": {
        "name": "Auto-Extract Pipeline",
        "description": "Imported pipeline",
        "input_schema": {"type": "object"},
        "output_schema": null,
        "tags": ["extraction"]
      },
      "steps": []
    },
    "on_duplicate": "rename",
    "new_name": null,
    "import_as_inactive": true
  }'
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `workflow_data` | object | Yes | -- | Exported workflow JSON |
| `on_duplicate` | string | No | `"fail"` | Duplicate name strategy: `fail`, `skip`, or `rename` |
| `new_name` | string | No | `null` | Override workflow name on import |
| `import_as_inactive` | bool | No | `false` | Import with `is_active=false` for testing |

**Response** `201 Created`

```json
{
  "workflow_id": "wf_new456",
  "message": "Workflow 'Auto-Extract Pipeline (imported)' imported successfully with 0 steps.",
  "was_existing": false
}
```

**Errors:** `400` if the export data is invalid (missing/incompatible `version`, missing required workflow fields — `name`, `input_schema`, `output_schema`); `409` if `on_duplicate` is `"fail"` and a workflow with the same name already exists.

---

## Workflow Triggers

### List Triggers for Workflow

```
GET /api/v1/workflows/{workflow_id}/triggers
```

Returns all triggers configured to fire this workflow.

```bash
curl http://localhost/api/v1/workflows/wf_abc123/triggers
```

**Response** `200 OK`

```json
[
  {
    "id": "trg_001",
    "name": "On Upload Complete",
    "event_source": "source.uploaded",
    "workflow_id": "wf_abc123",
    "enabled": true,
    "priority": 0,
    "created_at": "2026-03-01T12:00:00",
    "updated_at": "2026-03-01T12:00:00"
  }
]
```

**Errors:** `404` if the workflow does not exist.

---

## Workflow Executions

### Execute Workflow

```
POST /api/v1/workflows/{workflow_id}/executions
```

Queues a workflow for asynchronous execution. Returns immediately with an execution ID that you can use to poll for status.

```bash
curl -X POST http://localhost/api/v1/workflows/wf_abc123/executions \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {
      "source_id": "src_789"
    },
    "triggered_by": "manual"
  }'
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `inputs` | object | No | `{}` | Input values matching the workflow's `input_schema` |
| `triggered_by` | string | No | `"manual"` | Origin of the execution (e.g., `manual`, `trigger`, `api`) |

**Response** `202 Accepted`

```json
{
  "execution_id": "exec_xyz789",
  "status": "queued",
  "message": "Workflow execution queued successfully"
}
```

### List Executions

```
GET /api/v1/workflows/{workflow_id}/executions
```

Paginated execution history for a workflow.

```bash
curl "http://localhost/api/v1/workflows/wf_abc123/executions?status=completed&page=1&page_size=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | `50` | Items per page (max 1000) |
| `status` | string | No | `null` | Filter: `pending`, `running`, `completed`, `failed`, `cancelled` |

**Response** `200 OK`

```json
{
  "data": [
    {
      "id": "exec_xyz789",
      "workflow_id": "wf_abc123",
      "status": "completed",
      "triggered_by": "manual",
      "duration_ms": 12500,
      "created_at": "2026-03-09T08:30:00",
      "completed_at": "2026-03-09T08:30:13"
    }
  ],
  "pagination": {
    "total": 1,
    "page": 1,
    "page_size": 50,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

Each item in `data` contains the full execution object (see [Get Execution Details](#get-execution-details) for the complete schema), excluding `step_executions`.

### Get Execution Details

```
GET /api/v1/workflows/{workflow_id}/executions/{execution_id}
```

Returns full execution details including individual step execution results.

```bash
curl http://localhost/api/v1/workflows/wf_abc123/executions/exec_xyz789
```

**Response** `200 OK`

```json
{
  "id": "exec_xyz789",
  "workflow_id": "wf_abc123",
  "triggered_by": "manual",
  "trigger_id": null,
  "parent_execution_id": null,
  "inputs": {"source_id": "src_789"},
  "outputs": {"entities_extracted": 42},
  "status": "completed",
  "current_step_id": null,
  "failed_step_id": null,
  "error_message": null,
  "duration_ms": 12500,
  "created_at": "2026-03-09T08:30:00",
  "started_at": "2026-03-09T08:30:01",
  "completed_at": "2026-03-09T08:30:13",
  "step_executions": [
    {
      "step_id": "step_001",
      "step_name": "Extract Entities",
      "status": "completed",
      "started_at": "2026-03-09T08:30:01",
      "completed_at": "2026-03-09T08:30:13",
      "outputs": {"entities_extracted": 42},
      "error_message": null
    }
  ]
}
```

**Errors:** `404` if the workflow or execution does not exist.

### Cancel Execution

```
POST /api/v1/workflows/{workflow_id}/executions/{execution_id}/cancel
```

Gracefully cancels a running or queued execution.

```bash
curl -X POST http://localhost/api/v1/workflows/wf_abc123/executions/exec_xyz789/cancel
```

**Response** `200 OK`

```json
{
  "execution_id": "exec_xyz789",
  "status": "cancelled",
  "message": "Execution cancelled"
}
```

**Errors:** `404` if not found, `400` if the execution has already completed, failed, or been cancelled.

---

## Workflow Stats

### Per-Workflow Stats

```
GET /api/v1/workflows/{workflow_id}/stats
```

Aggregate execution statistics for a single workflow.

```bash
curl http://localhost/api/v1/workflows/wf_abc123/stats
```

**Response** `200 OK`

```json
{
  "workflow_id": "wf_abc123",
  "total_executions": 150,
  "successful_executions": 142,
  "failed_executions": 6,
  "cancelled_executions": 2,
  "success_rate": 0.9467,
  "avg_duration_ms": 12500,
  "min_duration_ms": 3200,
  "max_duration_ms": 45000,
  "last_execution_at": "2026-03-09T08:30:00",
  "last_success_at": "2026-03-09T08:30:00",
  "last_failure_at": "2026-03-08T14:12:00",
  "updated_at": "2026-03-09T08:30:13"
}
```

**Errors:** `404` if the workflow does not exist.

### Global Stats

```
GET /api/v1/workflows/stats
```

Aggregated statistics across all workflows in the current database.

```bash
curl http://localhost/api/v1/workflows/stats
```

**Response** `200 OK`

```json
{
  "total_workflows": 12,
  "active_workflows": 10,
  "inactive_workflows": 2,
  "total_executions": 1450,
  "successful_executions": 1380,
  "failed_executions": 55,
  "cancelled_executions": 15,
  "success_rate": 0.9517
}
```

---

## Execution Flow Example

A typical execute-poll-result workflow:

**1. Start execution**

```bash
curl -X POST http://localhost/api/v1/workflows/wf_abc123/executions \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"source_id": "src_789"}}'
```

Response (`202 Accepted`):

```json
{
  "execution_id": "exec_xyz789",
  "status": "queued",
  "message": "Workflow execution queued successfully"
}
```

**2. Poll for status**

```bash
curl http://localhost/api/v1/workflows/wf_abc123/executions/exec_xyz789
```

While running, the `status` field will be `"running"` and `current_step_id` will indicate which step is active.

**3. Check final result**

Once `status` is `"completed"` or `"failed"`, the response includes:

- `outputs` -- the final workflow outputs (on success)
- `error_message` and `failed_step_id` -- diagnostic info (on failure)
- `duration_ms` -- total wall-clock time
- `step_executions` -- per-step breakdown
