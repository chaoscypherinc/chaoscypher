# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Create command - Define a new template."""

import sys

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from chaoscypher_cli.commands.template.utils import PROPERTY_TYPES, parse_property
from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.option("--name", "-n", help="Template name")
@click.option(
    "--type",
    "-t",
    "template_type",
    default="node",
    type=click.Choice(["node", "edge"]),
    help="Template type (node or edge)",
)
@click.option("--description", help="Template description")
@click.option("--property", "-p", multiple=True, help="Property definition (name:type[:required])")
@click.option("--database", "-d", default="default", help="Database name")
@click.option("--interactive", "-i", is_flag=True, help="Use interactive wizard")
def create(
    name: str | None,
    template_type: str,
    description: str | None,
    property: tuple[str, ...],
    database: str,
    interactive: bool,
) -> None:
    """Define a new template for knowledge nodes or edges.

    Templates specify the schema for nodes/edges including
    properties, types, and validation rules.

    Property format: name:type[:required]
    Valid types: STRING, TEXT, INTEGER, FLOAT, BOOLEAN, DATE, DATETIME, URL, EMAIL, JSON

    Example:
        chaoscypher template create --interactive
        chaoscypher template create -n Person -p name:string:required -p age:integer
        chaoscypher template create -n WorksFor -t edge -p start_date:date
    """
    try:
        ctx = get_context(database_name=database)

        if interactive:
            # Interactive wizard
            console.print("[cyan]Interactive Template Wizard[/cyan]\n")

            name = Prompt.ask("Template name")
            template_type = Prompt.ask("Template type", choices=["node", "edge"], default="node")
            description = Prompt.ask("Description (optional)", default="")

            # Add properties interactively
            properties = []
            console.print("\n[dim]Add properties (leave name empty to finish):[/dim]")
            console.print(f"[dim]Valid types: {', '.join(PROPERTY_TYPES)}[/dim]\n")

            while True:
                prop_name = Prompt.ask("  Property name (or Enter to finish)", default="")
                if not prop_name:
                    break

                prop_type = Prompt.ask(
                    "    Type", choices=[t.lower() for t in PROPERTY_TYPES], default="string"
                ).upper()

                required = Confirm.ask("    Required?", default=False)

                properties.append(
                    {
                        "name": prop_name,
                        "display_name": prop_name.replace("_", " ").title(),
                        "property_type": prop_type,
                        "required": required,
                    }
                )

            console.print("\n[cyan]Template summary:[/cyan]")
            console.print(f"  Name: {name}")
            console.print(f"  Type: {template_type}")
            if description:
                console.print(f"  Description: {description}")
            console.print(f"  Properties: {len(properties)}")

            if not Confirm.ask("\nCreate this template?", default=True):
                console.print("[yellow]Cancelled.[/yellow]")
                return

        else:
            # Parse properties from command line
            if not name:
                console.print("[red]Error:[/red] Template name is required")
                console.print("  Use: --name <name> or --interactive")
                sys.exit(1)

            properties = []
            for prop_str in property:
                try:
                    prop = parse_property(prop_str)
                    properties.append(prop)
                except ValueError as e:
                    console.print(f"[red]Error:[/red] {e}")
                    sys.exit(1)

        # Create the template - import models here to avoid slow startup
        from chaoscypher_core.models import PropertyDefinition, PropertyType, TemplateCreate

        console.print(f"\n[cyan]Creating template:[/cyan] {name}")

        # Convert property dicts to PropertyDefinition models
        property_defs = [
            PropertyDefinition(
                name=str(p["name"]),
                display_name=str(p.get("display_name", str(p["name"]).replace("_", " ").title())),
                property_type=PropertyType(str(p["property_type"]).lower()),
                required=bool(p.get("required", False)),
            )
            for p in properties
        ]

        template_create = TemplateCreate(
            name=name,
            template_type=template_type,
            description=description or "",
            properties=property_defs,
        )
        result = ctx.template_service.create_template(template_create)

        console.print("[green]✓ Template created successfully![/green]")
        console.print(f"  [dim]ID:[/dim] {result.get('id')}")
        console.print(f"  [dim]Name:[/dim] {result.get('name')}")
        console.print(f"  [dim]Type:[/dim] {result.get('template_type')}")

        if properties:
            console.print("  [dim]Properties:[/dim]")
            for prop in properties:
                req = " (required)" if prop.get("required") else ""
                console.print(f"    • {prop['name']}: {prop['property_type']}{req}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
