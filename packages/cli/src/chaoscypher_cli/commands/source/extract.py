# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""chaoscypher source extract <id> — trigger entity extraction.

Runs entity/relationship extraction for a source that has already been
uploaded and indexed.  The source must be in INDEXED status, or in
COMMITTED status when --force is given.

Usage:
    chaoscypher source extract if_abc123
    chaoscypher source extract if_abc123 --depth quick
    chaoscypher source extract if_abc123 --domain technical
    chaoscypher source extract if_abc123 --force          # re-extract committed
    chaoscypher source extract if_abc123 --force --yes    # skip confirmation
"""

from __future__ import annotations

from typing import Any, get_args

import click
from rich.console import Console

from chaoscypher_cli.sources.domains import EXTRACT_DOMAIN_CHOICES
from chaoscypher_core.ports.types import FilteringMode


@click.command("extract")
@click.argument("source_id")
@click.option(
    "--depth",
    type=click.Choice(["quick", "full"]),
    default="full",
    show_default=True,
    help="Extraction depth: quick (fast sample) or full (all chunks).",
)
@click.option(
    "--domain",
    type=click.Choice(list(EXTRACT_DOMAIN_CHOICES)),
    default=None,
    help="Force extraction domain (default: auto-detect from content).",
)
@click.option(
    "--filtering-mode",
    type=click.Choice(list(get_args(FilteringMode))),
    default=None,
    help="Extraction filtering mode preset (overrides domain default).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Re-extract a committed source. "
        "Deletes existing graph nodes and edges before re-running extraction."
    ),
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the destructive-action confirmation prompt (use with --force).",
)
@click.option("--database", "-d", default="default", help="Target database.")
@click.option("--quiet", "-q", is_flag=True, help="Minimal output.")
def extract_cmd(
    source_id: str,
    depth: str,
    domain: str | None,
    filtering_mode: FilteringMode | None,
    force: bool,
    yes: bool,
    database: str,
    quiet: bool,
) -> None:
    """Extract entities and relationships from an indexed source.

    SOURCE_ID must be the file ID of a source that has already been indexed
    (e.g., via ``chaoscypher source add``).

    \b
    Examples:
        chaoscypher source extract if_abc123
        chaoscypher source extract if_abc123 --depth quick
        chaoscypher source extract if_abc123 --domain technical
        chaoscypher source extract if_abc123 --force      # re-extract committed
        chaoscypher source extract if_abc123 --force -y   # skip confirmation
    """  # noqa: D301
    import sys

    from rich.prompt import Confirm

    from chaoscypher_cli.context import get_context
    from chaoscypher_cli.sources import CLISourceProcessingService
    from chaoscypher_cli.utils.llm_check import check_llm_or_skip
    from chaoscypher_core.models import SourceStatus

    console = Console()

    try:
        ctx = get_context(database_name=database)

        with CLISourceProcessingService(ctx) as service:
            # --- Lookup source ---
            source = service.get_file_status(source_id)
            if source is None:
                console.print(f"[red]Source not found:[/red] {source_id}")
                sys.exit(1)

            source_status = source.get("status", "")

            # --- Guard: committed without --force ---
            if source_status == SourceStatus.COMMITTED and not force:
                console.print(
                    f"[yellow]{source_id} is already committed.[/yellow]\n"
                    "Use [bold]--force[/bold] to re-extract "
                    "(this deletes the existing graph nodes and edges for this source)."
                )
                sys.exit(1)

            # --- Guard: wrong status for extraction ---
            # Narrow to INDEXED only (not EXTRACTED) to match Cortex's trigger_extraction.
            # Re-running on an EXTRACTED source without resetting first would hit the
            # silent-drop in complete_extraction (commit_complete=True guard), producing
            # no new graph output. Force-re-extract on COMMITTED is the supported path.
            allowed_statuses = {SourceStatus.INDEXED}
            if force:
                allowed_statuses.add(SourceStatus.COMMITTED)

            if source_status not in allowed_statuses:
                console.print(
                    f"[red]Cannot extract source with status '{source_status}'.[/red]\n"
                    "Expected: indexed (or committed with --force)."
                )
                sys.exit(1)

            # --- Destructive confirmation for committed+force ---
            if source_status == SourceStatus.COMMITTED and force:
                filename = source.get("filename", source_id)
                if not yes and not Confirm.ask(
                    f"[red]Re-extracting [bold]{filename}[/bold] will delete its existing "
                    "graph nodes and edges. Continue?[/red]",
                ):
                    console.print("[dim]Cancelled.[/dim]")
                    sys.exit(0)

                if not quiet:
                    console.print("[cyan]Resetting committed source for re-extraction…[/cyan]")

                removed = service.reset_for_re_extraction(source_id)
                if not quiet:
                    nodes = removed.get("nodes_deleted", 0)
                    edges = removed.get("edges_deleted", 0)
                    console.print(
                        f"  [dim]Removed {nodes} node(s) and {edges} edge(s) from graph.[/dim]"
                    )

            # --- LLM availability check ---
            if not service.has_llm:
                proceed, should_skip = check_llm_or_skip("entity extraction")
                if not proceed or should_skip:
                    console.print(
                        "[red]No LLM provider configured.[/red] "
                        "Entity extraction requires an LLM. "
                        "Run [bold]chaoscypher config set llm.provider <provider>[/bold] first."
                    )
                    sys.exit(1)

            # --- Run extraction ---
            filename = source.get("filename", source_id)
            if not quiet:
                console.print(
                    f"\n[bold]Extracting:[/bold] [cyan]{filename}[/cyan]  "
                    f"[dim](depth: {depth})[/dim]\n"
                )

            _run_extraction(
                service=service,
                source_id=source_id,
                depth=depth,
                domain=domain,
                filtering_mode=filtering_mode,
                quiet=quiet,
                console=console,
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        sys.exit(130)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _run_extraction(
    service: Any,
    source_id: str,
    depth: str,
    domain: str | None,
    filtering_mode: FilteringMode | None,
    quiet: bool,
    console: Console,
) -> None:
    """Run the extraction stage with progress display.

    Args:
        service: CLISourceProcessingService instance.
        source_id: Source file ID.
        depth: Extraction depth ('quick' or 'full').
        domain: Forced domain override (None = auto-detect).
        filtering_mode: Filtering mode preset override.
        quiet: Suppress progress output.
        console: Rich console.
    """
    import time

    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

    # Temporarily override extraction_depth on the file record so the service
    # uses the depth specified on this command line invocation.
    file_record = service.ctx.storage_adapter.get_file(source_id, service.ctx.database_name)
    if file_record and depth != file_record.get("extraction_depth", "full"):
        service.ctx.storage_adapter.update_file(
            source_id, database_name=service.ctx.database_name, updates={"extraction_depth": depth}
        )

    # Resolve domain. An explicit ``--domain auto`` clears any prior forced
    # domain (revert to auto-detection); ``--domain X`` forces X; omitting
    # ``--domain`` (None) leaves whatever was previously set untouched.
    if domain == "auto":
        service.ctx.storage_adapter.update_file(
            source_id,
            database_name=service.ctx.database_name,
            updates={"forced_domain": None},
        )
    elif domain:
        service.ctx.storage_adapter.update_file(
            source_id,
            database_name=service.ctx.database_name,
            updates={"forced_domain": domain},
        )

    start_time = time.time()

    if quiet:
        extract_result, llm_summary = service.extract_entities(
            source_id,
            filtering_mode=filtering_mode,
        )
        stats = extract_result.get("stats", {})
        console.print(
            f"[green]OK[/green] {source_id} — "
            f"{stats.get('entities_count', 0)} entities, "
            f"{stats.get('relationships_count', 0)} relationships"
        )
        return

    # Progress bar for interactive mode
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Extracting[/cyan]"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total} groups"),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )
    task_id = progress.add_task("extract", total=1)

    detected_domain: list[str] = []

    def on_progress(current: int, total_groups: int) -> None:
        progress.update(task_id, total=total_groups, completed=current)

    def on_domain(domain_name: str) -> None:
        detected_domain.append(domain_name)
        console.print(f"  [dim]Domain:[/dim] [magenta]{domain_name}[/magenta]")

    with progress:
        extract_result, llm_summary = service.extract_entities(
            source_id,
            progress_callback=on_progress,
            domain_callback=on_domain,
            filtering_mode=filtering_mode,
        )

    elapsed = time.time() - start_time
    stats = extract_result.get("stats", {})
    entity_count = stats.get("entities_count", 0)
    rel_count = stats.get("relationships_count", 0)
    total_calls = llm_summary.get("total_calls", 0)
    cost = llm_summary.get("estimated_cost_usd", 0.0)

    console.print()
    console.print(f"  [green]✓[/green] [bold]Extraction complete[/bold]  [dim]{elapsed:.1f}s[/dim]")
    console.print(f"    Entities:      {entity_count}")
    console.print(f"    Relationships: {rel_count}")
    if total_calls:
        console.print(f"    LLM calls:     {total_calls}")
    if cost > 0:
        cost_str = f"${cost:.4f}" if cost >= 0.01 else "<$0.01"
        console.print(f"    Estimated cost: {cost_str}")
    console.print()
    console.print(
        "[dim]Run[/dim] [bold]chaoscypher source add "
        f"{source_id}[/bold] [dim]to commit to the graph.[/dim]"
    )


__all__ = ["extract_cmd"]
