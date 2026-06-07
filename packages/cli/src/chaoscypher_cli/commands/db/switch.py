# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Switch command - Set the current database."""

from __future__ import annotations

import sys

import click
from rich.console import Console

from chaoscypher_cli.commands.db.list import get_database_info, get_databases_dir


console = Console()


@click.command()
@click.argument("name")
def switch(name: str) -> None:
    """Switch to a different database.

    Sets the default database for subsequent commands.
    The database must already exist.

    Example:
        chaoscypher db switch my-project
    """
    databases_dir = get_databases_dir()
    db_path = databases_dir / name

    # Verify database exists
    db_info = get_database_info(name, db_path)
    if not db_info:
        console.print(f"[red]Database not found:[/red] {name}")
        console.print(f"\nCreate it with: [cyan]chaoscypher db create {name}[/cyan]")
        sys.exit(1)

    # Persist to settings.yaml — the single home for engine-level config
    # (2026-06 config unification). ConfigManager validates and writes
    # atomically; Cortex on the same data_dir picks the change up on reload.
    from chaoscypher_core.app_config import get_config_manager

    get_config_manager().update_settings({"current_database": name})

    console.print(f"[green]Switched to database '{name}'[/green]")
