# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher benchmark run [NAME]` - execute a named benchmark config.

Loads the config (built-in or user overlay), resolves dataset bundles by id,
applies any CLI-flag overrides, and dispatches to either the full three-stage
orchestrator (when embedders or chats are configured) or the legacy
extractors-only runner.

The flags are pure overrides of values in the config:
  --dataset ID         - filter to one dataset within the config
  --local-only         - drop commercial-provider models
  --seed N             - override seed
  --temperature F      - override temperature
  --keep-db            - preserve per-run temp DBs for inspection
  --out DIR            - override output directory
  --estimate           - print LLM-call estimate and exit without running
  --rebuild-graphs     - clear the benchmark graph cache before running
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console

from chaoscypher_cli.benchmark.config import DEFAULT_CONFIG_NAME, load_config
from chaoscypher_cli.benchmark.discovery import (
    load_dataset_bundle,
    user_benchmark_root,
)
from chaoscypher_cli.benchmark.leaderboard import render_leaderboard
from chaoscypher_cli.benchmark.models import filter_models
from chaoscypher_cli.benchmark.results import dump_results
from chaoscypher_cli.benchmark.runner import run_benchmark


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.config import BenchmarkConfig
    from chaoscypher_cli.benchmark.discovery import DatasetBundle


@click.command()
@click.argument("name", required=False, default=None)
@click.option(
    "--dataset",
    "dataset_id",
    default=None,
    help="Run only this dataset id (must be in the config's `datasets` list).",
)
@click.option(
    "--local-only",
    is_flag=True,
    help="Drop commercial-provider models so the run is free / API-key-free.",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Override the config's seed.",
)
@click.option(
    "--temperature",
    type=float,
    default=None,
    help="Override the config's temperature.",
)
@click.option(
    "--keep-db",
    is_flag=True,
    help="Preserve per-run temp databases for post-hoc inspection.",
)
@click.option(
    "--out",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "Output directory for the JSON and rendered Markdown files. "
        "Defaults to <data_dir>/benchmark/results/."
    ),
)
@click.option(
    "--estimate",
    is_flag=True,
    help="Print LLM-call estimate and exit without running.",
)
@click.option(
    "--rebuild-graphs",
    is_flag=True,
    help="Clear the benchmark graph cache before running.",
)
def run(
    name: str | None,
    dataset_id: str | None,
    local_only: bool,
    seed: int | None,
    temperature: float | None,
    keep_db: bool,
    out: Path | None,
    estimate: bool,
    rebuild_graphs: bool,
) -> None:
    """Run a named benchmark config (default: extraction)."""
    console = Console()

    config_name = name or DEFAULT_CONFIG_NAME
    try:
        cfg = load_config(config_name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort from exc
    except ValueError as exc:
        console.print(f"[red]Bad config:[/red] {exc}")
        raise click.Abort from exc

    # Apply CLI flag overrides on top of config values.
    effective_seed = seed if seed is not None else cfg.seed
    effective_temp = temperature if temperature is not None else cfg.temperature

    # Resolve dataset bundles by id (built-in + user overlay, user wins).
    try:
        bundles = [load_dataset_bundle(did) for did in cfg.dataset_ids]
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort from exc

    # Apply --dataset filter if requested.
    if dataset_id is not None:
        if dataset_id not in cfg.dataset_ids:
            console.print(
                f"[red]Dataset[/red] '{dataset_id}' "
                f"is not in config '{config_name}'. "
                f"Available: {', '.join(cfg.dataset_ids)}"
            )
            raise click.Abort
        bundles = [b for b in bundles if b.id == dataset_id]

    # --estimate: print stage-by-stage call count and exit.
    if estimate:
        _print_estimate(cfg, bundles)
        return

    is_full_mode = bool(cfg.embedders or cfg.chats)

    if is_full_mode:
        from dataclasses import replace

        from chaoscypher_cli.benchmark.models import _LOCAL_PROVIDERS
        from chaoscypher_cli.benchmark.orchestrator import default_wiring, run_full_benchmark

        workspace = user_benchmark_root() / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        wiring = default_wiring(workspace=workspace)
        if rebuild_graphs:
            wiring.cache.clear()

        if local_only:
            filtered_judge = (
                cfg.judge
                if (cfg.judge is not None and cfg.judge.provider in _LOCAL_PROVIDERS)
                else None
            )
            filtered_cfg = replace(
                cfg,
                extractors=filter_models(cfg.extractors or [], local_only=True) or None,
                embedders=filter_models(cfg.embedders or [], local_only=True) or None,
                chats=filter_models(cfg.chats or [], local_only=True) or None,
                judge=filtered_judge,
            )
            if filtered_cfg.chats and filtered_cfg.judge is None:
                console.print(
                    "[red]--local-only stripped the (commercial) judge; configure a local "
                    "judge or drop the chats role list.[/red]"
                )
                raise click.Abort
        else:
            filtered_cfg = cfg

        rows = asyncio.run(run_full_benchmark(filtered_cfg, bundles, wiring=wiring))
    else:
        # Extractors-only path: existing run_benchmark + per-bundle ExtractionDataset.
        models = filter_models(cfg.extractors or [], local_only=local_only)
        if not models:
            console.print(
                "[red]No models to run[/red] "
                f"(config '{config_name}' has {len(cfg.extractors or [])} extractors; "
                "all stripped by --local-only)."
            )
            raise click.Abort

        datasets = [b.extraction_dataset for b in bundles]
        if keep_db:
            for ds in datasets:
                ds.keep_db = True

        console.print(
            f"[cyan]Running[/cyan] config '{config_name}': "
            f"{len(models)} models x {len(datasets)} datasets "
            f"= {len(models) * len(datasets)} runs"
        )

        rows = asyncio.run(
            run_benchmark(
                models=models,
                datasets=datasets,
                config_name=config_name,
                seed=effective_seed,
                temperature=effective_temp,
            )
        )

    # Default output location: <data_dir>/benchmark/results/.
    out_dir = out if out is not None else (user_benchmark_root() / "results")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H%MZ")
    json_path = out_dir / f"{timestamp}.json"
    md_path = out_dir / f"{timestamp}.md"
    dump_results(rows, json_path)
    md_text = render_leaderboard(rows)
    md_path.write_text(md_text, encoding="utf-8")
    (out_dir / "latest.md").write_text(md_text, encoding="utf-8")

    console.print(f"[green]Wrote[/green] {json_path}")
    console.print(f"[green]Wrote[/green] {md_path}")
    console.print(f"[green]Updated[/green] {out_dir / 'latest.md'}")


def _print_estimate(cfg: BenchmarkConfig, bundles: list[DatasetBundle]) -> None:
    """Print a stage-by-stage LLM call count estimate and return."""
    n_ext = len(cfg.extractors or [])
    n_emb = len(cfg.embedders or [])
    n_chat = len(cfg.chats or [])
    total_queries = sum(len(b.queries.queries) if b.queries else 0 for b in bundles)
    queries_in_scope = sum(
        sum(1 for q in b.queries.queries if q.band != "out_of_scope") for b in bundles if b.queries
    )

    click.echo(f"Datasets: {len(bundles)}")
    click.echo(f"Stage 1 (extraction): {n_ext * len(bundles)} extraction runs")
    if n_emb:
        click.echo(
            f"Stage 2 (embedding): {n_ext * n_emb} (extractor, embedder) pairs "
            f"x ~150 entity embeds + {queries_in_scope} query embeds"
        )
    if n_chat:
        chat_calls = n_ext * n_emb * n_chat * total_queries
        click.echo(
            f"Stage 3 (chat): {n_ext * n_emb * n_chat} triples x {total_queries} queries "
            f"= {chat_calls} chat completions + {chat_calls} judge calls"
        )
    click.echo("")
    click.echo("Cost estimate is approximate; commercial models contribute.")


__all__ = ["run"]
