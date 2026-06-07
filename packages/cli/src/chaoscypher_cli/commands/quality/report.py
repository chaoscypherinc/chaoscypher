# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Report command - Export quality report in various formats."""

import contextlib
import csv
import json
import sys
from typing import Any

import click
from rich.console import Console

from chaoscypher_cli.context import pass_context


console = Console()


@click.command()
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json", "csv"]),
    default="table",
    help="Output format",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file (default: stdout)",
)
@click.option(
    "--domain",
    "-d",
    help="Filter by extraction domain",
)
@click.option(
    "--include-domains",
    is_flag=True,
    help="Include domain comparison in report",
)
@pass_context
def report(
    ctx: Any,
    output_format: str,
    output: str | None,
    domain: str | None,
    include_domains: bool,
) -> None:
    """Export a quality report in various formats.

    Generates a comprehensive quality report including:
    - Summary statistics
    - Per-source quality scores
    - Domain comparison (optional)

    Examples:
        chaoscypher quality report
        chaoscypher quality report --format json -o quality.json
        chaoscypher quality report --format csv -o quality.csv
        chaoscypher quality report --include-domains
    """
    from chaoscypher_cli.commands.quality.utils import (
        build_entity_chunk_mentions,
        get_quality_config,
    )
    from chaoscypher_core.services.quality import QualityScorer

    adapter = ctx.storage_adapter
    database_name = ctx.database_name

    try:
        # Get all sources (list_files excludes extraction_results for performance)
        source_list = adapter.list_files(database_name)

        # Score all sources
        results = []
        domain_metrics: dict[str, dict] = {}

        for source_summary in source_list:
            source_id = source_summary.get("id")
            source_domain = source_summary.get("extraction_domain")
            if domain and source_domain != domain:
                continue

            # Fetch full source data to get extraction_results
            source = adapter.get_file(source_id, database_name)
            if not source:
                continue

            extraction_results = source.get("extraction_results") or {}
            entities = extraction_results.get("entities", [])
            relationships = extraction_results.get("relationships", [])

            if not entities and not relationships:
                continue

            # Get domain-specific quality config
            quality_config = get_quality_config(source_domain, database_name)

            # Score
            scorer = QualityScorer(quality_config)
            entity_chunk_mentions = build_entity_chunk_mentions(entities)

            score = scorer.score_source(
                source_id=source.get("id"),
                entities=entities,
                relationships=relationships,
                entity_chunk_mentions=entity_chunk_mentions,
            )

            result = {
                "source_id": source.get("id"),
                "title": source.get("title", "Untitled"),
                "domain": source_domain or "unknown",
                "quality_grade": round(score.quality_grade, 1),
                "quality_label": score.quality_label,
                "entity_count": score.entity_count,
                "relationship_count": score.relationship_count,
                "total_score": round(score.total_score, 2),
                "entity_contribution": round(score.entity_contribution, 2),
                "relationship_contribution": round(score.relationship_contribution, 2),
                "connectivity_bonus": round(score.connectivity_bonus, 2),
                "avg_entity_quality": round(score.avg_entity_quality, 2),
                "avg_relationship_quality": round(score.avg_relationship_quality, 2),
                "connectivity_ratio": round(score.connectivity_ratio, 3),
                "low_quality_entity_count": score.low_quality_entity_count,
                "low_quality_relationship_count": score.low_quality_relationship_count,
            }
            results.append(result)

            # Aggregate by domain
            d = source_domain or "unknown"
            if d not in domain_metrics:
                domain_metrics[d] = {
                    "source_count": 0,
                    "total_score": 0.0,
                    "total_grade": 0.0,
                    "total_entities": 0,
                    "total_relationships": 0,
                    "total_entity_quality": 0.0,
                    "total_relationship_quality": 0.0,
                    "sources_with_relationships": 0,
                }
            domain_metrics[d]["source_count"] += 1
            domain_metrics[d]["total_score"] += score.total_score
            domain_metrics[d]["total_grade"] += score.quality_grade
            domain_metrics[d]["total_entities"] += score.entity_count
            domain_metrics[d]["total_relationships"] += score.relationship_count
            domain_metrics[d]["total_entity_quality"] += score.avg_entity_quality
            if score.relationship_count > 0:
                domain_metrics[d]["total_relationship_quality"] += score.avg_relationship_quality
                domain_metrics[d]["sources_with_relationships"] += 1

        if not results:
            console.print("[yellow]No sources found[/yellow]")
            return

        # Sort by quality grade (normalized 0-100 score)
        results.sort(key=lambda x: x["quality_grade"], reverse=True)

        # Calculate summary
        total_sources = len(results)
        total_entities = sum(r["entity_count"] for r in results)
        total_relationships = sum(r["relationship_count"] for r in results)
        avg_grade = sum(r["quality_grade"] for r in results) / total_sources
        avg_entity_quality = (
            sum(r["avg_entity_quality"] for r in results if r["entity_count"] > 0)
            / sum(1 for r in results if r["entity_count"] > 0)
            if any(r["entity_count"] > 0 for r in results)
            else 0.0
        )

        # Domain comparison
        domain_comparison = []
        for d, m in domain_metrics.items():
            count = m["source_count"]
            rel_count = m["sources_with_relationships"]
            domain_comparison.append(
                {
                    "domain": d,
                    "source_count": count,
                    "total_entities": m["total_entities"],
                    "total_relationships": m["total_relationships"],
                    "avg_grade": round(m["total_grade"] / count, 1) if count else 0.0,
                    "avg_entity_quality": round(m["total_entity_quality"] / count, 2)
                    if count
                    else 0.0,
                    "avg_relationship_quality": (
                        round(m["total_relationship_quality"] / rel_count, 2) if rel_count else 0.0
                    ),
                }
            )
        domain_comparison.sort(key=lambda x: x["avg_grade"], reverse=True)

        # Build report
        report_data = {
            "summary": {
                "total_sources": total_sources,
                "total_entities": total_entities,
                "total_relationships": total_relationships,
                "avg_grade": round(avg_grade, 1),
                "avg_entity_quality": round(avg_entity_quality, 2),
            },
            "sources": results,
        }
        if include_domains:
            report_data["domains"] = domain_comparison

        # Output - use context manager for file, nullcontext for stdout
        with open(output, "w") if output else contextlib.nullcontext(sys.stdout) as out_file:
            if output_format == "json":
                json.dump(report_data, out_file, indent=2)
                if output:
                    console.print(f"[green]Report written to {output}[/green]")

            elif output_format == "csv":
                writer = csv.DictWriter(out_file, fieldnames=results[0].keys())
                writer.writeheader()
                writer.writerows(results)
                if output:
                    console.print(f"[green]Report written to {output}[/green]")

            else:  # table
                from rich.table import Table

                # Summary
                console.print("\n[bold]Quality Report Summary[/bold]")
                console.print(f"Total sources: {total_sources}")
                console.print(f"Total entities: {total_entities}")
                console.print(f"Total relationships: {total_relationships}")
                console.print(f"Average grade: [green]{avg_grade:.1f}/100[/green]")
                console.print(f"Average entity quality: {avg_entity_quality:.2f}")
                console.print()

                # Sources table
                table = Table(title="Sources by Quality Grade")
                table.add_column("Title", style="cyan", max_width=30)
                table.add_column("Domain", style="yellow")
                table.add_column("Grade", justify="right")
                table.add_column("Label", justify="left")
                table.add_column("Entities", justify="right")
                table.add_column("Rels", justify="right")

                for r in results[:20]:
                    grade = r["quality_grade"]
                    grade_color = "green" if grade >= 70 else "yellow" if grade >= 50 else "red"
                    table.add_row(
                        r["title"][:30],
                        r["domain"],
                        f"[{grade_color}]{grade:.0f}[/{grade_color}]",
                        r["quality_label"],
                        str(r["entity_count"]),
                        str(r["relationship_count"]),
                    )

                console.print(table)

                if include_domains and domain_comparison:
                    console.print()
                    domain_table = Table(title="Domain Comparison")
                    domain_table.add_column("Domain", style="cyan")
                    domain_table.add_column("Sources", justify="right")
                    domain_table.add_column("Avg Grade", justify="right")
                    domain_table.add_column("Entities", justify="right")
                    domain_table.add_column("Avg Quality", justify="right")

                    for d in domain_comparison:
                        grade = d["avg_grade"]
                        grade_color = "green" if grade >= 70 else "yellow" if grade >= 50 else "red"
                        domain_table.add_row(
                            d["domain"],
                            str(d["source_count"]),
                            f"[{grade_color}]{grade:.0f}[/{grade_color}]",
                            str(d["total_entities"]),
                            f"{d['avg_entity_quality']:.1f}",
                        )
                    console.print(domain_table)

    finally:
        pass  # Context manages adapter lifecycle
