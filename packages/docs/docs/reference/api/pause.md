---
title: Pause / Resume API
description: Pause or resume document processing system-wide or per-source — prevents new extraction from starting and re-queues paused sources on resume.
---

# Pause / Resume API

Control processing at both the individual source level and system-wide. Pausing
prevents new extraction and indexing work from starting; resuming re-queues any
paused sources for recovery.

Nine endpoints are split across two path prefixes:

- **Per-source** endpoints are mounted at `/api/v1/sources`
- **System-wide** endpoints are mounted at `/api/v1/system/processing`

---

## Per-Source Pause / Resume

### Pause Single Source

```
POST /api/v1/sources/{source_id}/pause
```

Pause processing for a single source. The source will not be picked up for
extraction or recovery until it is resumed.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/pause \
  -H "Content-Type: application/json" \
  -d '{"reason": "Reviewing extraction settings"}'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_id` | string (path) | **Yes** | -- | Source ID |
| `reason` | string (body) | No | `null` | Optional human-readable reason (max 500 characters) |

**Response** `200 OK`

```json
{
  "source_id": "src_abc123",
  "paused": true
}
```

---

### Resume Single Source

```
POST /api/v1/sources/{source_id}/resume
```

Resume a paused source and immediately trigger recovery so it re-queues for
any pending processing.

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/resume
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_id` | string (path) | **Yes** | Source ID |

**Response** `200 OK`

```json
{
  "source_id": "src_abc123",
  "paused": false
}
```

---

### Bulk Pause Sources

```
POST /api/v1/sources/pause
```

Pause multiple sources in a single request. Returns the count of sources
successfully paused.

```bash
curl -X POST http://localhost/api/v1/sources/pause \
  -H "Content-Type: application/json" \
  -d '{
    "source_ids": ["src_abc123", "src_def456"],
    "reason": "Maintenance window"
  }'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_ids` | string[] (body) | **Yes** | -- | List of source IDs to pause (1--500 items) |
| `reason` | string (body) | No | `null` | Optional reason applied to all sources (max 500 characters) |

**Response** `200 OK`

```json
{
  "count": 2
}
```

---

### Bulk Resume Sources

```
POST /api/v1/sources/resume
```

Resume multiple sources in a single request. Each resumed source immediately
triggers recovery.

```bash
curl -X POST http://localhost/api/v1/sources/resume \
  -H "Content-Type: application/json" \
  -d '{
    "source_ids": ["src_abc123", "src_def456"]
  }'
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_ids` | string[] (body) | **Yes** | List of source IDs to resume (1--500 items) |

**Response** `200 OK`

```json
{
  "count": 2
}
```

---

## System-Wide Pause / Resume

These endpoints affect all processing across the entire database, not just
individual sources.

### Pause System Processing

```
POST /api/v1/system/processing/pause
```

Pause all source processing system-wide. No new extraction or indexing jobs
will be started until the system is resumed. Existing in-flight tasks are
not interrupted.

```bash
curl -X POST http://localhost/api/v1/system/processing/pause \
  -H "Content-Type: application/json" \
  -d '{"reason": "Scheduled maintenance"}'
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `reason` | string (body) | No | `null` | Optional reason for the pause (max 500 characters) |

**Response** `200 OK`

```json
{
  "paused": true
}
```

---

### Resume System Processing

```
POST /api/v1/system/processing/resume
```

Resume system-wide processing after a system pause.

```bash
curl -X POST http://localhost/api/v1/system/processing/resume
```

**Response** `200 OK`

```json
{
  "paused": false
}
```

---

### Get System Pause Status

```
GET /api/v1/system/processing/status
```

Returns the current system-wide pause state, including when the pause started
and the reason if provided.

```bash
curl http://localhost/api/v1/system/processing/status
```

**Response** `200 OK` -- [SystemPauseStatusResponse](#systempausestatusresponse)

```json
{
  "paused": true,
  "paused_at": "2026-04-13T09:00:00",
  "reason": "Scheduled maintenance"
}
```

When the system is not paused:

```json
{
  "paused": false,
  "paused_at": null,
  "reason": null
}
```

---

### List System Events

```
GET /api/v1/system/processing/events
```

Returns an audit trail of recent system-level events. Useful for monitoring
and diagnosing pause / resume activity, health changes, task failures, and
recovery operations.

```bash
curl "http://localhost/api/v1/system/processing/events?event_type=pause&limit=20"
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `event_type` | string (query) | No | `null` | Filter by event type: `pause`, `resume`, `health_change`, `task_failed`, `recovery` |
| `limit` | int (query) | No | `50` | Maximum number of events to return |

**Response** `200 OK` -- `list[SystemEventResponse]`

```json
[
  {
    "id": 1,
    "timestamp": "2026-04-13T09:00:00Z",
    "type": "pause",
    "action": "System processing paused",
    "source": "user",
    "reason": "Scheduled maintenance",
    "details": null,
    "database_name": "default"
  },
  {
    "id": 2,
    "timestamp": "2026-04-13T10:30:00Z",
    "type": "resume",
    "action": "System processing resumed",
    "source": "user",
    "reason": null,
    "details": null,
    "database_name": "default"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-incrementing primary key |
| `timestamp` | string? | ISO-8601 timestamp when the event was recorded |
| `type` | string? | Event type category: `pause`, `resume`, `health_change`, `task_failed`, or `recovery` |
| `action` | string? | Human-readable action description within the type (e.g. `"System processing paused"`) |
| `source` | string? | Who or what triggered the event (e.g. `user`, `health_monitor`, `reconciler`, `worker`) or the originating `source_id` |
| `reason` | string? | Human-readable reason captured at event time, if any |
| `details` | object? | Arbitrary structured payload; schema depends on event type |
| `database_name` | string? | Database the event originated from (events are not cross-database) |

:::note

The query parameter is named `event_type`, but the corresponding **response** field is named `type`.

:::

---

### Clear System Events

```
DELETE /api/v1/system/processing/events
```

Permanently deletes all system events from the audit log.

```bash
curl -X DELETE http://localhost/api/v1/system/processing/events
```

**Response** `200 OK`

```json
{
  "deleted": 42
}
```

| Field | Type | Description |
|-------|------|-------------|
| `deleted` | int | Number of events removed |

---

## Response Models

### SystemPauseStatusResponse

| Field | Type | Description |
|-------|------|-------------|
| `paused` | bool | Whether system-wide processing is currently paused |
| `paused_at` | datetime? | When the system was paused (`null` if not paused) |
| `reason` | string? | Reason provided when pausing (`null` if none given) |
