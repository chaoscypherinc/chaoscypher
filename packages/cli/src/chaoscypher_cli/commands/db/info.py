# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Info command - Show detailed database information."""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console
from rich.panel import Panel

from chaoscypher_cli.commands.db.list import format_size, get_database_info, get_databases_dir
from chaoscypher_cli.context import get_context, get_database_name
from chaoscypher_cli.utils.console import print_json, print_unwrapped


console = Console()


@click.command()
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def info(name: str, as_json: bool) -> None:
    """Show detailed information about a database.

    Displays filesystem metadata and content counts.

    Example:
        chaoscypher db info default
        chaoscypher db info my-project --json
    """
    databases_dir = get_databases_dir()
    db_path = databases_dir / name

    # Get basic info
    db_info = get_database_info(name, db_path)
    if not db_info:
        console.print(f"[red]Database not found:[/red] {name}")
        print_unwrapped(f"\nExpected location: {db_path}")
        sys.exit(1)

    # Get content counts by connecting to the database
    counts = {}
    try:
        ctx = get_context(database_name=name, auto_connect=True)
        stats = ctx.get_stats()
        counts = {
            "nodes": stats.get("nodes", 0),
            "edges": stats.get("edges", 0),
            "templates": stats.get("templates", 0),
        }
    except Exception:
        # Can't connect - just show filesystem info
        pass

    # Check if current
    is_current = name == get_database_name()

    # Build result
    result = {
        **db_info,
        "is_current": is_current,
        "contents": counts,
    }

    if as_json:
        print_json(json.dumps(result, indent=2))
        return

    # Rich output
    status = " [green](current)[/green]" if is_current else ""
    console.print(Panel(f"[bold]{name}[/bold]{status}", title="Database"))

    print_unwrapped(f"  [dim]Location:[/dim] {db_info['path']}")
    console.print(f"  [dim]Size:[/dim] {format_size(db_info['size'])}")
    modified = db_info["last_modified"][:19].replace("T", " ")
    console.print(f"  [dim]Modified:[/dim] {modified}")

    if counts:
        console.print("\n  [bold]Contents:[/bold]")
        console.print(f"    Nodes: {counts.get('nodes', 0):,}")
        console.print(f"    Edges: {counts.get('edges', 0):,}")
        console.print(f"    Templates: {counts.get('templates', 0):,}")
