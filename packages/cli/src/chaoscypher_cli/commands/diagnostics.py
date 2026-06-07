# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostics command - Export diagnostic bundle for bug reports.

Generates a ZIP file containing system info, database stats,
sanitized settings, and any available logs.

Example:
    chaoscypher diagnostics
    chaoscypher diagnostics --output /tmp/debug.zip
"""

from pathlib import Path

import click
from rich.console import Console


console = Console()


@click.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default=None,
    help="Output path for the ZIP file (default: current directory)",
)
def diagnostics(output: str | None) -> None:
    """Export diagnostic bundle for bug reports.

    Generates a ZIP file containing system info, database stats,
    sanitized settings, and log files (if available).

    Example:
        chaoscypher diagnostics
        chaoscypher diagnostics -o /tmp/debug.zip
    """
    from datetime import UTC, datetime

    from chaoscypher_core.services.diagnostics import DiagnosticCollector

    console.print()
    console.print("  [bold]Chaos Cypher Diagnostics[/bold]")
    console.print("  " + "-" * 35)

    db_path = None
    log_dir = None

    try:
        from chaoscypher_core.app_config import get_settings

        settings = get_settings()
        data_dir = Path(settings.paths.data_dir)

        db_dir = data_dir / "databases" / "default"
        db_file = db_dir / "app.db"
        if db_file.exists():
            db_path = db_file
            console.print(f"  [green]\u2713[/green] Database found: {db_file}")
        else:
            console.print("  [yellow]![/yellow] No database found")

        log_path = data_dir / "logs"
        if log_path.exists():
            log_dir = log_path
            log_count = len(list(log_path.glob("*.log")))
            console.print(f"  [green]\u2713[/green] Log directory: {log_count} log files")
        else:
            console.print("  [yellow]![/yellow] No log directory found")
    except Exception:
        console.print("  [yellow]![/yellow] Could not load CLI config, using defaults")

    collector = DiagnosticCollector(db_path=db_path, log_dir=log_dir)

    if output:
        output_path = Path(output)
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        output_path = Path(f"chaoscypher-diagnostics-{timestamp}.zip")

    console.print("  [dim]Collecting diagnostics...[/dim]")
    result = collector.export_bundle(output_path)

    size_kb = result.stat().st_size / 1024
    console.print(f"  [green]\u2713[/green] Bundle saved: {result} ({size_kb:.0f} KB)")
    console.print()
    console.print("  Attach this file to your bug report.")
    console.print()
