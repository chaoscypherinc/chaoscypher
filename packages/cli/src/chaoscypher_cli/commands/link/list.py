# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show all links (edges) in the knowledge graph."""

import json
import sys

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_json
from chaoscypher_core.app_config import get_settings


console = Console()


@click.command("list")
@click.option("--source", "-s", help="Filter by source node ID")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option("--page", "-p", default=1, type=int, help="Page number")
@click.option(
    "--limit",
    "-l",
    type=int,
    default=lambda: get_settings().cli.list_page_size,
    show_default="from settings.cli.list_page_size",
    help="Items per page",
)
@click.option("--database", "-d", default="default", help="Database name")
def list_links(
    source: str | None,
    output_format: str,
    page: int,
    limit: int,
    database: str,
) -> None:
    """List all links (edges) in the knowledge graph.

    Shows links with their source, target, label, and properties.
    Supports filtering by source node and pagination.

    Example:
        chaoscypher graph link list
        chaoscypher graph link list --source node-123
        chaoscypher graph link list --format json
        chaoscypher graph link list --page 2 --limit 100
    """
    try:
        ctx = get_context(database_name=database)

        # Get edges with pagination
        result = ctx.edge_service.list_edges(
            source_node_id=source,
            page=page,
            page_size=limit,
        )

        edges = result.get("data", [])
        pagination = result.get("pagination", {})

        if output_format == "json":
            print_json(json.dumps(result, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(result, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                print_json(json.dumps(result, indent=2, default=str))

        else:  # table format
            if not edges:
                console.print("[dim]No links found.[/dim]")
                if source:
                    console.print(f"[dim]Filter: source={source}[/dim]")
                console.print("\nCreate one with: chaoscypher graph link create <source> <target>")
                return

            table = Table(title="Links", show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Source", style="cyan")
            table.add_column("→", style="dim")
            table.add_column("Target", style="cyan")
            table.add_column("Label", style="green")
            table.add_column("Template", style="white")

            for edge in edges:
                # Truncate IDs for readability
                edge_id = edge.get("id", "")
                source_id = edge.get("source_node_id", "")
                target_id = edge.get("target_node_id", "")

                # Shorten long IDs
                if len(edge_id) > 15:
                    edge_id = edge_id[:12] + "..."
                if len(source_id) > 15:
                    source_id = source_id[:12] + "..."
                if len(target_id) > 15:
                    target_id = target_id[:12] + "..."

                label = edge.get("label", "(unlabeled)")
                if len(label) > 25:
                    label = label[:22] + "..."

                table.add_row(
                    edge_id,
                    source_id,
                    "→",
                    target_id,
                    label,
                    edge.get("template_id") or "(none)",
                )

            console.print(table)

            # Pagination info
            total = pagination.get("total", len(edges))
            total_pages = pagination.get("total_pages", 1)
            current_page = pagination.get("page", page)

            console.print(
                f"\n[dim]Page {current_page}/{total_pages} • Total: {total} link(s)[/dim]"
            )

            if current_page < total_pages:
                console.print(
                    f"[dim]Next page: chaoscypher graph link list --page {current_page + 1}[/dim]"
                )

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
