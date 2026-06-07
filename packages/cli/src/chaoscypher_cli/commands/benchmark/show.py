# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher benchmark show` - re-render the leaderboard from a saved results JSON."""

from __future__ import annotations

from pathlib import Path

import click

from chaoscypher_cli.benchmark.leaderboard import render_leaderboard
from chaoscypher_cli.benchmark.results import load_results


@click.command()
@click.argument(
    "results_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--out",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=None,
    help="Write the rendered Markdown to this path instead of stdout.",
)
def show(results_path: Path, out: Path | None) -> None:
    """Render a saved benchmark results JSON as a Markdown leaderboard."""
    rows = load_results(results_path)
    md = render_leaderboard(rows)
    if out is not None:
        out.write_text(md)
    else:
        click.echo(md)


__all__ = ["show"]
