---
id: upgrading
title: Upgrading
description: How to upgrade Chaos Cypher from a previous tag, including Alembic migrations and rollback.
---

# Upgrading Chaos Cypher

Chaos Cypher ships as a Docker image plus the Python CLI. This page covers tag-to-tag upgrades, schema migrations, and recovery from a failed upgrade.

## TL;DR

```bash
# all-in-one
docker compose pull && docker compose up -d

# multi-container dev (requires QUEUE_PASSWORD exported — see Installation)
make docker-down && git pull && make docker-dev
```

Cortex runs `alembic upgrade head` on startup before serving any request.

## Pre-upgrade checklist

1. Take a backup via the maintenance UI (**Settings → Maintenance → Backups → Create Backup**) or `POST /api/v1/backup`. See [Backup and Restore](./backup-restore.md) for the full procedure.
2. Read the [changelog](../about/changelog) for breaking changes between your tag and the target tag.
3. Verify disk space — migrations may rewrite tables.
4. **Drain the queue** (see next section).

## Drain the queue before upgrading

ChaosCypher persists in-flight queue tasks in Valkey across container restarts (AOF persistence). Until automatic payload-version negotiation ships, the safe upgrade path is to let the queue drain before swapping images:

1. Stop new submissions — pause any UI imports, disable triggers from the Triggers tab, and avoid CLI batch commands.
2. Wait for `/api/v1/queue/stats` to report 0 pending tasks across all queues (or use the Queue Monitor in the UI).
3. Stop the running container.
4. Start the new image.

If you must upgrade with tasks pending, any task whose `payload_version` is unknown to the new worker will be marked **failed (permanent)** with the reason logged as `task_unsupported_payload_version`. Re-submit those sources from the Source page after the upgrade — the queue itself will not loop on the poison task.

This advisory is temporary — version negotiation is on the roadmap.

## Upgrade flow (all-in-one)

1. Pull the new image: `docker pull ghcr.io/chaoscypherinc/chaoscypher:<tag>`.
2. Stop the old container: `docker compose down`.
3. Start the new: `docker compose up -d`.
4. Watch the logs: `docker compose logs -f chaoscypher`.
   - You'll see `alembic.runtime.migration` lines as Alembic walks pending revisions.
   - When you see `Application startup complete`, the upgrade succeeded.
5. Verify health: `curl http://localhost/api/v1/health`.

## Upgrade flow (multi-container dev)

```bash
make docker-down
git pull origin main
make install     # uv sync --all-packages --extra dev
make docker-dev  # requires QUEUE_PASSWORD exported — see Installation
```

## Rollback

If migrations fail or the new tag is unhealthy:

1. Stop the app container: `docker compose stop chaoscypher`.
2. Restore the pre-upgrade backup (see below for the exact path).
3. Pull the previous tag (or check out the previous commit): `docker pull ghcr.io/chaoscypherinc/chaoscypher:<previous-tag>`.
4. Start: `docker compose up -d`.

### Auto-backup location

Cortex takes a backup automatically before applying any pending migrations. The file is written to the database's own folder:

```
<data_dir>/databases/<db_name>/backups/pre-<first-pending-revision>-<YYYYMMDDTHHMMSSZ>.db
```

For example, if your data directory is `/data`, the database is `default`, and the first pending migration is `0031`, you will find a file such as:

```
/data/databases/default/backups/pre-0031-20260601T123045Z.db
```

To restore the most recent auto-backup, use the dedicated CLI rollback:

```bash
chaoscypher db migrate rollback
```

This finds the matching `pre-<revision>-<timestamp>.db` for the failed migration and restores it. You can also trigger a rollback from the maintenance UI — if startup is blocked on a `NEEDS_CONFIRMATION` migration, the **Maintenance** page shows a **Rollback** button that restores the auto-backup without needing the CLI. For an operator-initiated backup (not the migration auto-backup) the restore path is `POST /api/v1/backup/<filename>/restore` or the manual procedure documented in [Backup and Restore](./backup-restore.md#manual-restore-container-stopped).

### A note on Alembic downgrades

Do not use `alembic downgrade -1` as a recovery path. The baseline revision is the schema floor — its `downgrade()` is an intentional no-op (it does not drop tables), so downgrading below it cannot recover an earlier state. Restore from the auto-backup instead.

### Upgrading a database created before the migration squash

On 2026-06-02 the Alembic migration chain was squashed into a single consolidated `0001` baseline (see [ADR-0006](../architecture/adrs/0006-re-adopt-alembic.md#2026-06-02--migration-chain-squashed-for-the-public-launch)). Databases created before that change recorded a higher revision (for example `0050_chunk_job_finalize_claimed`) that the package no longer ships.

These databases **auto-recover on the next startup** — no operator action is needed:

- The self-healing migrator detects that the recorded revision is unknown to the current script directory and re-stamps the database to the `0001` baseline.
- The on-disk schema is unchanged by the squash (the consolidated baseline reproduces exactly the same tables), so **no data is lost** and nothing is re-applied destructively.

To inspect the state manually:

```bash
# Shows the revision the database is currently stamped at.
chaoscypher db migrate status
```

If a database is somehow left at an unrecognized revision, simply restart Cortex (or run `chaoscypher upgrade`) — startup re-runs the migrator, which re-stamps it to the baseline.

**In plain English:** if you have an existing ChaosCypher database, you don't need to do anything special for this upgrade. The app notices the old version label, quietly relabels it to match the new single baseline, and keeps all your data exactly as it was.

## Operator-grade upgrade: chaoscypher upgrade

`chaoscypher upgrade` runs `alembic upgrade head` against the configured database. Use it when you need to apply pending migrations without restarting the full stack — for example, after pulling a new package version in a dev environment or after restoring a backup.

```bash
chaoscypher upgrade
```

`chaoscypher upgrade` is the supported invocation — it locates the `alembic.ini` shipped inside `chaoscypher_core` and resolves the database path from settings. A bare `uv run alembic upgrade head` from a checkout fails (there is no `alembic.ini` at the repo root, and the shipped one resolves the database URL at runtime); contributors who need raw Alembic can use `alembic -c packages/core/src/chaoscypher_core/database/migrations/alembic.ini upgrade head`.

Per [ADR-0006](../architecture/adrs/0006-re-adopt-alembic.md), Alembic is the authoritative migration tool. Cortex runs the same `alembic upgrade head` on startup, so `chaoscypher upgrade` is useful when you need to trigger migrations outside the normal startup path.

## Breaking-change markers

Major version bumps may require manual steps. The [changelog](../about/changelog) flags these with `BREAKING:`. Read those entries before pulling a new major.

See also:
- [ADR-0006: Re-adopt Alembic](../architecture/adrs/0006-re-adopt-alembic.md)
- [Configuration reference](./configuration.md)
- [Backup and restore](./backup-restore.md)
