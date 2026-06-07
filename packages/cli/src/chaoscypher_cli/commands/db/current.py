# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Current command - Show the current database."""

from __future__ import annotations

import click
from rich.console import Console

from chaoscypher_cli.commands.db.list import format_size, get_database_info, get_databases_dir
from chaoscypher_cli.context import get_database_name
from chaoscypher_cli.utils.console import print_unwrapped


console = Console()


@click.command()
@click.option("--verbose", "-v", is_flag=True, help="Show detailed information")
def current(verbose: bool) -> None:
    """Show the current default database.

    Displays the database that commands use by default.
    Use --verbose for additional details.

    Example:
        chaoscypher db current
        chaoscypher db current -v
    """
    current_db = get_database_name()

    if not verbose:
        console.print(current_db)
        return

    # Verbose output with details
    databases_dir = get_databases_dir()
    db_path = databases_dir / current_db

    console.print(f"[bold]Current database:[/bold] {current_db}")

    info = get_database_info(current_db, db_path)
    if info:
        print_unwrapped(f"  [dim]Location:[/dim] {info['path']}")
        console.print(f"  [dim]Size:[/dim] {format_size(info['size'])}")
        modified = info["last_modified"][:19].replace("T", " ")
        console.print(f"  [dim]Last modified:[/dim] {modified}")
    else:
        print_unwrapped(f"  [dim]Location:[/dim] {db_path}")
        console.print("  [yellow]Database not initialized[/yellow]")
