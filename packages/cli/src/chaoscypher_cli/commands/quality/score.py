# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Score command - Score a single source's extraction quality."""

import json
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chaoscypher_cli.context import pass_context


console = Console()


@click.command()
@click.argument("source_id")
@click.option(
    "--details",
    "-d",
    is_flag=True,
    help="Show individual entity and relationship breakdowns",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
@pass_context
def score(ctx: Any, source_id: str, details: bool, output_json: bool) -> None:
    """Score a single source's extraction quality.

    SOURCE_ID is the ID of the source to score.

    The quality score (v7) evaluates:
    - Relationship quality (50% weight) - justification, confidence, specificity
    - Entity quality (35% weight) - description, confidence, properties, aliases
    - Topology score (15% weight) - connectivity + density (bell-shaped around target)
    - Pollution penalty (deduction for low-quality items, 0-15)
    - Structural penalty (deduction for hub-skew + reciprocal-rate noise, 0-15)

    Formula: (R * 0.5) + (E * 0.35) + (T * 0.15) - Pollution - Structural = Grade

    Examples:
        chaoscypher source quality score if_abc123
        chaoscypher source quality score if_abc123 --details
        chaoscypher source quality score if_abc123 --json
    """
    from chaoscypher_cli.commands.quality.utils import (
        build_entity_chunk_mentions,
        get_quality_config,
    )
    from chaoscypher_core.services.quality import QualityScorer

    adapter = ctx.storage_adapter
    database_name = ctx.database_name

    try:
        # Get source
        source = adapter.get_file(source_id, database_name)
        if not source:
            console.print(f"[red]Source {source_id} not found[/red]")
            raise click.Abort

        extraction_results = source.get("extraction_results") or {}
        entities = extraction_results.get("entities", [])
        relationships = extraction_results.get("relationships", [])

        if not entities and not relationships:
            console.print(f"[yellow]Source {source_id} has no extraction data[/yellow]")
            raise click.Abort

        # Get domain-specific quality config
        domain = source.get("extraction_domain")
        quality_config = get_quality_config(domain, database_name)

        # Score the source
        scorer = QualityScorer(quality_config)

        # Build entity chunk mentions
        entity_chunk_mentions = build_entity_chunk_mentions(entities)

        result = scorer.score_source(
            source_id=source_id,
            entities=entities,
            relationships=relationships,
            entity_chunk_mentions=entity_chunk_mentions,
        )

        # Output
        if output_json:
            output = {
                "source_id": source_id,
                "source_title": source.get("title"),
                "domain": domain,
                "entity_count": result.entity_count,
                "relationship_count": result.relationship_count,
                "entity_contribution": round(result.entity_contribution, 2),
                "relationship_contribution": round(result.relationship_contribution, 2),
                "connectivity_bonus": round(result.connectivity_bonus, 2),
                "total_score": round(result.total_score, 2),
                "avg_entity_quality": round(result.avg_entity_quality, 2),
                "avg_relationship_quality": round(result.avg_relationship_quality, 2),
                "connectivity_ratio": round(result.connectivity_ratio, 3),
                "low_quality_entity_count": result.low_quality_entity_count,
                "low_quality_relationship_count": result.low_quality_relationship_count,
                # v7 scoring metrics
                "quality_grade": round(result.quality_grade, 2),
                "quality_label": result.quality_label,
                "density_ratio": round(result.density_ratio, 3),
                "density_score": round(result.density_score, 2),
                "topology_score": round(result.topology_score, 2),
                "pollution_penalty": round(result.pollution_penalty, 2),
                "structural_penalty": round(result.structural_penalty, 2),
                "hub_skew": round(result.hub_skew, 3),
                "reciprocal_rate": round(result.reciprocal_rate, 3),
            }
            if details:
                output["entity_scores"] = [
                    {
                        "entity_name": s.entity_name,
                        "entity_type": s.entity_type,
                        "total_score": round(s.total_score, 2),
                    }
                    for s in result.entity_scores
                ]
                output["relationship_scores"] = [
                    {
                        "relationship_type": s.relationship_type,
                        "source_entity": s.source_entity,
                        "target_entity": s.target_entity,
                        "total_score": round(s.total_score, 2),
                    }
                    for s in result.relationship_scores
                ]
            console.print_json(json.dumps(output))
            return

        # Rich output
        title = source.get("title", source_id)
        console.print(Panel(f"[bold]{title}[/bold]\n{domain or 'unknown'} domain", title="Source"))

        # Quality Grade summary
        grade_color = (
            "green"
            if result.quality_grade >= 70
            else "yellow"
            if result.quality_grade >= 50
            else "red"
        )
        console.print(
            Panel(
                f"[bold {grade_color}]{result.quality_grade:.0f}/100 {result.quality_label}[/bold {grade_color}]",
                title="Quality Grade (v7)",
            )
        )

        # v7 Scoring breakdown table
        scoring_table = Table(
            title="Grade Calculation: (R*0.5) + (E*0.35) + (T*0.15) - Pollution - Structural"
        )
        scoring_table.add_column("Component", style="cyan")
        scoring_table.add_column("Score", justify="right")
        scoring_table.add_column("Weight", justify="right")
        scoring_table.add_column("Contribution", justify="right")

        r_contrib = result.avg_relationship_quality * 0.5
        e_contrib = result.avg_entity_quality * 0.35
        t_contrib = result.topology_score * 0.15

        scoring_table.add_row(
            "Relationship Quality (R)",
            f"{result.avg_relationship_quality:.1f}",
            "50%",
            f"[bold]{r_contrib:.1f}[/bold]",
        )
        scoring_table.add_row(
            "Entity Quality (E)",
            f"{result.avg_entity_quality:.1f}",
            "35%",
            f"[bold]{e_contrib:.1f}[/bold]",
        )
        scoring_table.add_row(
            "Topology Score (T)",
            f"{result.topology_score:.1f}",
            "15%",
            f"[bold]{t_contrib:.1f}[/bold]",
        )
        if result.pollution_penalty > 0:
            scoring_table.add_row(
                "Pollution Penalty",
                f"-{result.pollution_penalty:.0f}",
                "",
                f"[red]-{result.pollution_penalty:.0f}[/red]",
            )
        if result.structural_penalty > 0:
            detail = f"skew {result.hub_skew:.1f}x, recip {result.reciprocal_rate:.0%}"
            scoring_table.add_row(
                f"Structural Penalty ({detail})",
                f"-{result.structural_penalty:.0f}",
                "",
                f"[red]-{result.structural_penalty:.0f}[/red]",
            )
        scoring_table.add_row(
            "[bold]Final Grade[/bold]",
            "",
            "",
            f"[bold {grade_color}]{result.quality_grade:.0f}[/bold {grade_color}]",
        )

        console.print(scoring_table)

        # Topology breakdown
        topology_table = Table(title="Topology Score Breakdown")
        topology_table.add_column("Metric", style="cyan")
        topology_table.add_column("Value", justify="right")

        topology_table.add_row(
            "Connectivity",
            f"{result.connectivity_ratio:.1%} of entities connected",
        )
        topology_table.add_row(
            "Density Ratio",
            f"{result.density_ratio:.2f} edges/node (target: 2.5)",
        )
        topology_table.add_row(
            "Density Score",
            f"{result.density_score:.1f}/100",
        )
        topology_table.add_row(
            "[bold]Topology Score[/bold]",
            f"[bold]{result.topology_score:.1f}/100[/bold]",
        )

        console.print(topology_table)

        # Richness metrics table
        richness_table = Table(title="Richness Score (Volume Metric)")
        richness_table.add_column("Metric", style="cyan")
        richness_table.add_column("Value", justify="right")

        richness_table.add_row("Total Score", f"[bold green]{result.total_score:.2f}[/bold green]")
        richness_table.add_row("Entity Count", str(result.entity_count))
        richness_table.add_row("Relationship Count", str(result.relationship_count))
        richness_table.add_row("Entity Contribution", f"{result.entity_contribution:.2f}")
        richness_table.add_row(
            "Relationship Contribution", f"{result.relationship_contribution:.2f}"
        )
        richness_table.add_row("Connectivity Bonus", f"{result.connectivity_bonus:.2f}")

        if result.low_quality_entity_count > 0:
            richness_table.add_row(
                "Low Quality Entities",
                f"[yellow]{result.low_quality_entity_count}[/yellow]",
            )
        if result.low_quality_relationship_count > 0:
            richness_table.add_row(
                "Low Quality Relationships",
                f"[yellow]{result.low_quality_relationship_count}[/yellow]",
            )

        console.print(richness_table)

        if details:
            # Entity details
            if result.entity_scores:
                entity_table = Table(title="Entity Scores (Top 10)")
                entity_table.add_column("Name", style="cyan", max_width=30)
                entity_table.add_column("Type", style="yellow")
                entity_table.add_column("Score", justify="right")

                sorted_entities = sorted(
                    result.entity_scores, key=lambda x: x.total_score, reverse=True
                )[:10]
                for s in sorted_entities:
                    color = (
                        "green"
                        if s.total_score >= 60
                        else "yellow"
                        if s.total_score >= 40
                        else "red"
                    )
                    entity_table.add_row(
                        s.entity_name[:30],
                        s.entity_type,
                        f"[{color}]{s.total_score:.1f}[/{color}]",
                    )
                console.print(entity_table)

            # Relationship details
            if result.relationship_scores:
                rel_table = Table(title="Relationship Scores (Top 10)")
                rel_table.add_column("Type", style="cyan")
                rel_table.add_column("From", max_width=20)
                rel_table.add_column("To", max_width=20)
                rel_table.add_column("Score", justify="right")

                sorted_rels = sorted(
                    result.relationship_scores, key=lambda x: x.total_score, reverse=True
                )[:10]
                for rs in sorted_rels:
                    color = (
                        "green"
                        if rs.total_score >= 60
                        else "yellow"
                        if rs.total_score >= 40
                        else "red"
                    )
                    rel_table.add_row(
                        rs.relationship_type,
                        rs.source_entity[:20],
                        rs.target_entity[:20],
                        f"[{color}]{rs.total_score:.1f}[/{color}]",
                    )
                console.print(rel_table)

    finally:
        pass  # Context manages adapter lifecycle
