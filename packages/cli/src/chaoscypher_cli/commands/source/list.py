# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""List command - Show all ingested files."""

import json
import sys
from datetime import datetime
from typing import Any

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_cli.sources.tag_options import match_option, tag_option
from chaoscypher_cli.utils.console import print_caught_error, print_json
from chaoscypher_cli.utils.display import get_quality_color, get_status_color
from chaoscypher_core.models import SourceStatus, TagMatch


console = Console()

# The widest the Tags and Summary cells grow; longer text is cut with an ellipsis.
TAGS_COLUMN_WIDTH = 24
SUMMARY_COLUMN_WIDTH = 40


def _with_tags(ctx: Any, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give each row its ``tags``: the document's tag names, sorted (one query for the page)."""
    tags_by_source = ctx.storage_adapter.get_source_tags_batch([f["id"] for f in files])
    for f in files:
        f["tags"] = sorted(t["name"] for t in tags_by_source.get(f["id"], []))
    return files


def _total_in_db(ctx: Any, status: str | None, tag_names: list[str], tag_match: TagMatch) -> int:
    """How many sources the listing's filters select in the whole database (no limit)."""
    if tag_names:
        # The tag filter's count, from the same SQL filter as the rows.
        _page, total = ctx.storage_adapter.list_sources(
            page=1, page_size=1, status=status, tags=tag_names, match=tag_match
        )
        return int(total)
    if status:
        return int(
            ctx.storage_adapter.count_sources_by_statuses(
                statuses=[status], database_name=ctx.database_name
            )
        )
    return int(ctx.storage_adapter.count_sources(database_name=ctx.database_name))


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
@click.option(
    "--limit",
    "-n",
    "limit",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum number of files to fetch (default: adapter default, 100).",
)
@tag_option("Only documents carrying this tag (repeatable)")
@match_option()
@click.option("--database", "-d", default="default", help="Database name")
def list_files(
    status: str | None,
    pending: bool,
    awaiting: bool,
    output_format: str,
    limit: int | None,
    tags: tuple[str, ...],
    match: str,
    database: str,
) -> None:
    """List all ingested files.

    Shows files with their status in the source_processing pipeline.

    Example:
        chaoscypher source list
        chaoscypher source list --pending            # Show resumable files
        chaoscypher source list --status indexed
        chaoscypher source list --limit 500
        chaoscypher source list --format json
        chaoscypher source list --tag alpha --tag beta --match all
    """
    from chaoscypher_core.services.sources.tags import filter_tag_scope

    try:
        tag_names, tag_match = filter_tag_scope(tags, match)
        ctx = get_context(database_name=database)

        # Get source files from storage adapter. Use ``ctx.database_name``
        # (the resolved name, which honours ``db switch`` / env override /
        # config fallback) rather than the raw ``database`` arg — Click
        # leaves that at the literal "default" when the user didn't pass
        # ``--database``, which would otherwise query the ``default`` DB
        # even when ``db current`` reports a different active workspace.
        effective_status = SourceStatus.AWAITING_CONFIRMATION if awaiting else status
        # Only pass ``limit`` through when the user asked for one, so the
        # adapter's own default stays the single source of truth.
        # The tag filter, likewise only when given, runs in the adapter's
        # query, before its limit.
        filter_kwargs: dict[str, Any] = {} if limit is None else {"limit": limit}
        if tag_names:
            filter_kwargs.update(tags=tag_names, match=tag_match)
        files = ctx.storage_adapter.list_files(
            database_name=ctx.database_name, status=effective_status, **filter_kwargs
        )
        fetched_count = len(files)

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

        files = _with_tags(ctx, files) if files else files

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
                if tag_names:
                    console.print(
                        f"[dim]Filter: tags={', '.join(tag_names)} (match {tag_match})[/dim]"
                    )
                console.print("\nAdd files with: chaoscypher source add <file>")
                return

            table = Table(title="Ingested Files", show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Filename", style="cyan")
            table.add_column("Type", style="white")
            table.add_column("Size", style="white", justify="right")
            table.add_column("Status", style="green")
            table.add_column("Quality", justify="right")
            # Shown when a listed document has tags (or a summary): two empty
            # columns would only squeeze the others on a narrow terminal.
            show_tags = any(f["tags"] for f in files)
            show_summary = any(f.get("summary") for f in files)
            if show_tags:
                table.add_column(
                    "Tags", style="magenta", max_width=TAGS_COLUMN_WIDTH, overflow="ellipsis"
                )
            if show_summary:
                table.add_column(
                    "Summary",
                    max_width=SUMMARY_COLUMN_WIDTH,
                    overflow="ellipsis",
                    no_wrap=True,
                )
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

                labels = [
                    *([escape(", ".join(f["tags"]))] if show_tags else []),
                    *([escape(f.get("summary") or "")] if show_summary else []),
                ]
                table.add_row(
                    f.get("id", ""),
                    f.get("filename", ""),
                    f.get("file_type", "").split("/")[-1],
                    size_display,
                    status_display,
                    quality_display,
                    *labels,
                    created,
                )

            console.print(table)

            # Truncation visibility: the adapter caps the fetch (default
            # 100), so "Total" lied for databases with more sources than
            # the cap. Compare the fetched row count against the real
            # DB total and say so when rows were left behind.
            total_in_db = _total_in_db(
                ctx, str(effective_status) if effective_status else None, tag_names, tag_match
            )
            if total_in_db > fetched_count:
                console.print(
                    f"\n[dim]Showing first {len(files)} of {total_in_db} file(s)."
                    " Use --limit to fetch more.[/dim]"
                )
            else:
                console.print(f"\n[dim]Total: {len(files)} file(s)[/dim]")

            # Show resume hint for pending files
            if pending and files:
                console.print("\n[dim]To resume: chaoscypher source add <ID>[/dim]")
                console.print("[dim]Or use:    chaoscypher source add --resume[/dim]")

    except Exception as e:
        print_caught_error(e, console=console)
        sys.exit(1)
