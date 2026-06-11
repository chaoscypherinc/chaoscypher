---
title: Sources API
description: REST API for uploading, processing, tagging, and monitoring document sources — files, URLs, and archives — through the full extraction pipeline.
---

# Sources API

Manage document sources -- upload, process, tag, and monitor extraction.

All endpoints are prefixed with `/api/v1/sources` unless noted otherwise.

:::tip[Related pages]

- [User guide: Sources](../../user-guide/sources.md) — how to upload and manage sources in the UI, CLI, and Python SDK
- [Architecture: Extraction Pipeline](../../architecture/extraction-pipeline/overview.md) — how the multi-stage pipeline works internally

:::

---

## Upload & Import

### Upload Single File

```
POST /api/v1/sources
```

Upload a document via multipart form data. Returns `202 Accepted` immediately while
indexing and extraction run in the background.

```bash
curl -X POST http://localhost/api/v1/sources \
  -F "file=@document.pdf"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file` | file | **Yes** | -- | Document file to upload |
| `extract_entities` | bool | No | `true` | Run entity extraction after indexing |
| `analysis_depth` | string | No | `full` | Extraction depth: `full` or `quick` |
| `domain` | string | No | `null` | Force extraction domain (e.g. `technical`, `generic`). Auto-detected if omitted. |
| `auto_confirm` | bool | No | `false` | Bypass the domain-confirmation gate. When `false` and no `domain` is forced, the source parks at `awaiting_confirmation` after indexing until confirmed via [`POST /sources/{id}/confirmation`](#confirm-domain-extraction-gate). |
| `enable_normalization` | bool | No | auto | Normalize content on upload (encoding fixes, whitespace, OCR cleaning). Auto: on for prose files, off for structured formats (CSV, JSON, TSV, JSONL, NDJSON, XML); explicit `true`/`false` overrides. |
| `enable_vision` | bool | No | auto-detect | Enable vision processing for images in PDFs and image files. Default: auto-detect based on vision model configuration. |
| `content_filtering` | bool | No | `true` | Filter non-essential content (TOC, legal, boilerplate) from entity extraction. Filtered content remains searchable via RAG. |
| `filtering_mode` | string | No | domain default | Strictness of post-extraction filters: `unfiltered`, `minimal`, `lenient`, `balanced`, `strict`, `maximum`. Omitted = the domain's default; an explicit value overrides. See [Filtering Modes](../filtering-modes.md). |
| `skip_duplicates` | bool | No | `false` | Skip upload if identical content already exists (by SHA-256 hash) |
| `enable_direction_correction` | bool | No | `null` | When `true`, misdirected relationships are swapped to fix source/target order; when `false`, they are dropped. `null` = domain config / global default (`true`). |
| `protect_orphans` | bool | No | `null` | When `true`, orphan entities (no relationships) are kept; when `false`, dropped before commit. `null` = domain config / global default (`false`). |
| `enable_inverse_relationships` | bool | No | `null` | When `false`, inverse edges are not created during commit. `null` = global default (`true`). |
| `max_entity_degree_override` | int | No | `null` | Hard cap on relationships per entity for this source. `null` = domain / global default. |

:::info[Upload settings are persistent]

`auto_analyze`, `enable_normalization`, `enable_vision`, `content_filtering`, and `filtering_mode` are persisted on the source row at upload time. Recovery, retry, and re-extract reuse the persisted values by default — clients only re-pass them when they want to override.

:::

**Response** `202 Accepted` -- [SourceResponse](#sourceresponse)

```json
{
  "id": "src_abc123",
  "filename": "document.pdf",
  "file_type": "pdf",
  "file_size": 204800,
  "status": "pending",
  "enabled": true,
  "extraction_depth": "full",
  "created_at": "2026-03-09T12:00:00",
  "updated_at": "2026-03-09T12:00:00"
}
```

Key fields shown above. The full response includes lifecycle timestamps (`indexing_*`, `extraction_*`, `commit_*`), LLM metrics (`llm_total_calls`, `llm_total_input_tokens`, etc.), and progress fields (`current_step`, `step_description`) — all initially `null` or `0`. See [SourceResponse](#sourceresponse) for the complete schema.

:::tip[Polling for progress]

Use `GET /api/v1/sources/{id}` to poll the source status as it transitions
through `pending` -> `indexing` -> (`awaiting_confirmation` ->) `indexed` -> `extracting` -> `extracted` -> `committing` -> `committed`.

The `awaiting_confirmation` step occurs when no `domain` is forced and `auto_confirm` is `false` (the default): the source parks after indexing until its detected domain is confirmed via [Confirm Domain](#confirm-domain-extraction-gate).

:::

---

### Batch Upload

```
POST /api/v1/sources/batch
```

Upload multiple files simultaneously. Returns `202 Accepted`.

```bash
curl -X POST http://localhost/api/v1/sources/batch \
  -F "files=@doc1.pdf" \
  -F "files=@doc2.pdf"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `files` | file[] | **Yes** | -- | Multiple document files |
| `extract_entities` | bool | No | `true` | Run entity extraction after indexing |
| `analysis_depth` | string | No | `full` | Extraction depth: `full` or `quick` |
| `enable_normalization` | bool | No | auto | Normalize content on upload (auto: on for prose, off for CSV/JSON/TSV/JSONL/NDJSON/XML; explicit `true`/`false` overrides) |
| `enable_vision` | bool | No | auto-detect | Enable vision processing for images in PDFs and image files |
| `domain` | string | No | `null` | Force extraction domain |
| `auto_confirm` | bool | No | `false` | Bypass the domain-confirmation gate. When `false` and no `domain` is forced, each source parks at `awaiting_confirmation` after indexing until confirmed. |
| `content_filtering` | bool | No | `true` | Filter non-essential content (TOC, legal, boilerplate) from entity extraction. Filtered content remains searchable via RAG. |
| `filtering_mode` | string | No | domain default | Strictness of post-extraction filters (explicit value overrides the domain default). See [Filtering Modes](../filtering-modes.md). |
| `skip_duplicates` | bool | No | `false` | Skip files whose content already exists |
| `enable_direction_correction` | bool | No | `null` | Swap (`true`) or drop (`false`) misdirected relationships. `null` = domain / global default. |
| `protect_orphans` | bool | No | `null` | Keep (`true`) or drop (`false`) orphan entities. `null` = domain / global default. |
| `enable_inverse_relationships` | bool | No | `null` | When `false`, inverse edges are not created during commit. `null` = global default (`true`). |
| `max_entity_degree_override` | int | No | `null` | Hard cap on relationships per entity for these sources. `null` = domain / global default. |

**Response** `202 Accepted`

```json
{
  "uploaded": 2,
  "failed": 0,
  "files": [
    { "id": "src_abc123", "filename": "doc1.pdf", "status": "pending", "..." : "..." },
    { "id": "src_def456", "filename": "doc2.pdf", "status": "pending", "..." : "..." }
  ],
  "errors": []
}
```

Each item in `files` is a full [SourceResponse](#sourceresponse). When a file fails,
it appears in `errors` instead:

```json
{
  "uploaded": 1,
  "failed": 1,
  "files": [ { "..." : "..." } ],
  "errors": [
    { "filename": "bad.xyz", "error": "Unsupported file type" }
  ]
}
```

:::warning[Batch size limit]

Returns `400` if the number of files exceeds the configured `max_upload_files` limit.

:::

---

### Import from URL

```
POST /api/v1/sources/url
```

Fetch a web page, extract clean markdown content, and process it through the standard
file pipeline.

```bash
curl -X POST http://localhost/api/v1/sources/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | **Yes** | -- | URL to import (must start with `http://` or `https://`) |
| `extract_entities` | bool | No | `true` | Run entity extraction after indexing |
| `analysis_depth` | string | No | `full` | Extraction depth: `full` or `quick` |
| `enable_normalization` | bool | No | auto | Normalize content on upload (auto: on for prose, off for structured formats; explicit `true`/`false` overrides) |
| `enable_vision` | bool | No | `true` | Enable vision processing for images in fetched HTML / PDFs |
| `domain` | string | No | `null` | Force extraction domain |
| `auto_confirm` | bool | No | `false` | Bypass the domain-confirmation gate. When `false` and no `domain` is forced, the imported source parks at `awaiting_confirmation` after indexing until confirmed. |
| `content_filtering` | bool | No | `true` | Filter non-essential content from entity extraction. Filtered content remains searchable via RAG. |
| `filtering_mode` | string | No | domain default | Strictness of post-extraction filters (explicit value overrides the domain default). See [Filtering Modes](../filtering-modes.md). |
| `skip_duplicates` | bool | No | `false` | Skip if identical content exists |
| `enable_direction_correction` | bool | No | `null` | Swap (`true`) or drop (`false`) misdirected relationships. `null` = domain / global default. |
| `protect_orphans` | bool | No | `null` | Keep (`true`) or drop (`false`) orphan entities. `null` = domain / global default. |
| `enable_inverse_relationships` | bool | No | `null` | When `false`, inverse edges are not created during commit. `null` = global default (`true`). |
| `max_entity_degree_override` | int | No | `null` | Hard cap on relationships per entity for this source. `null` = domain / global default. |

:::note[URL fetcher Content-Type validation]

The URL fetcher validates the upstream `Content-Type` against the same allowlist used for direct file uploads (`batching.allowed_content_types`). It honors any `charset=…` parameter in the response header and routes binary responses (PDF, ZIP, DOCX, etc.) to the binary loader path so `application/pdf` URLs are no longer mishandled as HTML.

:::

**Response** `202 Accepted` -- [SourceResponse](#sourceresponse)

The response is identical in shape to the single file upload response. The `source_type`
will be `webpage` and `origin_url` will contain the imported URL.

| Status | Description |
|--------|-------------|
| `400` | Invalid URL format |
| `422` | Failed to fetch URL or content shorter than 50 characters |

---

## Source CRUD

### List Sources

```
GET /api/v1/sources
```

Paginated list of sources with optional filters. Returns
[PaginatedSourcesResponse](#paginatedsourcesresponse) containing
[SourceSummaryResponse](#sourcesummaryresponse) items.

```bash
curl "http://localhost/api/v1/sources?status=committed"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default (50) | Items per page (capped at `max_page_size`) |
| `source_type` | string | No | `null` | Filter by source type (`pdf`, `text`, `csv`, `webpage`, etc.) |
| `status` | string | No | `null` | Filter by processing status (`pending`, `indexing`, `vision_pending`, `indexed`, `awaiting_confirmation`, `extracting`, `mcp_extracting`, `extracted`, `committing`, `committed`, `error`) |
| `enabled` | string | No | `null` | Filter by enabled state: `enabled` or `disabled` |
| `search` | string | No | `null` | Search in title and origin URL |
| `tag_id` | string | No | `null` | Filter by tag ID |

**Response** `200 OK` -- [PaginatedSourcesResponse](#paginatedsourcesresponse)

```json
{
  "data": [
    {
      "id": "src_abc123",
      "filename": "research-paper.pdf",
      "file_type": "pdf",
      "file_size": 204800,
      "title": "A Research Paper",
      "status": "committed",
      "chunk_count": 42,
      "extraction_entities_count": 85,
      "extraction_relationships_count": 120,
      "cached_quality_grade": "A",
      "tags": [
        { "id": "tag_001", "name": "Research", "color": "#4dabf5" }
      ],
      "created_at": "2026-03-09T10:00:00"
    }
  ],
  "pagination": {
    "total": 1,
    "page": 1,
    "page_size": 20,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

Each item is a [SourceSummaryResponse](#sourcesummaryresponse) with additional fields including embedding info, LLM metrics, duration timings, and quality scores.

---

### Get Source

```
GET /api/v1/sources/{source_id}
```

Returns the full source detail including all lifecycle fields, LLM metrics, and
user metadata.

```bash
curl http://localhost/api/v1/sources/src_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK` -- [SourceResponse](#sourceresponse)

See the [Upload Single File](#upload-single-file) section for a full response example.

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Update Source

```
PATCH /api/v1/sources/{source_id}
```

Update mutable source fields.

```bash
curl -X PATCH http://localhost/api/v1/sources/src_abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Updated Title",
    "enabled": true,
    "user_metadata": { "category": "research" }
  }'
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `title` | string | No | New display title |
| `processing_status` | string | No | Override status (`ready` or `error`) |
| `enabled` | bool | No | Enable or disable the source |
| `user_metadata` | object | No | Arbitrary key-value metadata |

**Response** `200 OK` -- [SourceResponse](#sourceresponse)

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Delete Source

```
DELETE /api/v1/sources/{source_id}
```

Permanently deletes the source and cascades to all chunks, citations, graph nodes,
edges, templates, and search index entries.

```bash
curl -X DELETE http://localhost/api/v1/sources/src_abc123
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `204 No Content`

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

## Source Metadata

### List Extraction Domains

```
GET /api/v1/sources/domains
```

Returns available extraction domains for dropdown selection. Includes built-in
domains and any per-database custom domains.

```bash
curl http://localhost/api/v1/sources/domains
```

**Response** `200 OK`

```json
{
  "domains": [
    {
      "name": "generic",
      "description": "General-purpose entity extraction",
      "builtin": true,
      "extraction_density": "medium",
      "prompt_tokens": 1200
    },
    {
      "name": "technical",
      "description": "Technical documentation and specifications",
      "builtin": true,
      "extraction_density": "high",
      "prompt_tokens": 1800
    }
  ]
}
```

---

### Get Processing Stats

```
GET /api/v1/sources/stats
```

Aggregate processing statistics across all sources.

```bash
curl http://localhost/api/v1/sources/stats
```

**Response** `200 OK`

```json
{
  "total_files": 25,
  "by_status": {
    "committed": 20,
    "indexed": 3,
    "error": 2
  },
  "total_chunks": 1042,
  "total_entities": 850,
  "total_relationships": 1200
}
```

---

## Extraction Management

### Trigger Extraction

```
POST /api/v1/sources/{source_id}/extraction
```

Trigger manual entity extraction for a source. The source must be in `indexed` or
`extracted` status. Returns `202 Accepted` while extraction runs in the background.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/extraction \
  -H "Content-Type: application/json" \
  -d '{
    "analysis_depth": "full",
    "domain": "technical",
    "force": false
  }'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `analysis_depth` | string | No | `full` | Extraction depth: `full` or `quick` |
| `domain` | string | No | `null` | Force extraction domain. Auto-detected if omitted. |
| `filtering_mode` | string | No | persisted | Override the source's persisted `filtering_mode` for this run only. |
| `force` | bool | No | `false` | Re-extract even if extraction results already exist |

The endpoint reuses the source's persisted upload settings (`filtering_mode`, `enable_vision`, `content_filtering`) by default. Pass them in the body to override per-call without changing the row.

**Response** `202 Accepted`

```json
{
  "source_id": "src_abc123",
  "job_id": "job_xyz789",
  "status": "queued",
  "message": "Extraction started"
}
```

| Status | Description |
|--------|-------------|
| `400` | Source is not in an extractable state |
| `404` | Source not found |
| `409` | Extraction already in progress (use `force=true` to re-extract) |

---

### Get Extraction Progress

```
GET /api/v1/sources/{source_id}/extraction
```

Returns detailed extraction progress including job status, chunk-level counts,
and timing estimates.

```bash
curl http://localhost/api/v1/sources/src_abc123/extraction
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

```json
{
  "source_id": "src_abc123",
  "job_id": "job_xyz789",
  "status": "running",
  "has_extraction_job": true,
  "total_chunks": 10,
  "completed_chunks": 6,
  "failed_chunks": 0,
  "progress_percent": 60.0,
  "chunks_by_status": {
    "completed": 6,
    "running": 1,
    "queued": 3
  },
  "total_entities": 52,
  "total_relationships": 78,
  "extraction_depth": "full",
  "started_at": "2026-03-09T10:00:00",
  "completed_at": null,
  "timing": {
    "avg_duration_ms": 4200,
    "min_duration_ms": 2100,
    "max_duration_ms": 6800
  },
  "current_chunk": {
    "chunk_index": 6,
    "status": "running",
    "started_at": "2026-03-09T10:02:30"
  }
}
```

When no extraction job exists:

```json
{
  "source_id": "src_abc123",
  "status": "indexed",
  "has_extraction_job": false,
  "message": "No active extraction job for this source"
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Cancel Extraction

```
DELETE /api/v1/sources/{source_id}/extraction
```

Cancels all pending and queued extraction chunks. Already running or completed
chunks are not affected. Source status reverts to `indexed` (RAG search still
works).

```bash
curl -X DELETE http://localhost/api/v1/sources/src_abc123/extraction
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `204 No Content`

| Status | Description |
|--------|-------------|
| `404` | Source not found or no active extraction job |

---

### Confirm Domain (Extraction Gate)

Sources can pause in the `awaiting_confirmation` state so you can confirm (or
correct) the auto-detected extraction domain before the long extraction pass
runs. These two endpoints release parked sources for extraction.

#### Confirm Single Source

```
POST /api/v1/sources/{source_id}/confirmation
```

Confirm a single parked source's detected domain and any extraction option
overrides, then release it for extraction. All body fields are optional and
mirror the editable options of [Trigger Extraction](#trigger-extraction).

The endpoint is **state-aware**: a parked (`awaiting_confirmation`) source
CAS-flips to `indexed` and re-queues the extraction path; a pre-gate source
(`pending`, `indexing`, `vision_pending`, or `indexed` but not yet confirmed)
records the decision without re-queueing, and the analysis stage proceeds on its
own.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/confirmation \
  -H "Content-Type: application/json" \
  -d '{"domain": "technical"}'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `analysis_depth` | string | No | `full` | Extraction depth: `full` or `quick` |
| `domain` | string | No | `null` | Confirmed extraction domain. Defaults to the detected domain if omitted. |
| `filtering_mode` | string | No | persisted | Override the persisted `filtering_mode` for this run. |
| `content_filtering` | bool | No | `null` | Tri-state: `null` leaves the persisted value as-is; `true`/`false` overrides it. |
| `enable_direction_correction` | bool | No | `null` | Override relationship direction correction. |
| `protect_orphans` | bool | No | `null` | Override orphan-entity protection. |
| `enable_inverse_relationships` | bool | No | `null` | Override inverse-relationship generation. |
| `max_entity_degree_override` | int | No | `null` | Per-source entity degree cap (must be `> 0`). |

**Response** `202 Accepted`

```json
{
  "source_id": "src_abc123",
  "status": "indexed"
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |
| `409` | Source is past the extraction gate, already confirmed, or errored |

#### Bulk Confirm Sources

```
POST /api/v1/sources/confirmation
```

Confirm many parked sources in a single call. Each source is confirmed
independently with its detected domain and proposal options (no per-item
overrides — use the single endpoint to override). A per-item failure does not
abort the batch.

```bash
curl -X POST http://localhost/api/v1/sources/confirmation \
  -H "Content-Type: application/json" \
  -d '{"source_ids": ["src_abc123", "src_def456"]}'
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_ids` | string[] | **Yes** | IDs of the parked sources to confirm |

**Response** `202 Accepted`

```json
{
  "confirmed": 1,
  "failed": 1,
  "results": [
    { "source_id": "src_abc123", "ok": true, "error": null },
    { "source_id": "src_def456", "ok": false, "error": "Source is past the extraction gate" }
  ]
}
```

---

### Reclassify Source Domain

```
POST /api/v1/sources/{source_id}/reclassify
```

Change the extraction domain for a source and queue a new extraction pass.
Returns `202 Accepted` while the new extraction runs in the background.

For sources that are already `committed`, this endpoint atomically resets prior
graph artifacts (nodes, edges, templates) before dispatching so the new
extraction starts clean.

**When to use:** When auto-detection chose the wrong domain, or when you want to
re-run extraction under a different domain template after reviewing the initial
results. Prefer this over setting `domain` at upload time — reclassify decouples
domain selection from the upload flow.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/reclassify \
  -H "Content-Type: application/json" \
  -d '{"domain": "medical"}'
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `domain` | string | **Yes** | Domain name to use (e.g. `medical`, `legal`). See [`GET /sources/domains`](#list-extraction-domains). |

**Response** `202 Accepted`

```json
{
  "source_id": "src_abc123",
  "status": "extracting"
}
```

| Status | Description |
|--------|-------------|
| `400` | Source is not in a reclassifiable state (`indexed` or `committed` required) |
| `404` | Source not found |
| `503` | No LLM provider configured |

---

### List Extraction Tasks

```
GET /api/v1/sources/{source_id}/extraction/tasks
```

Paginated list of individual chunk extraction tasks (LLM processing groups).
Useful for debugging and analytics.

```bash
curl "http://localhost/api/v1/sources/src_abc123/extraction/tasks?page=1&page_size=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |
| `include_content` | bool | No | `false` | Include full `input_text` and `llm_response_json` (large payloads) |

**Response** `200 OK` -- [ExtractionTaskListResponse](#extractiontasklistresponse)

```json
{
  "tasks": [
    {
      "id": "task_001",
      "job_id": "job_xyz789",
      "chunk_index": 0,
      "hierarchical_group_id": "group_a",
      "small_chunk_ids": ["chunk_001", "chunk_002"],
      "status": "completed",
      "created_at": "2026-03-09T10:00:00",
      "queued_at": "2026-03-09T10:00:01",
      "started_at": "2026-03-09T10:00:05",
      "completed_at": "2026-03-09T10:00:09",
      "llm_duration_ms": 3800,
      "retry_count": 0,
      "entity_count": 8,
      "relationship_count": 12,
      "invalid_relationship_count": 1,
      "small_chunk_numbers": [1, 2],
      "input_text_length": 3200,
      "llm_response_length": 1800,
      "input_tokens": 1100,
      "output_tokens": 620,
      "context_window_available": 128000,
      "input_text": null,
      "llm_response_json": null,
      "filtering_log": null,
      "error_message": null,
      "error_type": null
    }
  ],
  "total": 10,
  "page": 1,
  "page_size": 20
}
```

:::note[Content fields]

Set `include_content=true` to populate `input_text` and `llm_response_json`.
By default only their lengths are returned for performance.

:::

---

### Get Extraction Task

```
GET /api/v1/sources/{source_id}/extraction/tasks/{task_id}
```

Returns a single extraction task with full details, including content fields.

```bash
curl http://localhost/api/v1/sources/src_abc123/extraction/tasks/task_001
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `task_id` | string (path) | **Yes** | Extraction task ID |

**Response** `200 OK` -- [ExtractionTaskResponse](#extractiontaskresponse)

Same shape as items in the task list, but with `input_text`, `llm_response_json`,
and `filtering_log` fully populated.

| Status | Description |
|--------|-------------|
| `404` | Extraction task not found |

---

### Get Extraction Task Stats

```
GET /api/v1/sources/{source_id}/extraction/stats
```

Aggregate statistics (min/avg/max) for extraction tasks, computed via SQL
aggregates without loading every row.

```bash
curl http://localhost/api/v1/sources/src_abc123/extraction/stats
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK` -- [ExtractionTaskStatsResponse](#extractiontaskstatsresponse)

```json
{
  "total_tasks": 10,
  "context_window": 128000,
  "min_input_tokens": 800,
  "max_input_tokens": 2400,
  "avg_input_tokens": 1500,
  "min_output_tokens": 300,
  "max_output_tokens": 900,
  "avg_output_tokens": 600,
  "min_total_tokens": 1100,
  "max_total_tokens": 3300,
  "avg_total_tokens": 2100,
  "min_utilization": 0.86,
  "max_utilization": 2.58,
  "avg_utilization": 1.64,
  "min_duration_ms": 2100,
  "max_duration_ms": 6800,
  "avg_duration_ms": 4200,
  "total_entities": 85,
  "avg_entities_per_task": 8.5,
  "total_relationships": 120,
  "avg_relationships_per_task": 12.0,
  "total_retries": 2,
  "max_retries_single_task": 1,
  "total_invalid_relationships": 5,
  "avg_invalid_per_task": 0.5,
  "total_entities_filtered": 3,
  "total_relationships_filtered": 7,
  "filtering_stage_summary": [
    { "stage": "exact_dedup", "total_removed": 2, "chunk_count": 2 },
    { "stage": "relationship_dedup", "total_removed": 5, "chunk_count": 3 }
  ],
  "system_prompt": "You are an entity extraction assistant...",
  "extraction_rules_template": "...",
  "entity_templates": "...",
  "relationship_templates": "...",
  "domain_guidance": "...",
  "domain_examples": "..."
}
```

| Status | Description |
|--------|-------------|
| `404` | No extraction statistics available for this source |

---

### Get Extraction Chart Data

```
GET /api/v1/sources/{source_id}/extraction/charts
```

Returns all extraction tasks with minimal fields for UI chart rendering.
No pagination -- returns all tasks at once for efficient charting.

```bash
curl http://localhost/api/v1/sources/src_abc123/extraction/charts
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

```json
[
  {
    "chunk_index": 0,
    "status": "completed",
    "retry_count": 0,
    "entity_count": 8,
    "relationship_count": 12,
    "input_text_length": 3200,
    "llm_duration_ms": 3800
  },
  {
    "chunk_index": 1,
    "status": "completed",
    "retry_count": 1,
    "entity_count": 6,
    "relationship_count": 9,
    "input_text_length": 2800,
    "llm_duration_ms": 5200
  }
]
```

---

### Get Cross-Chunk Filtering Log

```
GET /api/v1/sources/{source_id}/extraction/filteringlog
```

Returns the cross-chunk deduplication filtering log from the post-extraction
merging stage. Shows entities and relationships removed during structural
filtering, exact/semantic deduplication, and relationship deduplication.

```bash
curl http://localhost/api/v1/sources/src_abc123/extraction/filteringlog
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

```json
{
  "stages": [
    {
      "stage": "exact_dedup",
      "removed_count": 3,
      "details": ["Entity 'Python' duplicate removed", "..."]
    }
  ],
  "total_removed": 5
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found or no filtering log available |

---

### Retry Errored Source

```
POST /api/v1/sources/{source_id}/retry
```

Manually retry a source that is in `error` status. The retry target is determined
by `error_stage` on the source record — the service routes the source back to the
appropriate pipeline stage (indexing, extraction, or commit).

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/retry
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK` -- [SourceResponse](#sourceresponse)

Returns the updated source record after the retry has been queued.

| Status | Description |
|--------|-------------|
| `404` | Source not found |
| `409` | Source is not in `error` status or `error_stage` is unknown |

---

### Re-extract Source

```
POST /api/v1/sources/{source_id}/re_extract
```

Manually re-run entity extraction on a source (distinct from `retry`). The key difference:

- **Retry** preserves the cached extraction payload and re-runs only the failed stage
  (cheap — no additional LLM tokens for commit-only retries).
- **Re-extract** discards the cached payload and any previous extraction results,
  resets the source to `indexed`, and re-runs the full LLM extraction (expensive —
  costs LLM tokens).

Use this when you want to re-analyze a document after changing the extraction domain,
fixing domain-specific rules, or correcting the initial extraction output.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/re_extract
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `202 Accepted` -- [SourceResponse](#sourceresponse)

Returns the updated source record after the re-extraction job has been queued.

**Allowed source states:**

- `committed` — Atomically deletes graph artifacts (nodes, edges, templates), resets
  to `indexed`, and re-extracts.
- `error` (after post-INDEXING stage) — Resets to `indexed`, clears cached payload,
  and re-extracts.
- `indexed` / `extracted` / `extracting` / `mcp_extracting` / `committing` — Forcibly
  resets to `indexed`, clears payload, and re-extracts.

**Rejected source states:**

- `pending` / `indexing` — Returns `422`; the source has not yet produced extraction
  artifacts. Wait for indexing to complete, then retry.

:::info[Persisted settings carry over]

Re-extract reuses the source's persisted upload settings (`auto_analyze`, `enable_normalization`, `enable_vision`, `content_filtering`, `filtering_mode`) by default — what you uploaded with is what you re-extract with. Clients can override any of these per call by passing them in the request body.

`force_re_extract` also resets every quality counter on the source row back to zero and clears `vector_indexing_status` to `pending`, so the new run starts with a clean counter set.

:::

| Status | Description |
|--------|-------------|
| `404` | Source not found |
| `422` | Source is in `pending` or `indexing` state (not yet indexable) |

---

### List Source Recovery Events

```
GET /api/v1/sources/{source_id}/recovery_events
```

Returns the recovery audit trail for a source — every automatic recovery attempt, what was dispatched, and when. Backs the source detail page's recovery panel so operators can diagnose repeated failures without grepping container logs. Events are returned newest first.

```bash
curl "http://localhost/api/v1/sources/src_abc123/recovery_events?limit=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `limit` | int (query) | No | `50` | Maximum events to return (1--200) |

:::note

Recovery events use a `?limit=` cap (max 200) rather than the standard `?page=&page_size=` pagination model — events are an audit trail, not a paged resource collection.

:::

**Response** `200 OK`

```json
{
  "events": [
    {
      "id": "rev_001",
      "source_id": "src_abc123",
      "event_type": "recovery",
      "created_at": "2026-03-09T10:05:00",
      "reason": "Stalled extraction detected",
      "dispatched_operation": "OP_EXTRACT_SOURCE"
    }
  ]
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Cleanup Orphan Chunk Tasks

```
POST /api/v1/sources/cleanup/orphan_tasks
```

Triggers an immediate sweep of orphaned chunk tasks — tasks whose parent
extraction job completed or failed but whose rows were not updated. Normally
run automatically on a schedule; use this endpoint to trigger it on demand
after bulk operations or during recovery.

```bash
curl -X POST http://localhost/api/v1/sources/cleanup/orphan_tasks
```

**Response** `200 OK`

```json
{
  "deleted_count": 12,
  "retention_days": 7
}
```

| Field | Type | Description |
|-------|------|-------------|
| `deleted_count` | int | Number of orphaned task rows removed |
| `retention_days` | int | Configured orphan retention window (from `SourceRecoverySettings`) |

---

### Abort All Processing

```
DELETE /api/v1/sources/{source_id}/processing
```

Cancels all queued/running tasks (indexing or extraction) and resets the source
status appropriately.

```bash
curl -X DELETE http://localhost/api/v1/sources/src_abc123/processing
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `204 No Content`

| Status | Description |
|--------|-------------|
| `400` | Source is not in a processing state |
| `404` | Source not found |

:::info[Status after abort]

- `pending` / `indexing` -> `error` (with message "Processing/Indexing aborted by user")
- `extracting` -> `indexed` (RAG still usable)
- `committing` -> `extracted`

:::

---

## Chunks

### List Chunks

```
GET /api/v1/sources/{source_id}/chunks
```

Paginated list of document chunks for a source.

```bash
curl "http://localhost/api/v1/sources/src_abc123/chunks?page=1&page_size=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |
| `status` | string | No | `null` | Filter by chunk status |

**Response** `200 OK` -- [ChunkListResponse](#chunklistresponse)

```json
{
  "chunks": [
    {
      "id": "chunk_001",
      "source_id": "src_abc123",
      "chunk_index": 0,
      "content": "This is the first chunk of the document...",
      "page_number": 1,
      "section": "Introduction",
      "group_index": 0,
      "status": "indexed",
      "created_at": "2026-03-09T10:00:05"
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

---

### Get Chunks Batch

```
GET /api/v1/sources/{source_id}/chunks/batch
```

Fetch multiple small chunks by ID in a single batch request. This is used by the UI to display the raw text of chunks related to an extraction task.

```bash
curl "http://localhost/api/v1/sources/src_abc123/chunks/batch?ids=chunk_001,chunk_002"
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `ids` | string (query) | **Yes** | Comma-separated list of chunk IDs |

**Response** `200 OK`

```json
{
  "chunks": [
    {
      "id": "chunk_001",
      "content": "This is chunk one content...",
      "chunk_index": 0
    },
    {
      "id": "chunk_002",
      "content": "This is chunk two content...",
      "chunk_index": 1
    }
  ]
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---



### Get Chunk

```
GET /api/v1/sources/{source_id}/chunks/{chunk_id}
```

Returns a single chunk by ID.

```bash
curl http://localhost/api/v1/sources/src_abc123/chunks/chunk_001
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `chunk_id` | string (path) | **Yes** | Chunk ID |

**Response** `200 OK` -- [ChunkResponse](#chunkresponse)

```json
{
  "id": "chunk_001",
  "source_id": "src_abc123",
  "chunk_index": 0,
  "content": "This is the first chunk of the document...",
  "page_number": 1,
  "section": "Introduction",
  "group_index": 0,
  "status": "indexed",
  "created_at": "2026-03-09T10:00:05"
}
```

| Status | Description |
|--------|-------------|
| `404` | Chunk not found or does not belong to this source |

---

### Rerun Chunk

```
POST /api/v1/sources/{source_id}/chunks/{chunk_index}/rerun
```

Re-run extraction for a single chunk. Resets the chunk's extraction task to
`pending`, snapshots the prior result into the attempt history, walks the
source status back to `extracting`, and re-enqueues the chunk. Existing
committed entities are preserved (first-write-wins). Returns `202 Accepted`
while the re-run proceeds in the background.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/chunks/0/rerun
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `chunk_index` | int (path) | **Yes** | Zero-based chunk index |

**Response** `202 Accepted`

| Status | Description |
|--------|-------------|
| `404` | Source or chunk task not found |
| `409` | Chunk is not in a re-runnable state |

---

### List Chunk Attempts

```
GET /api/v1/sources/{source_id}/chunks/{chunk_index}/attempts
```

List the prior extraction attempts recorded for a chunk (each rerun snapshots
the previous result). Read-only.

```bash
curl http://localhost/api/v1/sources/src_abc123/chunks/0/attempts
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `chunk_index` | int (path) | **Yes** | Zero-based chunk index |

**Response** `200 OK` — list of attempt summaries.

| Status | Description |
|--------|-------------|
| `404` | Source or chunk not found |

---

### Get Chunk Attempt

```
GET /api/v1/sources/{source_id}/chunks/{chunk_index}/attempts/{attempt_id}
```

Fetch one prior attempt with its full extraction body. Read-only.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `chunk_index` | int (path) | **Yes** | Zero-based chunk index |
| `attempt_id` | string (path) | **Yes** | Attempt ID |

**Response** `200 OK` — the full attempt detail.

| Status | Description |
|--------|-------------|
| `404` | Source, chunk, or attempt not found |

---

## Citations

### List Citations

```
GET /api/v1/sources/{source_id}/citations
```

Paginated list of entity citations (attributions) for a source. Each citation
links an extracted entity back to the source chunk it was found in.

```bash
curl "http://localhost/api/v1/sources/src_abc123/citations?page=1&page_size=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |

**Response** `200 OK` -- [CitationListResponse](#citationlistresponse)

```json
{
  "citations": [
    {
      "id": "cit_001",
      "entity_uri": "urn:chaoscypher:node:abc123",
      "entity_label": "Python",
      "entity_type": "Programming Language",
      "source_id": "src_abc123",
      "chunk_id": "chunk_001",
      "confidence": 0.95,
      "extraction_method": "llm",
      "context_snippet": "...Python is a versatile programming language...",
      "created_at": "2026-03-09T10:05:00"
    }
  ],
  "total": 85,
  "page": 1,
  "page_size": 20
}
```

---

## Source Data Access

### Get Source Stats

```
GET /api/v1/sources/{source_id}/stats
```

Returns computed statistics for a single source.

```bash
curl http://localhost/api/v1/sources/src_abc123/stats
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

```json
{
  "chunk_count": 42,
  "citation_count": 85,
  "entity_count": 65,
  "relationship_count": 120,
  "total_content_length": 52000,
  "avg_chunk_length": 1238
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Get Source Entities

```
GET /api/v1/sources/{source_id}/entities
```

Paginated list of entities extracted from the document. Each entity includes
a computed `quality_score` (0-100).

```bash
curl "http://localhost/api/v1/sources/src_abc123/entities?page=1&page_size=20&sort_by=quality&sort_order=desc"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |
| `sort_by` | string | No | `default` | Sort field: `default`, `quality`, `confidence`, `name`, `type` |
| `sort_order` | string | No | `desc` | Sort direction: `asc` or `desc` |

**Response** `200 OK`

```json
{
  "entities": [
    {
      "name": "Python",
      "type": "ProgrammingLanguage",
      "confidence": 0.95,
      "description": "A versatile programming language",
      "source_chunks": [0, 3, 7],
      "quality_score": 92.5
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 85,
    "total_pages": 5,
    "has_next": true,
    "has_prev": false
  }
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Get Source Relationships

```
GET /api/v1/sources/{source_id}/relationships
```

Paginated list of relationships extracted from the document. Each relationship
is enriched with human-readable `from` and `to` entity names.

```bash
curl "http://localhost/api/v1/sources/src_abc123/relationships?page=1&page_size=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |

**Response** `200 OK`

```json
{
  "relationships": [
    {
      "source": 0,
      "target": 5,
      "type": "USES",
      "confidence": 0.88,
      "from": "FastAPI",
      "to": "Python"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 120,
    "total_pages": 6,
    "has_next": true,
    "has_prev": false
  }
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Get Source Templates

```
GET /api/v1/sources/{source_id}/templates
```

Paginated list of graph templates created from extraction of this source.

```bash
curl "http://localhost/api/v1/sources/src_abc123/templates?page=1&page_size=20&template_type=node"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `template_type` | string | No | `null` | Filter by type: `node` or `edge` |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |

**Response** `200 OK`

```json
{
  "templates": [
    {
      "id": "template_abc123",
      "name": "ProgrammingLanguage",
      "type": "node",
      "source_id": "src_abc123",
      "properties": ["name", "paradigm", "version"]
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 12,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Get Source LLM Metrics

```
GET /api/v1/sources/{source_id}/llm_metrics
```

Summary of LLM usage metrics for a source, including call counts, token
consumption, cost estimates, and derived rates.

```bash
curl http://localhost/api/v1/sources/src_abc123/llm_metrics
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

```json
{
  "source_id": "src_abc123",
  "has_metrics": true,
  "summary": {
    "total_calls": 6,
    "successful_calls": 6,
    "failed_calls": 0,
    "retry_calls": 1,
    "first_try_successes": 5,
    "retry_successes": 1,
    "permanent_failures": 0,
    "total_input_tokens": 24000,
    "total_output_tokens": 8500,
    "wasted_tokens": 400,
    "avg_call_duration_ms": 4200,
    "total_duration_ms": 25200,
    "estimated_cost_usd": 0.0325,
    "error_counts": {},
    "model": "gpt-4o",
    "success_rate": 1.0,
    "retry_rate": 0.167,
    "waste_percentage": 0.012
  }
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### List Source LLM Calls

```
GET /api/v1/sources/{source_id}/llm_metrics/calls
```

Paginated list of individual LLM API calls made during extraction of this source.

```bash
curl "http://localhost/api/v1/sources/src_abc123/llm_metrics/calls?page=1&page_size=20&success=true"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page` | int | No | `1` | Page number (1-indexed) |
| `page_size` | int | No | Server default | Items per page |
| `success` | bool | No | `null` | Filter by success status |

**Response** `200 OK`

```json
{
  "calls": [
    {
      "id": "call_001",
      "source_id": "src_abc123",
      "success": true,
      "input_tokens": 1100,
      "output_tokens": 620,
      "duration_ms": 3800,
      "model": "gpt-4o",
      "created_at": "2026-03-09T10:00:05"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 6,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

## Tags

Tag endpoints are mounted at `/api/v1/sources/tags` for tag CRUD, and nested
under individual sources for tag assignment.

### List All Tags

```
GET /api/v1/sources/tags
```

Returns all tags in the current database.

```bash
curl http://localhost/api/v1/sources/tags
```

**Response** `200 OK` -- `list[TagResponse]`

```json
[
  {
    "id": "tag_001",
    "database_name": "default",
    "name": "Research",
    "color": "#4dabf5",
    "description": "Research papers",
    "created_at": "2026-03-01T08:00:00"
  },
  {
    "id": "tag_002",
    "database_name": "default",
    "name": "Technical",
    "color": "#66bb6a",
    "description": null,
    "created_at": "2026-03-02T09:00:00"
  }
]
```

---

### Get Tag

```
GET /api/v1/sources/tags/{tag_id}
```

Returns a single tag by ID.

```bash
curl http://localhost/api/v1/sources/tags/tag_001
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tag_id` | string (path) | **Yes** | Tag ID |

**Response** `200 OK` -- [TagResponse](#tagresponse)

```json
{
  "id": "tag_001",
  "database_name": "default",
  "name": "Research",
  "color": "#4dabf5",
  "description": "Research papers",
  "created_at": "2026-03-01T08:00:00"
}
```

| Status | Description |
|--------|-------------|
| `404` | Tag not found |

---

### Create Tag

```
POST /api/v1/sources/tags
```

Create a new tag.

```bash
curl -X POST http://localhost/api/v1/sources/tags \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Research",
    "color": "#4dabf5",
    "description": "Research papers"
  }'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | **Yes** | -- | Tag display name |
| `color` | string | No | `null` | Hex color code (e.g. `#4dabf5`) |
| `description` | string | No | `null` | Tag description |

**Response** `201 Created` -- [TagResponse](#tagresponse)

| Status | Description |
|--------|-------------|
| `400` | Duplicate tag name or validation error |

---

### Update Tag

```
PATCH /api/v1/sources/tags/{tag_id}
```

Update tag properties. All fields are optional.

```bash
curl -X PATCH http://localhost/api/v1/sources/tags/tag_001 \
  -H "Content-Type: application/json" \
  -d '{ "name": "Updated Name", "color": "#ff5722" }'
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tag_id` | string (path) | **Yes** | Tag ID |
| `name` | string | No | Updated tag name |
| `color` | string | No | Updated hex color |
| `description` | string | No | Updated description |

**Response** `200 OK` -- [TagResponse](#tagresponse)

| Status | Description |
|--------|-------------|
| `400` | Duplicate tag name or validation error |
| `404` | Tag not found |

---

### Delete Tag

```
DELETE /api/v1/sources/tags/{tag_id}
```

Delete a tag. Removes the tag and all source-tag associations.

```bash
curl -X DELETE http://localhost/api/v1/sources/tags/tag_001
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tag_id` | string (path) | **Yes** | Tag ID |

**Response** `204 No Content`

| Status | Description |
|--------|-------------|
| `404` | Tag not found |

---

## Source Tag Assignment

### List Tags for Source

```
GET /api/v1/sources/{source_id}/tags
```

Returns all tags assigned to a specific source.

```bash
curl http://localhost/api/v1/sources/src_abc123/tags
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK` -- `list[TagResponse]`

```json
[
  {
    "id": "tag_001",
    "database_name": "default",
    "name": "Research",
    "color": "#4dabf5",
    "description": "Research papers",
    "created_at": "2026-03-01T08:00:00"
  }
]
```

---

### Assign Tag to Source

```
POST /api/v1/sources/{source_id}/tags/{tag_id}
```

Assign a tag to a source.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/tags/tag_001
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `tag_id` | string (path) | **Yes** | Tag ID |

**Response** `204 No Content`

| Status | Description |
|--------|-------------|
| `404` | Source or tag not found |

---

### Remove Tag from Source

```
DELETE /api/v1/sources/{source_id}/tags/{tag_id}
```

Remove a tag from a source.

```bash
curl -X DELETE http://localhost/api/v1/sources/src_abc123/tags/tag_001
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `tag_id` | string (path) | **Yes** | Tag ID |

**Response** `204 No Content`

| Status | Description |
|--------|-------------|
| `404` | Tag assignment not found |

---

## Page Images

Rendered page images are generated for PDF sources when vision processing is
enabled. Images are stored per-source under
`data/databases/{db_name}/images/{source_id}/` and served directly by the API.

### List Source Images

```
GET /api/v1/sources/{source_id}/images
```

Returns a list of available rendered page images for a source document.
Returns an empty list if no images have been generated.

```bash
curl http://localhost/api/v1/sources/src_abc123/images
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK` -- `list[object]`

```json
[
  {
    "filename": "page_1.png",
    "url": "/sources/src_abc123/images/page_1.png"
  },
  {
    "filename": "page_2.png",
    "url": "/sources/src_abc123/images/page_2.png"
  }
]
```

Images are sorted by filename. The `url` field is the path to pass to the
[Get Source Image](#get-source-image) endpoint.

---

### Get Source Image

```
GET /api/v1/sources/{source_id}/images/{filename}
```

Serve a specific rendered page image. Returns the image as `image/png`.
Path traversal is prevented -- the resolved path must remain within the
source image directory.

```bash
curl http://localhost/api/v1/sources/src_abc123/images/page_1.png \
  --output page_1.png
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |
| `filename` | string (path) | **Yes** | Image filename (e.g. `page_1.png`) |

**Response** `200 OK` -- PNG image binary (`Content-Type: image/png`)

| Status | Description |
|--------|-------------|
| `403` | Path traversal attempt detected |
| `404` | Image file not found |

---

## Vision Page Retries

For image-bearing sources processed through the vision pipeline, these endpoints
inspect and retry per-page vision description tasks. The single/batch retry
endpoints require the source to still be in `vision_pending` state (pre-finalize
retry only); listing is read-only and works in any state so the per-page panel
can show post-finalize history.

### List Vision Pages

```
GET /api/v1/sources/{source_id}/vision_pages
```

Return the vision job summary and every per-page description row for the source,
ordered by `(page_number, region_index)`. `job` is `null` for text-only sources.
Read-only.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

| Status | Description |
|--------|-------------|
| `404` | Source not found |

---

### Retry One Vision Page

```
POST /api/v1/sources/{source_id}/vision_pages/{page_number}/retry
```

Reset one page description row to `pending` and re-enqueue its vision task.
Returns `202 Accepted`.

```bash
curl -X POST "http://localhost/api/v1/sources/src_abc123/vision_pages/1/retry?region_index=0"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `page_number` | int (path) | **Yes** | -- | Page number to retry |
| `region_index` | int (query) | No | `0` | Region index within the page |

**Response** `202 Accepted`

| Status | Description |
|--------|-------------|
| `404` | Source, vision job, or page not found |
| `409` | Source is not in `vision_pending` state |

---

### Retry Failed Vision Pages

```
POST /api/v1/sources/{source_id}/vision_pages/retry_failed
```

Reset every `failed` page description row for the source to `pending` and
re-enqueue each. Truncated pages are preserved. Returns `202 Accepted`.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/vision_pages/retry_failed
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `202 Accepted`

| Status | Description |
|--------|-------------|
| `404` | Source or vision job not found |
| `409` | Source is not in `vision_pending` state |

---

## Response Models

### SourceResponse

Full source detail model returned by get, create, and update endpoints. Contains
all lifecycle fields across indexing, extraction, commit, and LLM metrics stages.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Source ID |
| `database_name` | string | Database this source belongs to |
| `filename` | string | Original filename |
| `filepath` | string? | Storage path |
| `file_type` | string? | File type (e.g. `pdf`, `csv`) |
| `file_size` | int? | File size in bytes |
| `title` | string? | Display title |
| `source_type` | string? | Source type (e.g. `pdf`, `webpage`) |
| `origin_url` | string? | Original URL for web imports |
| `version` | int | Version number (default `1`) |
| `parent_id` | string? | Parent source ID |
| `status` | string | Lifecycle status: `pending` \| `indexing` \| `vision_pending` \| `indexed` \| `awaiting_confirmation` \| `extracting` \| `mcp_extracting` \| `extracted` \| `committing` \| `committed` \| `error` |
| `enabled` | bool | Whether the source is active |
| `error_message` | string? | Error description if status is `error` |
| `error_stage` | string? | Stage where error occurred |
| `chunk_count` | int | Number of chunks created |
| `total_content_length` | int | Total character count of all chunks |
| `embedding_model` | string? | Embedding model used |
| `embedding_dimensions` | int? | Embedding vector dimensions |
| `indexing_started_at` | datetime? | When indexing started |
| `indexing_completed_at` | datetime? | When indexing finished |
| `indexing_duration_seconds` | float? | Calculated indexing duration |
| `extraction_depth` | string? | Extraction depth (`full` or `quick`) |
| `extraction_entities_count` | int | Entities extracted |
| `extraction_relationships_count` | int | Relationships extracted |
| `extraction_domain` | string? | Domain used for extraction |
| `extraction_domain_auto` | bool | Whether domain was auto-detected |
| `extraction_started_at` | datetime? | When extraction started |
| `extraction_completed_at` | datetime? | When extraction finished |
| `extraction_duration_seconds` | float? | Calculated extraction duration |
| `current_extraction_job_id` | string? | Active extraction job ID |
| `commit_started_at` | datetime? | When commit started |
| `commit_completed_at` | datetime? | When commit finished |
| `commit_duration_seconds` | float? | Calculated commit duration |
| `commit_nodes_created` | int | Graph nodes committed |
| `commit_edges_created` | int | Graph edges committed |
| `commit_templates_created` | int | Templates created |
| `current_step` | int? | Current processing step number |
| `total_steps` | int? | Total processing steps |
| `step_description` | string? | Current step label |
| `llm_total_calls` | int | Total LLM API calls |
| `llm_successful_calls` | int | Successful LLM calls |
| `llm_failed_calls` | int | Failed LLM calls |
| `llm_retry_calls` | int | Retried LLM calls |
| `llm_first_try_successes` | int | Calls that succeeded on first attempt |
| `llm_retry_successes` | int | Calls that succeeded after retry |
| `llm_permanent_failures` | int | Calls that permanently failed |
| `llm_total_input_tokens` | int | Total input tokens consumed |
| `llm_total_output_tokens` | int | Total output tokens generated |
| `llm_wasted_tokens` | int | Tokens wasted on failed calls |
| `llm_avg_call_duration_ms` | int? | Average call duration in ms |
| `llm_total_duration_ms` | int | Total LLM call duration in ms |
| `llm_estimated_cost_usd` | float? | Estimated cost in USD |
| `llm_error_counts` | object? | Error type breakdown |
| `llm_model` | string? | LLM model used |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
| `user_metadata` | object? | User-defined metadata |
| `upload_options` | object | Persisted upload settings — see [UploadOptions](#uploadoptions) |
| `quality_metrics` | object | Per-stage quality counters and loader/search status — see [QualityMetrics](#qualitymetrics). Full reference: [Quality Metrics API](quality-metrics.md). |
| `vector_indexing_status` | string | One of `pending`, `indexed`, `degraded`, `failed`. Mirrored at the top level for convenience; also lives inside `quality_metrics`. See [Search Status](../../user-guide/search-status.md). |
| `stage_progress` | object | Per-LLM-stage live progress map keyed by stage name (`vision`, `embedding`, `mcp_extraction`). Empty `{}` when the source has no in-flight or completed LLM stages. Each value is a [StageProgressRecord](#stageprogressrecord). Replaces the six `extraction_chunks_*` fields removed in migration 0030. |

---

### UploadOptions

The settings the user (or default) supplied at upload time, persisted on the source row so recovery, retry, and re-extract honor them without the client having to re-pass them.

| Field | Type | Description |
|-------|------|-------------|
| `auto_analyze` | bool | Auto-queue extraction after indexing finishes |
| `extraction_depth` | string | `full` or `quick` |
| `forced_domain` | string? | User-forced extraction domain, or `null` for auto-detect |
| `enable_normalization` | bool? | `null` = use file-type default; `true`/`false` = user override |
| `enable_vision` | bool | Use the vision model on images and scanned PDFs |
| `content_filtering` | bool | Apply domain content-exclusion rules during extraction |
| `filtering_mode` | string | `unfiltered` / `minimal` / `lenient` / `balanced` / `strict` / `maximum` |
| `enable_direction_correction` | bool? | `null` = domain/global default; `true` swaps misdirected relationships, `false` drops them |
| `protect_orphans` | bool? | `null` = domain/global default; `true` keeps orphan entities, `false` drops them before commit |
| `enable_inverse_relationships` | bool? | `null` = global default (`true`); `false` skips inverse-edge creation at commit |
| `max_entity_degree_override` | int? | Per-source cap on relationships per entity; `null` = domain/global default |

---

### StageProgressRecord

One row from `SourceResponse.stage_progress` — live progress for a single LLM-bound stage of the import pipeline. The stage processes a known total of items (pages, chunks) and writes one record per source-id + stage-name pair. The `avg_ms` field is an exponentially-weighted moving average (α=0.3) updated on every tick, which the UI converts into a live remaining-time estimate.

Active stages today: `vision` (per-page), `embedding` (per-batch), `mcp_extraction` (per-chunk).

| Field | Type | Description |
|-------|------|-------------|
| `stage_name` | string | One of `vision` / `embedding` / `mcp_extraction`. |
| `total` | int | Total items to process for this stage (e.g. PDF page count). |
| `processed` | int | Items finished so far. UI surfaces `processed / total`. |
| `avg_ms` | int? | EMA-smoothed milliseconds per item. `null` until the first tick. |
| `started_at` | datetime | Wall-clock time the stage opened. |
| `last_activity` | datetime | Wall-clock time of the most recent tick. |
| `completed_at` | datetime? | Wall-clock time the stage closed cleanly. `null` while in-flight. |
| `extras` | object? | Stage-specific JSON. For `mcp_extraction`: `entities_preview` and `relationships_preview` int counts surfaced in the tooltip. |

The record's lifetime: row INSERTed at stage open with `total` set and `processed=0`; UPDATEd on every tick (incrementing `processed`, refreshing `avg_ms` and `last_activity`); UPDATEd a final time at close (setting `completed_at`). Rows are FK-cascaded with the source — deleting a source removes its progress rows.

---

### QualityMetrics

45 quality counters spanning every silent-drop / silent-merge / silent-skip site of the import pipeline, plus encoding and vector-search companion fields. Counters reset to zero on `Re-extract` (`force_re_extract`); the quality grade itself is not affected.

The counters are grouped by pipeline stage; every field, stage by stage, is listed in the [Quality Metrics API](quality-metrics.md).

**Full per-field reference:** [Quality Metrics API](quality-metrics.md). The canonical enum lives at `chaoscypher_core.services.quality.counters.QualityCounter`; the SQLite adapter derives its integer-increment allowlist from the enum so new counters surface automatically.

Two counters are JSON-shaped (`loader_html_dropped_tags`, `loader_pptx_shapes_skipped`) — `dict[str, int]` per-tag / per-shape breakdowns rather than scalar totals.

---

### SourceSummaryResponse

Lightweight model used in list views. Excludes large payload fields like
`user_metadata`, detailed timestamps, and full LLM metrics. Includes a `tags`
array enriched at the API layer.

Refer to the [List Sources](#list-sources) response example for the full shape.

---

### PaginatedSourcesResponse

Pagination wrapper for source list responses.

| Field | Type | Description |
|-------|------|-------------|
| `data` | SourceSummaryResponse[] | Page of source summaries |
| `pagination` | object | Pagination metadata (total, page, page_size, total_pages, has_next, has_prev) |

---

### ChunkResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Chunk ID |
| `source_id` | string? | Parent source ID |
| `chunk_index` | int | Position in the document (0-indexed) |
| `content` | string | Chunk text content |
| `page_number` | int? | PDF page number |
| `section` | string? | Section heading |
| `group_index` | int? | Hierarchical group index |
| `status` | string | Chunk status |
| `created_at` | datetime | Creation timestamp |

---

### ChunkListResponse

| Field | Type | Description |
|-------|------|-------------|
| `chunks` | ChunkResponse[] | Page of chunks |
| `total` | int | Total chunks for this source |
| `page` | int | Current page number |
| `page_size` | int | Items per page |

---

### CitationResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Citation ID |
| `entity_uri` | string | URI of the cited entity |
| `entity_label` | string | Human-readable entity name |
| `entity_type` | string? | Entity template name |
| `source_id` | string | Source this citation belongs to |
| `chunk_id` | string | Chunk where the entity was found |
| `confidence` | float | Extraction confidence (0.0--1.0) |
| `extraction_method` | string | How the entity was extracted (e.g. `llm`) |
| `context_snippet` | string? | Text snippet surrounding the entity mention |
| `created_at` | datetime | Creation timestamp |

---

### CitationListResponse

| Field | Type | Description |
|-------|------|-------------|
| `citations` | CitationResponse[] | Page of citations |
| `total` | int | Total citations for this source |
| `page` | int | Current page number |
| `page_size` | int | Items per page |

---

### ExtractionTaskResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Task ID |
| `job_id` | string | Parent extraction job ID |
| `chunk_index` | int | Chunk group index |
| `hierarchical_group_id` | string? | Group identifier for hierarchical chunking |
| `small_chunk_ids` | string[]? | Individual chunk IDs in this group |
| `status` | string | `pending` \| `queued` \| `running` \| `completed` \| `failed` |
| `created_at` | datetime | Creation timestamp |
| `queued_at` | datetime? | When queued for processing |
| `started_at` | datetime? | When LLM processing started |
| `completed_at` | datetime? | When processing finished |
| `llm_duration_ms` | int? | LLM call duration in ms |
| `retry_count` | int | Number of retries |
| `entity_count` | int | Entities extracted by this task |
| `relationship_count` | int | Relationships extracted |
| `invalid_relationship_count` | int | Invalid relationships filtered out |
| `small_chunk_numbers` | int[]? | 1-indexed chunk numbers for UI display |
| `input_text_length` | int? | Input text character count |
| `llm_response_length` | int? | LLM response character count |
| `input_tokens` | int? | Actual input token count from LLM API |
| `output_tokens` | int? | Actual output token count from LLM API |
| `context_window_available` | int? | Model context window size |
| `input_text` | string? | Full input text (only in detail view or when `include_content=true`) |
| `llm_response_json` | string? | Raw LLM JSON response (only in detail view or when `include_content=true`) |
| `filtering_log` | object? | Per-chunk filtering diagnostics (detail view only) |
| `finish_reason` | string? | Normalized provider finish reason: `stop`, `length`, `content_filter`, `tool_calls`, `error`, `unknown`. `null` for tasks that predate the field (migration 0022). |
| `aborted_by_loop` | bool? | `true` when the streaming loop detector aborted the LLM mid-response. Tasks predating migration 0022 carry `null`. |
| `error_message` | string? | Error message if failed |
| `error_type` | string? | Error classification |

---

### ExtractionTaskListResponse

| Field | Type | Description |
|-------|------|-------------|
| `tasks` | ExtractionTaskResponse[] | Page of extraction tasks |
| `total` | int | Total tasks for this source |
| `page` | int | Current page number |
| `page_size` | int | Items per page |

---

### ExtractionTaskStatsResponse

Aggregate statistics computed via SQL aggregates across all extraction tasks.

| Field | Type | Description |
|-------|------|-------------|
| `total_tasks` | int | Total extraction tasks |
| `context_window` | int? | LLM context window size |
| `min_input_tokens` | int? | Minimum input tokens across tasks |
| `max_input_tokens` | int? | Maximum input tokens |
| `avg_input_tokens` | int? | Average input tokens |
| `min_output_tokens` | int? | Minimum output tokens |
| `max_output_tokens` | int? | Maximum output tokens |
| `avg_output_tokens` | int? | Average output tokens |
| `min_total_tokens` | int? | Minimum total tokens (input + output) |
| `max_total_tokens` | int? | Maximum total tokens |
| `avg_total_tokens` | int? | Average total tokens |
| `min_utilization` | float? | Minimum context window utilization % |
| `max_utilization` | float? | Maximum utilization % |
| `avg_utilization` | float? | Average utilization % |
| `min_duration_ms` | int? | Minimum LLM call duration in ms |
| `max_duration_ms` | int? | Maximum duration |
| `avg_duration_ms` | int? | Average duration |
| `total_entities` | int | Total entities across all tasks |
| `avg_entities_per_task` | float | Average entities per task |
| `total_relationships` | int | Total relationships |
| `avg_relationships_per_task` | float | Average relationships per task |
| `total_retries` | int | Total retry attempts |
| `max_retries_single_task` | int | Most retries on a single task |
| `total_invalid_relationships` | int | Total invalid relationships filtered |
| `avg_invalid_per_task` | float | Average invalid relationships per task |
| `total_entities_filtered` | int | Entities removed by pipeline filtering |
| `total_relationships_filtered` | int | Relationships removed by filtering |
| `filtering_stage_summary` | object[]? | Per-stage filtering breakdown |
| `system_prompt` | string? | System prompt used for extraction |
| `extraction_rules_template` | string? | Extraction rules portion of the prompt |
| `entity_templates` | string? | Entity template portion |
| `relationship_templates` | string? | Relationship template portion |
| `domain_guidance` | string? | Domain-specific guidance |
| `domain_examples` | string? | Domain-specific examples |

---

### TagResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Tag ID |
| `database_name` | string | Database this tag belongs to |
| `name` | string | Tag display name |
| `color` | string? | Hex color code |
| `description` | string? | Tag description |
| `created_at` | datetime | Creation timestamp |
