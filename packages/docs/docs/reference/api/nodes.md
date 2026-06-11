---
title: Nodes API
description: REST API for managing knowledge graph nodes — list, create, read, update, and delete typed entities (people, organizations, concepts) at /api/v1/nodes.
---

# Nodes API

Manage knowledge graph nodes (entities). Nodes represent the core entities in your knowledge graph -- people, organizations, concepts, locations, and any other typed objects defined by templates.

**Base path:** `/api/v1/nodes`

---

## List Nodes

Retrieve a paginated list of nodes with optional filtering, minimal mode for performance, and stats enrichment.

```
GET /api/v1/nodes
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `template_id` | string | No | — | Filter nodes by template ID |
| `source_ids` | list[string] | No | — | Filter nodes by source document IDs (repeat param for multiple) |
| `page` | integer | No | `1` | Page number (starts at 1) |
| `page_size` | integer | No | From settings | Items per page (capped at `max_page_size` from settings) |
| `minimal` | boolean | No | `false` | Load minimal fields only (id, label, template_id, position). Excludes properties and embedding for better performance with large graphs |
| `include_stats` | boolean | No | `false` | Include edge_count, citation_count, and relationship_type_count for each node. Slightly slower due to additional queries |

### Example Request

```bash
curl 'http://localhost/api/v1/nodes?include_stats=true'
```

Filter by template:

```bash
curl 'http://localhost/api/v1/nodes?template_id=tmpl-person-abc123'
```

Filter by source documents:

```bash
curl 'http://localhost/api/v1/nodes?source_ids=src-001&source_ids=src-002'
```

### Response

**Status:** `200 OK`

```json
{
  "data": [
    {
      "id": "node-a1b2c3d4",
      "template_id": "tmpl-person-abc123",
      "label": "Albert Einstein",
      "properties": { "nationality": "German-Swiss-American", "field": "Theoretical Physics" },
      "position": { "x": 120.5, "y": 340.2 },
      "edge_count": 12,
      "citation_count": 3
    }
  ],
  "pagination": { "page": 1, "page_size": 20, "total": 87 }
}
```

:::info[Stats fields]

The `edge_count`, `incoming_edge_count`, `outgoing_edge_count`, `citation_count`, and `relationship_type_count` fields are only populated when `include_stats=true`. Otherwise they return `null`.

:::

:::tip[Performance]

For large graphs (1000+ nodes), use `minimal=true` to skip loading `properties` and `embedding` fields. This significantly reduces response size and query time.

:::

---

## Create Node

Create a new node in the knowledge graph. The node is automatically indexed for search.

```
POST /api/v1/nodes
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `template_id` | string | **Yes** | Template ID (must exist) |
| `label` | string | **Yes** | Human-readable label/title |
| `properties` | object | No | Property values matching the template schema |
| `position` | object | No | Canvas position with `x` and `y` coordinates |
| `embedding` | list[float] | No | Embedding vector for semantic search |
| `source_id` | string | No | Source document ID (links node to a source) |

### Example Request

```bash
curl -X POST http://localhost/api/v1/nodes \
  -H 'Content-Type: application/json' \
  -d '{
    "template_id": "tmpl-person-abc123",
    "label": "Albert Einstein",
    "properties": {
      "nationality": "German-Swiss-American",
      "field": "Theoretical Physics"
    },
    "position": {"x": 100, "y": 200}
  }'
```

### Response

**Status:** `201 Created`

```json
{
  "id": "node-a1b2c3d4",
  "template_id": "tmpl-person-abc123",
  "label": "Albert Einstein",
  "properties": { "nationality": "German-Swiss-American", "field": "Theoretical Physics" },
  "position": { "x": 100.0, "y": 200.0 },
  "created_at": "2026-03-09T12:00:00Z"
}
```

Full response includes `embedding`, `source_id`, `updated_at`, and stat fields (all initially `null`). Same schema as [Get Node](#get-node).

:::warning[Template must exist]

If the specified `template_id` does not exist, the API returns `404 Not Found`.

:::

---

## Get Node

Retrieve a single node by ID with all fields, including properties, position, and embedding.

```
GET /api/v1/nodes/{node_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | **Yes** | Node ID |

### Example Request

```bash
curl http://localhost/api/v1/nodes/node-a1b2c3d4
```

### Response

**Status:** `200 OK`

```json
{
  "id": "node-a1b2c3d4",
  "template_id": "tmpl-person-abc123",
  "label": "Albert Einstein",
  "properties": {
    "nationality": "German-Swiss-American",
    "field": "Theoretical Physics",
    "birth_year": 1879
  },
  "position": {
    "x": 120.5,
    "y": 340.2
  },
  "embedding": [0.0123, -0.0456, 0.0789, "..."],
  "source_id": null,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-02-20T14:22:00Z",
  "edge_count": null,
  "incoming_edge_count": null,
  "outgoing_edge_count": null,
  "citation_count": null,
  "relationship_type_count": null
}
```

:::note[404 Not Found]

Returned when no node exists with the given ID.

:::

---

## Update Node

Update an existing node. Only provided fields are modified; omitted fields remain unchanged. The search index is automatically updated.

```
PATCH /api/v1/nodes/{node_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | **Yes** | Node ID |

### Request Body

All fields are optional. Only provided fields are updated.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `label` | string | No | New label |
| `properties` | object | No | New properties (full replacement) |
| `position` | object | No | New position with `x` and `y` coordinates |
| `embedding` | list[float] | No | New embedding vector |

### Example Request

```bash
curl -X PATCH http://localhost/api/v1/nodes/node-a1b2c3d4 \
  -H 'Content-Type: application/json' \
  -d '{
    "label": "Albert Einstein (1879-1955)",
    "properties": {
      "nationality": "German-Swiss-American",
      "field": "Theoretical Physics",
      "birth_year": 1879,
      "death_year": 1955
    }
  }'
```

### Response

**Status:** `200 OK`

Returns the full updated node. Same schema as [Get Node](#get-node).

:::note[Properties replacement]

The `properties` field performs a **full replacement**, not a merge. Include all desired properties in the update, not just the changed ones.

:::

---

## Update Position

Optimized endpoint for saving node layout positions without triggering event publishing. Designed for the "Save Layout" feature where many positions may be saved rapidly.

```
PATCH /api/v1/nodes/{node_id}/position
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | **Yes** | Node ID |

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `position` | object | **Yes** | Position with `x` (float) and `y` (float) coordinates |

### Example Request

```bash
curl -X PATCH http://localhost/api/v1/nodes/node-a1b2c3d4/position \
  -H 'Content-Type: application/json' \
  -d '{
    "position": {"x": 150.0, "y": 250.0}
  }'
```

### Response

**Status:** `200 OK`

Returns the updated `NodeResponse` (same schema as [Get Node](#get-node)).

```json
{
  "id": "node-a1b2c3d4",
  "template_id": "tmpl-person-abc123",
  "label": "Albert Einstein",
  "properties": {
    "nationality": "German-Swiss-American",
    "field": "Theoretical Physics"
  },
  "position": {
    "x": 150.0,
    "y": 250.0
  },
  "embedding": null,
  "source_id": null,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-03-09T15:05:00Z",
  "edge_count": null,
  "incoming_edge_count": null,
  "outgoing_edge_count": null,
  "citation_count": null,
  "relationship_type_count": null
}
```

:::info[Performance optimization]

Unlike the general [Update Node](#update-node) endpoint, this endpoint does not trigger event publishing. This avoids overwhelming the system when saving positions for many nodes during layout adjustments. The search index is still updated.

:::

---

## Delete Node

Delete a node from the knowledge graph. The node is automatically removed from the search index.

```
DELETE /api/v1/nodes/{node_id}
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | **Yes** | Node ID |

### Example Request

```bash
curl -X DELETE http://localhost/api/v1/nodes/node-a1b2c3d4
```

### Response

**Status:** `204 No Content`

No response body.

:::note[404 Not Found]

Returned when no node exists with the given ID.

:::

---

## Batch Operations

Queue multiple node operations (create, update, delete) for background processing. Operations are sent to the Operations queue and executed asynchronously.

```
POST /api/v1/nodes/batch
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operations` | list[object] | **Yes** | List of operations to perform |

Each operation object:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operation` | string | **Yes** | Operation type: `create`, `update`, or `delete` |
| `data` | object | **Yes** | Operation-specific data |

**Operation data by type:**

- **create** -- Provide `template_id`, `label`, and optionally `properties`, `position`, `embedding`
- **update** -- Provide `id` (node ID) and any fields to update (`label`, `properties`, `position`, `embedding`)
- **delete** -- Provide `id` (node ID)

### Example Request

```bash
curl -X POST http://localhost/api/v1/nodes/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "operations": [
      {
        "operation": "create",
        "data": {
          "template_id": "tmpl-person-abc123",
          "label": "Marie Curie",
          "properties": {
            "nationality": "Polish-French",
            "field": "Physics and Chemistry"
          }
        }
      },
      {
        "operation": "update",
        "data": {
          "id": "node-a1b2c3d4",
          "label": "Albert Einstein (Updated)"
        }
      },
      {
        "operation": "delete",
        "data": {
          "id": "node-x9y8z7w6"
        }
      }
    ]
  }'
```

### Response

**Status:** `202 Accepted`

```json
{
  "task_id": "task-abc123def456",
  "status": "queued",
  "message": "Bulk nodes operation queued with 3 operations"
}
```

:::info[Tracking batch results]

Use the returned `task_id` to track progress:

- **Check status:** [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task)
- **Get results:** [`GET /api/v1/queue/tasks/{task_id}/result`](queue.md#get-task-result)

:::

:::warning[Partial failures]

Operations are executed in order. If one operation fails, subsequent operations may still execute. Check the task result for individual operation outcomes.

:::

---

## Get Connections

Retrieve nodes directly connected to a given node via edges, with their relationship information and total edge counts.

```
GET /api/v1/nodes/{node_id}/connections
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | **Yes** | Node ID |

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sort_by` | string | No | `edge_count` | Sort field: `edge_count`, `label`, or `relationship` |
| `page` | integer | No | `1` | Page number (starts at 1) |
| `page_size` | integer | No | From settings | Items per page (capped at `max_page_size` from settings) |

### Example Request

```bash
curl 'http://localhost/api/v1/nodes/node-a1b2c3d4/connections?sort_by=edge_count&page=1&page_size=10'
```

### Response

**Status:** `200 OK`

```json
{
  "data": [
    {
      "id": "node-e5f6g7h8",
      "label": "Princeton University",
      "template_id": "tmpl-org-def456",
      "edge_count": 8,
      "relationship": "affiliated_with",
      "direction": "outgoing"
    },
    {
      "id": "node-i9j0k1l2",
      "label": "Theory of Relativity",
      "template_id": "tmpl-concept-ghi789",
      "edge_count": 5,
      "relationship": "authored",
      "direction": "outgoing"
    },
    {
      "id": "node-m3n4o5p6",
      "label": "Niels Bohr",
      "template_id": "tmpl-person-abc123",
      "edge_count": 3,
      "relationship": "collaborated_with",
      "direction": "incoming"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 10,
    "total": 12
  }
}
```

Each connected node includes:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Connected node ID |
| `label` | string | Connected node label |
| `template_id` | string | Template of the connected node |
| `edge_count` | integer | Total edges for this connected node (importance indicator) |
| `relationship` | string | Edge label connecting to the parent node |
| `direction` | string | `incoming` or `outgoing` relative to the queried node |

:::note[404 Not Found]

Returned when no node exists with the given ID.

:::

---

## Get Citations

Retrieve source attributions for a node -- the document chunks where this entity was mentioned or extracted from. Useful for tracing entity provenance and verifying extraction accuracy.

```
GET /api/v1/nodes/{node_id}/citations
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | **Yes** | Node ID |

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | integer | No | `1` | Page number (starts at 1) |
| `page_size` | integer | No | From settings | Items per page (capped at 100 due to chunk content size) |

### Example Request

```bash
curl 'http://localhost/api/v1/nodes/node-a1b2c3d4/citations?page=1&page_size=10'
```

### Response

**Status:** `200 OK`

```json
{
  "data": [
    {
      "id": "cit-abc123",
      "source": {
        "id": "src-doc-001",
        "title": "History of Modern Physics.pdf",
        "source_type": "pdf",
        "origin_url": null
      },
      "chunk": {
        "id": "chunk-xyz789",
        "content": "Albert Einstein published his theory of special relativity in 1905, fundamentally changing our understanding of space and time.",
        "page_number": 42,
        "section": "Chapter 3: The Revolution of 1905",
        "chunk_metadata": {
          "char_count": 124,
          "token_count": 22
        }
      },
      "confidence": 0.95,
      "extraction_method": "llm",
      "context_snippet": "...published his theory of special relativity in 1905...",
      "citation_metadata": null,
      "created_at": "2026-01-15T10:35:00Z"
    },
    {
      "id": "cit-def456",
      "source": {
        "id": "src-doc-002",
        "title": "Nobel Prize Winners",
        "source_type": "webpage",
        "origin_url": "https://example.com/nobel-physics"
      },
      "chunk": {
        "id": "chunk-uvw456",
        "content": "The 1921 Nobel Prize in Physics was awarded to Albert Einstein for his discovery of the law of the photoelectric effect.",
        "page_number": null,
        "section": "Physics Laureates",
        "chunk_metadata": null
      },
      "confidence": 0.92,
      "extraction_method": "llm",
      "context_snippet": "...Nobel Prize in Physics was awarded to Albert Einstein...",
      "citation_metadata": {
        "extraction_round": 1
      },
      "created_at": "2026-02-01T08:15:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 10,
    "total": 3
  }
}
```

Each citation contains:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Citation ID |
| `source` | object | Source document reference |
| `source.id` | string | Source document ID |
| `source.title` | string | Source document title |
| `source.source_type` | string | Document type (e.g., `pdf`, `webpage`, `text`) |
| `source.origin_url` | string or null | Original URL if the source was imported from the web |
| `chunk` | object | Text chunk reference |
| `chunk.id` | string | Chunk ID |
| `chunk.content` | string | Full text of the chunk where the entity was found |
| `chunk.page_number` | integer or null | Page number in the source document |
| `chunk.section` | string or null | Section heading in the source document |
| `chunk.chunk_metadata` | object or null | Additional chunk metadata (char count, token count, etc.) |
| `confidence` | float | Extraction confidence score (0.0 to 1.0) |
| `extraction_method` | string | How the entity was extracted (e.g., `llm`) |
| `context_snippet` | string or null | Short text snippet around the entity mention |
| `citation_metadata` | object or null | Additional citation metadata |
| `created_at` | datetime | When the citation was created |

:::info[Page size cap]

Citations are capped at 100 items per page due to the size of chunk content in each response item.

:::

:::note[404 Not Found]

Returned when no node exists with the given ID.

:::

---

## Response Schema Reference

### NodeResponse

Returned by Create, Get, Update, and Update Position endpoints.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique node identifier |
| `template_id` | string | Template type for this node |
| `label` | string | Human-readable label |
| `properties` | object | Key-value property data |
| `position` | object or null | Canvas position (`x`, `y` floats) |
| `embedding` | list[float] or null | Embedding vector for semantic search |
| `source_id` | string or null | Source document ID if this node represents a source |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last modification timestamp |
| `edge_count` | integer or null | Total edges (only with `include_stats=true`) |
| `incoming_edge_count` | integer or null | Incoming edges (only with `include_stats=true`) |
| `outgoing_edge_count` | integer or null | Outgoing edges (only with `include_stats=true`) |
| `citation_count` | integer or null | Number of citations (only with `include_stats=true`) |
| `relationship_type_count` | integer or null | Distinct relationship types (only with `include_stats=true`) |

### PaginatedNodesResponse

Wrapper for paginated node lists.

| Field | Type | Description |
|-------|------|-------------|
| `data` | list[NodeResponse] | Array of node objects |
| `pagination` | object | Pagination metadata (`page`, `page_size`, `total`) |

### ConnectionsResponse

Wrapper for node connections results.

| Field | Type | Description |
|-------|------|-------------|
| `data` | list[ConnectedNodeResponse] | Array of connected node objects |
| `pagination` | object | Pagination metadata (`page`, `page_size`, `total`) |

### CitationListResponse

Wrapper for paginated citation results.

| Field | Type | Description |
|-------|------|-------------|
| `data` | list[CitationResponse] | Array of citation objects |
| `pagination` | object | Pagination metadata (`page`, `page_size`, `total`) |

### BulkRequest / BulkResponse

Used for batch operations.

**BulkRequest:**

| Field | Type | Description |
|-------|------|-------------|
| `operations` | list[BulkOperationRequest] | Array of operations |

**BulkOperationRequest:**

| Field | Type | Description |
|-------|------|-------------|
| `operation` | string | `create`, `update`, or `delete` |
| `data` | object | Operation-specific data |

**BulkResponse:**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique task identifier for tracking |
| `status` | string | Always `queued` on acceptance |
| `message` | string | Confirmation with operation count |
