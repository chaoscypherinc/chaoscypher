# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory relink command - Link the memories not linked yet, in the foreground.

A memory whose link could not finish (the model was busy, failing or missing, or
the process ended) is kept and marked; a write-mode server links it on its next
start. This command runs the same link step now, oldest first, ignoring the
backoff a failing memory waits out at start-up. It prints counts only, never a
memory's text.
"""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.signals import Termination, cancelled_by_signal


console = Console()

EXIT_LEFT_UNLINKED = 3
"""The exit code when some memory is still not linked after the run."""


@click.command()
@click.option("--database", "-d", default="default", help="Database name")
@click.option(
    "--limit",
    "-n",
    type=click.IntRange(min=1),
    default=None,
    help="Link at most N memories, oldest first (default: every one)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print how many memories are not linked yet, by state, and link nothing",
)
@click.option(
    "--include-empty",
    is_flag=True,
    help="Also link again the memories that linked with no facts",
)
def relink(database: str, limit: int | None, dry_run: bool, include_empty: bool) -> None:
    """Link the memories not linked yet, oldest first.

    Prints one line of counts and exits 0 when nothing is left unlinked, 3 when
    some memory still is (its model failed, none is set up, or another process
    is linking it).

    \b
    Examples:
        chaoscypher memory relink
        chaoscypher memory relink --dry-run
        chaoscypher memory relink --limit 10 -d my-project
        chaoscypher memory relink --include-empty
    """  # noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.
    try:
        ctx = get_context(database_name=database)
        engine = ctx._engine
        if engine is None:
            msg = "Engine not initialized — context.connect() must be called first"
            raise RuntimeError(msg)

        from chaoscypher_core.mcp.memory_relink import (
            RelinkReport,
            counts_line,
            memory_views,
            relink_pending,
        )

        if dry_run:
            click.echo(counts_line(view for _, view in memory_views(engine)))
            return

        stopped = Termination()

        async def run() -> RelinkReport | None:
            """The relink; None when a signal stopped it (the claim it held is given back)."""
            async with cancelled_by_signal() as stop:
                done = await relink_pending(
                    engine, honour_backoff=False, include_empty=include_empty, limit=limit
                )
            stopped.signal = stop.signal
            return None if stop.signal is not None else done

        report = asyncio.run(run())
        if report is None:
            sys.exit(stopped.exit_code)
        click.echo(report.line())
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    sys.exit(0 if report.left == 0 else EXIT_LEFT_UNLINKED)
