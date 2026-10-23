# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory export command - Export one scope's memories to a .ccx memory pack.

The pack carries each memory with who said it, its scope and its date, and the
facts it links, so ``chaoscypher graph package load`` brings it back as memories.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--scope", "-s", default=None, help="Scope to export (default: every memory)")
@click.option(
    "--title",
    "-t",
    default=None,
    help="Display title for the pack (default: memory:SCOPE)",
)
@click.option("--database", "-d", default="default", help="Database name")
def export(output: str, scope: str | None, title: str | None, database: str) -> None:
    """Export the memories of one scope to a .ccx memory pack.

    Import the pack into another database with chaoscypher graph package load;
    the import verifies the package's signatures.

    \b
    Examples:
        chaoscypher memory export --scope work work.ccx
        chaoscypher memory export everything.ccx
        chaoscypher memory export -s work -t "Work notes" -d my-project work.ccx
    """  # noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.
    try:
        ctx = get_context(database_name=database)
        engine = ctx._engine
        if engine is None:
            msg = "Engine not initialized — context.connect() must be called first"
            raise RuntimeError(msg)

        from chaoscypher_core.mcp.memory_pack import export_memory_pack, memory_source_ids

        count = len(memory_source_ids(engine, scope))
        data = export_memory_pack(engine, scope=scope, title=title)

        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
        console.print(f"[green]✓ Exported {count} memories to {output_path}[/green]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
