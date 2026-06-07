---
title: Tools API
description: Manage system tools and user tool instances — list available workflow tools, create custom configurations with parameters and tags for use in automation workflows.
---

# Tools

Manage system tools and user-configured tool instances. System tools are built-in capabilities available to all users. User tools are personalized configurations of system tools with custom parameters and tags.

---

## System Tools

### List System Tools

```
GET /api/v1/tools/system
```

Returns all system tools with optional filters. The response contains summary data only -- use the detail endpoint for full input/output schemas.

```bash
curl http://localhost:8080/api/v1/tools/system?category=extraction&is_active=true
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `category` | string | No | Filter by category |
| `is_active` | bool | No | Filter by active flag |

**Response** `200 OK`

```json
[
  {
    "id": "extract_entities",
    "category": "extraction",
    "name": "Entity Extraction",
    "description": "Extract entities and relationships from source documents",
    "version": "1.0.0",
    "is_active": true,
    "created_at": "2026-03-01T12:00:00",
    "updated_at": "2026-03-01T12:00:00"
  }
]
```

:::note

The list endpoint returns `SystemToolSummaryResponse` objects which exclude `input_schema` and `output_schema` for performance. Fetch a single tool to get the full schemas.

:::

### Get System Tool

```
GET /api/v1/tools/system/{tool_id}
```

Returns full details for a specific system tool, including input and output JSON schemas.

```bash
curl http://localhost:8080/api/v1/tools/system/extract_entities
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_id` | string (path) | Yes | System tool ID |

**Response** `200 OK`

```json
{
  "id": "extract_entities",
  "category": "extraction",
  "name": "Entity Extraction",
  "description": "Extract entities and relationships from source documents",
  "input_schema": {
    "type": "object",
    "properties": {
      "source_id": {"type": "string", "description": "Source document ID"},
      "depth": {"type": "string", "enum": ["shallow", "full"], "default": "full"}
    },
    "required": ["source_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "entities_extracted": {"type": "integer"},
      "relationships_extracted": {"type": "integer"}
    }
  },
  "version": "1.0.0",
  "is_active": true,
  "created_at": "2026-03-01T12:00:00",
  "updated_at": "2026-03-01T12:00:00"
}
```

**Errors:** `404` if the system tool does not exist.

---

## User Tools

### List User Tools

```
GET /api/v1/tools
```

Returns user-configured tool instances with optional filters.

Returns all tool instances (single-user deployment).

```bash
curl http://localhost:8080/api/v1/tools?system_tool_id=extract_entities&is_active=true
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `system_tool_id` | string | No | Filter by parent system tool ID |
| `is_active` | bool | No | Filter by active flag |
| `page` | int | No | Page number (1-based) |
| `page_size` | int | No | Items per page |

**Response** `200 OK`

This endpoint returns a `PaginatedUserToolsResponse` envelope (`data` + `pagination`), unlike [List System Tools](#list-system-tools) which returns a bare array.

```json
{
  "data": [
    {
      "id": "ut_abc123",
      "name": "Deep Extraction",
      "description": "Full-depth entity extraction with custom domain",
      "system_tool_id": "extract_entities",
      "configuration": {
        "depth": "full",
        "domain": "biomedical"
      },
      "tags": ["extraction", "biomedical"],
      "is_active": true,
      "created_by": null,
      "created_at": "2026-03-05T10:00:00",
      "updated_at": "2026-03-05T10:00:00",
      "system_tool": {
        "id": "extract_entities",
        "category": "extraction",
        "name": "Entity Extraction",
        "description": "Extract entities and relationships from source documents",
        "input_schema": {
          "type": "object",
          "properties": {
            "source_id": {"type": "string"},
            "depth": {"type": "string"}
          },
          "required": ["source_id"]
        },
        "output_schema": {
          "type": "object",
          "properties": {
            "entities_extracted": {"type": "integer"},
            "relationships_extracted": {"type": "integer"}
          }
        },
        "version": "1.0.0",
        "is_active": true,
        "created_at": "2026-03-01T12:00:00",
        "updated_at": "2026-03-01T12:00:00"
      }
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

### Create User Tool

```
POST /api/v1/tools
```

Creates a new user tool as a configured instance of a system tool.

The local operator owns all tools (single-user deployment).

```bash
curl -X POST http://localhost:8080/api/v1/tools \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Deep Extraction",
    "description": "Full-depth entity extraction with custom domain",
    "system_tool_id": "extract_entities",
    "configuration": {
      "depth": "full",
      "domain": "biomedical"
    },
    "tags": ["extraction", "biomedical"],
    "is_active": true
  }'
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | -- | Tool name |
| `description` | string | No | `null` | Human-readable description |
| `system_tool_id` | string | Yes | -- | ID of the parent system tool |
| `configuration` | object | Yes | -- | Tool-specific configuration parameters |
| `tags` | string[] | No | `null` | Searchable tags |
| `is_active` | bool | No | `true` | Whether the tool is active |

**Response** `201 Created` -- returns the full `UserToolResponse` object (see List User Tools for shape).

### Get User Tool

```
GET /api/v1/tools/{tool_id}
```

Returns a single user tool by ID, including the nested system tool details.

Returns the tool by ID. Returns `404` if no tool with that ID exists.

```bash
curl http://localhost:8080/api/v1/tools/ut_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_id` | string (path) | Yes | User tool ID |

**Response** `200 OK` -- returns a single `UserToolResponse` (see List User Tools for shape).

**Errors:** `404` if the user tool does not exist.

### Update User Tool

```
PATCH /api/v1/tools/{tool_id}
```

Partial update -- only include the fields you want to change.

The local operator can update any tool (single-user deployment).

```bash
curl -X PATCH http://localhost:8080/api/v1/tools/ut_abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated description",
    "configuration": {
      "depth": "shallow",
      "domain": "legal"
    },
    "is_active": false
  }'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Tool name |
| `description` | string | No | Description |
| `configuration` | object | No | Configuration parameters (replaces existing) |
| `tags` | string[] | No | Tags (replaces existing) |
| `is_active` | bool | No | Active flag |

**Response** `200 OK` -- returns the updated `UserToolResponse`.

**Errors:** `404` if the user tool does not exist.

### Delete User Tool

```
DELETE /api/v1/tools/{tool_id}
```

Permanently deletes a user tool.

The local operator can delete any tool (single-user deployment).

```bash
curl -X DELETE http://localhost:8080/api/v1/tools/ut_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_id` | string (path) | Yes | User tool ID |

**Response** `204 No Content` -- empty body on success.

**Errors:** `404` if the user tool does not exist.

---

## Tool Statistics

### Get Tool Stats

```
GET /api/v1/tools/stats/{tool_type}/{tool_id}
```

Returns execution statistics for a specific tool.

```bash
curl http://localhost:8080/api/v1/tools/stats/system/extract_entities
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_type` | string (path) | Yes | `system` or `user` |
| `tool_id` | string (path) | Yes | Tool ID |

**Response** `200 OK`

```json
{
  "tool_type": "system",
  "tool_id": "extract_entities",
  "total_calls": 320,
  "successful_calls": 305,
  "failed_calls": 15,
  "avg_execution_ms": 8500,
  "last_called_at": "2026-03-09T08:30:00",
  "updated_at": "2026-03-09T08:30:13"
}
```

**Errors:** `404` if statistics are not found for the given tool type and ID.
