---
title: API Reference
description: Complete REST API reference for Chaos Cypher — served at /api/v1/ via nginx, covering sources, graph, chat, search, workflows, and more.
---

# API Reference

The Chaos Cypher REST API is served through nginx at `http://localhost/api/v1/` (HTTP) or `https://localhost/api/v1/` (when TLS is enabled).

## Base URL

```
http://localhost/api/v1
```

All endpoints described in this reference are relative to the base URL above.

## Authentication

Chaos Cypher is single-user and local-first. Nginx terminates every request and gates `/api/` behind an internal `auth_request` subrequest to `/api/v1/auth/verify`. Sessions are HMAC-signed `cc_session` cookies — no JWT, no bearer tokens, no user management.

On first run, `/api/v1/auth/status` reports `{"setup_needed": true}` and the UI shows a one-time setup form that creates the single operator credential. After setup, you log in with that username/password and the cookie is set automatically.

### Setup (first run only)

```bash
curl -X POST http://localhost/api/v1/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "s3cureP4ss!"}'
```

### Login

```bash
curl -X POST http://localhost/api/v1/auth/login \
  -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "s3cureP4ss!"}'
```

**Response** `200 OK`: empty body. The response sets an httpOnly `cc_session` cookie; pass `-b cookies.txt` on subsequent requests.

### Authenticated requests

```bash
curl http://localhost/api/v1/sources -b cookies.txt
```

Without the cookie, nginx returns `401 {"error": "authentication_required"}` before the request ever reaches Cortex.

### Logout

```bash
curl -X POST http://localhost/api/v1/auth/logout -b cookies.txt
```

### Status

`GET /api/v1/auth/status` is public and returns `{"setup_needed", "authenticated", "username"}`. Used by the UI to decide between setup, login, and dashboard.

### API keys

For programmatic access (scripts, [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) clients), create an API key in Settings. Send it as `Authorization: Bearer <key>` on any `/api/` request; nginx's `auth_request` accepts it as an alternative to the session cookie. API keys are stored hashed in `/data/credentials.json`.

---

## Common Patterns

### Pagination

List endpoints support pagination via query parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `page` | `1` | Page number (1-indexed) |
| `page_size` | `50` | Records per page (max: 1000) |

**Example request:**

```bash
curl "http://localhost/api/v1/sources?page=3&page_size=10"
```

**Example response** (list endpoint with pagination metadata):

```json
{
  "items": [
    { "id": "src_abc123", "name": "quarterly-report.pdf", "status": "committed" },
    { "id": "src_def456", "name": "meeting-notes.docx", "status": "indexed" }
  ],
  "total": 47,
  "page": 3,
  "page_size": 10
}
```

:::tip[Queue task pagination]

The queue task list endpoint uses `offset` instead of `skip` and returns a `pagination` object with additional fields:

```json
{
  "tasks": [...],
  "total": 10,
  "total_in_queue": 3,
  "queues": null,
  "pagination": {
    "limit": 50,
    "offset": 0,
    "total": 42,
    "total_in_queue": 3,
    "has_more": false
  }
}
```

:::

### Async Operations

Long-running operations (source processing, exports, extractions) return HTTP `202 Accepted` with a task ID:

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

Poll the [task status](queue.md#get-task) until it reaches a terminal state:

```bash
curl http://localhost/api/v1/queue/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Task status response:**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "queue": "operations",
  "operation": "process_source",
  "status": "completed",
  "priority": "50",
  "created_at": "2026-03-09T14:30:00Z",
  "started_at": "2026-03-09T14:30:01Z",
  "completed_at": "2026-03-09T14:30:45Z",
  "attempts": "1",
  "metadata": "{\"source_id\": \"src_abc123\"}"
}
```

**Task status values:**

| Status | Description |
|--------|-------------|
| `queued` | Waiting to be picked up by a worker |
| `running` | Currently being processed |
| `completed` | Finished successfully |
| `failed` | Processing failed (check task result for error details) |
| `cancelled` | Cancelled by user |

Once a task is `completed`, retrieve its result:

```bash
curl http://localhost/api/v1/queue/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890/result
```

```json
{
  "result": {
    "source_id": "src_abc123",
    "entities_extracted": 42,
    "relationships_extracted": 18
  }
}
```

### Error Format

Errors use two formats depending on how they originate.

**Domain exceptions** (raised by business logic) return a structured body with an `error` code, `message`, and optional `details`:

```json
{
  "error": "NOT_FOUND",
  "message": "Source not found: src_abc123",
  "details": {
    "resource_type": "Source",
    "identifier": "src_abc123"
  }
}
```

**HTTP exceptions** (raised directly by endpoints) return a `detail` field:

```json
{
  "detail": "Source not found"
}
```

Or for structured HTTP errors:

```json
{
  "detail": {
    "code": "VALIDATION_FAILED",
    "message": "Invalid data provided for source upload"
  }
}
```

### Error Status Codes

| Status | Meaning | Example |
|--------|---------|---------|
| `200` | Success | GET request succeeded |
| `201` | Created | Resource created successfully |
| `202` | Accepted | Async operation queued |
| `400` | Bad request | Invalid input or business rule violation |
| `401` | Unauthorized | Missing or invalid authentication token |
| `403` | Forbidden | Insufficient permissions |
| `404` | Not found | Resource does not exist |
| `409` | Conflict | Duplicate resource (e.g., username already taken) |
| `422` | Unprocessable entity | Operation failed due to business logic constraints |
| `429` | Too many requests | LLM provider rate limit exceeded |
| `500` | Internal server error | Unexpected failure |
| `503` | Service unavailable | External service down (Valkey, LLM provider) |

#### Error Examples

**`400` Bad Request** -- validation error:

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Workflow name is required",
  "details": {
    "field": "name"
  }
}
```

**`404` Not Found** -- resource does not exist:

```json
{
  "error": "NOT_FOUND",
  "message": "Workflow not found: wf_abc123",
  "details": {
    "resource_type": "Workflow",
    "identifier": "wf_abc123"
  }
}
```

**`409` Conflict** -- duplicate resource:

```json
{
  "error": "CONFLICT",
  "message": "Username already exists"
}
```

**`422` Unprocessable Entity** -- business logic constraint:

```json
{
  "error": "OPERATION_ERROR",
  "message": "Cannot delete workflow with active executions",
  "details": {
    "operation": "delete_workflow"
  }
}
```

**`500` Internal Server Error** -- unexpected failure:

```json
{
  "detail": {
    "code": "OPERATION_FAILED",
    "message": "An unexpected error occurred. Please contact support if this persists."
  }
}
```

---

## API Sections

| Section | Base Path | Description |
|---------|-----------|-------------|
| [Health](health.md) | `/health` | System health monitoring and subsystem status |
| [Auth](auth.md) | `/auth` | Single-user login/logout, session status, password/username change, and API keys (session-cookie auth) |
| [Sources](sources.md) | `/sources` | Document upload, processing, chunking, extraction, and tagging |
| [Nodes](nodes.md) | `/nodes` | Knowledge graph nodes (entities) |
| [Edges](edges.md) | `/edges` | Knowledge graph relationships |
| [Graph](graph.md) | `/graph` | Graph queries and visualization data |
| [Grounding](grounding.md) | `/graph/grounding` | Entity grounding and linking |
| [Search](search.md) | `/search` | Full-text and semantic search |
| [Chat](chat.md) | `/chats` | Conversations and AI chat |
| [Workflows](workflows.md) | `/workflows` | Automation workflows and executions |
| [Tools](tools.md) | `/tools` | Tool registry for workflow steps |
| [Triggers](triggers.md) | `/triggers` | Event-based workflow triggers |
| [Templates](templates.md) | `/templates` | Knowledge graph node and edge schema templates |
| [Lexicon](lexicon.md) | `/lexicon` | Domain vocabulary and term management |
| [Quality](quality.md) | `/quality` | Source and extraction quality scoring |
| [Databases](databases.md) | `/databases` | Multi-database management |
| [Queue](queue.md) | `/queue` | Task queue management and monitoring |
| [Exports](exports.md) | `/exports` | Data export operations |
| [LLM](llm.md) | `/llm` | LLM provider configuration and model listing |
| [Counts](counts.md) | `/counts` | Aggregate counts across resources |
| [Pause](pause.md) | `/sources`, `/system/processing` | Per-source and system-wide pause / resume controls |
| [Settings](settings.md) | `/settings` | Application configuration |
| [Backup](backup.md) | `/backup` | Database backup operations |
| [Logs](logs.md) | `/logs` | Container service logs (all-in-one deployment) |
| [Diagnostics](diagnostics.md) | `/diagnostics` | Diagnostic bundle export for troubleshooting |
| [Edition](edition.md) | `/edition` | Edition info, license status, and feature list |
