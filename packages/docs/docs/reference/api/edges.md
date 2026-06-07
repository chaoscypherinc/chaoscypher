---
title: Edges API
description: REST API for managing knowledge graph edges — list, create, update, and delete typed relationships between nodes at /api/v1/edges.
---

# Edges API

Manage knowledge graph edges (relationships between nodes).

**Base URL:** `/api/v1/edges`

---

## List Edges

```
GET /api/v1/edges
```

Returns a paginated list of edges. Supports filtering by source node, source documents, and a minimal mode for large graphs.

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_node_id` | string | No | — | Filter edges originating from this node |
| `source_ids` | list[string] | No | — | Filter by source document IDs (repeat param for multiple) |
| `page` | int | No | `1` | Page number (starts at 1) |
| `page_size` | int | No | `50` | Items per page (max `1000`) |
| `minimal` | bool | No | `false` | Return only essential fields for better performance |

:::tip[Minimal mode]

When `minimal=true`, only `id`, `source_node_id`, `target_node_id`, `label`, and `template_id` are loaded. Properties are excluded for better performance with large graphs.

:::

### Example Request

```bash
curl "http://localhost:8080/api/v1/edges?source_node_id=node-abc-123"
```

Filtering by multiple source documents:

```bash
curl -X GET "http://localhost:8080/api/v1/edges?source_ids=src-001&source_ids=src-002"
```

### Response — `200 OK`

```json
{
  "data": [
    {
      "id": "edge-550e8400-e29b-41d4-a716-446655440000",
      "template_id": "rel-works-at",
      "source_node_id": "node-abc-123",
      "target_node_id": "node-def-456",
      "label": "works at",
      "properties": {
        "start_date": "2020-01",
        "role": "Senior Researcher"
      },
      "created_at": "2026-03-01T10:30:00",
      "updated_at": "2026-03-01T10:30:00"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

---

## Create Edge

```
POST /api/v1/edges
```

Creates a new edge (relationship) between two existing nodes.

### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `template_id` | string | Yes | — | Relationship type template ID |
| `source_node_id` | string | Yes | — | Source node ID (from) |
| `target_node_id` | string | Yes | — | Target node ID (to) |
| `label` | string | Yes | — | Human-readable relationship label |
| `properties` | object | No | `{}` | Relationship properties |
| `source_id` | string | No | `null` | Source document ID for filtering |

### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/edges \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "rel-works-at",
    "source_node_id": "node-abc-123",
    "target_node_id": "node-def-456",
    "label": "works at",
    "properties": {
      "start_date": "2020-01",
      "role": "Senior Researcher"
    }
  }'
```

### Response — `201 Created`

```json
{
  "id": "edge-550e8400-e29b-41d4-a716-446655440000",
  "template_id": "rel-works-at",
  "source_node_id": "node-abc-123",
  "target_node_id": "node-def-456",
  "label": "works at",
  "properties": {
    "start_date": "2020-01",
    "role": "Senior Researcher"
  },
  "created_at": "2026-03-01T10:30:00",
  "updated_at": "2026-03-01T10:30:00"
}
```

---

## Get Edge

```
GET /api/v1/edges/{edge_id}
```

Returns full details for a single edge.

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `edge_id` | string | Yes | Edge ID |

### Example Request

```bash
curl -X GET http://localhost:8080/api/v1/edges/edge-550e8400-e29b-41d4-a716-446655440000
```

### Response — `200 OK`

```json
{
  "id": "edge-550e8400-e29b-41d4-a716-446655440000",
  "template_id": "rel-works-at",
  "source_node_id": "node-abc-123",
  "target_node_id": "node-def-456",
  "label": "works at",
  "properties": {
    "start_date": "2020-01",
    "role": "Senior Researcher"
  },
  "created_at": "2026-03-01T10:30:00",
  "updated_at": "2026-03-01T10:30:00"
}
```

### Errors

| Status | Description |
|--------|-------------|
| `404` | Edge not found |

---

## Update Edge

```
PATCH /api/v1/edges/{edge_id}
```

Partially updates an existing edge. Only provided fields are modified.

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `edge_id` | string | Yes | Edge ID |

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `label` | string | No | New relationship label |
| `properties` | object | No | New properties (full replacement, not merge) |

:::warning[Properties are replaced, not merged]

Providing `properties` replaces the entire properties object. Include all desired properties in the update, not just the changed ones.

:::

### Example Request

```bash
curl -X PATCH http://localhost:8080/api/v1/edges/edge-550e8400-e29b-41d4-a716-446655440000 \
  -H "Content-Type: application/json" \
  -d '{
    "label": "employed by",
    "properties": {
      "role": "Director",
      "start_date": "2020-01"
    }
  }'
```

### Response — `200 OK`

Returns the full updated edge. Same schema as [Get Edge](#get-edge).

### Errors

| Status | Description |
|--------|-------------|
| `404` | Edge not found |

---

## Delete Edge

```
DELETE /api/v1/edges/{edge_id}
```

Permanently deletes an edge.

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `edge_id` | string | Yes | Edge ID |

### Example Request

```bash
curl -X DELETE http://localhost:8080/api/v1/edges/edge-550e8400-e29b-41d4-a716-446655440000
```

### Response — `204 No Content`

No response body.

### Errors

| Status | Description |
|--------|-------------|
| `404` | Edge not found |

---

## Batch Operations

```
POST /api/v1/edges/batch
```

Queues a batch of create, update, and delete operations for background processing on the Operations queue.

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operations` | list | Yes | List of operation objects |
| `operations[].operation` | string | Yes | `"create"`, `"update"`, or `"delete"` |
| `operations[].data` | object | Yes | Operation-specific data (see below) |

**Operation data by type:**

- **create** — Provide `template_id`, `source_node_id`, `target_node_id`, `label`, and optionally `properties`.
- **update** — Provide `id` of the edge to update, plus `label` and/or `properties`.
- **delete** — Provide `id` of the edge to delete.

### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/edges/batch \
  -H "Content-Type: application/json" \
  -d '{
    "operations": [
      {
        "operation": "create",
        "data": {
          "template_id": "rel-works-at",
          "source_node_id": "node-123",
          "target_node_id": "node-456",
          "label": "relates_to",
          "properties": {"strength": 0.8}
        }
      },
      {
        "operation": "update",
        "data": {
          "id": "edge-789",
          "label": "depends_on"
        }
      },
      {
        "operation": "delete",
        "data": {"id": "edge-012"}
      }
    ]
  }'
```

### Response — `202 Accepted`

```json
{
  "task_id": "task-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued",
  "message": "Bulk edges operation queued with 3 operations"
}
```

:::info[Tracking batch results]

Use the returned `task_id` to monitor progress:

- **Check status:** [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task)
- **Get results:** [`GET /api/v1/queue/tasks/{task_id}/result`](queue.md#get-task-result)

Operations execute in order. If one operation fails, subsequent operations may still execute. Check the task result for individual operation outcomes.

:::

---

## Response Models

### EdgeResponse

Returned by create, get, and update endpoints.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique edge identifier |
| `template_id` | string | Relationship type template ID |
| `source_node_id` | string | Source node ID |
| `target_node_id` | string | Target node ID |
| `label` | string | Human-readable relationship label |
| `properties` | object | Key-value properties |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

### PaginatedEdgesResponse

Returned by the list endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `data` | list[EdgeResponse] | Array of edge objects |
| `pagination` | object | Pagination metadata |
| `pagination.page` | int | Current page number |
| `pagination.page_size` | int | Items per page |
| `pagination.total` | int | Total number of matching edges |
| `pagination.total_pages` | int | Total number of pages |
| `pagination.has_next` | bool | True if a next page exists |
| `pagination.has_prev` | bool | True if a previous page exists |

### BulkResponse

Returned by the batch endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Task ID for tracking the background operation |
| `status` | string | Always `"queued"` on acceptance |
| `message` | string | Summary with operation count |
