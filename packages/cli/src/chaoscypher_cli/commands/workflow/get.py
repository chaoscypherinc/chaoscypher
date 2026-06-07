# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Get command - Show workflow details."""

import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.argument("workflow_id")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option("--database", "-d", default="default", help="Database name")
def get(workflow_id: str, output_format: str, database: str) -> None:
    """Show details of a specific workflow.

    WORKFLOW_ID is the unique identifier or name of the workflow.

    Example:
        chaoscypher workflow get entity-extraction
        chaoscypher workflow get wf-123 --format json
    """
    try:
        ctx = get_context(database_name=database)

        workflow = ctx.workflow_service.get_workflow(workflow_id)

        if not workflow:
            # Try to find by name
            workflows = ctx.workflow_service.list_workflows()
            matching = [w for w in workflows if w.get("name") == workflow_id]

            if not matching:
                console.print(f"[red]Workflow not found:[/red] {workflow_id}")
                sys.exit(1)

            workflow = matching[0]

        # workflow_service returns a TypedDict — already a plain dict at runtime.
        # Copy so we can mutate (add steps) without altering the cached value.
        workflow_dict: dict[str, Any] = dict(workflow)

        # Get steps
        steps = ctx.workflow_service.list_workflow_steps(workflow_dict.get("id", workflow_id))

        if output_format == "json":
            workflow_dict["steps"] = steps
            console.print(json.dumps(workflow_dict, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                workflow_dict["steps"] = steps
                console.print(yaml.dump(workflow_dict, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                workflow_dict["steps"] = steps
                console.print(json.dumps(workflow_dict, indent=2, default=str))

        else:  # table format
            name = workflow_dict.get("name", workflow_id)
            is_active = workflow_dict.get("is_active", True)

            # Status badge
            status_badge = "[green]active[/green]" if is_active else "[yellow]inactive[/yellow]"

            console.print(
                Panel(
                    f"[bold]{name}[/bold] {status_badge}\n"
                    f"[dim]ID: {workflow_dict.get('id', workflow_id)}[/dim]",
                    title="Workflow",
                    border_style="cyan",
                )
            )

            # Basic info
            info_table = Table(show_header=False, box=None)
            info_table.add_column("Field", style="dim", width=20)
            info_table.add_column("Value", style="white")

            if workflow_dict.get("description"):
                info_table.add_row("Description", workflow_dict.get("description"))

            info_table.add_row(
                "Active", "[green]Yes[/green]" if is_active else "[yellow]No[/yellow]"
            )
            info_table.add_row("Created", str(workflow_dict.get("created_at", "")))

            if workflow_dict.get("updated_at"):
                info_table.add_row("Updated", str(workflow_dict.get("updated_at")))

            if workflow_dict.get("last_run_at"):
                info_table.add_row("Last Run", str(workflow_dict.get("last_run_at")))

            console.print(info_table)

            # Steps
            if steps:
                console.print("\n[cyan]Steps:[/cyan]")

                step_table = Table(show_header=True, box=None)
                step_table.add_column("#", style="dim", width=3)
                step_table.add_column("Name", style="cyan")
                step_table.add_column("Tool Type", style="white")
                step_table.add_column("Tool ID", style="dim")

                for i, step in enumerate(steps, 1):
                    step_table.add_row(
                        str(step.get("order", i)),
                        step.get("name", "Unnamed"),
                        step.get("tool_type", "N/A"),
                        step.get("tool_id", "N/A"),
                    )

                console.print(step_table)
            else:
                console.print("\n[dim]No steps defined.[/dim]")

            # Statistics if available
            stats = workflow_dict.get("statistics", {})
            if stats:
                console.print("\n[cyan]Statistics:[/cyan]")
                console.print(f"  Total runs: {stats.get('total_runs', 0)}")
                console.print(f"  Successful: {stats.get('successful_runs', 0)}")
                console.print(f"  Failed: {stats.get('failed_runs', 0)}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
