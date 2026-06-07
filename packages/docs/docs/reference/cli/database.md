---
title: Database Commands
description: Manage isolated Chaos Cypher databases from the CLI — list, create, switch, and delete databases, each a self-contained workspace with its own graph and history.
---

# Database Commands

The `db` command group manages isolated databases. Each database is a self-contained workspace with its own sources, knowledge graph, chat history, workflows, triggers, and search indexes.

```bash
chaoscypher db --help
```

---

## List Databases

Show all databases with name, size, last modified date, and status:

```bash
chaoscypher db list
```

### Sample Output (Table)

```
                  Databases
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Name           ┃    Size ┃ Modified            ┃ Status  ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ default        │ 2.3 MB  │ 2026-03-09 14:22:01 │ current │
│ research-2026  │ 8.7 MB  │ 2026-03-08 10:15:43 │         │
│ medical_notes  │ 1.1 MB  │ 2026-02-28 09:04:17 │         │
└────────────────┴─────────┴─────────────────────┴─────────┘
```

### Sample Output (JSON)

```bash
chaoscypher db list --json
```

```json
[
  {
    "name": "default",
    "path": "/home/user/.local/share/chaoscypher/databases/default",
    "size": 2411724,
    "last_modified": "2026-03-09T14:22:01+00:00",
    "is_current": true
  },
  {
    "name": "research-2026",
    "path": "/home/user/.local/share/chaoscypher/databases/research-2026",
    "size": 9122816,
    "last_modified": "2026-03-08T10:15:43+00:00",
    "is_current": false
  }
]
```

### Sample Output (Quiet)

```bash
chaoscypher db list --quiet
```

```
default
research-2026
medical_notes
```

### Options

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON |
| `--quiet`, `-q` | Only show database names |

---

## Current Database

Show which database is currently active:

```bash
chaoscypher db current
```

### Sample Output

```
default
```

### Sample Output (Verbose)

```bash
chaoscypher db current -v
```

```
Current database: default
  Location: /home/user/.local/share/chaoscypher/databases/default
  Size: 2.3 MB
  Last modified: 2026-03-09 14:22:01
```

If the database directory exists but has not been initialized:

```
Current database: my-project
  Location: /home/user/.local/share/chaoscypher/databases/my-project
  Database not initialized
```

### Options

| Option | Description |
|--------|-------------|
| `--verbose`, `-v` | Show detailed information (location, size, last modified) |

---

## Create a Database

```bash
chaoscypher db create <name>
```

Creates a new database with the required directory structure, including `app.db` (SQLite) and `search/` (search indexes). The database is not automatically switched to after creation.

Database names must be alphanumeric (hyphens and underscores allowed). The pattern is `[a-zA-Z0-9_-]+`.

### Sample Output

```bash
chaoscypher db create research-2026
```

```
Creating database 'research-2026'...

Created database 'research-2026'
  Location: /home/user/.local/share/chaoscypher/databases/research-2026

Switch to it with: chaoscypher db switch research-2026
```

### Error: Invalid Name

```bash
chaoscypher db create "my project!"
```

```
Invalid database name.
Names must be alphanumeric (hyphens and underscores allowed).
```

### Error: Already Exists

```bash
chaoscypher db create default
```

```
Database already exists: default
```

---

## Switch Database

Set the default database for all subsequent CLI commands:

```bash
chaoscypher db switch <name>
```

The database must already exist (have an initialized `app.db`).

### Sample Output

```bash
chaoscypher db switch research-2026
```

```
Switched to database 'research-2026'
```

### Error: Not Found

```bash
chaoscypher db switch nonexistent
```

```
Database not found: nonexistent

Create it with: chaoscypher db create nonexistent
```

---

## Database Info

Show detailed information about a specific database, including filesystem metadata and content counts (nodes, edges, templates):

```bash
chaoscypher db info <name>
```

### Sample Output (Table)

```bash
chaoscypher db info default
```

```
╭──────────── Database ────────────╮
│ default (current)                │
╰──────────────────────────────────╯
  Location: /home/user/.local/share/chaoscypher/databases/default
  Size: 2.3 MB
  Modified: 2026-03-09 14:22:01

  Contents:
    Nodes: 1,247
    Edges: 3,891
    Templates: 12
```

### Sample Output (JSON)

```bash
chaoscypher db info default --json
```

```json
{
  "name": "default",
  "path": "/home/user/.local/share/chaoscypher/databases/default",
  "size": 2411724,
  "last_modified": "2026-03-09T14:22:01+00:00",
  "is_current": true,
  "contents": {
    "nodes": 1247,
    "edges": 3891,
    "templates": 12
  }
}
```

### Error: Not Found

```bash
chaoscypher db info nonexistent
```

```
Database not found: nonexistent

Expected location: /home/user/.local/share/chaoscypher/databases/nonexistent
```

### Options

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON |

---

## Delete a Database

Permanently remove a database and all its data:

```bash
chaoscypher db delete <name>
```

### Sample Output (With Confirmation)

```bash
chaoscypher db delete old-project
```

```
WARNING: This will permanently delete:
  - All knowledge graph data
  - All sources and extractions
  - All workflows and triggers
  - Search indexes

  Location: /home/user/.local/share/chaoscypher/databases/old-project

Are you sure you want to delete 'old-project'? [y/N]: y
Deleted database 'old-project'
```

### Skip Confirmation

```bash
chaoscypher db delete old-project --yes
```

```
Deleted database 'old-project'
```

### Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |

:::warning[Safety checks]

- Cannot delete the `default` database
- Cannot delete the currently active database (switch to another database first)

:::

### Error: Cannot Delete Default

```bash
chaoscypher db delete default
```

```
Cannot delete the 'default' database.
```

### Error: Cannot Delete Current

```bash
chaoscypher db delete research-2026
```

```
Cannot delete the current database.

Switch to another database first:
  chaoscypher db switch default
```

---

## Schema Migrations (db migrate)

The app auto-applies **safe** migrations on startup, so you usually never need
to touch this command group. It exists for the cases that need operator
judgement — `needs_confirmation`/destructive migrations, dedup decisions, or
recovery from a half-applied upgrade.

All three subcommands operate on the database named by `--database` or, when
omitted, the one selected by `chaoscypher db switch` (the current database).

```bash
chaoscypher db migrate --help
```

### Status

List pending migrations and the upgrade state (`Ready` / `Blocked`), along with
the last pre-upgrade backup if one exists:

```bash
chaoscypher db migrate status [--database <name>] [--json]
```

When the database is up to date, it prints:

```
No pending migrations. Database is up to date.
```

Otherwise it prints the upgrade state and a table of pending migrations
(revision, tier, description). Pass `--json` for a machine-readable dump.

### Apply

Apply all pending migrations:

```bash
chaoscypher db migrate apply [--database <name>] [--yes/-y]
```

`safe_auto` migrations are applied immediately. If any pending migration is a
higher tier (e.g. `needs_confirmation`), you are prompted to confirm first —
pass `--yes`/`-y` to skip the prompt.

### Rollback

Restore the database from the pre-upgrade backup:

```bash
chaoscypher db migrate rollback [--database <name>] [--yes/-y]
```

This is only valid when a pre-upgrade backup exists (i.e. an upgrade was
blocked). **All work done since that backup is lost.** You are prompted to
confirm unless `--yes`/`-y` is passed.

:::warning[Blocked databases]

When a database's upgrade state is blocked, the CLI refuses to run normal
DB-touching commands and points you here. Run `db migrate status` to see what's
pending, then `db migrate apply` (or `db migrate rollback` to back out of a
failed upgrade).

:::

### Options

| Option | Applies to | Description |
|--------|-----------|-------------|
| `--database <name>` | all three | Target database. Defaults to the current database. |
| `--json` | `status` | Emit machine-readable JSON instead of the rendered table. |
| `--yes`, `-y` | `apply`, `rollback` | Skip the confirmation prompt. |

---

## Storage Location

Databases are stored under the platform-specific data directory:

| Platform | Path |
|----------|------|
| Linux | `~/.local/share/chaoscypher/databases/{db_name}/` |
| macOS | `~/Library/Application Support/chaoscypher/databases/{db_name}/` |
| Windows | `%LOCALAPPDATA%\chaoscypher\databases\{db_name}\` |

Each database directory contains:

```
{db_name}/
└── app.db       # All data: sources, graph, chat, workflows, search indexes (FTS5 + sqlite-vec)
```

You can override the data directory with the `CHAOSCYPHER_DATA_DIR` environment variable:

```bash
export CHAOSCYPHER_DATA_DIR=/custom/path
# Databases will be stored at /custom/path/databases/{db_name}/
```
