# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show all ingested files."""

import json
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_json
from chaoscypher_cli.utils.display import get_quality_color, get_status_color
from chaoscypher_core.models import SourceStatus


console = Console()


@click.command("list")
@click.option(
    "--status",
    "-s",
    help="Filter by status (pending, indexing, indexed, extracted, committed, error)",
)
@click.option(
    "--pending",
    "-p",
    is_flag=True,
    help="Show only files not yet committed (excludes committed and errored)",
)
@click.option(
    "--awaiting",
    "-a",
    is_flag=True,
    help="Show only sources awaiting domain confirmation.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json", "yaml"]),
    help="Output format",
)
@click.option("--database", "-d", default="default", help="Database name")
def list_files(
    status: str | None, pending: bool, awaiting: bool, output_format: str, database: str
) -> None:
    """List all ingested files.

    Shows files with their status in the source_processing pipeline.

    Example:
        chaoscypher source list
        chaoscypher source list --pending            # Show resumable files
        chaoscypher source list --status indexed
        chaoscypher source list --format json
    """
    try:
        ctx = get_context(database_name=database)

        # Get source files from storage adapter. Use ``ctx.database_name``
        # (the resolved name, which honours ``db switch`` / env override /
        # config fallback) rather than the raw ``database`` arg — Click
        # leaves that at the literal "default" when the user didn't pass
        # ``--database``, which would otherwise query the ``default`` DB
        # even when ``db current`` reports a different active workspace.
        effective_status = SourceStatus.AWAITING_CONFIRMATION if awaiting else status
        files = ctx.storage_adapter.list_files(
            database_name=ctx.database_name, status=effective_status
        )

        # Apply pending filter (excludes committed and errored). Use the real
        # enum value ``SourceStatus.ERROR`` ("error"): there is no "failed"
        # status, so the old literal never matched and errored sources leaked
        # into --pending.
        if pending:
            files = [
                f
                for f in files
                if f.get("status") not in (SourceStatus.COMMITTED, SourceStatus.ERROR)
            ]

        if output_format == "json":
            print_json(json.dumps(files, indent=2, default=str))

        elif output_format == "yaml":
            try:
                import yaml

                console.print(yaml.dump(files, default_flow_style=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                print_json(json.dumps(files, indent=2, default=str))

        else:  # table format
            if not files:
                console.print("[dim]No ingested files found.[/dim]")
                if status:
                    console.print(f"[dim]Filter: status={status}[/dim]")
                console.print("\nAdd files with: chaoscypher source add <file>")
                return

            table = Table(title="Ingested Files", show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Filename", style="cyan")
            table.add_column("Type", style="white")
            table.add_column("Size", style="white", justify="right")
            table.add_column("Status", style="green")
            table.add_column("Quality", justify="right")
            table.add_column("Created", style="dim")

            for f in files:
                # Format status with color
                file_status = f.get("status", "unknown")
                scolor = get_status_color(file_status)
                status_display = f"[{scolor}]{file_status}[/{scolor}]"

                # Format file size
                file_size = f.get("file_size", 0)
                if file_size >= 1_000_000:
                    size_display = f"{file_size / 1_000_000:.1f} MB"
                elif file_size >= 1_000:
                    size_display = f"{file_size / 1_000:.1f} KB"
                else:
                    size_display = f"{file_size} B"

                # Format date
                created = f.get("created_at", "")
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        created = dt.strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        pass

                # Format quality grade
                quality_grade = f.get("cached_quality_grade")
                quality_label = f.get("cached_quality_label")
                if quality_grade is not None:
                    qcolor = get_quality_color(quality_grade)
                    quality_display = f"[{qcolor}]{quality_grade:.0f} {quality_label}[/{qcolor}]"
                else:
                    quality_display = "[dim]-[/dim]"

                table.add_row(
                    f.get("id", ""),
                    f.get("filename", ""),
                    f.get("file_type", "").split("/")[-1],
                    size_display,
                    status_display,
                    quality_display,
                    created,
                )

            console.print(table)
            console.print(f"\n[dim]Total: {len(files)} file(s)[/dim]")

            # Show resume hint for pending files
            if pending and files:
                console.print("\n[dim]To resume: cc source add <ID>[/dim]")
                console.print("[dim]Or use:    cc source add --resume[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
