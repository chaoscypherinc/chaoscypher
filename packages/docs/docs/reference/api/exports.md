---
title: Exports API
description: Export and import knowledge graph data as CCX v2.0 packages — async endpoints with task polling to download nodes, edges, templates, sources, and workflows.
---

# Exports

Export and import knowledge graph data using the CCX v2.0 package format.

All endpoints are prefixed with `/api/v1/exports`. Every operation is
asynchronous -- the server returns `202 Accepted` with a `task_id` you can poll
to track progress and download results.

---

## Polling Workflow

All three endpoints follow the same async pattern:

1. Call the endpoint -- receive a `task_id` in the response.
2. Poll [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task) until `status` is `"complete"`.
3. Fetch the result:
    - **Exports:** Download the `.ccx` file via [`GET /api/v1/queue/tasks/{task_id}/result`](queue.md#get-task-result).
    - **Imports:** Retrieve import statistics via the same result endpoint.

```bash
# Poll task status
curl http://localhost:8080/api/v1/queue/tasks/{task_id}
```

```json
{
  "task_id": "task_abc123",
  "status": "complete"
}
```

---

## Full Export

```
POST /api/v1/exports
```

Queue a full knowledge graph export as a `.ccx` file. Select which graph
components to include via query parameters.

```bash
curl -X POST "http://localhost:8080/api/v1/exports?include_templates=true&include_knowledge=true&include_workflows=true&include_sources=true"
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `include_templates` | bool | No | `true` | Include user-created templates |
| `include_knowledge` | bool | No | `true` | Include knowledge graph nodes and edges |
| `include_workflows` | bool | No | `true` | Include workflow definitions, steps, and triggers |
| `include_sources` | bool | No | `true` | Include document sources and metadata |
| `include_embeddings` | bool | No | `false` | Include embedding vectors (for same-model migration) |

### Export Contents

| Component | What is Included |
|-----------|-----------------|
| **Templates** | User-created node and edge templates |
| **Knowledge** | All knowledge graph nodes and edges |
| **Workflows** | Workflow definitions, steps, and triggers |
| **Sources** | Document sources with chunks and metadata |
| **Embeddings** | Embedding vectors for nodes and chunks (opt-in, excluded by default to reduce file size) |

### Response `202 Accepted` -- ExportResponse

```json
{
  "task_id": "task_abc123",
  "status": "queued",
  "message": "Graph export queued. Use /api/v1/queue/tasks/{task_id}/result to download when complete."
}
```

### Examples

Export only knowledge nodes and edges (no templates, workflows, or sources):

```bash
curl -X POST "http://localhost:8080/api/v1/exports?include_templates=false&include_workflows=false&include_sources=false"
```

---

## Import CCX

```
POST /api/v1/exports/import
```

Upload and import a `.ccx` package file. The file is sent as multipart form
data.

```bash
curl -X POST http://localhost:8080/api/v1/exports/import \
  -F "file=@my-export.ccx" \
  -F "merge=false"
```

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file` | file | **Yes** | -- | `.ccx` package file to import |
| `merge` | bool | No | `false` | Merge with existing data (`true`) or replace (`false`) |

### Merge vs Replace

| Mode | Behavior |
|------|----------|
| **Replace** (`merge=false`) | Clears existing data before importing |
| **Merge** (`merge=true`) | Adds imported data alongside existing graph data |

### Response `202 Accepted` -- ImportResponse

```json
{
  "task_id": "task_def456",
  "status": "queued",
  "message": "CCX import queued for file: my-export.ccx. Use /api/v1/queue/tasks/{task_id}/result to get results."
}
```

### Import Results

When the task completes, [`GET /api/v1/queue/tasks/{task_id}/result`](queue.md#get-task-result) returns
statistics about the import:

- Number of templates imported
- Number of nodes imported
- Number of edges imported
- Number of workflows imported
- Number of workflows imported
- Any errors or warnings

### Errors

| Status | Cause |
|--------|-------|
| `400` | Invalid CCX file format |
| `503` | Operations service unavailable |

---

## Source-Filtered Export

```
POST /api/v1/exports/by_sources
```

Queue an export containing only data related to specific sources. Useful when
you want to extract knowledge that originated from particular documents.

The list of source UUIDs is sent as a JSON request body. Component toggles are
query parameters.

```bash
curl -X POST "http://localhost:8080/api/v1/exports/by_sources?include_templates=true" \
  -H "Content-Type: application/json" \
  -d '["src_abc123", "src_def456"]'
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| (root) | `list[string]` | **Yes** | List of source UUIDs to include in the export |

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `include_templates` | bool | No | `true` | Include templates linked to or used by the specified sources |
| `include_embeddings` | bool | No | `false` | Include embedding vectors (for same-model migration) |

### What Gets Exported

- **Entities** that have citations from the specified sources
- **Edges** where both endpoints are in the entity set (referential integrity)
- **Templates** linked to the sources (via TemplateSourceAssignment) and templates used by exported entities
- **Source metadata**, chunks, citations, and tags

### Response `202 Accepted` -- ExportResponse

```json
{
  "task_id": "task_ghi789",
  "status": "queued",
  "message": "Source-filtered export queued for 2 sources. Use /api/v1/queue/tasks/{task_id}/result to download when complete."
}
```

---

## Response Models

### ExportResponse

Returned by `POST /exports` and `POST /exports/by_sources`.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique task identifier for polling |
| `status` | string | Initial status (`"queued"`) |
| `message` | string | Human-readable description with polling instructions |

### ImportResponse

Returned by `POST /exports/import`.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique task identifier for polling |
| `status` | string | Initial status (`"queued"`) |
| `message` | string | Human-readable description with polling instructions |
