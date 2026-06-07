# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Get command - Show template details."""

import json
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("template_id")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option("--database", "-d", default="default", help="Database name")
def get(template_id: str, output_format: str, database: str) -> None:
    """Show details of a specific template.

    TEMPLATE_ID is the unique identifier of the template.

    Example:
        chaoscypher template get Person
        chaoscypher template get tmpl-123 --format json
    """
    try:
        ctx = get_context(database_name=database)

        template = ctx.template_service.get_template(template_id)

        if not template:
            console.print(f"[red]Template not found:[/red] {template_id}")
            sys.exit(1)

        # Convert to dict if needed
        if hasattr(template, "model_dump"):
            template_dict = template.model_dump()
        else:
            template_dict = dict(template) if not isinstance(template, dict) else template

        if output_format == "json":
            console.print(json.dumps(template_dict, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(template_dict, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                console.print(json.dumps(template_dict, indent=2, default=str))

        else:  # table format
            name = template_dict.get("name", template_id)
            template_type = template_dict.get("template_type", "node")

            # Type badge
            if template_type == "node":
                type_badge = "[cyan]node[/cyan]"
            else:
                type_badge = "[magenta]edge[/magenta]"

            console.print(
                Panel(
                    f"[bold]{name}[/bold] {type_badge}\n"
                    f"[dim]ID: {template_dict.get('id', template_id)}[/dim]",
                    title="Template",
                    border_style="cyan",
                )
            )

            # Basic info
            info_table = Table(show_header=False, box=None)
            info_table.add_column("Field", style="dim", width=20)
            info_table.add_column("Value", style="white")

            if template_dict.get("description"):
                info_table.add_row("Description", template_dict.get("description"))

            info_table.add_row("Type", template_type)
            info_table.add_row("Created", str(template_dict.get("created_at", "")))

            if template_dict.get("updated_at"):
                info_table.add_row("Updated", str(template_dict.get("updated_at")))

            console.print(info_table)

            # Properties
            properties = template_dict.get("properties", [])
            if properties:
                console.print("\n[cyan]Properties:[/cyan]")

                prop_table = Table(show_header=True, box=None)
                prop_table.add_column("Name", style="cyan")
                prop_table.add_column("Type", style="white")
                prop_table.add_column("Required", style="dim")
                prop_table.add_column("Display Name", style="dim")

                for prop in properties:
                    required = "[green]yes[/green]" if prop.get("required") else "[dim]no[/dim]"
                    prop_table.add_row(
                        prop.get("name", ""),
                        prop.get("property_type", "STRING"),
                        required,
                        prop.get("display_name", ""),
                    )

                console.print(prop_table)
            else:
                console.print("\n[dim]No properties defined.[/dim]")

            # Constraints if present
            constraints = template_dict.get("constraints", [])
            if constraints:
                console.print("\n[cyan]Constraints:[/cyan]")
                for constraint in constraints:
                    console.print(f"  • {constraint}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
