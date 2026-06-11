# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Create command - Create a new database."""

from __future__ import annotations

import re
import sys

import click
from rich.console import Console

from chaoscypher_cli.commands.db.list import get_databases_dir
from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_unwrapped


console = Console()


def validate_database_name(name: str) -> bool:
    """Validate database name.

    Database names must be alphanumeric with hyphens and underscores allowed.

    Args:
        name: Database name to validate.

    Returns:
        True if valid, False otherwise.
    """
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))


@click.command()
@click.argument("name")
def create(name: str) -> None:
    """Create a new database.

    Creates the database directory containing:
    - app.db (SQLite database; search indexes — FTS5 + sqlite-vec — live inside it)

    Database names must be alphanumeric (hyphens and underscores allowed).

    Example:
        chaoscypher db create my-project
        chaoscypher db create research_2024
    """
    # Validate name
    if not validate_database_name(name):
        console.print("[red]Invalid database name.[/red]")
        console.print("Names must be alphanumeric (hyphens and underscores allowed).")
        sys.exit(1)

    databases_dir = get_databases_dir()
    db_path = databases_dir / name
    app_db_path = db_path / "app.db"

    # Check if database is already initialized (has app.db)
    if app_db_path.exists():
        console.print(f"[red]Database already exists:[/red] {name}")
        sys.exit(1)

    # Create by getting context (auto-creates directory structure)
    try:
        console.print(f"Creating database '{name}'...")
        ctx = get_context(database_name=name, auto_connect=True)

        console.print(f"\n[green]Created database '{name}'[/green]")
        print_unwrapped(f"  [dim]Location:[/dim] {ctx.database_dir}")
        console.print(f"\nSwitch to it with: [cyan]chaoscypher db switch {name}[/cyan]")

    except Exception as e:
        console.print(f"[red]Failed to create database:[/red] {e}")
        sys.exit(1)
