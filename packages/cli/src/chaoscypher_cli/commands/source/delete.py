# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete command - Remove an source_processing record."""

import sys

import click
from rich.console import Console
from rich.prompt import Confirm

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("file_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.option("--database", "-d", default="default", help="Database name")
def delete(file_id: str, force: bool, database: str) -> None:
    """Delete an ingested file record.

    FILE_ID is the source_processing file identifier.
    This removes the source record, orphaned graph nodes, search-index
    rows, the staging file, and any rendered vision images — the same
    cascade the API performs.

    Example:
        chaoscypher source delete if_abc123def456
        chaoscypher source delete if_abc123def456 --force
    """
    try:
        ctx = get_context(database_name=database)

        # Get source first
        file_record = ctx.storage_adapter.get_source(file_id, ctx.database_name)

        if not file_record:
            console.print(f"[red]File not found:[/red] {file_id}")
            sys.exit(1)

        console.print("[cyan]File to delete:[/cyan]")
        console.print(f"  [dim]ID:[/dim] {file_record.get('id')}")
        console.print(f"  [dim]Filename:[/dim] {file_record.get('filename')}")
        console.print(f"  [dim]Status:[/dim] {file_record.get('status')}")

        if not force:
            if not Confirm.ask("\nAre you sure you want to delete this file?", default=False):
                console.print("[yellow]Cancelled.[/yellow]")
                return

        # Same cascade as the API path: SQL cascade + orphaned graph nodes in
        # one transaction, then vector rows, staged file, and vision images.
        from chaoscypher_core.services.graph.management.source import SourceService

        service = SourceService(
            repository=ctx.storage_adapter,
            database_name=ctx.database_name,
            settings=ctx.settings,
        )
        deleted = service.delete_source(
            file_id,
            graph_repo=ctx.graph_repository,
            search_repo=ctx.search_repository,
        )
        if not deleted:
            console.print(f"[red]File not found:[/red] {file_id}")
            sys.exit(1)

        console.print("[green]✓ File deleted successfully[/green]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
