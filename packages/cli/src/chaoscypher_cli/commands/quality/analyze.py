# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Analyze command - Batch quality analysis across sources."""

import json

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context


console = Console()


@click.command()
@click.option(
    "--domain",
    help="Filter by extraction domain",
)
@click.option(
    "--min-entities",
    default=0,
    type=int,
    help="Minimum entity count to include",
)
@click.option(
    "--sort",
    "-s",
    type=click.Choice(["score", "entities", "quality"]),
    default="score",
    help="Sort by: score (total), entities (count), quality (average)",
)
@click.option(
    "--limit",
    "-n",
    default=20,
    type=int,
    help="Number of sources to show",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
@click.option("--database", "-d", default="default", help="Database name")
def analyze(
    domain: str | None, min_entities: int, sort: str, limit: int, output_json: bool, database: str
) -> None:
    """Analyze extraction quality across multiple sources.

    Scores all sources and provides aggregated metrics to compare
    extraction quality across different sources and domains.

    Examples:
        chaoscypher quality analyze
        chaoscypher quality analyze --domain literary
        chaoscypher quality analyze --min-entities 10 --sort quality
        chaoscypher quality analyze --json
    """
    from chaoscypher_cli.commands.quality.utils import (
        build_entity_chunk_mentions,
        get_quality_config,
    )
    from chaoscypher_core.services.quality import QualityScorer

    ctx = get_context(database_name=database)

    # Get all sources (list_files uses load_only, excludes extraction_results)
    sources = ctx.storage_adapter.list_files(ctx.database_name)

    # Filter and score sources
    results = []
    total_entity_quality = 0.0
    total_relationship_quality = 0.0
    count_with_entities = 0
    count_with_relationships = 0

    for source in sources:
        # Filter by domain
        source_domain = source.get("extraction_domain")
        if domain and source_domain != domain:
            continue

        # Fetch full record to get extraction_results (excluded from list_files)
        full = ctx.storage_adapter.get_file(source["id"], ctx.database_name)
        if not full:
            continue

        extraction_results = full.get("extraction_results") or {}
        entities = extraction_results.get("entities", [])
        relationships = extraction_results.get("relationships", [])

        # Filter by entity count
        if len(entities) < min_entities:
            continue

        if not entities and not relationships:
            continue

        # Get domain-specific quality config
        quality_config = get_quality_config(source_domain, ctx.database_name)

        # Score the source
        scorer = QualityScorer(quality_config)
        entity_chunk_mentions = build_entity_chunk_mentions(entities)

        source_id = source.get("id", "")
        assert isinstance(source_id, str), f"Expected str source_id, got {type(source_id)}"
        score = scorer.score_source(
            source_id=source_id,
            entities=entities,
            relationships=relationships,
            entity_chunk_mentions=entity_chunk_mentions,
        )

        results.append(
            {
                "source_id": source.get("id"),
                "title": source.get("title", "Untitled"),
                "domain": source_domain,
                "entity_count": score.entity_count,
                "relationship_count": score.relationship_count,
                "total_score": round(score.total_score, 2),
                "avg_entity_quality": round(score.avg_entity_quality, 2),
                "avg_relationship_quality": round(score.avg_relationship_quality, 2),
                "connectivity_ratio": round(score.connectivity_ratio, 3),
                "low_quality_entity_count": score.low_quality_entity_count,
            }
        )

        if score.entity_count > 0:
            total_entity_quality += score.avg_entity_quality
            count_with_entities += 1

        if score.relationship_count > 0:
            total_relationship_quality += score.avg_relationship_quality
            count_with_relationships += 1

    if not results:
        console.print("[yellow]No sources found matching criteria[/yellow]")
        return

    # Sort
    sort_key = {
        "score": "total_score",
        "entities": "entity_count",
        "quality": "avg_entity_quality",
    }[sort]
    results.sort(key=lambda x: x[sort_key], reverse=True)

    # Calculate averages
    total_score_sum = sum(r["total_score"] for r in results)
    avg_score = total_score_sum / len(results)
    avg_entity_quality = total_entity_quality / count_with_entities if count_with_entities else 0.0
    avg_relationship_quality = (
        total_relationship_quality / count_with_relationships if count_with_relationships else 0.0
    )

    # Output
    if output_json:
        output = {
            "sources": results[:limit],
            "total_sources": len(results),
            "avg_score": round(avg_score, 2),
            "avg_entity_quality": round(avg_entity_quality, 2),
            "avg_relationship_quality": round(avg_relationship_quality, 2),
        }
        console.print_json(json.dumps(output))
        return

    # Summary
    console.print("\n[bold]Quality Analysis Summary[/bold]")
    console.print(f"Total sources: {len(results)}")
    console.print(f"Average score: [green]{avg_score:.2f}[/green]")
    console.print(f"Average entity quality: {avg_entity_quality:.2f}")
    console.print(f"Average relationship quality: {avg_relationship_quality:.2f}")
    console.print()

    # Results table
    table = Table(title=f"Top {min(limit, len(results))} Sources by {sort.title()}")
    table.add_column("Title", style="cyan", max_width=30)
    table.add_column("Domain", style="yellow")
    table.add_column("Entities", justify="right")
    table.add_column("Rels", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Avg Qual", justify="right")
    table.add_column("Conn %", justify="right")

    for r in results[:limit]:
        score_color = (
            "green"
            if r["total_score"] >= 1000
            else "yellow"
            if r["total_score"] >= 500
            else "white"
        )
        qual_color = (
            "green"
            if r["avg_entity_quality"] >= 60
            else "yellow"
            if r["avg_entity_quality"] >= 40
            else "red"
        )

        low_qual_indicator = ""
        if r["low_quality_entity_count"] > 5:
            low_qual_indicator = " [red]*[/red]"

        table.add_row(
            r["title"][:30],
            r["domain"] or "-",
            str(r["entity_count"]) + low_qual_indicator,
            str(r["relationship_count"]),
            f"[{score_color}]{r['total_score']:.0f}[/{score_color}]",
            f"[{qual_color}]{r['avg_entity_quality']:.1f}[/{qual_color}]",
            f"{r['connectivity_ratio']:.0%}",
        )

    console.print(table)
    console.print("[dim]* indicates >5 low-quality entities[/dim]")
