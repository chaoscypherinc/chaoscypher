---
title: "ADR-0006: Re-adopt Alembic for Schema Migrations"
description: Decision record replacing the reflective auto-migrator with Alembic-tracked migration files to support safe rollbacks and constraint changes.
---

# ADR-0006: Re-adopt Alembic for schema migrations

**Status:** Accepted
**Date:** 2026-04-20
**Supersedes (in part):** the reflective auto-migrator approach adopted after [ADR-0002 — Dependency / license policy](./0002-dependency-license-policy.md)

## Context

Alongside the dependency-policy work in [ADR-0002](./0002-dependency-license-policy.md) (Accepted 2026-02-06), we kept a reflective auto-migrator that ran `ALTER TABLE` at startup based on SQLModel introspection (`chaoscypher_core.adapters.sqlite.engine.apply_schema_updates`). Alembic was temporarily removed from dependencies in favour of this approach (April 2026).

As the schema grew, three problems surfaced:

1. **Drift hazards** — branch merges with conflicting model changes left the running database in an undefined intermediate state.
2. **No rollback** — once the auto-migrator applied an `ALTER`, there was no recorded operation to reverse it.
3. **Unsafe constraint changes** — adding `NOT NULL`, `UNIQUE`, foreign key, or rebuild-table changes with the reflective migrator silently failed when existing rows didn't qualify, or carried stale constraints forward through upgrades undetected.

## Decision

Re-adopt Alembic as the canonical schema migration framework, effective 2026-04-20.

- All schema changes ship as Alembic revision files under `packages/core/src/chaoscypher_core/database/migrations/versions/`.
- Cortex runs `alembic upgrade head` on startup before serving any request.
- The reflective auto-migrator (`apply_schema_updates`) is retained only for two responsibilities:
  - Idempotent data backfills that do not change schema.
  - The constraint-drift logger that warns when models and the live schema disagree.

## Consequences

### Positive

- Reviewable migrations; reversible operations; tested before merge.
- Clear upgrade/downgrade story for operators.
- Constraint, FK, and rebuild-table changes are now expressible and safe.

### Negative

- Adds an Alembic dependency (re-added after the brief April 2026 removal).
- Contributors must learn `alembic revision --autogenerate -m "..."`.
- A migration step now sits in the Cortex startup path.

### Neutral

- Existing data backfill code in `apply_schema_updates` continues to run after `alembic upgrade head` for now; over time, those backfills migrate into Alembic data-migration revisions and the function shrinks to the constraint-drift logger only.
- The baseline revision (`0001`) is a full-schema create: its `upgrade()` issues `create_table` for every table so that a fresh `alembic upgrade head` reproduces exactly what `SQLModel.metadata` declares. It is the single source of truth for the starting schema; all later model changes ship as additional revisions layered on top. (This corrects an earlier draft of this ADR that described the baseline as "no DDL" — that was never accurate for this codebase.)

## Implementation notes

- Contributor workflow for a schema change:
  1. Update the SQLModel models, then add a new revision file under `packages/core/src/chaoscypher_core/database/migrations/versions/` — either generated with `alembic revision --autogenerate -m "..."` or written by hand mirroring the existing numbered revisions.
  2. Declare the migration tier in the revision file via the `CC_TIER` module attribute: `"safe_auto"` (purely additive), `"needs_confirmation"` (data or type changes), or `"manual"` (destructive changes). By default the startup runner auto-applies all pending migrations; with `migrations.auto_apply_destructive` disabled it stops before the first `manual` migration and waits for an operator to apply it.
  3. Run the migration parity tests under `packages/core/tests/unit/database/migrations/` (`test_baseline_matches_metadata.py`, `test_no_undeclared_changes.py`) — they fail whenever the migration chain and `SQLModel.metadata` disagree.
- The baseline revision (`0001`) carries the full schema DDL — its `upgrade()` creates every table, verified equivalent to `SQLModel.metadata` by the `test_baseline_matches_metadata` and `test_no_undeclared_changes` parity tests. Subsequent revisions apply constraint/index/data changes on top.

### 2026-06-02 — migration chain squashed for the public launch

For the public-repo launch the 50-file chain (`0001`–`0050`) was squashed into a single consolidated `0001` baseline, regenerated from `SQLModel.metadata` via `alembic revision --autogenerate` against an empty database. The revision id stays `0001` and `down_revision` stays `None`, so the self-healing startup runner's `_BASELINE_REVISION` and `ensure_stamped` semantics are unchanged. Databases created before the squash — recorded at a now-deleted revision such as `0050_…` — auto-recover on the next startup: `ensure_stamped` re-stamps any revision absent from the script directory to the baseline. The schema is unchanged, so no data is lost.
