# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show available templates."""

import json
import sys

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context


console = Console()


@click.command("list")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Show detailed information including properties"
)
@click.option(
    "--type",
    "-t",
    "template_type",
    type=click.Choice(["node", "edge"]),
    help="Filter by template type",
)
@click.option("--database", "-d", default="default", help="Database name")
def list_templates(
    output_format: str,
    verbose: bool,
    template_type: str | None,
    database: str,
) -> None:
    """Show available templates in the knowledge graph.

    Lists all templates defined in the database.

    Example:
        chaoscypher template list
        chaoscypher template list --format json
        chaoscypher template list --verbose
        chaoscypher template list --type node
    """
    try:
        ctx = get_context(database_name=database)

        # Get templates
        result = ctx.template_service.list_templates(template_type=template_type)
        templates = result.get("data", [])

        if output_format == "json":
            console.print(json.dumps(templates, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(templates, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                console.print(json.dumps(templates, indent=2, default=str))

        else:  # table format
            if not templates:
                console.print("[dim]No templates found.[/dim]")
                console.print("\nCreate one with: chaoscypher template create --interactive")
                return

            table = Table(title="Templates", show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="green")

            if verbose:
                table.add_column("Properties", style="white")
                table.add_column("Description", style="dim")

            for t in templates:
                row = [
                    t.get("id", ""),
                    t.get("name", ""),
                    t.get("template_type", "node"),
                ]

                if verbose:
                    # Format properties
                    props = t.get("properties", [])
                    if props:
                        props_str = ", ".join(
                            f"{p.get('name', '')}:{p.get('property_type', 'STRING').lower()}"
                            for p in props[:3]
                        )
                        if len(props) > 3:
                            props_str += f" (+{len(props) - 3} more)"
                    else:
                        props_str = "(none)"

                    row.append(props_str)
                    row.append(
                        t.get("description", "")[:40] + "..."
                        if len(t.get("description", "")) > 40
                        else t.get("description", "")
                    )

                table.add_row(*row)

            console.print(table)

            # Summary
            pagination = result.get("pagination", {})
            total = pagination.get("total", len(templates))
            console.print(f"\n[dim]Total: {total} template(s)[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
