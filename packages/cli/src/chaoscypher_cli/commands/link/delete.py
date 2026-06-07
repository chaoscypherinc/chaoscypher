# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete link command - Remove a link between nodes."""

import sys

import click
from rich.console import Console
from rich.prompt import Confirm

from chaoscypher_cli.context import get_context
from chaoscypher_core.app_config import get_settings


console = Console()


@click.command()
@click.argument("link_id", required=False)
@click.option("--source", "-s", help="Source node ID (used with --target)")
@click.option("--target", "-t", help="Target node ID (used with --source)")
@click.option("--type", "link_type", help="Link type to filter by")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.option("--database", "-d", default="default", help="Database name")
def delete(
    link_id: str | None,
    source: str | None,
    target: str | None,
    link_type: str | None,
    force: bool,
    database: str,
) -> None:
    """Remove a link from the knowledge graph.

    Can delete by LINK_ID or by specifying --source and --target nodes.

    Example:
        chaoscypher link delete link-123
        chaoscypher link delete --source person-1 --target company-1
        chaoscypher link delete -s node-1 -t node-2 --type "works_for"
    """
    try:
        ctx = get_context(database_name=database)

        if link_id:
            # Delete by link ID
            edge = ctx.edge_service.get_edge(link_id)
            if not edge:
                console.print(f"[red]Link not found:[/red] {link_id}")
                sys.exit(1)

            console.print("[cyan]Link to delete:[/cyan]")
            console.print(f"  [dim]ID:[/dim] {edge.get('id')}")
            console.print(f"  [dim]Source:[/dim] {edge.get('source_node_id')}")
            console.print(f"  [dim]Target:[/dim] {edge.get('target_node_id')}")
            console.print(f"  [dim]Type:[/dim] {edge.get('template_id')}")

            if not force:
                if not Confirm.ask("\nAre you sure you want to delete this link?", default=False):
                    console.print("[yellow]Cancelled.[/yellow]")
                    return

            ctx.edge_service.delete_edge(link_id)
            console.print("[green]✓ Link deleted successfully![/green]")

        elif source and target:
            # Find links between source and target
            edges_result = ctx.edge_service.list_edges(
                source_node_id=source,
                page_size=get_settings().cli.edge_batch_size,
            )
            edges = edges_result.get("data", [])
            matching = [e for e in edges if e.get("target_node_id") == target]

            # Filter by type if specified
            if link_type:
                matching = [e for e in matching if e.get("template_id") == link_type]

            if not matching:
                console.print(f"[yellow]No links found from {source} to {target}[/yellow]")
                if link_type:
                    console.print(f"  [dim]Type filter:[/dim] {link_type}")
                return

            console.print(f"[cyan]Found {len(matching)} link(s) to delete:[/cyan]")
            for edge in matching:
                console.print(f"  • {edge.get('id')} ({edge.get('template_id')})")

            if not force:
                if not Confirm.ask(f"\nDelete {len(matching)} link(s)?", default=False):
                    console.print("[yellow]Cancelled.[/yellow]")
                    return

            deleted = 0
            for edge in matching:
                ctx.edge_service.delete_edge(edge.get("id"))
                deleted += 1

            console.print(f"[green]✓ Deleted {deleted} link(s)![/green]")

        else:
            console.print("[red]Error:[/red] Must provide LINK_ID or both --source and --target")
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
