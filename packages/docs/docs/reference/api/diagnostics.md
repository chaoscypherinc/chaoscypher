---
title: Diagnostics API
description: Export a diagnostic bundle (system info, logs, settings, queue stats) and check system and upgrade status via the Chaos Cypher diagnostics endpoints.
---

# Diagnostics API

System-level status and diagnostic endpoints.

**Paths:** `/api/v1/diagnostics`, `/api/v1/system`, `/api/v1/upgrade`, `/api/v1/admin`

---

## Export Diagnostics

```
GET /api/v1/diagnostics/export
```

Downloads a ZIP file containing diagnostic information for bug reports and troubleshooting. The bundle includes system info, sanitized settings (secrets masked), database statistics, service logs, queue status, and service health.

#### Response

**Status:** `200 OK`
**Content-Type:** `application/zip`
**Filename:** `chaoscypher-diagnostics.zip`

Binary ZIP file download.

#### Bundle Contents

| File | Description |
|------|-------------|
| System info | OS, Python version, package versions |
| Settings | Sanitized application settings (API keys masked) |
| Database stats | Node, edge, template, source counts |
| Logs | Recent logs from all managed services |
| Queue status | Queue lengths and worker state |
| Service status | PID, uptime, and state for each service |

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/diagnostics/export -o chaoscypher-diagnostics.zip
```

The bundle gracefully handles missing components (e.g., if Valkey is unreachable, queue stats are omitted rather than causing an error).

---

## System Dashboard

```
GET /api/v1/system/dashboard
```

Aggregated live-status snapshot consumed by the UI dashboard polling loop. Returns entity counts, LLM queue statistics, operations queue depth, workflow run statistics, and the system pause status in a single request.

```bash
curl http://localhost:8080/api/v1/system/dashboard
```

**Response** `200 OK` -- `DashboardResponse`

```json
{
  "counts": {
    "nodes": 1500,
    "edges": 3200,
    "sources": 42,
    "templates": 8
  },
  "llm": {},
  "queue": {},
  "workflows": {},
  "processing": {
    "paused": false,
    "paused_at": null,
    "reason": null
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `counts` | object | Knowledge entity counts (nodes, edges, sources, templates) |
| `llm` | object | LLM queue and cost statistics |
| `queue` | object | Operations and LLM queue depth |
| `workflows` | object | Workflow run statistics |
| `processing` | object | System pause status — see [Pause API](pause.md) |

---

## Database Upgrade

These endpoints expose the Alembic migration state and allow operator-triggered schema upgrades. The system applies pending migrations automatically on startup, but you can also trigger them manually.

### Get Pending Migrations

```
GET /api/v1/upgrade/pending
```

Returns the current upgrade state and any unapplied schema migrations.

```bash
curl http://localhost:8080/api/v1/upgrade/pending
```

**Response** `200 OK`

```json
{
  "ready": true,
  "blocked_on": [],
  "message": "Database is up to date.",
  "last_backup": "/data/backups/pre-upgrade-2026-05-01.db"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ready` | bool | `true` if the app is ready to serve requests |
| `blocked_on` | object[] | Pending migrations blocking the app (empty when `ready=true`) |
| `message` | string | Human-readable status message |
| `last_backup` | string \| null | Path to the pre-upgrade backup, or `null` if none exists |

---

### Apply Upgrades

```
POST /api/v1/upgrade/apply
```

Applies all pending schema migrations. Creates a backup of the database before applying if the startup runner did not already do so.

```bash
curl -X POST http://localhost:8080/api/v1/upgrade/apply
```

**Response** `200 OK`

```json
{
  "applied": ["a1b2c3d4e5f6", "b2c3d4e5f6a1"],
  "current_revision": "b2c3d4e5f6a1",
  "backup_path": "/data/backups/pre-upgrade-2026-05-01.db"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `applied` | string[] | Revision IDs that were applied |
| `current_revision` | string \| null | Revision the database is now stamped at |
| `backup_path` | string \| null | Pre-apply backup location |

---

### Rollback Upgrade

```
POST /api/v1/upgrade/rollback
```

Restores the database from the pre-upgrade backup. Use this to undo a failed migration.

```bash
curl -X POST http://localhost:8080/api/v1/upgrade/rollback
```

**Response** `200 OK`

```json
{
  "restored_from": "/data/backups/pre-upgrade-2026-05-01.db",
  "revision": "a1b2c3d4e5f6"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `restored_from` | string | Backup file the database was restored from |
| `revision` | string \| null | Alembic revision after restore |

**Errors:** `404` if no pre-upgrade backup exists.

---

## Plugin Management

### Reload Plugins

```
POST /api/v1/admin/plugins/reload
```

Invalidates all plugin registry caches so the next call rediscovers installed plugins. Use this after installing or removing a plugin without restarting the backend.

```bash
curl -X POST http://localhost:8080/api/v1/admin/plugins/reload
```

**Response** `200 OK`

```json
{
  "cleared": 3,
  "registries": ["LoaderRegistry", "CleanerRegistry", "ArchiveRegistry"]
}
```

The response contains the list of registry classes whose cache had entries and the total number of cache entries cleared.
