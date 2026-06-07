---
title: Databases API
description: Manage isolated Chaos Cypher databases — list, create, switch, and delete databases, each with its own knowledge graph, search indices, and workflows.
---

# Databases

Manage multiple isolated databases. Each database has its own knowledge graph, search indices, workflows, and application data. Use these endpoints to list, create, switch between, and delete databases.

All endpoints are prefixed with `/api/v1/databases`.

---

## List Databases

```
GET /api/v1/databases
```

Returns all available databases sorted alphabetically by name. Only directories containing an initialized `app.db` are included.

```bash
curl http://localhost:8080/api/v1/databases
```

**Response** `200 OK` -- [DatabaseListResponse](#databaselistresponse)

```json
{
  "databases": [
    {
      "name": "default",
      "path": "/data/databases/default",
      "exists": true,
      "size": 524288,
      "last_modified": "2026-03-09T14:22:10+00:00"
    },
    {
      "name": "research-project",
      "path": "/data/databases/research-project",
      "exists": true,
      "size": 1048576,
      "last_modified": "2026-03-08T09:15:33+00:00"
    }
  ]
}
```

---

## Get Current Database

```
GET /api/v1/databases/current
```

Returns the name and metadata of the currently active database.

```bash
curl http://localhost:8080/api/v1/databases/current
```

**Response** `200 OK` -- [CurrentDatabaseResponse](#currentdatabaseresponse)

```json
{
  "current": "default",
  "info": {
    "name": "default",
    "path": "/data/databases/default",
    "exists": true,
    "size": 524288,
    "last_modified": "2026-03-09T14:22:10+00:00"
  }
}
```

### Errors

| Status | Condition | Error Code |
|--------|-----------|------------|
| `404` | Current database directory does not exist on disk | `NOT_FOUND` |

```json
{
  "error": "NOT_FOUND",
  "message": "Database not found: default"
}
```

---

## Switch Database

```
PATCH /api/v1/databases/current
```

Switch the active database. If the target database directory exists but has no `app.db`, one is created automatically. After switching, the frontend should refresh to load the new database context.

```bash
curl -X PATCH http://localhost:8080/api/v1/databases/current \
  -H "Content-Type: application/json" \
  -d '{"name": "research-project"}'
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **Yes** | Name of the database to switch to |

**Response** `200 OK` -- [DatabaseSwitchResponse](#databaseswitchresponse)

```json
{
  "success": true,
  "message": "Database switched to 'research-project' successfully. Refresh the page to load the new database.",
  "database": "research-project"
}
```

### Errors

| Status | Condition | Error Code |
|--------|-----------|------------|
| `404` | Target database does not exist | `NOT_FOUND` |

```json
{
  "error": "NOT_FOUND",
  "message": "Database not found: nonexistent-db"
}
```

---

## Get Database

```
GET /api/v1/databases/{name}
```

Returns metadata for a specific database.

```bash
curl http://localhost:8080/api/v1/databases/research-project
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | **Yes** | Database name |

**Response** `200 OK` -- [DatabaseResponse](#databaseresponse)

```json
{
  "name": "research-project",
  "path": "/data/databases/research-project",
  "exists": true,
  "size": 1048576,
  "last_modified": "2026-03-08T09:15:33+00:00"
}
```

### Errors

| Status | Condition | Error Code |
|--------|-----------|------------|
| `404` | Database does not exist | `NOT_FOUND` |

```json
{
  "error": "NOT_FOUND",
  "message": "Database not found: nonexistent-db"
}
```

---

## Create Database

```
POST /api/v1/databases
```

Create a new database with a fully initialized directory structure including `app.db` (with default seed data, search indices, and knowledge graph tables).

```bash
curl -X POST http://localhost:8080/api/v1/databases \
  -H "Content-Type: application/json" \
  -d '{"name": "my_new_database"}'
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **Yes** | Database name. Must be alphanumeric; underscores and hyphens are allowed. |

**Response** `201 Created` -- [DatabaseResponse](#databaseresponse)

```json
{
  "name": "my_new_database",
  "path": "/data/databases/my_new_database",
  "exists": true,
  "size": 262144,
  "last_modified": "2026-03-09T15:00:00+00:00"
}
```

### Errors

| Status | Condition | Error Code |
|--------|-----------|------------|
| `400` | Name contains invalid characters | `VALIDATION_ERROR` |
| `400` | Database already exists | `VALIDATION_ERROR` |

**Invalid name:**

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Database name must be alphanumeric (underscores and hyphens allowed)"
}
```

**Already exists:**

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Database 'my_new_database' already exists"
}
```

---

## Delete Database

```
DELETE /api/v1/databases/{name}
```

Permanently delete a database and all its data, including knowledge graphs, search indices, and application data. This operation cannot be undone.

```bash
curl -X DELETE http://localhost:8080/api/v1/databases/old-project
```

### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | **Yes** | Database name to delete |

**Response** `204 No Content` -- empty body

### Safety Checks

- Cannot delete the currently active database. Switch to another database first.
- Cannot delete the `default` database.

### Errors

| Status | Condition | Error Code |
|--------|-----------|------------|
| `400` | Attempting to delete the currently active database | `VALIDATION_ERROR` |
| `400` | Attempting to delete the `default` database | `VALIDATION_ERROR` |
| `400` | Database does not exist | `VALIDATION_ERROR` |

**Active database:**

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Cannot delete the currently active database. Switch to another database first."
}
```

**Default database:**

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Cannot delete default database"
}
```

**Does not exist:**

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Database 'nonexistent' does not exist"
}
```

---

## Models

### DatabaseResponse

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Database name |
| `path` | string | Absolute path to the database directory |
| `exists` | boolean | Whether `app.db` exists in the directory |
| `size` | integer | Size of `app.db` in bytes (0 if not initialized) |
| `last_modified` | string \| null | ISO 8601 timestamp of last `app.db` modification, or `null` if not initialized |

### DatabaseListResponse

| Field | Type | Description |
|-------|------|-------------|
| `databases` | [DatabaseResponse](#databaseresponse)[] | List of databases sorted by name |

### CurrentDatabaseResponse

| Field | Type | Description |
|-------|------|-------------|
| `current` | string | Name of the currently active database |
| `info` | [DatabaseResponse](#databaseresponse) | Metadata for the current database |

### DatabaseCreateRequest

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **Yes** | Database name (alphanumeric, underscores, hyphens) |

### DatabaseSwitchRequest

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **Yes** | Name of the database to switch to |

### DatabaseSwitchResponse

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the switch was successful |
| `message` | string | Human-readable result message |
| `database` | string | Name of the database that is now active |
