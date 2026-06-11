---
title: Templates API
description: Manage knowledge graph templates — define entity and edge schemas, property types, validation rules, and default values for all nodes and edges.
---

# Templates

Manage knowledge graph templates. Templates define the schema for nodes and edges -- they specify which properties an entity type has, their data types, validation rules, and default values. Every node and edge in the knowledge graph is associated with a template.

**Base path:** `/api/v1/templates`

---

## List Templates

Retrieve a paginated list of templates with optional filtering by type.

```
GET /api/v1/templates
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `template_type` | string | No | -- | Filter by type: `node` or `edge` |
| `page` | integer | No | `1` | Page number (starts at 1) |
| `page_size` | integer | No | From settings | Items per page (capped at `max_page_size` from settings) |

### Example Request

```bash
curl 'http://localhost/api/v1/templates?page=1&page_size=20'
```

Filter by type:

```bash
curl 'http://localhost/api/v1/templates?template_type=node'
```

### Response

**Status:** `200 OK`

```json
{
  "data": [
    {
      "id": "tmpl-person-abc123",
      "name": "Person",
      "description": "A person entity with biographical details",
      "template_type": "node",
      "properties": [
        {
          "name": "nationality",
          "display_name": "Nationality",
          "property_type": "string",
          "required": false,
          "description": "Country of citizenship"
        }
      ],
      "is_system": false,
      "created_at": "2026-01-15T10:30:00Z",
      "updated_at": "2026-02-20T14:22:00Z"
    }
  ],
  "pagination": {
    "total": 14,
    "page": 1,
    "page_size": 20,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

Null fields (`default_value`, `enum_values`, `validation_pattern`, `allowed_node_types`) are omitted for brevity. See [PropertyDefinition](#propertydefinition) for all fields.

---

## Create Template

Create a new template definition.

```
POST /api/v1/templates
```

### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | **Yes** | -- | Template name (cannot start with `system_`) |
| `template_type` | string | **Yes** | -- | Type: `node` or `edge` |
| `description` | string | No | `null` | Human-readable description |
| `icon` | string | No | `null` | Icon identifier for visual display |
| `color` | string | No | `null` | Color hex code for visual display (e.g. `#4dabf5`) |
| `properties` | [PropertyDefinition](#propertydefinition)[] | No | `[]` | List of property definitions |
| `source_id` | string | No | `null` | Source ID for enabled filtering |

:::warning[Reserved prefix]

Template names starting with `system_` are reserved for built-in templates and will be rejected with a `400` error.

:::

### Example Request

```bash
curl -X POST 'http://localhost/api/v1/templates' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Person",
    "template_type": "node",
    "description": "A person entity with biographical details",
    "properties": [
      {
        "name": "nationality",
        "display_name": "Nationality",
        "property_type": "string",
        "required": false,
        "description": "Country of citizenship"
      }
    ]
  }'
```

See [PropertyDefinition](#propertydefinition) for additional property fields like `enum_values`, `default_value`, and `validation_pattern`.

### Response

**Status:** `201 Created`

Returns the full [TemplateResponse](#templateresponse). Example:

```json
{
  "id": "tmpl-person-abc123",
  "name": "Person",
  "description": "A person entity with biographical details",
  "template_type": "node",
  "properties": [
    {
      "name": "nationality",
      "display_name": "Nationality",
      "property_type": "string",
      "required": false,
      "description": "Country of citizenship"
    }
  ],
  "is_system": false,
  "created_at": "2026-03-09T12:00:00Z",
  "updated_at": "2026-03-09T12:00:00Z"
}
```

### Errors

| Status | Reason |
|--------|--------|
| `400` | Template name starts with `system_` |
| `422` | Validation error (missing required fields, invalid `property_type`, etc.) |

---

## Get Template

Retrieve a single template by ID.

```
GET /api/v1/templates/{template_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `template_id` | string | **Yes** | Unique template identifier |

### Example Request

```bash
curl 'http://localhost/api/v1/templates/tmpl-person-abc123'
```

### Response

**Status:** `200 OK`

```json
{
  "id": "tmpl-person-abc123",
  "name": "Person",
  "description": "A person entity with biographical details",
  "template_type": "node",
  "properties": [
    {
      "name": "nationality",
      "display_name": "Nationality",
      "property_type": "string",
      "required": false,
      "description": "Country of citizenship"
    }
  ],
  "is_system": false,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-02-20T14:22:00Z"
}
```

### Errors

| Status | Reason |
|--------|--------|
| `404` | Template not found |

---

## Update Template

Update an existing template. Only provided fields are updated -- omitted fields remain unchanged.

```
PATCH /api/v1/templates/{template_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `template_id` | string | **Yes** | Unique template identifier |

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | New template name (cannot start with `system_`) |
| `description` | string | No | New description |
| `icon` | string | No | New icon identifier |
| `color` | string | No | New color hex code |
| `properties` | [PropertyDefinition](#propertydefinition)[] | No | Replacement property definitions (replaces the entire list) |

:::note[Property replacement]

When `properties` is provided, the entire property list is replaced. To add a property, include all existing properties plus the new one.

:::

### Example Request

```bash
curl -X PATCH 'http://localhost/api/v1/templates/tmpl-person-abc123' \
  -H 'Content-Type: application/json' \
  -d '{
    "description": "A person entity with extended biographical details",
    "properties": [
      {
        "name": "nationality",
        "display_name": "Nationality",
        "property_type": "string",
        "required": false,
        "description": "Country of citizenship"
      }
    ]
  }'
```

Property structure is the same as [Create Template](#create-template). When `properties` is provided, the entire list is replaced.

### Response

**Status:** `200 OK`

Returns the full updated [TemplateResponse](#templateresponse).

```json
{
  "id": "tmpl-person-abc123",
  "name": "Person",
  "description": "A person entity with extended biographical details",
  "template_type": "node",
  "properties": [
    {
      "name": "nationality",
      "display_name": "Nationality",
      "property_type": "string",
      "required": false,
      "description": "Country of citizenship"
    }
  ],
  "is_system": false,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-03-09T12:15:00Z"
}
```

### Errors

| Status | Reason |
|--------|--------|
| `400` | Template name starts with `system_` |
| `404` | Template not found |
| `422` | Validation error |

---

## Delete Template

Delete a template. By default, deletion is blocked if any nodes or edges reference the template.

```
DELETE /api/v1/templates/{template_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `template_id` | string | **Yes** | Unique template identifier |

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `force` | boolean | No | `false` | Delete even if nodes or edges are using this template |

### Example Request

```bash
curl -X DELETE 'http://localhost/api/v1/templates/tmpl-person-abc123'
```

Force delete a template that is in use:

```bash
curl -X DELETE 'http://localhost/api/v1/templates/tmpl-person-abc123?force=true'
```

### Response

**Status:** `204 No Content`

No response body.

### Errors

| Status | Reason |
|--------|--------|
| `404` | Template not found |
| `409` | Template is in use by nodes or edges (use `force=true` to override) |

---

## Regenerate Template Embeddings

Queue a background job to regenerate embeddings for all templates. Embeddings enable semantic template search (e.g., searching "people" finds the "character" template).

```
POST /api/v1/templates/embeddings
```

:::tip[When to use]

- After importing new templates
- When embeddings are missing or outdated
- After changing the embedding model

:::

### Example Request

```bash
curl -X POST 'http://localhost/api/v1/templates/embeddings'
```

### Response

**Status:** `202 Accepted`

```json
{
  "task_id": "task-abc123def456",
  "status": "queued",
  "message": "Template embedding regeneration started"
}
```

:::info[Tracking progress]

Use [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task) to check the status of the background job.

:::

---

## Batch Operations

Queue a batch of create, update, and delete operations to be processed in the background.

```
POST /api/v1/templates/batch
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operations` | [BulkOperationRequest](#bulkoperationrequest)[] | **Yes** | List of operations to execute |

Each operation in the list has the following structure:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operation` | string | **Yes** | Operation type: `create`, `update`, or `delete` |
| `data` | object | **Yes** | Operation-specific payload (see examples below) |

### Example Request

```bash
curl -X POST 'http://localhost/api/v1/templates/batch' \
  -H 'Content-Type: application/json' \
  -d '{
    "operations": [
      {
        "operation": "create",
        "data": { "name": "Location", "template_type": "node", "description": "A geographic location" }
      },
      {
        "operation": "update",
        "data": { "id": "tmpl-person-abc123", "description": "Updated person template" }
      },
      {
        "operation": "delete",
        "data": { "id": "tmpl-old-def456", "force": true }
      }
    ]
  }'
```

Each `data` object matches the request body of the corresponding individual endpoint ([Create](#create-template), [Update](#update-template), [Delete](#delete-template)).

### Response

**Status:** `202 Accepted`

```json
{
  "task_id": "task-batch-789xyz",
  "status": "queued",
  "message": "Bulk templates operation queued with 3 operations"
}
```

:::info[Tracking results]

- Use [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task) to check operation status.
- Use [`GET /api/v1/queue/tasks/{task_id}/result`](queue.md#get-task-result) to get individual operation outcomes once completed.

:::

:::warning[Partial failures]

Operations are executed in order, but a failure in one operation does not stop subsequent operations. Check the task result for individual outcomes.

:::

---

## Reference

### TemplateResponse

The standard response object returned for all template endpoints.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique template identifier |
| `name` | string | Template name |
| `description` | string \| null | Human-readable description |
| `template_type` | string | Type: `node` or `edge` |
| `icon` | string \| null | Icon identifier for visual display (e.g. `person`, `building`, `document`) |
| `color` | string \| null | Color hex code for visual display (e.g. `#4dabf5`) |
| `properties` | [PropertyDefinition](#propertydefinition)[] | Property definitions for this template |
| `is_system` | boolean | Whether this is a built-in system template |
| `created_at` | datetime | ISO 8601 creation timestamp |
| `updated_at` | datetime | ISO 8601 last update timestamp |

### PropertyDefinition

Defines a single property within a template.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | **Yes** | -- | Property name (unique within template) |
| `display_name` | string | **Yes** | -- | Human-readable display name |
| `property_type` | [PropertyType](#propertytype) | **Yes** | -- | Data type of the property |
| `required` | boolean | No | `false` | Whether this property is required when creating nodes/edges |
| `default_value` | any | No | `null` | Default value if not provided |
| `enum_values` | string[] | No | `null` | Allowed values (only for `enum` property type) |
| `description` | string | No | `null` | Description of the property |
| `validation_pattern` | string | No | `null` | Regex pattern for validation |
| `allowed_node_types` | string[] | No | `null` | For `node_reference` and `node_reference_list` types: allowed template IDs |

### PropertyType

Supported property data types.

| Value | Description |
|-------|-------------|
| `string` | Short text value |
| `text` | Long-form text |
| `integer` | Whole number |
| `float` | Decimal number |
| `boolean` | True or false |
| `date` | Date value (ISO 8601) |
| `datetime` | Date and time value (ISO 8601) |
| `url` | URL string |
| `email` | Email address |
| `enum` | One of a predefined set of values (requires `enum_values`) |
| `json` | Arbitrary JSON object |
| `node_reference` | Reference to another node (use `allowed_node_types` to constrain) |
| `node_reference_list` | List of references to other nodes (use `allowed_node_types` to constrain) |

### BulkOperationRequest

A single operation within a batch request.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operation` | string | **Yes** | Operation type: `create`, `update`, or `delete` |
| `data` | object | **Yes** | Operation payload -- matches the request body of the corresponding individual endpoint |
