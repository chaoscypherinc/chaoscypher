---
id: upgrading
title: Upgrading
description: How to upgrade Chaos Cypher from a previous tag, including Alembic migrations and rollback.
---

# Upgrading Chaos Cypher

Chaos Cypher ships as a Docker image plus the Python CLI. This page covers tag-to-tag upgrades, schema migrations, and recovery from a failed upgrade.

## TL;DR

```bash
# all-in-one (published-image install; if you built from source, use
# `make docker-rebuild` — the repo compose file has no image to pull)
docker compose pull && docker compose up -d

# multi-container dev (requires QUEUE_PASSWORD exported — see Installation)
make docker-down && git pull && make docker-dev
```

Cortex runs `alembic upgrade head` on startup before serving any request.

## Pre-upgrade checklist

1. Take a backup via the web UI (**Settings → Backup → Create Backup Now**) or `POST /api/v1/backup`. See [Backup and Restore](./backup-restore.md) for the full procedure.
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

1. Point the `image:` line of your `docker-compose.yml` at the new tag (`ghcr.io/chaoscypherinc/chaoscypher:<tag>`) and pull it: `docker compose pull`. `docker compose up` starts whatever that line names, so a `docker pull` of some other tag changes nothing. If you track `:latest`, `docker compose pull` alone fetches the new build — note the release you are leaving first, because rolling back means pinning it by number.
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

## Upgrade the edge proxy together with Cortex

In the all-in-one image nginx and Cortex ship in the same container, so this is automatic. If you run the multi-container layout, pull **both** images together. API-key (bearer) auth is throttled per client address, and Cortex learns that address from headers the nginx `/auth/verify` location sets. A new Cortex behind an nginx config that predates those headers still authenticates correctly, but every caller shares one throttle bucket — so five failed key attempts from anywhere pause bearer auth for 60 seconds for everyone. Browser sessions (cookie auth) are unaffected, and the pause clears itself. Matching the two images removes it. The same degraded mode applies behind any *additional* proxy — a Cloudflare Tunnel, Caddy, or Tailscale Serve in front of nginx — because nginx then sees that proxy as `$remote_addr` and forwards the same address for every caller, so all callers again share one bucket; teaching nginx the true client address via `real_ip_header`/`set_real_ip_from` is future work.

## Rollback

If migrations fail or the new tag is unhealthy:

1. Stop the app container: `docker compose stop chaoscypher`.
2. Restore the pre-upgrade backup (see below for the exact path and command).
3. Point the `image:` line back at the previous tag (`ghcr.io/chaoscypherinc/chaoscypher:<previous-tag>`) and pull it: `docker compose pull` (or check out the previous commit for the multi-container layout). If you were on `:latest`, pin the previous version explicitly — restarting `:latest` starts the new build again, which re-migrates the backup you just restored.
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

A new database is not upgraded: building its schema the first time takes no backup and records no upgrade, so there is no post-upgrade notice and nothing to roll back — on any surface (startup, `chaoscypher upgrade`, `chaoscypher db migrate apply`, the **Maintenance** page, MCP `apply_upgrade`). If that first build is cut short (a crash or restart partway), the database is left with empty tables and no recorded version; the next start drops them and builds it again, still without a backup, since nothing in it is data. A database that has tables holding rows but no recorded version is treated as an existing install: it is backed up and upgraded.

On the all-in-one image `/data` is a named Docker volume, so restore the auto-backup inside a helper container that mounts it (find the volume name with `docker volume ls | grep chaoscypher`, and use the newest `pre-*.db` file in the backups folder):

```bash
docker run --rm -v <volume-name>:/data alpine sh -c '
  cp /data/databases/default/backups/pre-0031-20260601T123045Z.db /data/databases/default/app.db &&
  rm -f /data/databases/default/app.db-wal /data/databases/default/app.db-shm'
```

The CLI rollback restores the same file without the copy:

```bash
chaoscypher db migrate rollback
```

It works only while the upgrade-state row still records that backup — after a failed or blocked upgrade, or a data-changing one, whose record lasts until the next upgrade or a rollback (a restart keeps it). A successful upgrade that applied only safe migrations clears the record, and the command then exits with "No pre-upgrade backup available"; use the copy above. It also reads the data directory of the machine it runs on, so a CLI installed on the host does not see the all-in-one container's volume. You can also trigger a rollback from the maintenance UI — if startup is blocked on a `NEEDS_CONFIRMATION` migration, the **Maintenance** page shows a **Rollback** button that restores the auto-backup without needing the CLI. For an operator-initiated backup (not the migration auto-backup) the restore path is `POST /api/v1/backup/<filename>/restore` or the manual procedure documented in [Backup and Restore](./backup-restore.md#manual-restore-container-stopped).

### A note on Alembic downgrades

Do not use `alembic downgrade -1` as a recovery path. The baseline revision is the schema floor — its `downgrade()` is an intentional no-op (it does not drop tables), so downgrading below it cannot recover an earlier state. Restore from the auto-backup instead.

### Databases created before the migration squash are not supported

On 2026-06-02 the Alembic migration chain was squashed into a single consolidated `0001` baseline (see [ADR-0006](../architecture/adrs/0006-re-adopt-alembic.md#2026-06-02--migration-chain-squashed-for-the-public-launch)). Databases created before that change recorded a higher revision (for example `0050_chunk_job_finalize_claimed`) that the package no longer ships.

The squash predates ChaosCypher's first public release (v0.1.0), so **no released build ever produced such a database.** They are unsupported, and startup refuses them instead of trying to upgrade them:

```
This database is stamped at migration revision '0050_chunk_job_finalize_claimed',
which is not part of this build's migration history ...
```

**First, check whether you downgraded.** The same message appears when a database is stamped *ahead* of the running build — for example after rolling an image back. In that case the database is healthy and nothing needs recovering: re-install the newer version of ChaosCypher and it will start normally. The revision id in the message tells you which case you are in; a pre-squash id is a high number from the retired chain, such as `0050_…`.

If the database really is from before the squash, back up the database file, then either:

- **Re-create the database** — move the old file aside and let ChaosCypher build a fresh one, or
- **Export and re-import** — export your data from a build that still opens the old database, then import it into a current install.

**In plain English:** the database doesn't match this version of ChaosCypher. If you just downgraded, put the newer version back. If not, the database is older than the first public release and is too old to upgrade — ChaosCypher tells you so at startup rather than silently mangling it, so save a copy, start a new database, and bring your data across.

### Databases from before the core entity types

Since the core entity types (the floor's eight types, with pack subtypes, see [Extraction Domains](../user-guide/domains.md)), a database's graph is **not migrated in place**. A database whose graph was extracted before them still opens and works: its nodes keep their old type names (`Character`, `Military Unit`) and their old per-type templates, and nothing is rewritten. The dashboard shows a one-line notice, `GET /api/v1/databases/current` returns it as `notice`, and the log carries one `database_predates_core_types` warning per database.

To bring such a database onto the core types:

1. **Export it with the previous version** — before updating, **Settings → General → Import & Export → Export** (or `chaoscypher graph package export`), with sources included.
2. **Update** Chaos Cypher.
3. **Import the export** into a new database — **Settings → General → Import & Export → Import** (see [Import & Export](../user-guide/import-export.md#importing)).

The import maps the old types: each node is stored under its core type with the old name as its `entity_subtype`, a name the mapping table does not hold is kept as a `Concept` flagged `unplaced_type`, the old per-type templates give way to one per core type, and relationships come across unchanged. The import's result lists the mapping (`core_types_mapped`: old type, core type, count).

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
