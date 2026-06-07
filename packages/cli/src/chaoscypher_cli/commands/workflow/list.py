# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show available workflows."""

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
@click.option("--verbose", "-v", is_flag=True, help="Show detailed information including steps")
@click.option("--category", "-c", help="Filter by category")
@click.option("--active/--inactive", default=None, help="Filter by active status")
@click.option("--database", "-d", default="default", help="Database name")
def list_workflows(
    output_format: str,
    verbose: bool,
    category: str | None,
    active: bool | None,
    database: str,
) -> None:
    """Show available workflows in the knowledge base.

    Lists all workflows defined in the database, including
    built-in and custom workflows.

    Example:
        chaoscypher workflow list
        chaoscypher workflow list --format json
        chaoscypher workflow list --verbose
        chaoscypher workflow list --category research
        chaoscypher workflow list --active
    """
    try:
        ctx = get_context(database_name=database)

        # Get workflows with filters
        workflows = ctx.workflow_service.list_workflows(
            category=category,
            is_active=active,
        )

        if output_format == "json":
            console.print(json.dumps(workflows, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(workflows, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                console.print(json.dumps(workflows, indent=2, default=str))

        else:  # table format
            if not workflows:
                console.print("[dim]No workflows found.[/dim]")
                console.print("\nUse the web UI to create workflows.")
                return

            table = Table(title="Workflows", show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Name", style="cyan")
            table.add_column("Category", style="green")
            table.add_column("Status", style="white")

            if verbose:
                table.add_column("Steps", style="white")
                table.add_column("Description", style="dim")

            for wf in workflows:
                status = "[green]Active[/green]" if wf.get("is_active") else "[dim]Inactive[/dim]"

                row = [
                    wf.get("id", ""),
                    wf.get("name", ""),
                    wf.get("category", "(none)"),
                    status,
                ]

                if verbose:
                    # Get step count
                    steps = ctx.workflow_service.list_workflow_steps(wf.get("id", ""))
                    row.append(str(len(steps)))

                    desc = wf.get("description", "") or ""
                    if len(desc) > 40:
                        desc = desc[:37] + "..."
                    row.append(desc)

                table.add_row(*row)

            console.print(table)

            # Summary
            active_count = sum(1 for w in workflows if w.get("is_active"))
            console.print(
                f"\n[dim]Total: {len(workflows)} workflow(s) ({active_count} active)[/dim]"
            )

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
