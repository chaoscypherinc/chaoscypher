# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show all nodes in the knowledge graph."""

import json
import sys

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_core.app_config import get_settings


console = Console()


@click.command("list")
@click.option("--template", "-t", help="Filter by template ID")
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
def list_nodes(
    template: str | None,
    output_format: str,
    page: int,
    limit: int,
    database: str,
) -> None:
    """List all nodes in the knowledge graph.

    Shows nodes with their ID, label, template, and property count.
    Supports filtering by template and pagination.

    Example:
        chaoscypher node list
        chaoscypher node list --template Person
        chaoscypher node list --format json
        chaoscypher node list --page 2 --limit 100
    """
    try:
        ctx = get_context(database_name=database)

        # Get nodes with pagination
        result = ctx.node_service.list_nodes(
            template_id=template,
            page=page,
            page_size=limit,
        )

        nodes = result.get("data", [])
        pagination = result.get("pagination", {})

        if output_format == "json":
            console.print(json.dumps(result, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(result, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                console.print(json.dumps(result, indent=2, default=str))

        else:  # table format
            if not nodes:
                console.print("[dim]No nodes found.[/dim]")
                if template:
                    console.print(f"[dim]Filter: template={template}[/dim]")
                console.print("\nCreate one with: chaoscypher node create")
                return

            table = Table(title="Nodes", show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Label", style="cyan")
            table.add_column("Template", style="green")
            table.add_column("Properties", style="white", justify="right")
            table.add_column("Created", style="dim")

            for node in nodes:
                # Count properties
                props = node.get("properties", {})
                prop_count = len(props) if props else 0

                # Format created date
                created = node.get("created_at", "")
                if created:
                    try:
                        from datetime import datetime

                        if isinstance(created, str):
                            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            created = dt.strftime("%Y-%m-%d")
                    except (ValueError, TypeError):  # fmt: skip
                        pass

                # Truncate long labels
                label = node.get("label", "")
                if len(label) > 40:
                    label = label[:37] + "..."

                table.add_row(
                    node.get("id", ""),
                    label,
                    node.get("template_id", "(none)"),
                    str(prop_count),
                    str(created) if created else "",
                )

            console.print(table)

            # Pagination info
            total = pagination.get("total", len(nodes))
            total_pages = pagination.get("total_pages", 1)
            current_page = pagination.get("page", page)

            console.print(
                f"\n[dim]Page {current_page}/{total_pages} • Total: {total} node(s)[/dim]"
            )

            if current_page < total_pages:
                console.print(
                    f"[dim]Next page: chaoscypher node list --page {current_page + 1}[/dim]"
                )

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
