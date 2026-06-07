# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete command - Delete a database."""

from __future__ import annotations

import shutil
import sys

import click
from rich.console import Console

from chaoscypher_cli.commands.db.list import get_database_info, get_databases_dir
from chaoscypher_cli.context import get_database_name


console = Console()


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
    # Safety check: cannot delete 'default'
    if name == "default":
        console.print("[red]Cannot delete the 'default' database.[/red]")
        sys.exit(1)

    # Safety check: cannot delete current database
    if name == get_database_name():
        console.print("[red]Cannot delete the current database.[/red]")
        console.print("\nSwitch to another database first:")
        console.print("  [cyan]chaoscypher db switch default[/cyan]")
        sys.exit(1)

    databases_dir = get_databases_dir()
    db_path = databases_dir / name

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
