# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recalculate command - Batch recalculate and cache quality scores."""

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.option(
    "--domain",
    help="Only recalculate sources in this domain",
)
@click.option(
    "--outdated-only",
    is_flag=True,
    help="Only recalculate sources with outdated or missing cached scores",
)
@click.option(
    "--source-id",
    "-s",
    multiple=True,
    help="Recalculate specific source(s) by ID (can be repeated)",
)
@click.option("--database", "-d", default="default", help="Database name")
def recalculate(
    domain: str | None, outdated_only: bool, source_id: tuple[str, ...], database: str
) -> None:
    """Recalculate and cache quality scores for sources.

    This command recalculates quality scores and caches them in the database.
    Useful when:
    - The scoring algorithm has been updated
    - Sources have outdated or missing cached scores
    - You want to refresh all quality metrics

    Examples:
        chaoscypher source quality recalculate
        chaoscypher source quality recalculate --domain literary
        chaoscypher source quality recalculate --outdated-only
        chaoscypher source quality recalculate -s if_abc123 -s if_xyz789
    """
    from chaoscypher_cli.commands.quality.utils import (
        build_entity_chunk_mentions,
        get_quality_config,
    )
    from chaoscypher_core.services.quality import SCORING_VERSION, QualityScorer

    ctx = get_context(database_name=database)

    # Get sources to recalculate (list_files uses load_only, excludes extraction_results)
    all_sources = ctx.storage_adapter.list_files(ctx.database_name)

    # Filter sources
    sources_to_process = []
    for source in all_sources:
        # Skip if not extracted
        if not source.get("extraction_complete"):
            continue

        # Filter by source IDs if specified
        if source_id and source.get("id") not in source_id:
            continue

        # Filter by domain if specified
        if domain and source.get("extraction_domain") != domain:
            continue

        # Filter by outdated status if specified
        if outdated_only:
            cached_version = source.get("cached_scores_version")
            if cached_version is not None and cached_version == SCORING_VERSION:
                continue

        sources_to_process.append(source)

    if not sources_to_process:
        console.print("[yellow]No sources found matching criteria[/yellow]")
        return

    console.print(
        f"\n[bold]Recalculating quality scores for {len(sources_to_process)} source(s)[/bold]"
    )
    console.print(f"Scoring algorithm version: {SCORING_VERSION}\n")

    success_count = 0
    error_count = 0
    errors: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=len(sources_to_process))

        for source in sources_to_process:
            source_id_str = source.get("id", "unknown")
            title = (source.get("title") or source.get("filename") or "Untitled")[:40]
            progress.update(task, description=f"Processing: {title}...")

            try:
                # Fetch full record for extraction_results
                full = ctx.storage_adapter.get_file(source_id_str, ctx.database_name)
                if not full:
                    progress.advance(task)
                    continue

                extraction_results = full.get("extraction_results") or {}
                entities = extraction_results.get("entities", [])
                relationships = extraction_results.get("relationships", [])

                if not entities and not relationships:
                    progress.advance(task)
                    continue

                # Get domain-specific quality config
                source_domain = source.get("extraction_domain")
                quality_config = get_quality_config(source_domain, ctx.database_name)

                # Calculate scores
                scorer = QualityScorer(quality_config)
                entity_chunk_mentions = build_entity_chunk_mentions(entities)

                cacheable_scores = scorer.get_cacheable_scores(
                    source_id=source_id_str,
                    entities=entities,
                    relationships=relationships,
                    entity_chunk_mentions=entity_chunk_mentions,
                )

                ctx.storage_adapter.update_file(
                    source_id_str, database_name=ctx.database_name, updates=cacheable_scores
                )
                success_count += 1

            except Exception as e:
                error_count += 1
                errors.append({"source_id": source_id_str, "error": str(e)})
                console.print(f"[red]Error processing {source_id_str}: {e}[/red]")

            progress.advance(task)

    # Summary
    console.print("\n[bold]Recalculation Complete[/bold]")
    console.print(f"[green]Successfully processed: {success_count}[/green]")
    if error_count > 0:
        console.print(f"[red]Errors: {error_count}[/red]")
        for err in errors[:5]:  # Show first 5 errors
            console.print(f"  - {err['source_id']}: {err['error']}")
        if len(errors) > 5:
            console.print(f"  ... and {len(errors) - 5} more errors")
