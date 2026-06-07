---
id: backup-restore
title: Backup and Restore
description: What chaoscypher backup captures, how to restore, and a worked retention example.
---

# Backup and Restore

Chaos Cypher stores all persistent state in a single SQLite database (`app.db`) per knowledge base. Backups are point-in-time copies of that file, written using SQLite's online backup API so you can back up a live, actively-used database without stopping the container.

## What a backup captures

A backup captures the entire `app.db` file for a given database, which includes:

- **All graph data** — nodes, edges, templates, relationships.
- **All source metadata and extraction results** — source rows, chunk records, quality counters, stage-progress entries.
- **Settings** — every configuration row stored in the database (LLM provider choice, embedding settings, filter presets).
- **Automations** — workflow definitions, trigger rules, execution history.
- **Chat history** — all chat sessions and message threads for that database.
- **Authentication tokens** — hashed credentials and session data.

What a backup does **not** capture:

- **Uploaded source files** — raw documents under `<data_dir>/databases/<db_name>/uploads/`. Back these up separately if you need to re-run extraction without re-uploading.
- **Valkey queue state** — in-flight tasks. Drain the queue (wait for the Queue Monitor to show zero pending tasks) before taking a backup you intend to restore from.
- **Vector index files** — the sqlite-vec virtual tables are embedded in `app.db`, so they are included. Cached embedding model weights under `<data_dir>/models/` are not; they are re-downloaded on demand.
- **Container logs** — `/data/logs/` is not part of the database backup.

## Creating a backup

### Via the REST API

The primary backup interface is the Cortex API. The endpoint creates a clean, compacted copy using `VACUUM INTO`, which compresses freed pages and avoids blocking active writers.

:::note Authentication
Every request must authenticate with either the browser session cookie or an API key. There is no HTTP Basic Auth. Mint an API key via **Settings → API Keys** in the web UI (which calls `POST /api/v1/auth/keys`); keys are prefixed `cc_live_`. Pass it as a Bearer token, e.g. `export CHAOSCYPHER_API_KEY=cc_live_...`. Note that `http://localhost` resolves through the nginx edge, which performs `auth_request` verification, so the same auth applies to every example on this page.
:::

```bash
# Create a backup of the current database
curl -s -H "Authorization: Bearer $CHAOSCYPHER_API_KEY" \
  -X POST http://localhost/api/v1/backup \
  | jq .

# Example response
{
  "database": "default",
  "filename": "app_20260601_143022.db",
  "size": 12582912,
  "created_at": "20260601_143022"
}
```

The backup file is written to:

```
<data_dir>/backups/<database_name>/app_YYYYMMDD_HHMMSS.db
```

For example, with `data_dir=/data` and database `default`:

```
/data/backups/default/app_20260601_143022.db
```

### Via the maintenance UI

Open **Settings → Maintenance** in the web interface. The Backups panel shows existing backups and a **Create Backup** button.

### Listing existing backups

```bash
curl -s -H "Authorization: Bearer $CHAOSCYPHER_API_KEY" \
  http://localhost/api/v1/backup \
  | jq .backups
```

### Downloading a backup

```bash
curl -s -H "Authorization: Bearer $CHAOSCYPHER_API_KEY" \
  -OJ \
  "http://localhost/api/v1/backup/app_20260601_143022.db/download"
```

## Restoring a backup

### Via the REST API

```bash
curl -s -H "Authorization: Bearer $CHAOSCYPHER_API_KEY" \
  -X POST \
  "http://localhost/api/v1/backup/app_20260601_143022.db/restore"
```

The restore endpoint:

1. Validates the backup file is a valid SQLite database.
2. Creates a safety copy of the current database at `app.db.pre_restore` before overwriting.
3. Removes WAL journal files (`app.db-wal`, `app.db-shm`) to prevent post-restore corruption.
4. Replaces `app.db` with the backup file.
5. Invalidates the SQLAlchemy connection pool so subsequent requests use the restored database immediately.

### Via the maintenance UI

Open **Settings → Maintenance → Backups**, find the backup in the list, and click **Restore**.

### Manual restore (container stopped)

If the API is unreachable, restore manually:

```bash
# Stop the container
docker compose stop chaoscypher

# Replace the database (adjust paths to match your data_dir and database name)
cp /data/backups/default/app_20260601_143022.db \
   /data/databases/default/app.db

# Remove WAL files to prevent corruption
rm -f /data/databases/default/app.db-wal \
      /data/databases/default/app.db-shm

# Restart
docker compose start chaoscypher
```

### After restoring

Run `chaoscypher upgrade` (or let Cortex auto-apply on startup) to ensure the schema is current if the backup predates a migration:

```bash
chaoscypher upgrade
```

## Migration pre-upgrade backups

Cortex automatically creates a backup before applying any pending Alembic migration. These auto-backups land at:

```
<data_dir>/backups/pre-<first-pending-revision>-<YYYYMMDDTHHMMSSZ>.db
```

To roll back a failed migration, use the `db migrate` command:

```bash
chaoscypher db migrate status    # see what's pending and where the auto-backup is
chaoscypher db migrate rollback  # restore from the pre-upgrade auto-backup
```

See [Upgrading](./upgrading.md) for the full upgrade and rollback procedure.

## Worked example: cron backup with retention

This cron job runs daily at 02:00, creates a backup via the API, and deletes backups older than 14 days from the local filesystem.

```bash
# /etc/cron.d/chaoscypher-backup
# Store the API key in a root-only file (e.g. chmod 600 /etc/chaoscypher/api_key)
# rather than inlining it in the crontab.
0 2 * * * root \
  curl -s -H "Authorization: Bearer $(cat /etc/chaoscypher/api_key)" -X POST http://localhost/api/v1/backup > /dev/null && \
  find /data/backups/default -name "app_*.db" -mtime +14 -delete
```

You can also use the API's delete endpoint to prune old backups by filename:

```bash
# Delete a specific backup
curl -s -H "Authorization: Bearer $CHAOSCYPHER_API_KEY" \
  -X DELETE \
  "http://localhost/api/v1/backup/app_20260515_020000.db"
```

## Backup storage location

By default, backups are written under `<data_dir>/backups/`. If you mount `/data` as a Docker named volume or bind-mount, backups persist across container recreations. Copy the backup directory to off-site storage (object storage, another host) for disaster recovery:

```bash
# Example: rsync to a remote host
rsync -az /data/backups/ user@backup-host:/backups/chaoscypher/
```

## See also

- [Production Deployment](./production.md) — TLS, scaling, log rotation
- [Upgrading](./upgrading.md) — pre-upgrade backup procedure and schema rollback
