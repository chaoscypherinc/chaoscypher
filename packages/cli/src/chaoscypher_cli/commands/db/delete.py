# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete command - Delete a database."""

from __future__ import annotations

import shutil
import sys
from typing import TYPE_CHECKING

import click
from rich.console import Console

from chaoscypher_cli.commands.db.create import validate_database_name
from chaoscypher_cli.commands.db.list import get_database_info, get_databases_dir
from chaoscypher_cli.context import get_database_name


if TYPE_CHECKING:
    from pathlib import Path


console = Console()


def _same_database_dir(candidate: Path, protected: Path) -> bool:
    """Return True when ``candidate`` names the same directory as ``protected``.

    Path equality alone is case-sensitive for ``PosixPath``, but macOS's
    default APFS volume is case-insensitive, so ``Default`` opens the
    ``default`` directory there. ``samefile`` compares the directories the
    filesystem actually resolves, covering every case-folding (or aliased)
    filesystem while leaving genuinely distinct directories deletable.
    """
    if candidate == protected:
        return True
    try:
        return candidate.exists() and protected.exists() and candidate.samefile(protected)
    except OSError:
        # Unresolvable paths cannot be proven distinct; refuse the delete.
        return True


@click.command()
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def delete(name: str, yes: bool) -> None:
    """Delete a database.

    Permanently removes the database and all its data.
    Cannot delete the current database or 'default'.

    Example:
        chaoscypher db delete old-project
        chaoscypher db delete old-project --yes
    """
    # Same validation as `db create`: also blocks path traversal (..,
    # separators, absolute paths) before any filesystem path is built.
    if not validate_database_name(name):
        console.print(f"[red]Invalid database name:[/red] {name}")
        console.print("Database names must be alphanumeric (hyphens and underscores allowed).")
        sys.exit(1)

    databases_dir = get_databases_dir()
    db_path = databases_dir / name

    # Filesystem-level guards instead of string equality: on case-insensitive
    # filesystems (Windows, macOS APFS) 'Default' opens the 'default' directory.
    if _same_database_dir(db_path, databases_dir / "default"):
        console.print("[red]Cannot delete the 'default' database.[/red]")
        sys.exit(1)

    if _same_database_dir(db_path, databases_dir / get_database_name()):
        console.print("[red]Cannot delete the current database.[/red]")
        console.print("\nSwitch to another database first:")
        console.print("  [cyan]chaoscypher db switch default[/cyan]")
        sys.exit(1)

    # Verify database exists
    db_info = get_database_info(name, db_path)
    if not db_info:
        console.print(f"[red]Database not found:[/red] {name}")
        sys.exit(1)

    # Confirmation prompt
    if not yes:
        console.print("\n[yellow]WARNING:[/yellow] This will permanently delete:")
        console.print("  - All knowledge graph data")
        console.print("  - All sources and extractions")
        console.print("  - All workflows and triggers")
        console.print("  - Search indexes")
        console.print(f"\n  Location: {db_path}\n")

        if not click.confirm(f"Are you sure you want to delete '{name}'?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            return

    # Delete
    try:
        shutil.rmtree(db_path)
        console.print(f"[green]Deleted database '{name}'[/green]")
    except Exception as e:
        console.print(f"[red]Failed to delete database:[/red] {e}")
        sys.exit(1)
