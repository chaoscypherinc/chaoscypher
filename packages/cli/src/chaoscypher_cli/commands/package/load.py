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
from typing import TYPE_CHECKING, Any

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
        chaoscypher graph package load my-knowledge.ccx
        chaoscypher graph package load export.ccx --no-templates
        chaoscypher graph package load backup.ccx --merge
        chaoscypher graph package load data.ccx -d my-project
    """
    try:
        ctx = get_context(database_name=database)

        from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions

        # Create the CCX 3.0 importer (upsert-by-IRI; re-import is idempotent).
        importer = CcxImporter(
            graph_repository=ctx.graph_repository,
            sources_repository=None,  # CLI doesn't have sources repo
            workflow_db=None,  # CLI doesn't have workflow DB for triggers
        )

        # Build options
        options = ImportOptions(
            skip_existing_templates=merge,  # If merge, reuse local templates by name
            import_templates=templates,
            import_knowledge=knowledge,
            import_workflows=workflows,
            import_sources=False,  # Sources not available in CLI
            # Use resolved ctx.database_name (honours db switch / env / config)
            # rather than the raw Click default which stays "default".
            database_name=ctx.database_name,
        )

        archive_path = Path(package)

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
            stats = asyncio.run(importer.import_from_path(archive_path, options))

        # Make the imported knowledge nodes searchable (re-embed + index). The
        # CLI has no queue, so unlike the worker import paths this runs inline —
        # matching what OP_INDEX_IMPORTED_NODES does for lexicon imports.
        _index_imported_nodes(ctx, stats)

        # Display results
        _display_import_results(stats)

        if not stats.is_success:
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _index_imported_nodes(ctx: Any, stats: ImportStats) -> None:
    """Re-embed + index the imported knowledge nodes so they are searchable.

    The CLI has no worker/queue, so it runs the same node-indexing the worker
    enqueues (``OP_INDEX_IMPORTED_NODES``) synchronously. Best-effort: an import
    is still a success even if the local embedding model is unavailable (the
    nodes are still keyword-searchable via FTS).
    """
    if not stats.imported_node_ids:
        return
    from types import SimpleNamespace

    from chaoscypher_core.operations.importing.imported_source_handler import (
        handle_index_imported_nodes,
    )

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Indexing nodes for search...", total=None)
            result = asyncio.run(
                handle_index_imported_nodes(
                    data={"node_ids": stats.imported_node_ids},
                    source_repository=ctx.storage_adapter,
                    graph_repository=ctx.graph_repository,
                    # The node path only needs the embedding provider off this.
                    indexing_service=SimpleNamespace(embedding_service=ctx.embedding_service),
                    search_repository=ctx.search_repository,
                    metadata={"database_name": ctx.database_name},
                )
            )
        console.print(f"[green]✓ Indexed {result.get('nodes_indexed', 0)} nodes for search[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Search indexing skipped: {e}[/yellow]")


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
