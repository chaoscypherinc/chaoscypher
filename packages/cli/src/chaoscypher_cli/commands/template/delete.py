# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete command - Remove a template."""

import sys

import click
from rich.console import Console
from rich.prompt import Confirm

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("template_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.option("--database", "-d", default="default", help="Database name")
def delete(template_id: str, force: bool, database: str) -> None:
    """Delete a template from the knowledge graph.

    TEMPLATE_ID is the unique identifier of the template to delete.

    Warning: Deleting a template does not delete nodes created from it,
    but those nodes will no longer have a valid template reference.

    Example:
        chaoscypher template delete Person
        chaoscypher template delete tmpl-123 --force
    """
    try:
        ctx = get_context(database_name=database)

        # Get template first to show info
        template = ctx.template_service.get_template(template_id)

        if not template:
            console.print(f"[red]Template not found:[/red] {template_id}")
            sys.exit(1)

        # Convert to dict if needed
        if hasattr(template, "model_dump"):
            template_dict = template.model_dump()
        else:
            template_dict = dict(template) if not isinstance(template, dict) else template

        console.print("[cyan]Template to delete:[/cyan]")
        console.print(f"  [dim]ID:[/dim] {template_dict.get('id', template_id)}")
        console.print(f"  [dim]Name:[/dim] {template_dict.get('name', 'Unknown')}")
        console.print(f"  [dim]Type:[/dim] {template_dict.get('template_type', 'node')}")

        properties = template_dict.get("properties", [])
        if properties:
            console.print(f"  [dim]Properties:[/dim] {len(properties)}")

        if not force:
            console.print(
                "\n[yellow]Warning:[/yellow] Nodes using this template will lose their template reference."
            )
            if not Confirm.ask("Are you sure you want to delete this template?", default=False):
                console.print("[yellow]Cancelled.[/yellow]")
                return

        # Delete template
        ctx.template_service.delete_template(template_id)

        console.print("[green]✓ Template deleted successfully[/green]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
