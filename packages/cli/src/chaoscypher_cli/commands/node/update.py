# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Update node command - Modify an existing node."""

import sys
from typing import Any

import click
from rich.console import Console

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("node_id")
@click.option("--label", "-l", help="New label for the node")
@click.option("--set", "-s", "set_property", multiple=True, help="Set property (key=value)")
@click.option("--unset", "-u", multiple=True, help="Remove property by key")
@click.option("--database", "-d", default="default", help="Database name")
def update(
    node_id: str,
    label: str | None,
    set_property: tuple[str, ...],
    unset: tuple[str, ...],
    database: str,
) -> None:
    """Modify an existing node in the knowledge graph.

    NODE_ID is the unique identifier of the node to update.

    Example:
        chaoscypher node update node-123 --label "Updated Name"
        chaoscypher node update node-123 -s role=CEO -s department=Executive
        chaoscypher node update node-123 -u obsolete_field
    """
    try:
        ctx = get_context(database_name=database)

        # First, get the existing node
        existing = ctx.node_service.get_node(node_id)
        if not existing:
            console.print(f"[red]Node not found:[/red] {node_id}")
            sys.exit(1)

        # Build updates
        updates: dict[str, Any] = {}

        if label:
            updates["label"] = label

        # Handle property updates
        if set_property or unset:
            properties = dict(existing.get("properties", {}))

            # Set new properties
            for prop in set_property:
                if "=" in prop:
                    key, value = prop.split("=", 1)
                    properties[key] = value
                else:
                    console.print(f"[red]Invalid property format:[/red] {prop}")
                    console.print("  Use: --set key=value")
                    sys.exit(1)

            # Remove properties
            for key in unset:
                if key in properties:
                    del properties[key]
                else:
                    console.print(f"[yellow]Property not found:[/yellow] {key}")

            updates["properties"] = properties

        if not updates:
            console.print("[yellow]No updates specified.[/yellow]")
            return

        # Apply updates - import model here to avoid slow startup
        from chaoscypher_core.models import NodeUpdate

        console.print(f"[cyan]Updating node:[/cyan] {node_id}")

        node_update = NodeUpdate(
            label=updates.get("label"),
            properties=updates.get("properties"),
        )
        result = ctx.node_service.update_node(node_id, node_update)

        console.print("[green]✓ Node updated successfully![/green]")

        if label:
            console.print(f"  [dim]Label:[/dim] {result.get('label')}")

        if set_property:
            console.print(f"  [dim]Properties set:[/dim] {', '.join(set_property)}")

        if unset:
            console.print(f"  [dim]Properties removed:[/dim] {', '.join(unset)}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
