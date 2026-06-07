# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package load command - Import a .ccx package file.

Imports a CCX (Chaos Cypher eXchange) package file into the
knowledge graph, including templates, nodes, edges, and workflows.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from chaoscypher_cli.context import get_context


if TYPE_CHECKING:
    from chaoscypher_core.services.package.importer.models import ImportStats

console = Console()


@click.command("load")
@click.argument("package", type=click.Path(exists=True))
@click.option(
    "--merge/--replace",
    default=False,
    help="Reuse local templates when a CCX template shares the same name. "
    "Default is --replace: every import creates fresh, fully self-contained templates.",
)
@click.option("--templates/--no-templates", default=True, help="Import templates (default: yes)")
@click.option(
    "--knowledge/--no-knowledge",
    default=True,
    help="Import knowledge nodes and edges (default: yes)",
)
@click.option("--workflows/--no-workflows", default=True, help="Import workflows (default: yes)")
@click.option("--database", "-d", default="default", help="Database name")
def load(
    package: str,
    merge: bool,
    templates: bool,
    knowledge: bool,
    workflows: bool,
    database: str,
) -> None:
    r"""Import a .ccx package file into the knowledge graph.

    Imports the selected content types from a CCX package. Each import is
    fully self-contained: every entity, including templates, is given a
    fresh ID. Pass --merge to reuse local templates that share a name with
    a template inside the package.

    \b
    Examples:
        chaoscypher package load my-knowledge.ccx
        chaoscypher package load export.ccx --no-templates
        chaoscypher package load backup.ccx --merge
        chaoscypher package load data.ccx -d my-project
    """
    try:
        ctx = get_context(database_name=database)

        from chaoscypher_core.services.package.importer import ImportOptions, ImportService

        # Create import service
        service = ImportService(
            graph_repository=ctx.graph_repository,
            sources_repository=None,  # CLI doesn't have sources repo
            workflow_db=None,  # CLI doesn't have workflow DB for triggers
        )

        # Build options
        options = ImportOptions(
            verify_checksums=True,
            skip_existing_templates=merge,  # If merge, skip existing templates
            import_templates=templates,
            import_knowledge=knowledge,
            import_workflows=workflows,
            import_sources=False,  # Sources not available in CLI
            # Use resolved ctx.database_name (honours db switch / env / config)
            # rather than the raw Click default which stays "default".
            database_name=ctx.database_name,
        )

        archive_path = Path(package)

        if archive_path.suffix.lower() == ".cxl":
            raise ValueError(
                "The .cxl bundle format has been replaced by .ccx. Re-export the bundle."
            )

        console.print(f"[cyan]Importing:[/cyan] {archive_path.name}")
        if merge:
            console.print("[dim]Mode: Merge (reuse local templates by name)[/dim]")
        else:
            console.print("[dim]Mode: Replace (fresh templates, fully self-contained)[/dim]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(f"Importing {archive_path.name}...", total=None)
            stats = asyncio.run(service.import_from_path(archive_path, options))

        # Display results
        _display_import_results(stats)

        if not stats.is_success:
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _display_import_results(stats: ImportStats) -> None:
    """Display import statistics.

    Args:
        stats: Import statistics from the service.
    """
    table = Table(title="Import Results")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right")

    table.add_row("Templates imported", str(stats.templates_imported))
    table.add_row("Templates skipped", str(stats.templates_skipped))
    table.add_row("Nodes imported", str(stats.nodes_imported))
    table.add_row("Edges imported", str(stats.edges_imported))
    table.add_row("Workflows imported", str(stats.workflows_imported))
    table.add_row("Workflow edges imported", str(stats.workflow_edges_imported))

    console.print(table)

    if stats.checksum_verified:
        console.print("[green]✓ Checksums verified[/green]")

    if stats.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warning in stats.warnings:
            console.print(f"  • {warning}")

    if stats.errors:
        console.print("\n[red]Errors:[/red]")
        for error in stats.errors:
            console.print(f"  • {error}")

    if stats.is_success:
        console.print(f"\n[green]✓ Imported {stats.total_items} items[/green]")
    else:
        console.print("\n[red]✗ Import failed with errors[/red]")
