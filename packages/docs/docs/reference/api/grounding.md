---
title: Grounding API (MCP)
sidebar_label: Grounding (MCP)
description: Read-only grounding endpoints for MCP integrations to discover, traverse, and query knowledge nodes and relationships without mutating the graph.
---

# Grounding API (MCP Integration)

Read-only endpoints for AI agents connecting via the [Model Context Protocol](../../user-guide/mcp.md). These endpoints let external agents discover, explore, and query the knowledge graph without mutating it.

**Base URL:** `/api/v1/graph/grounding`

All grounding endpoints are **read-only** (`GET`).

See [Graph Operations](./graph.md) for maintenance endpoints (cleanup, canvas, snapshots).

:::tip[Related pages]

- [User guide: MCP Server](../../user-guide/mcp.md) — setup instructions, available tools, and configuration for Claude Desktop, Cursor, and other clients
- [CLI reference: MCP Server](../../reference/cli/mcp.md) — `chaoscypher mcp` command for stdio transport

:::

---

### Search Nodes

```
GET /api/v1/graph/grounding/nodes
```

Search and list nodes in the knowledge graph. This is the primary entry point for knowledge discovery by AI agents.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `q` | string | No | — | Search query (filters by label or property values) |
| `template_id` | string | No | — | Filter nodes by template/entity type |
| `page` | int | No | `1` | 1-based page number |
| `page_size` | int | No | settings default | Items per page (capped to server max) |

:::tip[Text search]

The `q` parameter performs a case-insensitive match against node labels and all property values. Combine with `template_id` for targeted searches.

`q` is applied in Python after the SQL page is fetched, so `pagination.total` reflects the SQL-filtered count (`template_id` only). Iterate pages until `has_next` is false to discover every match.

:::

#### Example Requests

Search for nodes by text:

```bash
curl -X GET "http://localhost/api/v1/graph/grounding/nodes?q=Einstein"
```

Filter by template type with pagination:

```bash
curl -X GET "http://localhost/api/v1/graph/grounding/nodes?template_id=person&page=1&page_size=50"
```

#### Response — `200 OK`

```json
{
  "data": [
    {
      "id": "node-abc-123",
      "template_id": "person",
      "label": "Albert Einstein",
      "properties": {
        "birth_year": 1879,
        "field": "Physics"
      },
      "position": {"x": 100.0, "y": 200.0},
      "source_id": "src-001",
      "embedding": null,
      "created_at": "2026-03-01T10:00:00",
      "updated_at": "2026-03-01T10:00:00"
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

#### Node Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique node identifier |
| `label` | string | Human-readable label/title |
| `template_id` | string | Template type this node follows |
| `properties` | object | Key-value property map |
| `position` | object or null | Graph canvas position (`x`, `y`) |
| `source_id` | string or null | Source document this node was extracted from |
| `embedding` | list[float] or null | Vector embedding for similarity search |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

#### Pagination Fields

| Field | Type | Description |
|-------|------|-------------|
| `total` | int | Total number of matching nodes (SQL-filtered) |
| `page` | int | Current page (1-based) |
| `page_size` | int | Items per page |
| `total_pages` | int | Total page count |
| `has_next` | bool | Whether another page is available |
| `has_prev` | bool | Whether a previous page is available |

---

### Get Node with Edges

```
GET /api/v1/graph/grounding/nodes/{node_id}
```

Returns a single node with all its connected edges (both incoming and outgoing). Provides full local graph context for reasoning about a specific entity.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | Yes | Node ID to retrieve |

#### Example Request

```bash
curl -X GET http://localhost/api/v1/graph/grounding/nodes/node-abc-123
```

#### Response — `200 OK`

```json
{
  "node": {
    "id": "node-abc-123",
    "template_id": "person",
    "label": "Albert Einstein",
    "properties": {
      "birth_year": 1879,
      "field": "Physics"
    },
    "position": null,
    "source_id": "src-001",
    "embedding": null,
    "created_at": "2026-03-01T10:00:00",
    "updated_at": "2026-03-01T10:00:00"
  },
  "outgoing_edges": [
    {
      "id": "edge-xyz-789",
      "template_id": "rel-worked-on",
      "source_node_id": "node-abc-123",
      "target_node_id": "node-def-456",
      "label": "worked on",
      "properties": {
        "year": 1905
      },
      "created_at": "2026-03-01T10:30:00",
      "updated_at": "2026-03-01T10:30:00"
    }
  ],
  "incoming_edges": [
    {
      "id": "edge-uvw-321",
      "template_id": "rel-mentored-by",
      "source_node_id": "node-ghi-654",
      "target_node_id": "node-abc-123",
      "label": "mentored by",
      "properties": {},
      "created_at": "2026-03-01T11:00:00",
      "updated_at": "2026-03-01T11:00:00"
    }
  ],
  "total_outgoing": 1,
  "total_incoming": 1
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `node` | Node | Full node object (see [Node Fields](#node-fields) above) |
| `outgoing_edges` | list[Edge] | Edges where this node is the source |
| `incoming_edges` | list[Edge] | Edges where this node is the target |
| `total_outgoing` | int | Count of outgoing edges |
| `total_incoming` | int | Count of incoming edges |

#### Errors

| Status | Description |
|--------|-------------|
| `404` | Node not found |

---

### List Edges

```
GET /api/v1/graph/grounding/edges
```

Search and list edges (relationships) in the knowledge graph. Useful for understanding connection patterns and graph structure.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_node_id` | string | No | — | Filter edges by source node ID |
| `target_node_id` | string | No | — | Filter edges by target node ID |
| `page` | int | No | `1` | 1-based page number |
| `page_size` | int | No | settings default | Items per page (capped to server max) |

#### Example Requests

Get all edges from a specific node:

```bash
curl -X GET "http://localhost/api/v1/graph/grounding/edges?source_node_id=node-abc-123"
```

Find a specific connection between two nodes:

```bash
curl -X GET "http://localhost/api/v1/graph/grounding/edges?source_node_id=node-abc-123&target_node_id=node-def-456"
```

Paginate through all edges:

```bash
curl -X GET "http://localhost/api/v1/graph/grounding/edges?page=3&page_size=50"
```

#### Response — `200 OK`

```json
{
  "data": [
    {
      "id": "edge-xyz-789",
      "template_id": "rel-worked-on",
      "source_node_id": "node-abc-123",
      "target_node_id": "node-def-456",
      "label": "worked on",
      "properties": {"year": 1905},
      "created_at": "2026-03-01T10:30:00",
      "updated_at": "2026-03-01T10:30:00"
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

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `data` | list[Edge] | Array of edge objects |
| `pagination` | object | Pagination envelope (see below) |

#### Pagination Fields

| Field | Type | Description |
|-------|------|-------------|
| `total` | int | Total number of matching edges |
| `page` | int | Current page (1-based) |
| `page_size` | int | Items per page |
| `total_pages` | int | Total page count |
| `has_next` | bool | Whether another page is available |
| `has_prev` | bool | Whether a previous page is available |

#### Edge Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique edge identifier |
| `template_id` | string | Relationship type template ID |
| `source_node_id` | string | Source node ID (from) |
| `target_node_id` | string | Target node ID (to) |
| `label` | string | Human-readable relationship label |
| `properties` | object | Key-value relationship properties |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

---

### Get Node Neighbors

```
GET /api/v1/graph/grounding/nodes/{node_id}/neighbors
```

Returns nodes connected to the specified node via edges. Enables graph traversal, path finding, and relationship discovery by following edges in a chosen direction.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_id` | string | Yes | Node ID to find neighbors for |

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `direction` | string | No | `both` | Edge direction to follow: `outgoing`, `incoming`, or `both` |
| `limit` | int | No | `50` (server default page size) | Maximum neighbors to return (min `1`, capped to server max) |

:::info[Direction semantics]

- **`outgoing`** -- follow edges where this node is the **source** (what this node relates to)
- **`incoming`** -- follow edges where this node is the **target** (what relates to this node)
- **`both`** -- follow edges in both directions

:::

#### Example Requests

Get all neighbors:

```bash
curl -X GET http://localhost/api/v1/graph/grounding/nodes/node-abc-123/neighbors
```

Get only outgoing neighbors with a limit:

```bash
curl -X GET "http://localhost/api/v1/graph/grounding/nodes/node-abc-123/neighbors?direction=outgoing&limit=10"
```

#### Response — `200 OK`

```json
{
  "node_id": "node-abc-123",
  "neighbors": [
    {
      "node": {
        "id": "node-def-456",
        "template_id": "concept",
        "label": "Theory of Relativity",
        "properties": {
          "year": 1905
        },
        "position": null,
        "source_id": "src-001",
        "embedding": null,
        "created_at": "2026-03-01T10:00:00",
        "updated_at": "2026-03-01T10:00:00"
      },
      "relationship_type": "worked on",
      "edge_id": "edge-xyz-789",
      "direction": "outgoing",
      "edge_properties": {
        "year": 1905
      }
    }
  ],
  "total": 1,
  "direction": "both"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | The source node ID that was queried |
| `neighbors` | list[NeighborNode] | Connected nodes with relationship context |
| `total` | int | Total number of neighbors returned |
| `direction` | string | Direction filter that was applied |

#### NeighborNode Fields

| Field | Type | Description |
|-------|------|-------------|
| `node` | Node | Full neighbor node object (see [Node Fields](#node-fields)) |
| `relationship_type` | string | Edge label describing the relationship |
| `edge_id` | string | ID of the connecting edge |
| `direction` | string | `outgoing` or `incoming` relative to the queried node |
| `edge_properties` | object | Key-value properties on the connecting edge |

#### Errors

| Status | Description |
|--------|-------------|
| `404` | Node not found |
| `400` | Invalid `direction` parameter (must be `outgoing`, `incoming`, or `both`) |
