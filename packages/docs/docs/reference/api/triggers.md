---
title: Triggers API
description: Create and manage event triggers that automatically fire a linked workflow when a system event matches — automate extraction, embeddings, and graph maintenance.
---

# Triggers

Event triggers watch for system events and automatically execute a linked workflow when conditions match. Use triggers to automate repetitive tasks such as generating embeddings whenever a new node is created in the knowledge graph.

---

## List Triggers

```
GET /api/v1/triggers
```

Returns all triggers with optional filters. The response contains summary data only -- use the detail endpoint for full trigger configuration including `filters` and `workflow_inputs`.

```bash
curl http://localhost:8080/api/v1/triggers?event_source=node.create&enabled=true
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | `50` | Items per page (max 1000) |
| `event_source` | string | No | `null` | Filter by event source (e.g. `node.create`) |
| `enabled` | bool | No | `null` | Filter by enabled flag |

**Response** `200 OK`

```json
{
  "data": [
    {
      "id": "trg_abc123",
      "name": "Auto-Embed on Node Create",
      "event_source": "node.create",
      "workflow_id": "system_workflow_generate_embeddings_v1",
      "enabled": true,
      "priority": 0,
      "created_at": "2026-03-01T12:00:00",
      "updated_at": "2026-03-01T12:00:00"
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

:::note

The list endpoint returns `TriggerSummaryResponse` objects which exclude `filters` and `workflow_inputs` for performance. Fetch a single trigger to get the full configuration.

:::

---

## Get Trigger Stats

```
GET /api/v1/triggers/{trigger_id}/stats
```

Returns aggregate execution statistics for a single trigger, computed from the persisted execution history.

```bash
curl http://localhost:8080/api/v1/triggers/trg_abc123/stats
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `trigger_id` | string (path) | Yes | Trigger ID |

**Response** `200 OK`

```json
{
  "total_executions": 42,
  "successful_executions": 40,
  "failed_executions": 2,
  "success_rate": 0.9524,
  "average_duration_ms": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_executions` | int | Total number of times this trigger has fired |
| `successful_executions` | int | Executions that completed successfully |
| `failed_executions` | int | Executions that failed |
| `success_rate` | float | Ratio of successful to total executions |
| `average_duration_ms` | int | Average execution duration in ms |

:::note

`average_duration_ms` always returns `0` in this release — execution-duration tracking is planned but not yet persisted.

:::

**Errors:** `404` if the trigger does not exist.

---

## Create Trigger

```
POST /api/v1/triggers
```

Creates a new event trigger linked to a workflow.

The local operator owns all triggers (single-user deployment).

```bash
curl -X POST http://localhost:8080/api/v1/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Auto-Embed on Node Create",
    "event_source": "node.create",
    "filters": {},
    "workflow_id": "system_workflow_generate_embeddings_v1",
    "workflow_inputs": null,
    "enabled": true,
    "priority": 0
  }'
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Human-readable trigger name |
| `event_source` | string | Yes | -- | Event source to listen for (see [Event Sources](#event-sources)) |
| `filters` | object | Yes | -- | Filter criteria applied to event data before firing |
| `workflow_id` | string | Yes | -- | ID of the workflow to execute when the trigger fires |
| `workflow_inputs` | object | No | `null` | Static inputs passed to the workflow on each execution |
| `enabled` | bool | No | `true` | Whether the trigger is active |
| `priority` | int | No | `0` | Execution priority (lower values execute first) |

**Response** `201 Created`

```json
{
  "id": "trg_abc123",
  "name": "Auto-Embed on Node Create",
  "event_source": "node.create",
  "filters": {},
  "workflow_id": "system_workflow_generate_embeddings_v1",
  "workflow_inputs": null,
  "enabled": true,
  "priority": 0,
  "created_at": "2026-03-01T12:00:00",
  "updated_at": "2026-03-01T12:00:00"
}
```

---

## Get Trigger

```
GET /api/v1/triggers/{trigger_id}
```

Returns full details for a specific trigger, including `filters` and `workflow_inputs`.

Returns the trigger by ID. Returns `404` if no trigger with that ID exists.

```bash
curl http://localhost:8080/api/v1/triggers/trg_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `trigger_id` | string (path) | Yes | Trigger ID |

**Response** `200 OK`

```json
{
  "id": "trg_abc123",
  "name": "Auto-Embed on Node Create",
  "event_source": "node.create",
  "filters": {},
  "workflow_id": "system_workflow_generate_embeddings_v1",
  "workflow_inputs": null,
  "enabled": true,
  "priority": 0,
  "created_at": "2026-03-01T12:00:00",
  "updated_at": "2026-03-01T12:00:00"
}
```

**Errors:** `404` if the trigger does not exist.

---

## Update Trigger

```
PATCH /api/v1/triggers/{trigger_id}
```

Partial update -- only include the fields you want to change.

The local operator can update any trigger (single-user deployment).

```bash
curl -X PATCH http://localhost:8080/api/v1/triggers/trg_abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": false,
    "priority": 10
  }'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Trigger name |
| `event_source` | string | No | Event source to listen for |
| `filters` | object | No | Filter criteria (replaces existing) |
| `workflow_id` | string | No | Linked workflow ID |
| `workflow_inputs` | object | No | Static workflow inputs (replaces existing) |
| `enabled` | bool | No | Active flag |
| `priority` | int | No | Execution priority |

**Response** `200 OK` -- returns the updated `TriggerResponse`.

```json
{
  "id": "trg_abc123",
  "name": "Auto-Embed on Node Create",
  "event_source": "node.create",
  "filters": {},
  "workflow_id": "system_workflow_generate_embeddings_v1",
  "workflow_inputs": null,
  "enabled": false,
  "priority": 10,
  "created_at": "2026-03-01T12:00:00",
  "updated_at": "2026-03-09T14:30:00"
}
```

**Errors:** `404` if the trigger does not exist.

---

## Delete Trigger

```
DELETE /api/v1/triggers/{trigger_id}
```

Permanently deletes a trigger.

The local operator can delete any trigger (single-user deployment).

```bash
curl -X DELETE http://localhost:8080/api/v1/triggers/trg_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `trigger_id` | string (path) | Yes | Trigger ID |

**Response** `204 No Content`

No response body.

**Errors:** `404` if the trigger does not exist.

---

## Event Sources

The `event_source` field is a free-form string following the `{entity}.{action}` convention. The trigger engine matches events by exact string comparison against the `event_source` of each enabled trigger.

Built-in event sources dispatched by Chaos Cypher:

| Event Source | Dispatched When |
|--------------|-----------------|
| `node.create` | A new node is added to the knowledge graph |
| `node.update` | An existing node is modified |
| `edge.create` | A new edge (relationship) is added to the knowledge graph |

:::tip

These are the event sources used by the default seed triggers (Auto-Embed on Node Create / Update). Custom workflows and plugins can dispatch additional event sources via `publish_event_sync()`.

:::

---

## Response Models

### TriggerResponse

Returned by **Get**, **Create**, and **Update** endpoints.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique trigger ID |
| `name` | string | Human-readable name |
| `event_source` | string | Event source the trigger listens for |
| `filters` | object | Filter criteria applied to event data |
| `workflow_id` | string | ID of the linked workflow |
| `workflow_inputs` | object \| null | Static inputs passed to the workflow |
| `enabled` | bool | Whether the trigger is active |
| `priority` | int | Execution priority |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

### TriggerSummaryResponse

Returned by the **List** endpoint. Same as `TriggerResponse` but excludes `filters` and `workflow_inputs`.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique trigger ID |
| `name` | string | Human-readable name |
| `event_source` | string | Event source the trigger listens for |
| `workflow_id` | string | ID of the linked workflow |
| `enabled` | bool | Whether the trigger is active |
| `priority` | int | Execution priority |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
