# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete node command - Remove a node from the knowledge graph."""

import sys

import click
from rich.console import Console
from rich.prompt import Confirm

from chaoscypher_cli.commands.node._edges import list_connected_edges
from chaoscypher_cli.context import get_context
from chaoscypher_core.app_config import get_settings


console = Console()


@click.command()
@click.argument("node_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.option("--cascade", "-c", is_flag=True, help="Also delete connected links")
@click.option("--database", "-d", default="default", help="Database name")
def delete(node_id: str, force: bool, cascade: bool, database: str) -> None:
    """Remove a node from the knowledge graph.

    NODE_ID is the unique identifier of the node to delete.

    Example:
        chaoscypher graph node delete node-123
        chaoscypher graph node delete node-123 --force
        chaoscypher graph node delete node-123 --cascade
    """
    try:
        ctx = get_context(database_name=database)

        # First, verify the node exists
        existing = ctx.node_service.get_node(node_id)
        if not existing:
            console.print(f"[red]Node not found:[/red] {node_id}")
            sys.exit(1)

        # Show node info
        console.print("[cyan]Node to delete:[/cyan]")
        console.print(f"  [dim]ID:[/dim] {existing.get('id')}")
        console.print(f"  [dim]Label:[/dim] {existing.get('label')}")
        console.print(f"  [dim]Template:[/dim] {existing.get('template_id')}")

        # Check for connected edges
        edge_batch = get_settings().cli.edge_batch_size
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

        connected_edges = {edge["id"]: edge for edge in [*outgoing, *incoming] if edge.get("id")}
        total_edges = len(connected_edges)

        if total_edges > 0:
            console.print(
                f"\n[yellow]Warning:[/yellow] This node has {total_edges} connected link(s)"
            )
            if cascade:
                console.print("  [dim]Cascade mode: Links will also be deleted[/dim]")
            else:
                console.print(
                    "  [dim]Links will become orphaned. Use --cascade to delete them.[/dim]"
                )

        # Confirm deletion
        if not force:
            if not Confirm.ask("\nAre you sure you want to delete this node?", default=False):
                console.print("[yellow]Cancelled.[/yellow]")
                return

        # Delete connected edges if cascade
        if cascade and total_edges > 0:
            console.print(f"\n[cyan]Deleting {total_edges} connected link(s)...[/cyan]")
            for edge_id in connected_edges:
                ctx.edge_service.delete_edge(edge_id)
            console.print(f"  [green]✓ Deleted {total_edges} link(s)[/green]")

        # Delete the node
        console.print("\n[cyan]Deleting node...[/cyan]")
        ctx.node_service.delete_node(node_id)
        console.print("[green]✓ Node deleted successfully![/green]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
