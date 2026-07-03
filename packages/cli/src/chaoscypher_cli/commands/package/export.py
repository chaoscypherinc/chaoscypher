# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package export command - Export knowledge graph to .ccx file.

Exports the knowledge graph to a CCX (Chaos Cypher eXchange) package
file containing templates, nodes, edges, lenses, and workflows.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from chaoscypher_cli.context import get_context


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext

console = Console()


@click.command()
@click.option("--output", "-o", help="Output .ccx file path")
@click.option("--templates/--no-templates", default=True, help="Include templates (default: yes)")
@click.option(
    "--knowledge/--no-knowledge",
    default=True,
    help="Include knowledge nodes and edges (default: yes)",
)
@click.option("--lenses/--no-lenses", default=True, help="Include lens definitions (default: yes)")
@click.option("--workflows/--no-workflows", default=True, help="Include workflows (default: yes)")
@click.option(
    "--embeddings/--no-embeddings",
    default=False,
    help="Include embedding vectors (default: no, for same-model migration)",
)
@click.option("--lens-id", help="Export only a specific lens by ID")
@click.option("--database", "-d", default="default", help="Database name")
@click.option(
    "--title",
    "-t",
    default=None,
    help="Display title for the export (stored in the package manifest)",
)
def export(
    output: str | None,
    templates: bool,
    knowledge: bool,
    lenses: bool,
    workflows: bool,
    embeddings: bool,
    lens_id: str | None,
    database: str,
    title: str | None,
) -> None:
    r"""Export knowledge graph to .ccx package file.

    Creates a CCX package containing the selected content types from the
    knowledge graph. The package uses JSON-LD format for graph data.

    \b
    Examples:
        chaoscypher graph package export
        chaoscypher graph package export --output my-backup.ccx --title "My Research"
        chaoscypher graph package export --no-workflows --lens-id lens_abc123
        chaoscypher graph package export -d my-project -o project.ccx -t "Project A"
    """
    if not any([templates, knowledge, lenses, workflows]):
        raise click.UsageError(
            "at least one content type required: enable --templates, --knowledge,"
            " --lenses, or --workflows"
        )

    try:
        ctx = get_context(database_name=database)

        from chaoscypher_core.services.export import CcxExporter

        # Create export service
        service = CcxExporter(
            graph_repository=ctx.graph_repository,
            settings=ctx.settings,
            workflow_db=None,  # CLI doesn't have workflow DB for triggers
            sources_repository=None,  # CLI doesn't have sources repo
        )

        # Generate output filename if not provided
        output_path = Path(output) if output else Path(service.get_export_filename())

        # Ensure .ccx extension
        if output_path.suffix != ".ccx":
            output_path = output_path.with_suffix(".ccx")

        console.print(f"[cyan]Exporting to:[/cyan] {output_path}")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Exporting knowledge graph...", total=None)

            data = service.export(
                include_templates=templates,
                include_knowledge=knowledge,
                include_lenses=lenses,
                include_workflows=workflows,
                include_sources=False,  # Sources not available in CLI
                include_embeddings=embeddings,
                lens_id=lens_id,
                title=title,
            )

        # Write to file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)

        # Display results
        file_size = output_path.stat().st_size
        _display_export_results(output_path, file_size, ctx)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _display_export_results(output_path: Path, file_size: int, ctx: CLIContext) -> None:
    """Display export statistics.

    Args:
        output_path: Path to the exported file.
        file_size: Size of the exported file in bytes.
        ctx: CLI context for getting database stats.
    """
    from chaoscypher_core.services.package.archive.info import format_size

    # Get stats from context
    stats = ctx.get_stats()

    table = Table(title="Export Summary")
    table.add_column("Item", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Output file", str(output_path))
    table.add_row("File size", format_size(file_size))
    table.add_row("Database", stats["database_name"])
    table.add_row("Nodes in DB", str(stats["nodes"]))
    table.add_row("Edges in DB", str(stats["edges"]))
    table.add_row("Templates in DB", str(stats["templates"]))

    console.print(table)
    console.print(f"\n[green]✓ Export complete: {output_path}[/green]")
