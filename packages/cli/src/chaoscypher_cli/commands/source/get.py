# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Get command - Display source details."""

import sys
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.display import get_quality_color, get_status_color


console = Console()


def _format_duration(ms: int | float | None) -> str:
    """Format milliseconds into human-readable duration.

    Args:
        ms: Duration in milliseconds

    Returns:
        Formatted string (e.g. "1.2s", "3.5m", "450ms")
    """
    if ms is None or ms <= 0:
        return "-"
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}m"
    if ms >= 1_000:
        return f"{ms / 1_000:.1f}s"
    return f"{int(ms)}ms"


_STAGE_LABELS: dict[str, str] = {
    "vision": "Vision processing",
    "embedding": "Embedding",
    "mcp_extraction": "MCP Entity Extraction",
}


def _format_duration_seconds(seconds: float) -> str:
    """Convert seconds to a human-readable duration string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g. "45s", "3m", "2h 15m")
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _format_stage_progress(stage_progress: dict[str, dict]) -> str | None:
    """Format active stages as a Rich-friendly block. Returns None when empty.

    Active = total > 0 AND completed_at is None. Completed and unstarted
    stages are skipped.

    Args:
        stage_progress: Dict keyed by stage name with progress details.

    Returns:
        Formatted multi-line string, or None if no active stages.
    """
    active = {
        name: r
        for name, r in stage_progress.items()
        if r.get("completed_at") is None and r.get("total", 0) > 0
    }
    if not active:
        return None

    lines = ["Stages:"]
    for name, r in active.items():
        avg_ms = r.get("avg_ms")
        avg_str = f" · ~{avg_ms / 1000:.1f}s avg" if avg_ms else ""
        if avg_ms and r["processed"] < r["total"]:
            remaining_s = (r["total"] - r["processed"]) * (avg_ms / 1000)
            eta_str = f" · ~{_format_duration_seconds(remaining_s)}"
        else:
            eta_str = ""
        label = _STAGE_LABELS.get(name, name)
        lines.append(f"  {label:<22} {r['processed']:>5} / {r['total']:<5}{avg_str}{eta_str}")
    return "\n".join(lines)


def _show_extraction_pipeline(
    ctx: Any,
    source_id: str,
    file_record: dict[str, Any],
) -> None:
    """Display extraction pipeline summary matching Web UI cards.

    Shows: Groups, Entities (dedup%), Relations (remap%), Templates,
    Filtered, Invalid, Retries, Avg Time, Est. Cost.

    Works in two modes:
    - Full: When per-chunk task stats exist (internal pipeline extraction)
    - Lite: When only file-level commit counts exist (MCP extraction)

    Args:
        ctx: CLI context with storage_adapter and database_name
        source_id: Source file ID
        file_record: File record dict from storage
    """
    # Try to get per-chunk task stats (only exists for internal pipeline)
    stats = ctx.storage_adapter.get_extraction_task_stats(
        source_id=source_id,
        database_name=ctx.database_name,
    )

    # Check if we have at least file-level extraction data
    final_entities = file_record.get("extraction_entities_count", 0)
    final_rels = file_record.get("extraction_relationships_count", 0)
    templates_created = file_record.get("commit_templates_created", 0)

    if not stats and not final_entities and not final_rels:
        return

    # Aggregate filtering stats if task data exists
    total_entity_filtered = 0
    total_rel_filtered = 0
    stage_counts: dict[str, dict[str, int]] = {}
    if stats:
        filtering_logs = ctx.storage_adapter.get_extraction_tasks_filtering_logs(
            source_id=source_id,
            database_name=ctx.database_name,
        )
        for task in filtering_logs:
            log = task.get("filtering_log")
            if not log or not isinstance(log, dict):
                continue
            for stage in log.get("stages", []):
                stage_name = stage.get("stage", "unknown")
                removed = stage.get("removed_count", 0)
                if stage_name not in stage_counts:
                    stage_counts[stage_name] = {"removed": 0, "chunk_count": 0}
                stage_counts[stage_name]["removed"] += removed
                stage_counts[stage_name]["chunk_count"] += 1
                if (
                    "entity" in stage_name
                    or "type_rescue" in stage_name
                    or "implausible" in stage_name
                ):
                    total_entity_filtered += removed
                else:
                    total_rel_filtered += removed

    console.print("\n[cyan]Extraction Pipeline:[/cyan]")

    ptable = Table(show_header=False, box=None)
    ptable.add_column("Field", style="dim", width=20)
    ptable.add_column("Value", style="white")

    # Groups / Chunks
    chunk_count = file_record.get("chunk_count", 0)
    if stats:
        total_tasks = stats.get("total_tasks", 0)
        groups_str = str(total_tasks)
        if chunk_count:
            groups_str += f" [dim](from {chunk_count} chunks)[/dim]"
        ptable.add_row("Groups", groups_str)
    elif chunk_count:
        ptable.add_row("Chunks", str(chunk_count))

    # Entities (raw -> final with dedup%)
    raw_entities = stats.get("total_entities", 0) if stats else 0
    if final_entities and raw_entities > final_entities:
        dedup_pct = round(((raw_entities - final_entities) / raw_entities) * 100)
        ptable.add_row(
            "Entities",
            f"[green]{final_entities:,}[/green] [dim]from {raw_entities:,} ({dedup_pct}% deduped)[/dim]",
        )
    elif final_entities:
        ptable.add_row("Entities", f"{final_entities:,}")
    elif raw_entities:
        avg_ent = stats.get("avg_entities_per_task", 0) if stats else 0
        ptable.add_row("Entities", f"{raw_entities:,} [dim]({avg_ent:.1f}/group)[/dim]")

    # Relationships (raw -> final with remap%)
    raw_rels = stats.get("total_relationships", 0) if stats else 0
    if final_rels and raw_rels > final_rels:
        remap_pct = round(((raw_rels - final_rels) / raw_rels) * 100)
        ptable.add_row(
            "Relationships",
            f"[green]{final_rels:,}[/green] [dim]from {raw_rels:,} ({remap_pct}% remapped)[/dim]",
        )
    elif final_rels:
        ptable.add_row("Relationships", f"{final_rels:,}")
    elif raw_rels:
        avg_rel = stats.get("avg_relationships_per_task", 0) if stats else 0
        ptable.add_row("Relationships", f"{raw_rels:,} [dim]({avg_rel:.1f}/group)[/dim]")

    # Templates
    if templates_created:
        ptable.add_row("Templates", f"{templates_created:,}")

    # Per-chunk stats only available from internal pipeline
    if stats:
        # Filtered
        total_filtered = total_entity_filtered + total_rel_filtered
        if total_filtered > 0:
            ptable.add_row(
                "Filtered",
                f"[yellow]{total_filtered:,}[/yellow]"
                f" [dim]({total_entity_filtered}E / {total_rel_filtered}R)[/dim]",
            )
        else:
            ptable.add_row("Filtered", "[dim]0[/dim]")

        # Invalid relationships
        total_invalid = stats.get("total_invalid_relationships", 0)
        if total_invalid > 0:
            avg_invalid = stats.get("avg_invalid_per_task", 0)
            ptable.add_row(
                "Invalid",
                f"[red]{total_invalid:,}[/red] [dim]({avg_invalid:.1f}/group)[/dim]",
            )
        else:
            ptable.add_row("Invalid", "[dim]0[/dim]")

        # Retries
        total_retries = stats.get("total_retries", 0)
        if total_retries > 0:
            ptable.add_row("Retries", f"[yellow]{total_retries}[/yellow]")
        else:
            ptable.add_row("Retries", "[dim]0[/dim]")

        # Avg Time
        avg_duration = stats.get("avg_duration_ms")
        if avg_duration:
            ptable.add_row("Avg Time", _format_duration(avg_duration))

    # Est. Cost (from file-level LLM metrics)
    cost = file_record.get("llm_estimated_cost_usd")
    model = file_record.get("llm_model", "")
    if cost and cost > 0:
        cost_str = f"${cost:.4f}" if cost >= 0.01 else "<$0.01"
        ptable.add_row("Est. Cost", cost_str)
    elif model and "ollama" in model.lower():
        ptable.add_row("Est. Cost", "$0.00 (local)")

    if model:
        ptable.add_row("Model", model)

    console.print(ptable)

    # Show per-stage filtering breakdown if there are stages
    if stage_counts:
        console.print("\n  [dim]Filtering stages:[/dim]")
        for name, data in sorted(stage_counts.items()):
            label = name.replace("_", " ")
            console.print(f"    {label}: [yellow]{data['removed']}[/yellow] removed")


def _format_domain_row(
    *,
    forced_domain: str | None,
    detected_domain: str | None,
    domain_version: str | None,
    changed: bool,
) -> tuple[str, str | None]:
    """Build the (value, optional-detail-line) for the Domain table row.

    Returns the Rich-markup domain value and an optional second line shown
    when the plugin has changed since extraction.
    """
    version_suffix = f" [dim]v{domain_version}[/dim]" if domain_version else ""
    if forced_domain:
        value = f"[cyan]{forced_domain}[/cyan] (forced){version_suffix}"
    elif detected_domain:
        value = f"[dim]{detected_domain}[/dim] (auto-detected){version_suffix}"
    else:
        value = "[dim]auto[/dim]"
    detail = "[yellow]⚠ changed since extraction[/yellow]" if changed else None
    return value, detail


def _domain_changed(ctx: Any, file_record: dict[str, Any]) -> bool:
    """True if the source's stored domain hash differs from the live plugin."""
    stored = file_record.get("domain_content_hash")
    domain = file_record.get("extraction_domain")
    if not stored or not domain:
        return False
    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )

        fp = get_domain_registry(database_name=ctx.database_name).get_domain_fingerprint(domain)
    except Exception:
        return False
    return fp is not None and fp.content_hash != stored


@click.command()
@click.argument("source_id")
@click.option("--database", "-d", default="default", help="Database name")
def get(source_id: str, database: str) -> None:
    """Get detailed information about a source.

    SOURCE_ID is the source file identifier (e.g., if_abc123def456).

    Example:
        chaoscypher source get if_abc123def456
    """
    try:
        ctx = get_context(database_name=database)

        # Get file from adapter
        file_record = ctx.storage_adapter.get_file(source_id, ctx.database_name)

        if not file_record:
            console.print(f"[red]File not found:[/red] {source_id}")
            sys.exit(1)

        # Format file size
        file_size = file_record.get("file_size", 0)
        if file_size >= 1_000_000:
            size_display = f"{file_size:,} bytes ({file_size / 1_000_000:.1f} MB)"
        elif file_size >= 1_000:
            size_display = f"{file_size:,} bytes ({file_size / 1_000:.1f} KB)"
        else:
            size_display = f"{file_size:,} bytes"

        # Format status with color
        status = file_record.get("status", "unknown")
        status_color = get_status_color(status)

        # Display file details
        console.print(
            Panel(
                f"[bold]{file_record.get('filename', 'Unknown')}[/bold]\n[dim]ID: {source_id}[/dim]",
                title="Source File",
                border_style="cyan",
            )
        )

        table = Table(show_header=False, box=None)
        table.add_column("Field", style="dim", width=20)
        table.add_column("Value", style="white")

        table.add_row("Status", f"[{status_color}]{status}[/{status_color}]")
        table.add_row("File Type", file_record.get("file_type", ""))
        table.add_row("File Size", size_display)
        table.add_row("File Path", file_record.get("filepath", ""))
        table.add_row("Created", str(file_record.get("created_at", "")))
        table.add_row("Updated", str(file_record.get("updated_at", "")))

        # Extraction settings
        extraction_depth = file_record.get("extraction_depth", "full")
        table.add_row("Extraction Depth", extraction_depth)

        forced_domain = file_record.get("forced_domain")
        detected_domain = file_record.get("detected_domain")
        domain_version = file_record.get("domain_version")
        changed = _domain_changed(ctx, file_record)
        value, detail = _format_domain_row(
            forced_domain=forced_domain,
            detected_domain=detected_domain,
            domain_version=domain_version,
            changed=changed,
        )
        table.add_row("Domain", value)
        if detail:
            table.add_row("", detail)

        extract = file_record.get("extract_entities", False)
        table.add_row("Extract Entities", "[green]Yes[/green]" if extract else "[dim]No[/dim]")

        console.print(table)

        # Show quality scores if available
        quality_grade = file_record.get("cached_quality_grade")
        if quality_grade is not None:
            quality_label = file_record.get("cached_quality_label", "Unknown")
            qcolor = get_quality_color(quality_grade)

            console.print("\n[cyan]Quality Score:[/cyan]")

            qtable = Table(show_header=False, box=None)
            qtable.add_column("Field", style="dim", width=20)
            qtable.add_column("Value", style="white")

            qtable.add_row(
                "Grade",
                f"[{qcolor}]{quality_grade:.0f}/100 ({quality_label})[/{qcolor}]",
            )

            avg_entity = file_record.get("cached_avg_entity_quality")
            if avg_entity is not None:
                qtable.add_row("Entity Quality", f"{avg_entity:.0f}/100")

            avg_rel = file_record.get("cached_avg_relationship_quality")
            if avg_rel is not None:
                qtable.add_row("Relationship Quality", f"{avg_rel:.0f}/100")

            topology = file_record.get("cached_topology_score")
            if topology is not None:
                qtable.add_row("Topology Score", f"{topology:.0f}/100")

            pollution = file_record.get("cached_pollution_penalty")
            if pollution is not None and pollution > 0:
                qtable.add_row("Pollution Penalty", f"[yellow]-{pollution:.0f}[/yellow]")

            structural = file_record.get("cached_structural_penalty")
            if structural is not None and structural > 0:
                hub = file_record.get("cached_hub_skew") or 1.0
                recip = file_record.get("cached_reciprocal_rate") or 0.0
                qtable.add_row(
                    "Structural Penalty",
                    f"[yellow]-{structural:.0f} (hub {hub:.1f}x, recip {recip:.0%})[/yellow]",
                )

            low_ent = file_record.get("cached_low_quality_entity_count")
            low_rel = file_record.get("cached_low_quality_relationship_count")
            if low_ent or low_rel:
                parts = []
                if low_ent:
                    parts.append(f"{low_ent} entities")
                if low_rel:
                    parts.append(f"{low_rel} relationships")
                qtable.add_row("Low Quality", f"[yellow]{', '.join(parts)}[/yellow]")

            console.print(qtable)

        # Show extraction pipeline stats (matches Web UI summary cards)
        _show_extraction_pipeline(ctx, source_id, file_record)

        # Show active stage progress (mirrors Web UI Stages section)
        stage_progress = file_record.get("stage_progress") or {}
        stage_block = _format_stage_progress(stage_progress)
        if stage_block:
            console.print(f"\n[cyan]{stage_block}[/cyan]")

        # Show LLM metrics if available (per-call level detail)
        llm_total = file_record.get("llm_total_calls", 0)
        if llm_total > 0:
            console.print("\n[cyan]LLM Metrics:[/cyan]")

            ltable = Table(show_header=False, box=None)
            ltable.add_column("Field", style="dim", width=20)
            ltable.add_column("Value", style="white")

            # Call stats
            successful = file_record.get("llm_successful_calls", 0)
            failed = file_record.get("llm_failed_calls", 0)
            retries = file_record.get("llm_retry_calls", 0)
            calls_info = f"{successful}/{llm_total}"
            if retries > 0:
                calls_info += f" ({retries} retries)"
            if failed > 0:
                calls_info += f" [red]({failed} failed)[/red]"
            ltable.add_row("Calls", calls_info)

            # Token stats
            input_tokens = file_record.get("llm_total_input_tokens", 0)
            output_tokens = file_record.get("llm_total_output_tokens", 0)
            total_tokens = input_tokens + output_tokens
            tokens_info = f"{total_tokens:,}"
            wasted = file_record.get("llm_wasted_tokens", 0)
            if wasted > 0:
                waste_pct = (wasted / total_tokens * 100) if total_tokens > 0 else 0
                tokens_info += f" ([yellow]{wasted:,} wasted, {waste_pct:.0f}%[/yellow])"
            ltable.add_row("Tokens", tokens_info)
            ltable.add_row("Input / Output", f"{input_tokens:,} / {output_tokens:,}")

            # Duration
            duration_ms = file_record.get("llm_total_duration_ms", 0)
            avg_ms = file_record.get("llm_avg_call_duration_ms")
            if duration_ms > 0:
                duration_str = _format_duration(duration_ms)
                if avg_ms:
                    duration_str += f" (avg {avg_ms}ms/call)"
                ltable.add_row("Duration", duration_str)

            console.print(ltable)

        # Show error if failed
        if file_record.get("error"):
            console.print(f"\n[red]Error:[/red] {file_record.get('error')}")

        # Show indexing stats if available
        indexing = file_record.get("indexing_stats")
        if indexing:
            console.print("\n[cyan]Indexing Stats:[/cyan]")
            console.print(f"  Chunks: {indexing.get('chunk_count', 0)}")
            console.print(f"  Tokens: {indexing.get('token_count', 0):,}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
