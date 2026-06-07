# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Create link command - Link two nodes together."""

import sys

import click
from rich.console import Console

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("source_node")
@click.argument("target_node")
@click.option(
    "--type",
    "-t",
    "link_type",
    required=True,
    help="Relationship type/template (e.g., 'works_for', 'owns', 'related_to')",
)
@click.option("--label", "-l", help="Optional label for the link")
@click.option("--bidirectional", "-b", is_flag=True, help="Create link in both directions")
@click.option("--database", "-d", default="default", help="Database name")
def create(
    source_node: str,
    target_node: str,
    link_type: str,
    label: str | None,
    bidirectional: bool,
    database: str,
) -> None:
    """Create a link between two nodes.

    SOURCE_NODE is the starting node ID.
    TARGET_NODE is the ending node ID.

    Example:
        chaoscypher link create person-1 company-1 --type "works_for"
        chaoscypher link create node-1 node-2 -t "related_to" --bidirectional
        chaoscypher link create a b -t "influences" -l "strongly influences"
    """
    try:
        ctx = get_context(database_name=database)

        # Verify source node exists
        source = ctx.node_service.get_node(source_node)
        if not source:
            console.print(f"[red]Source node not found:[/red] {source_node}")
            sys.exit(1)

        # Verify target node exists
        target = ctx.node_service.get_node(target_node)
        if not target:
            console.print(f"[red]Target node not found:[/red] {target_node}")
            sys.exit(1)

        # Import model here to avoid slow startup
        from chaoscypher_core.models import EdgeCreate

        console.print(f"[cyan]Creating link:[/cyan] {source_node} → {target_node}")
        console.print(f"  [dim]Type:[/dim] {link_type}")
        if label:
            console.print(f"  [dim]Label:[/dim] {label}")

        # Create the link
        edge_create = EdgeCreate(
            template_id=link_type,
            source_node_id=source_node,
            target_node_id=target_node,
            label=label or link_type,
            properties={},
        )
        result = ctx.edge_service.create_edge(edge_create)

        console.print("[green]✓ Link created successfully![/green]")
        console.print(f"  [dim]ID:[/dim] {result.get('id')}")

        # Create reverse link if bidirectional
        if bidirectional:
            console.print(f"\n[cyan]Creating reverse link:[/cyan] {target_node} → {source_node}")

            reverse_edge_create = EdgeCreate(
                template_id=link_type,
                source_node_id=target_node,
                target_node_id=source_node,
                label=label or link_type,
                properties={},
            )
            reverse_result = ctx.edge_service.create_edge(reverse_edge_create)

            console.print("[green]✓ Reverse link created![/green]")
            console.print(f"  [dim]ID:[/dim] {reverse_result.get('id')}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
