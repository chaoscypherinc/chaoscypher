# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show all databases."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_database_name
from chaoscypher_cli.engine_config import data_dir
from chaoscypher_cli.utils.console import print_json, print_unwrapped
from chaoscypher_core.services.package.archive.info import format_size


if TYPE_CHECKING:
    from pathlib import Path


console = Console()


def get_databases_dir() -> Path:
    """Get the databases directory path.

    Resolves CHAOSCYPHER_DATA_DIR / platformdirs directly — the same
    resolution CLIContext uses for engine bootstrap — rather than cli.yaml's
    ``paths`` section, which CLIContext never honored. A divergent value
    there would make ``db create`` (context-driven) and ``db list``
    disagree about where databases live.

    Returns:
        Path to the databases directory within the data directory.
    """
    return data_dir() / "databases"


def get_database_info(name: str, db_path: Path) -> dict | None:
    """Get information about a database.

    Args:
        name: Database name.
        db_path: Path to the database directory.

    Returns:
        Dictionary with database info, or None if not a valid database.
    """
    app_db = db_path / "app.db"
    if not app_db.exists():
        return None

    stat = app_db.stat()
    return {
        "name": name,
        "path": str(db_path),
        "size": stat.st_size,
        "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


@click.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--quiet", "-q", is_flag=True, help="Only show database names")
def list_databases(as_json: bool, quiet: bool) -> None:
    """List all available databases.

    Shows databases with their size and last modified time.
    The current database is marked with 'current' status.

    Example:
        chaoscypher db list
        chaoscypher db list --json
        chaoscypher db list --quiet
    """
    current_db = get_database_name()
    databases_dir = get_databases_dir()

    # Collect database info
    databases = []
    if databases_dir.exists():
        for entry in sorted(databases_dir.iterdir()):
            if entry.is_dir():
                info = get_database_info(entry.name, entry)
                if info:
                    info["is_current"] = entry.name == current_db
                    databases.append(info)

    # Output format
    if quiet:
        for db in databases:
            console.print(db["name"])
        return

    if as_json:
        print_json(json.dumps(databases, indent=2))
        return

    # Table output
    if not databases:
        console.print("[dim]No databases found.[/dim]")
        console.print("\nCreate one with: [cyan]chaoscypher db create <name>[/cyan]")
        print_unwrapped(f"Databases directory: {databases_dir}")
        return

    table = Table(title="Databases", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Modified", style="dim")
    table.add_column("Status", style="green")

    for db in databases:
        status = "current" if db["is_current"] else ""
        # Parse ISO timestamp for display
        modified = db["last_modified"][:19].replace("T", " ")
        table.add_row(
            db["name"],
            format_size(db["size"]),
            modified,
            status,
        )

    console.print(table)
