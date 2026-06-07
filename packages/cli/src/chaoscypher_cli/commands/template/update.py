# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Update command - Modify an existing template."""

import sys
from typing import Any

import click
from rich.console import Console

from chaoscypher_cli.commands.template.utils import parse_property
from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("template_id")
@click.option("--name", "-n", help="New template name")
@click.option("--description", help="New description")
@click.option("--add-property", "-a", multiple=True, help="Add property (name:type[:required])")
@click.option("--remove-property", "-r", multiple=True, help="Remove property by name")
@click.option("--database", "-d", default="default", help="Database name")
def update(
    template_id: str,
    name: str | None,
    description: str | None,
    add_property: tuple[str, ...],
    remove_property: tuple[str, ...],
    database: str,
) -> None:
    """Modify an existing template.

    TEMPLATE_ID is the unique identifier of the template to update.

    Property format: name:type[:required]
    Valid types: STRING, TEXT, INTEGER, FLOAT, BOOLEAN, DATE, DATETIME, URL, EMAIL, JSON

    Example:
        chaoscypher template update Person --name "Individual"
        chaoscypher template update Person --description "A person entity"
        chaoscypher template update Person -a phone:string -a address:text
        chaoscypher template update Person -r obsolete_field
    """
    try:
        ctx = get_context(database_name=database)

        # Get existing template
        existing = ctx.template_service.get_template(template_id)
        if not existing:
            console.print(f"[red]Template not found:[/red] {template_id}")
            sys.exit(1)

        # Convert to dict if needed
        if hasattr(existing, "model_dump"):
            existing_dict = existing.model_dump()
        else:
            existing_dict = dict(existing) if not isinstance(existing, dict) else existing

        # Build updates
        updates: dict[str, Any] = {}

        if name:
            updates["name"] = name

        if description is not None:
            updates["description"] = description

        # Handle property updates
        if add_property or remove_property:
            properties = list(existing_dict.get("properties", []))

            # Remove properties
            if remove_property:
                property_names = {p.get("name") for p in properties}
                for prop_name in remove_property:
                    if prop_name in property_names:
                        properties = [p for p in properties if p.get("name") != prop_name]
                        console.print(f"  [dim]Removing property:[/dim] {prop_name}")
                    else:
                        console.print(f"[yellow]Property not found:[/yellow] {prop_name}")

            # Add new properties
            for prop_str in add_property:
                try:
                    prop = parse_property(prop_str)
                    # Check for duplicates
                    if any(p.get("name") == prop["name"] for p in properties):
                        console.print(f"[yellow]Property already exists:[/yellow] {prop['name']}")
                        continue
                    properties.append(prop)
                    console.print(
                        f"  [dim]Adding property:[/dim] {prop['name']} ({prop['property_type']})"
                    )
                except ValueError as e:
                    console.print(f"[red]Error:[/red] {e}")
                    sys.exit(1)

            updates["properties"] = properties

        if not updates:
            console.print("[yellow]No updates specified.[/yellow]")
            return

        # Apply updates - import models here to avoid slow startup
        from chaoscypher_core.models import PropertyDefinition, PropertyType, TemplateUpdate

        console.print(f"[cyan]Updating template:[/cyan] {template_id}")

        # Convert properties to PropertyDefinition if present
        property_defs = None
        if "properties" in updates:
            property_defs = [
                PropertyDefinition(
                    name=str(p.get("name", "")),
                    display_name=str(
                        p.get("display_name", str(p.get("name", "")).replace("_", " ").title())
                    ),
                    property_type=PropertyType(str(p.get("property_type", "STRING")).lower()),
                    required=bool(p.get("required", False)),
                )
                for p in updates["properties"]
            ]

        template_update = TemplateUpdate(
            name=updates.get("name"),
            description=updates.get("description"),
            properties=property_defs,
        )
        ctx.template_service.update_template(template_id, template_update)

        console.print("[green]✓ Template updated successfully![/green]")

        if name:
            console.print(f"  [dim]Name:[/dim] {name}")

        if description is not None:
            console.print(f"  [dim]Description:[/dim] {description}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
