# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Update command - Modify an existing link."""

import sys
from typing import Any

import click
from rich.console import Console

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("link_id")
@click.option("--label", "-l", help="New label for the link")
@click.option("--set", "-s", "set_property", multiple=True, help="Set property (key=value)")
@click.option("--unset", "-u", multiple=True, help="Remove property by key")
@click.option("--database", "-d", default="default", help="Database name")
def update(
    link_id: str,
    label: str | None,
    set_property: tuple[str, ...],
    unset: tuple[str, ...],
    database: str,
) -> None:
    """Modify an existing link in the knowledge graph.

    LINK_ID is the unique identifier of the link to update.

    Example:
        chaoscypher graph link update edge-123 --label "Works For"
        chaoscypher graph link update edge-123 -s context="Updated context"
        chaoscypher graph link update edge-123 -u obsolete_field
    """
    try:
        ctx = get_context(database_name=database)

        # First, get the existing link
        existing = ctx.edge_service.get_edge(link_id)
        if not existing:
            console.print(f"[red]Link not found:[/red] {link_id}")
            sys.exit(1)

        # Convert to dict if needed
        if hasattr(existing, "model_dump"):
            existing_dict = existing.model_dump()
        else:
            existing_dict = dict(existing) if not isinstance(existing, dict) else existing

        # Build updates
        updates: dict[str, Any] = {}

        if label:
            updates["label"] = label

        # Handle property updates
        if set_property or unset:
            properties = dict(existing_dict.get("properties", {}))

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
        from chaoscypher_core.models import EdgeUpdate

        console.print(f"[cyan]Updating link:[/cyan] {link_id}")

        # EdgeUpdate only supports label and properties
        edge_update = EdgeUpdate(
            label=updates.get("label"),
            properties=updates.get("properties"),
        )
        ctx.edge_service.update_edge(link_id, edge_update)

        console.print("[green]✓ Link updated successfully![/green]")

        if label:
            console.print(f"  [dim]Label:[/dim] {label}")

        if set_property:
            console.print(f"  [dim]Properties set:[/dim] {', '.join(set_property)}")

        if unset:
            console.print(f"  [dim]Properties removed:[/dim] {', '.join(unset)}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
