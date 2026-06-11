---
title: Graph Operations API
sidebar_label: Graph operations
description: Graph maintenance endpoints for cleanup, canvas rendering, source groups, and snapshot management.
---

# Graph Operations

Graph maintenance and bulk-read endpoints. These cover cleanup, canvas rendering, source groups, and the pre-computed snapshot used by the dashboard.

**Base URL:** `/api/v1/graph`

See [Grounding API](./grounding.md) for the read-only MCP endpoints that AI agents use to query the knowledge graph.

---

## Graph Operations

### Cleanup Corrupt Nodes

```
POST /api/v1/graph/cleanup
```

Enqueues a background cleanup pass that removes corrupt nodes from the knowledge graph. Corrupt nodes are those missing required predicates (`nodeId`, `templateId`, or `label`). These typically appear as nodes with "None" values in the UI and cannot be deleted through normal operations.

The operation runs on the worker — the endpoint returns immediately with a `task_id` you can use to poll for results.

Use this endpoint to:

- Remove nodes that show as "None" in the UI
- Clean up after data import failures
- Fix graph integrity issues

#### Example Request

```bash
curl -X POST http://localhost/api/v1/graph/cleanup
```

#### Response — `202 Accepted`

```json
{
  "task_id": "task-abc-123",
  "status": "queued",
  "operation_type": "graph_cleanup",
  "message": "Reset operation queued for background execution"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | ID of the queued background task |
| `status` | string | Always `"queued"` on 202 response |
| `operation_type` | string | Always `"graph_cleanup"` |
| `message` | string | Human-readable status message |

#### Polling for Results

Poll `GET /api/v1/queue/tasks/{task_id}/result` until the task is complete. The result payload contains `{nodes_removed, edges_removed}`. See the [Queue API](./queue.md) for full polling details.

---

### Get Source Groups

```
GET /api/v1/graph/source_groups
```

Returns image-type sources with their extracted entity node IDs for graph canvas visualization. Used by the frontend to create virtual source group nodes. Only includes committed image sources that have at least one extracted entity.

#### Example Request

```bash
curl http://localhost/api/v1/graph/source_groups
```

#### Response — `200 OK`

```json
{
  "groups": [
    {
      "source_id": "abc-123",
      "title": "temple_photo.jpg",
      "source_type": "jpg",
      "filename": "temple_photo.jpg",
      "extraction_domain": "historical",
      "extraction_domain_icon": "castle",
      "entity_count": 5,
      "entity_node_ids": ["node-1", "node-2", "node-3", "node-4", "node-5"]
    }
  ]
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `groups` | array | List of source groups |
| `groups[].source_id` | string | Source UUID |
| `groups[].title` | string | Display title (filename if no title set) |
| `groups[].source_type` | string | File extension (e.g., "jpg", "png") |
| `groups[].filename` | string | Original filename |
| `groups[].extraction_domain` | string \| null | Domain the source was extracted under |
| `groups[].extraction_domain_icon` | string \| null | Icon name for the domain, enriched for canvas display |
| `groups[].entity_count` | int | Number of extracted entities |
| `groups[].entity_node_ids` | string[] | Entity node IDs in the graph |

---

### Get Canvas Data (Bulk)

```
GET /api/v1/graph/canvas
```

Bulk fetch all graph data for canvas rendering in a single request. Returns minimal node, edge, and template data optimized for the graph canvas — no properties, embeddings, or timestamps. Replaces the need for separate paginated calls to `/nodes`, `/edges`, and `/templates`.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_ids` | string[] | No | — | Filter by source document IDs |

#### Example Request

```bash
curl http://localhost/api/v1/graph/canvas
```

With source filtering:

```bash
curl "http://localhost/api/v1/graph/canvas?source_ids=src-001&source_ids=src-002"
```

#### Response — `200 OK`

```json
{
  "nodes": [
    {
      "id": "node-abc-123",
      "template_id": "person",
      "label": "Albert Einstein",
      "position": { "x": 100.0, "y": 200.0 },
      "source_id": "src-001"
    }
  ],
  "edges": [
    {
      "id": "edge-xyz-789",
      "source_node_id": "node-abc-123",
      "target_node_id": "node-def-456",
      "template_id": "rel-worked-on",
      "label": "worked on"
    }
  ],
  "templates": [
    {
      "id": "person",
      "name": "Person",
      "template_type": "node",
      "icon": "user",
      "color": "#4A90D9",
      "description": "A person entity"
    }
  ],
  "total_nodes": 1,
  "total_edges": 1,
  "truncated": false
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | array | Minimal node objects (id, template_id, label, position, source_id) |
| `edges` | array | Minimal edge objects (id, source_node_id, target_node_id, template_id, label) |
| `templates` | array | Template objects (id, name, template_type, icon, color, description) |
| `total_nodes` | int | Number of nodes returned |
| `total_edges` | int | Number of edges returned |
| `truncated` | bool | `true` if the graph exceeds canvas rendering limits |

:::info[Canvas rendering limits]

Responses are capped at `canvas_max_nodes` (default 5,000) and `canvas_max_edges` (default 15,000) to prevent browser out-of-memory errors. These limits are configurable via `settings.yaml` under `pagination.canvas_max_nodes` and `pagination.canvas_max_edges`. When a cap is hit, `truncated` is `true` in the response.

:::

---

## Graph Snapshot

Pre-computed graph breakdown used by the dashboard to show entity and relationship type distributions without scanning the full graph on every request.

### Get Graph Snapshot

```
GET /api/v1/graph/snapshot
```

Returns the latest pre-computed graph breakdown. If no snapshot exists yet, enqueues a background build and returns `204 No Content`. If the snapshot is stale (older than 1 hour or node count has drifted by more than 10%), a background rebuild is enqueued automatically while the stale data is returned immediately.

```bash
curl http://localhost/api/v1/graph/snapshot
```

**Response** `200 OK` — `GraphBreakdown` (see response model below), or `204 No Content` when no snapshot has been built yet.

Use `POST /api/v1/graph/snapshot/refresh` to trigger a manual build.

---

### Refresh Graph Snapshot

```
POST /api/v1/graph/snapshot/refresh
```

Manually enqueue a graph snapshot rebuild. The rebuild runs asynchronously on the Neuron worker.

```bash
curl -X POST http://localhost/api/v1/graph/snapshot/refresh
```

**Response** `202 Accepted`

```json
{
  "task_id": "task-abc-123",
  "status": "queued",
  "message": "Graph snapshot refresh queued"
}
```

Poll `GET /api/v1/queue/tasks/{task_id}/result` for completion.
