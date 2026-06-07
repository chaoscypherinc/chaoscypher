---
title: Counts API
description: Single-request endpoint that returns counts for all resource types — nodes, edges, sources, templates, and more — optimized for dashboard summaries.
---

# Counts

Retrieve resource counts across the current database in a single request. This endpoint is optimized for header navigation display and dashboard summaries.

## Endpoint

### Get Counts

```
GET /api/v1/counts
```

Returns counts of all major resource types, with system resources filtered out.

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `knowledge_nodes` | `int` | Non-system nodes (excludes workflows) |
| `links` | `int` | Total edge count |
| `templates` | `int` | User-created templates (excludes system templates) |
| `workflows` | `int` | Workflow count (nodes with `template_id='system_workflow'`) |
| `lenses` | `int` | **Deprecated.** Effectively `0` since Lenses were retired in [ADR-0001](../../architecture/adrs/0001-remove-discovery-and-lenses-features.md) (computed live from `system_lens` nodes, of which none are created post-removal). Field preserved for API backward-compatibility. |
| `sources` | `int` | Document sources (PDFs, text, CSV, etc.) |
| `awaiting_confirmation` | `int` | Sources parked in the `awaiting_confirmation` state pending domain confirmation (default `0`) |

#### Example

```bash
curl http://localhost:8080/api/v1/counts
```

```json
{
  "knowledge_nodes": 142,
  "links": 87,
  "templates": 5,
  "workflows": 3,
  "lenses": 0,
  "sources": 12,
  "awaiting_confirmation": 0
}
```
