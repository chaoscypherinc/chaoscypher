---
title: Backup API
description: REST API for creating, downloading, restoring, and managing Chaos Cypher database backups at /api/v1/backup.
---

# Backup API

Create, restore, and manage database backups.

**Base path:** `/api/v1/backup`

:::note[Authentication required]

All backup endpoints require an authenticated user when auth is enabled.

:::

---

## Create Backup

```
POST /api/v1/backup
```

Creates a backup of the current database.

#### Response

**Status:** `201 Created`

```json
{
  "database": "default",
  "filename": "app_20260413_142530.db",
  "size": 5242880,
  "created_at": "2026-04-13T14:25:30.000000"
}
```

#### curl Example

```bash
curl -s -X POST http://localhost:8080/api/v1/backup
```

---

## List Backups

```
GET /api/v1/backup
```

Lists all available backups for the current database.

#### Response

**Status:** `200 OK`

```json
{
  "backups": [
    {
      "filename": "app_20260413_142530.db",
      "size": 5242880,
      "created_at": "2026-04-13T14:25:30.000000"
    }
  ]
}
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/backup
```

---

## Restore Backup

```
POST /api/v1/backup/{filename}/restore
```

Restores the current database from a backup file.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filename` | string | Yes | Backup filename (format: `app_YYYYMMDD_HHMMSS.db`) |

#### Response

**Status:** `200 OK`

```json
{
  "database": "default",
  "restored_from": "app_20260413_142530.db"
}
```

#### curl Example

```bash
curl -s -X POST http://localhost:8080/api/v1/backup/app_20260413_142530.db/restore
```

---

## Download Backup

```
GET /api/v1/backup/{filename}/download
```

Downloads a backup file as a binary SQLite database.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filename` | string | Yes | Backup filename |

#### Response

**Status:** `200 OK`
**Content-Type:** `application/x-sqlite3`

Binary file download.

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/backup/app_20260413_142530.db/download -o backup.db
```

---

## Delete Backup

```
DELETE /api/v1/backup/{filename}
```

Deletes a specific backup file.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filename` | string | Yes | Backup filename |

#### Response

**Status:** `204 No Content`

No response body.

#### curl Example

```bash
curl -s -X DELETE http://localhost:8080/api/v1/backup/app_20260413_142530.db
```

---

## Response Models Reference

### BackupResponse

| Field | Type | Description |
|-------|------|-------------|
| `database` | string | Database name |
| `filename` | string | Backup filename |
| `size` | integer | File size in bytes |
| `created_at` | string | Creation timestamp |

### BackupListResponse

| Field | Type | Description |
|-------|------|-------------|
| `backups` | BackupSummaryResponse[] | List of available backups |

### RestoreResponse

| Field | Type | Description |
|-------|------|-------------|
| `database` | string | Database name |
| `restored_from` | string | Backup filename that was restored |
