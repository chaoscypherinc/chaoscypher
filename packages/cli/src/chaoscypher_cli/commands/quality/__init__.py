# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality Commands - ChaosCypher CLI.

Commands for evaluating extraction quality:
- score: Score a single source
- analyze: Batch analysis with filters
- report: Export quality report
- recalculate: Batch recalculate and cache scores

Example:
    chaoscypher source quality score <source_id>
    chaoscypher source quality analyze --domain literary
    chaoscypher source quality report --format json
    chaoscypher source quality recalculate
"""

import click

from chaoscypher_cli.commands.quality.analyze import analyze
from chaoscypher_cli.commands.quality.recalculate import recalculate
from chaoscypher_cli.commands.quality.report import report
from chaoscypher_cli.commands.quality.score import score


@click.group()
def quality() -> None:
    """Evaluate extraction quality across sources.

    Analyze and compare extraction quality using domain-specific
    scoring that evaluates entities, relationships, and connectivity.
    """


quality.add_command(score)
quality.add_command(analyze)
quality.add_command(report)
quality.add_command(recalculate)

__all__ = ["quality"]
