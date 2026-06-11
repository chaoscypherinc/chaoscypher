# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Create node command - Add a new node to the knowledge graph."""

import json
import sys

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.option("--template", "-t", required=True, help="Template ID to use for the node")
@click.option("--label", "-l", required=True, help="Label/name of the node")
@click.option(
    "--property", "-p", multiple=True, help="Property in key=value format (can specify multiple)"
)
@click.option("--json-props", "-j", help="Properties as JSON string")
@click.option("--database", "-d", default="default", help="Database name")
@click.option("--interactive", "-i", is_flag=True, help="Use interactive wizard")
def create(
    template: str,
    label: str,
    property: tuple[str, ...],
    json_props: str | None,
    database: str,
    interactive: bool,
) -> None:
    """Add a new node to the knowledge graph.

    Creates a node using either an interactive wizard or command-line flags.

    Example:
        chaoscypher graph node create -t Person -l "John Doe"
        chaoscypher graph node create -t Person -l "Jane" -p role=CEO -p department=Executive
        chaoscypher graph node create -t Event -l "Meeting" -j '{"date": "2024-01-15"}'
        chaoscypher graph node create --interactive
    """
    try:
        ctx = get_context(database_name=database)

        if interactive:
            # Interactive wizard
            console.print("[cyan]Interactive Node Creation Wizard[/cyan]\n")

            # List available templates
            templates_result = ctx.template_service.list_templates()
            templates = templates_result.get("data", [])

            if not templates:
                console.print("[yellow]No templates found. Create a template first.[/yellow]")
                console.print("  Use: chaoscypher graph template create")
                sys.exit(1)

            console.print("[dim]Available templates:[/dim]")
            for t in templates:
                console.print(f"  • {t['id']} - {t['name']}")

            template = Prompt.ask("\nTemplate ID", default=templates[0]["id"] if templates else "")
            label = Prompt.ask("Node label")

            # Get template properties
            template_data = ctx.template_service.get_template(template)
            if template_data:
                props_def = template_data.get("properties", [])
                if props_def:
                    console.print("\n[dim]Template properties:[/dim]")
                    properties = {}
                    for prop in props_def:
                        prop_name = prop.get("name", "")
                        prop_type = prop.get("property_type", "STRING")
                        required = prop.get("required", False)
                        default = prop.get("default_value")

                        prompt_text = f"  {prop_name} ({prop_type})"
                        if required:
                            prompt_text += " [required]"

                        value = Prompt.ask(prompt_text, default=default or "")
                        if value:
                            properties[prop_name] = value
                else:
                    properties = {}
            else:
                properties = {}

            if Confirm.ask("\nCreate this node?", default=True):
                pass  # Continue with creation
            else:
                console.print("[yellow]Cancelled.[/yellow]")
                return

        else:
            # Parse properties from command line
            properties = {}

            # From -p flags
            for prop in property:
                if "=" in prop:
                    key, value = prop.split("=", 1)
                    properties[key] = value
                else:
                    console.print(f"[red]Invalid property format:[/red] {prop}")
                    console.print("  Use: --property key=value")
                    sys.exit(1)

            # From JSON
            if json_props:
                try:
                    json_properties = json.loads(json_props)
                    properties.update(json_properties)
                except json.JSONDecodeError as e:
                    console.print(f"[red]Invalid JSON:[/red] {e}")
                    sys.exit(1)

        # Create the node - import model here to avoid slow startup
        from chaoscypher_core.models import NodeCreate

        console.print("\n[cyan]Creating node...[/cyan]")

        node_create = NodeCreate(
            template_id=template,
            label=label,
            properties=properties,
        )
        result = ctx.node_service.create_node(node_create)

        console.print("[green]✓ Node created successfully![/green]")
        console.print(f"  [dim]ID:[/dim] {result['id']}")
        console.print(f"  [dim]Template:[/dim] {result['template_id']}")
        console.print(f"  [dim]Label:[/dim] {result['label']}")

        if result.get("properties"):
            console.print("  [dim]Properties:[/dim]")
            for k, v in result["properties"].items():
                console.print(f"    • {k}: {v}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
