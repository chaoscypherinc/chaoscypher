# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher db migrate` — schema migration controls.

Three subcommands mirror the Cortex /upgrade API so CLI users get the
same capabilities the Interface offers:

    chaoscypher db migrate status    — list pending migrations
    chaoscypher db migrate apply     — apply all pending (with --yes to skip prompt)
    chaoscypher db migrate rollback  — restore from pre-upgrade backup

All three operate on the database named by ``--database`` or, when
omitted, the one selected by ``chaoscypher db switch``.
"""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_database_name


console = Console()


def _resolve_database(database: str | None) -> str:
    """Resolve the target database name, defaulting to the current one.

    The current database lives in settings.yaml as of the 2026-06 config
    unification; ``get_database_name`` does the cheap raw peek (env var →
    settings.yaml current_database → "default").
    """
    if database:
        return database
    return get_database_name()


def _service(database: str | None):  # type: ignore[no-untyped-def]
    """Build an UpgradeService pointing at the resolved DB.

    Accepts ``None`` so callers can forward Click's ``--database`` option
    directly; ``_resolve_database`` falls back to the CLI's current DB.
    """
    from chaoscypher_core.database.migrations.upgrade import UpgradeService

    return UpgradeService(database_name=_resolve_database(database))


@click.group(name="migrate")
def migrate() -> None:
    """Manage schema migrations.

    The app auto-applies safe migrations on startup. This command is
    for the cases that need operator judgement — dedup decisions,
    destructive data changes, or recovery from a half-applied upgrade.
    """


@migrate.command("status")
@click.option(
    "--database",
    default=None,
    help="Database name. Defaults to the current database.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of the rendered table.",
)
def status(database: str | None, as_json: bool) -> None:
    """Show pending migrations and upgrade state."""
    svc = _service(database)
    resp = svc.pending()

    if as_json:
        click.echo(resp.model_dump_json(indent=2))
        return

    if resp.ready and not resp.blocked_on:
        console.print("[green]No pending migrations.[/green] Database is up to date.")
        return

    state_line = "[green]Ready[/green]" if resp.ready else "[red]Blocked[/red]"
    console.print(f"Upgrade state: {state_line}")
    if resp.message:
        console.print(f"  {resp.message}")
    if resp.last_backup:
        console.print(f"  Last backup: {resp.last_backup}")

    table = Table(title="Pending migrations")
    table.add_column("Revision")
    table.add_column("Tier")
    table.add_column("Description")
    for m in resp.blocked_on:
        tier_style = {
            "safe_auto": "dim green",
            "needs_confirmation": "yellow",
            "manual": "red",
        }.get(str(m.tier), "")
        table.add_row(m.revision, f"[{tier_style}]{m.tier}[/{tier_style}]", m.description)
    console.print(table)


@migrate.command("apply")
@click.option(
    "--database",
    default=None,
    help="Database name. Defaults to the current database.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip the confirmation prompt (required for needs_confirmation migrations).",
)
def apply(database: str | None, yes: bool) -> None:
    """Apply all pending migrations.

    If any pending migration is tier=needs_confirmation, you'll be
    prompted to confirm unless --yes is passed. Safe_auto migrations
    are applied immediately.
    """
    svc = _service(database)
    pending = svc.pending()

    if pending.ready and not pending.blocked_on:
        console.print("[green]Nothing to apply.[/green]")
        return

    needs_confirm = [m for m in pending.blocked_on if str(m.tier) != "safe_auto"]
    if needs_confirm and not yes:
        console.print(f"[yellow]{len(needs_confirm)} migration(s) need confirmation:[/yellow]")
        for m in needs_confirm:
            console.print(f"  - {m.revision} ({m.tier}): {m.description}")
        if pending.last_backup:
            console.print(f"\nPre-upgrade backup: {pending.last_backup}")
        click.confirm("Apply all pending migrations?", abort=True)

    try:
        result = svc.apply()
    except Exception as exc:
        console.print(f"[red]Migration failed:[/red] {exc}")
        if pending.last_backup:
            console.print(
                f"Roll back with: "
                f"chaoscypher db migrate rollback --database {_resolve_database(database)}"
            )
        sys.exit(1)

    console.print(f"[green]Applied {len(result.applied)} migration(s)[/green]")
    for rev in result.applied:
        console.print(f"  - {rev}")
    console.print(f"Now at revision: [bold]{result.current_revision}[/bold]")


@migrate.command("rollback")
@click.option(
    "--database",
    default=None,
    help="Database name. Defaults to the current database.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip the confirmation prompt.",
)
def rollback(database: str | None, yes: bool) -> None:
    """Restore the DB from the pre-upgrade backup.

    Only valid when an upgrade was blocked (``last_backup`` is set in
    the upgrade-state row). All work done since the backup will be lost.
    """
    svc = _service(database)
    pending = svc.pending()

    if not pending.last_backup:
        console.print("[red]No pre-upgrade backup available.[/red]")
        sys.exit(1)

    if not yes:
        console.print(f"About to restore from: [bold]{pending.last_backup}[/bold]")
        console.print("[yellow]All changes made since that backup will be lost.[/yellow]")
        click.confirm("Proceed with rollback?", abort=True)

    try:
        result = svc.rollback()
    except Exception as exc:
        console.print(f"[red]Rollback failed:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]Restored from:[/green] {result.restored_from}")
    console.print(f"Revision after restore: [bold]{result.revision}[/bold]")


__all__ = ["migrate"]
