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
  --seed N             - override seed (the extraction seed: it keys the graph cache slot)
  --chat-seed N        - override the chat and judge stages' seed only (the config's
                         ``chat_seed``): the graph, its slot and the pins stay the seed's
  --temperature F      - override temperature
  --keep-db            - preserve per-run temp DBs for inspection
  --out DIR            - override output directory
  --estimate           - print LLM-call estimate and exit without running
  --rebuild-graphs     - clear the benchmark graph cache before running
  --model-timeout S    - wall-clock cap per (model, dataset) run; over it = failed row
  --replay             - serve every model call from the LLM caches; a miss refuses the row
  --replay-pack NAME   - pin the extractor build that pack's manifest records (implies --replay)
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    from chaoscypher_cli.benchmark.results import BenchmarkResult


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
    help=(
        "Override the config's seed (the extraction seed: a new seed is a new graph cache "
        "slot). For another chat seed over the same graph, use --chat-seed."
    ),
)
@click.option(
    "--chat-seed",
    type=int,
    default=None,
    help=(
        "Override the chat and judge stages' seed only (the config's chat_seed); the graph, "
        "its cache slot and the pins keep the extraction seed."
    ),
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
@click.option(
    "--reuse-graph",
    is_flag=True,
    help="Skip re-extracting a reference graph the workspace cache already holds.",
)
@click.option(
    "--model-timeout",
    type=float,
    default=None,
    help=(
        "Wall-clock cap in seconds for one (model, dataset) run. A run over it is "
        "recorded as a failed row and the sweep continues."
    ),
)
@click.option(
    "--replay",
    is_flag=True,
    help=(
        "Serve every model call from the LLM caches (a reference pack's store, installed by "
        "install.py, or a recorded run): a call the caches cannot answer is refused "
        "(`replay miss`), never sent to a model."
    ),
)
@click.option(
    "--replay-pack",
    "replay_packs",
    multiple=True,
    metavar="NAME",
    help=(
        "Key the extractor's calls on the build this installed pack's manifest records "
        "(model_build), so the replay asks no server for a digest; implies --replay. Repeatable."
    ),
)
def run(
    name: str | None,
    dataset_id: str | None,
    local_only: bool,
    seed: int | None,
    chat_seed: int | None,
    temperature: float | None,
    keep_db: bool,
    out: Path | None,
    estimate: bool,
    rebuild_graphs: bool,
    reuse_graph: bool,
    model_timeout: float | None,
    replay: bool,
    replay_packs: tuple[str, ...],
) -> None:
    """Run a named benchmark config (default: extraction)."""
    console = Console()
    replay_context = _replay_context(console, replay=replay, pack_names=replay_packs)

    config_name = name or DEFAULT_CONFIG_NAME
    try:
        cfg = load_config(config_name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort from exc
    except (ValueError, TypeError) as exc:
        # a wrong shape (`extractors: {…}`) raises TypeError from the loader
        console.print(f"[red]Bad config:[/red] {exc}")
        raise click.Abort from exc

    # Apply CLI flag overrides on top of config values.
    effective_seed = seed if seed is not None else cfg.seed
    effective_temp = temperature if temperature is not None else cfg.temperature

    # Default output location: <data_dir>/benchmark/results/. Resolved before the
    # run so rows can be persisted as they land (a hung model used to take the
    # whole in-memory sweep with it).
    out_dir = out if out is not None else (user_benchmark_root() / "results")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve dataset bundles by id (built-in + user overlay, user wins).
    try:
        bundles = [load_dataset_bundle(did) for did in cfg.dataset_ids]
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort from exc
    except (ValueError, TypeError) as exc:
        # A malformed manifest, or a dataset kind the bundle loader does not
        # accept as a primary dataset (e.g. `probes`), used to escape as a
        # raw traceback.
        console.print(f"[red]Bad dataset:[/red] {exc}")
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

    is_full_mode = bool(cfg.embedders or cfg.chats)
    if chat_seed is not None and not cfg.chats:
        # nothing would decode with it: the row's seed would not be the flag's
        console.print(f"[red]--chat-seed:[/red] config '{config_name}' has no chat stage to seed")
        raise click.Abort

    from chaoscypher_cli.benchmark.models import assert_registry_coverage

    all_models = [
        *(cfg.extractors or []),
        *(cfg.embedders or []),
        *(cfg.chats or []),
        *([cfg.judge] if cfg.judge else []),
    ]
    # --local-only drops commercial models, so only require price entries for
    # the models that will actually run (local providers never need a price).
    models_to_check = filter_models(all_models, local_only=True) if local_only else all_models
    missing = assert_registry_coverage(models_to_check)
    if missing:
        console.print(
            "[red]Missing registry price entries (add to models_registry.yaml):[/red] "
            + ", ".join(missing)
        )
        raise click.Abort

    # --estimate: print stage-by-stage call count and exit. Runs AFTER the
    # registry-coverage gate so `--estimate` validates what the docs say it
    # validates (a model missing a price entry fails here, not mid-run).
    if estimate:
        _print_estimate(cfg, bundles)
        return

    if is_full_mode:
        from dataclasses import replace

        from chaoscypher_cli.benchmark.graph_cache import (
            GraphCacheMismatchError,
            GraphCacheMissError,
            dedup_embedder_for,
            default_workspace,
            resolution_for,
        )
        from chaoscypher_cli.benchmark.models import _LOCAL_PROVIDERS
        from chaoscypher_cli.benchmark.orchestrator import default_wiring, run_full_benchmark
        from chaoscypher_core.app_config import get_settings

        workspace = default_workspace()
        workspace.mkdir(parents=True, exist_ok=True)
        dedup_embedder = dedup_embedder_for(cfg, get_settings())
        console.print(f"Entity merges (semantic dedup) with: {dedup_embedder}")
        wiring = default_wiring(
            workspace=workspace,
            dedup_embedder=dedup_embedder,
            resolution=resolution_for(get_settings()),
        )
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
        else:
            filtered_cfg = cfg

        # Apply the --seed / --temperature CLI overrides in full mode too —
        # the orchestrator reads config.seed / config.temperature, so leaving
        # the raw config values here silently ignored both flags.
        filtered_cfg = replace(
            filtered_cfg,
            seed=effective_seed,
            temperature=effective_temp,
            chat_seed=chat_seed if chat_seed is not None else filtered_cfg.chat_seed,
        )

        landed_full: list[BenchmarkResult] = []
        partial_full = out_dir / "partial.json"

        def _persist_full(row: BenchmarkResult) -> None:
            landed_full.append(row)
            dump_results(landed_full, partial_full)

        try:
            with replay_context:
                rows = asyncio.run(
                    run_full_benchmark(
                        filtered_cfg,
                        bundles,
                        wiring=wiring,
                        model_timeout=model_timeout,
                        on_row=_persist_full,
                        reuse_cached_graph=reuse_graph,
                    )
                )
        except (GraphCacheMissError, GraphCacheMismatchError) as exc:
            console.print(f"[red]Graph cache:[/red] {exc}")
            raise click.Abort from exc
        partial_full.unlink(missing_ok=True)
    else:
        # Extractors-only path: existing run_benchmark + per-bundle ExtractionDataset.
        if reuse_graph or rebuild_graphs:
            # Neither flag is read on this path (the graph cache serves the
            # embedding and chat stages); refusing beats a silent no-op.
            console.print(
                f"[red]--reuse-graph / --rebuild-graphs apply to a full config[/red] "
                f"(embedders or chats); '{config_name}' runs extractors only."
            )
            raise click.Abort
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

        landed: list[BenchmarkResult] = []
        partial_path = out_dir / "partial.json"

        def _persist(row: BenchmarkResult) -> None:
            landed.append(row)
            dump_results(landed, partial_path)

        with replay_context:
            rows = asyncio.run(
                run_benchmark(
                    models=models,
                    datasets=datasets,
                    config_name=config_name,
                    seed=effective_seed,
                    temperature=effective_temp,
                    model_timeout=model_timeout,
                    on_row=_persist,
                )
            )
        partial_path.unlink(missing_ok=True)

    # Second resolution: two runs finishing in the same minute (a `quick`
    # smoke straight after an `--estimate`d re-run) overwrote each other.
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H%M%SZ")
    json_path = out_dir / f"{timestamp}.json"
    md_path = out_dir / f"{timestamp}.md"
    dump_results(rows, json_path)
    md_text = render_leaderboard(
        rows,
        default_embedder=cfg.default_embedder,
        default_chat=cfg.default_chat,
        weights=cfg.weights,
    )
    md_path.write_text(md_text, encoding="utf-8")
    (out_dir / "latest.md").write_text(md_text, encoding="utf-8")

    console.print(f"[green]Wrote[/green] {json_path}")
    console.print(f"[green]Wrote[/green] {md_path}")
    console.print(f"[green]Updated[/green] {out_dir / 'latest.md'}")


def _replay_context(
    console: Console, *, replay: bool, pack_names: tuple[str, ...]
) -> contextlib.AbstractContextManager[Any]:
    """The context the run executes in: :func:`~chaoscypher_cli.benchmark.orchestrator.replaying` under ``--replay``.

    ``--replay-pack`` names installed packs (``<data_dir>/benchmark/reference``)
    whose manifest ``model_build`` pins the extractor's build. Checked before
    anything runs.

    Raises:
        click.Abort: When a named pack is missing, ships no LLM store, two packs
            disagree on one extractor's build, or a cache is off.
    """
    if not (replay or pack_names):
        return contextlib.nullcontext()
    from chaoscypher_cli.benchmark.orchestrator import (
        check_replayable,
        replay_builds,
        replaying,
    )
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.benchmark.reference import ReferencePackError, load_pack
    from chaoscypher_core.exceptions import ValidationError

    settings = get_settings()
    try:
        packs = [load_pack(settings.paths.data_dir, name) for name in pack_names]
        builds = replay_builds(packs)
        check_replayable(settings)
    except (ReferencePackError, ValidationError) as exc:
        console.print(f"[red]--replay:[/red] {exc}")
        raise click.Abort from exc
    for (provider, model), build in builds.items():
        console.print(f"Replaying {provider}/{model} as {build}")
    return replaying(settings, builds)


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
