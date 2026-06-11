# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Get command - Show link details."""

import json
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_json


console = Console()


@click.command()
@click.argument("link_id")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option("--database", "-d", default="default", help="Database name")
def get(link_id: str, output_format: str, database: str) -> None:
    """Show details of a specific link.

    LINK_ID is the unique identifier of the link.

    Example:
        chaoscypher graph link get edge-123
        chaoscypher graph link get edge-123 --format json
    """
    try:
        ctx = get_context(database_name=database)

        link = ctx.edge_service.get_edge(link_id)

        if not link:
            console.print(f"[red]Link not found:[/red] {link_id}")
            sys.exit(1)

        # Convert to dict if needed
        if hasattr(link, "model_dump"):
            link_dict = link.model_dump()
        else:
            link_dict = dict(link) if not isinstance(link, dict) else link

        if output_format == "json":
            print_json(json.dumps(link_dict, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(link_dict, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                print_json(json.dumps(link_dict, indent=2, default=str))

        else:  # table format
            # Display link details
            label = link_dict.get("label", link_dict.get("relationship_type", "Unknown"))
            console.print(
                Panel(
                    f"[bold]{label}[/bold]\n[dim]ID: {link_id}[/dim]",
                    title="Link",
                    border_style="cyan",
                )
            )

            table = Table(show_header=False, box=None)
            table.add_column("Field", style="dim", width=20)
            table.add_column("Value", style="white")

            table.add_row("Source Node", link_dict.get("source_node_id", ""))
            table.add_row("Target Node", link_dict.get("target_node_id", ""))
            table.add_row(
                "Relationship Type", link_dict.get("relationship_type", link_dict.get("label", ""))
            )

            if link_dict.get("template_id"):
                table.add_row("Template", link_dict.get("template_id"))

            if link_dict.get("weight"):
                table.add_row("Weight", str(link_dict.get("weight")))

            table.add_row("Created", str(link_dict.get("created_at", "")))

            if link_dict.get("updated_at"):
                table.add_row("Updated", str(link_dict.get("updated_at")))

            console.print(table)

            # Show properties if present
            properties = link_dict.get("properties", {})
            if properties:
                console.print("\n[cyan]Properties:[/cyan]")
                for key, value in properties.items():
                    console.print(f"  [dim]{key}:[/dim] {value}")

            # Show metadata if present
            metadata = link_dict.get("metadata", {})
            if metadata:
                console.print("\n[cyan]Metadata:[/cyan]")
                for key, value in metadata.items():
                    console.print(f"  [dim]{key}:[/dim] {value}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
