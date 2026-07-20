# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Get node command - Show details of a node."""

import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.commands.node._edges import list_connected_edges
from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_json
from chaoscypher_core.app_config import get_settings


console = Console()


@click.command()
@click.argument("node_id")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option("--include-links", "-l", is_flag=True, help="Include connected links")
@click.option("--database", "-d", default="default", help="Database name")
def get(node_id: str, output_format: str, include_links: bool, database: str) -> None:
    """Show details of a node in the knowledge graph.

    NODE_ID is the unique identifier of the node to display.

    Example:
        chaoscypher graph node get node-123
        chaoscypher graph node get node-123 --format json
        chaoscypher graph node get node-123 --include-links
    """
    try:
        ctx = get_context(database_name=database)

        # Get node
        node = ctx.node_service.get_node(node_id)

        if not node:
            console.print(f"[red]Node not found:[/red] {node_id}")
            sys.exit(1)

        # Get connected edges if requested
        edges = []
        if include_links:
            edge_batch = get_settings().cli.edge_batch_size
            # Get outgoing edges (where this node is the source). Paginate so
            # high-degree nodes are not silently truncated to the first page.
            outgoing = list_connected_edges(
                ctx.edge_service,
                node_id=node_id,
                page_size=edge_batch,
                edge_filter="source_node_id",
            )

            # Get incoming edges (where this node is the target)
            incoming = list_connected_edges(
                ctx.edge_service,
                node_id=node_id,
                page_size=edge_batch,
                edge_filter="target_node_id",
            )

            edges = outgoing + incoming

        if output_format == "json":
            output: dict[str, Any] = {"node": node}
            if include_links:
                output["links"] = edges
            print_json(json.dumps(output, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                output_yaml: dict[str, Any] = {"node": node}
                if include_links:
                    output_yaml["links"] = edges
                console.print(yaml.dump(output_yaml, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                print_json(json.dumps({"node": node}, indent=2, default=str))

        else:  # table format
            # Node details table
            table = Table(title=f"Node: {node_id}", show_header=True)
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("ID", node.get("id", ""))
            table.add_row("Label", node.get("label", ""))
            table.add_row("Template", node.get("template_id", ""))
            table.add_row("Created", str(node.get("created_at", "")))
            table.add_row("Updated", str(node.get("updated_at", "")))

            # Add properties
            properties = node.get("properties", {})
            if properties:
                props_str = "\n".join(f"  {k}: {v}" for k, v in properties.items())
                table.add_row("Properties", props_str)
            else:
                table.add_row("Properties", "(none)")

            # Add position if present
            position = node.get("position")
            if position:
                table.add_row("Position", f"x={position.get('x')}, y={position.get('y')}")

            # Add embedding info
            embedding = node.get("embedding")
            if embedding:
                table.add_row("Embedding", f"[{len(embedding)} dimensions]")
            else:
                table.add_row("Embedding", "(none)")

            console.print(table)

            # Links table if requested
            if include_links and edges:
                console.print()
                links_table = Table(title="Connected Links", show_header=True)
                links_table.add_column("ID", style="dim")
                links_table.add_column("Direction", style="cyan")
                links_table.add_column("Related Node", style="white")
                links_table.add_column("Relationship", style="green")

                for edge in edges:
                    if edge.get("source_node_id") == node_id:
                        direction = "→ (outgoing)"
                        related = edge.get("target_node_id", "")
                    else:
                        direction = "← (incoming)"
                        related = edge.get("source_node_id", "")

                    links_table.add_row(
                        edge.get("id", ""),
                        direction,
                        related,
                        edge.get("label", edge.get("template_id", "")),
                    )

                console.print(links_table)
            elif include_links:
                console.print("\n[dim]No connected links found.[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
