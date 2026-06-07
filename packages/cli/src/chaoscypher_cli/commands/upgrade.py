# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""chaoscypher upgrade — apply pending Alembic migrations to a database."""

from __future__ import annotations

import click

from chaoscypher_cli.context import get_database_name


def _resolve_database(database: str | None) -> str:
    """Resolve the target database name, defaulting to the current one.

    The current database lives in settings.yaml as of the 2026-06 config
    unification; ``get_database_name`` does the cheap raw peek (env var →
    settings.yaml current_database → "default").
    """
    if database:
        return database
    return get_database_name()


@click.command("upgrade")
@click.option(
    "--database",
    default=None,
    help="Database name. Defaults to the current database.",
)
def upgrade_command(database: str | None) -> None:
    """Apply pending Alembic migrations to the configured database.

    Routes through Core's ``UpgradeService`` — the same code path the Cortex
    ``/upgrade`` API and ``chaoscypher db migrate apply`` use — so it targets
    the resolved database and reuses the app's migration runner. (Cortex
    applies these automatically on startup via ``run_startup_migrations``;
    this is the operator-grade equivalent for ad-hoc invocation. For the
    confirmation-gated workflow on risky migrations, use ``db migrate apply``.)
    """
    from chaoscypher_core.database.migrations.upgrade import UpgradeService

    db = _resolve_database(database)
    service = UpgradeService(database_name=db)

    pending = service.pending()
    if pending.ready and not pending.blocked_on:
        click.echo(f"Schema is at head ({db}).")
        return

    result = service.apply()
    click.echo(
        f"Applied {len(result.applied)} migration(s) to {db}; "
        f"now at revision {result.current_revision}."
    )


__all__ = ["upgrade_command"]
