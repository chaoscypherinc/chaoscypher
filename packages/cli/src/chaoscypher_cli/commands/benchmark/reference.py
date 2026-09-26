# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher benchmark reference` - export and list grounded-chat reference packs.

A reference pack is the fixed graph a grounded-chat run retrieves from,
exported from a local run's graph cache so an MCP client's model (through
``start_benchmark`` with ``suite: chat``) answers from exactly the graph the
local chat models did. See :mod:`chaoscypher_core.benchmark.reference`.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table


if TYPE_CHECKING:
    from chaoscypher_core.benchmark.reference import ReferencePack


def _default_data_dir() -> Path:
    """The configured data directory (where the MCP server looks for packs)."""
    from chaoscypher_core.app_config import get_settings

    return Path(get_settings().paths.data_dir)


def _extractor_label(model_id: str) -> str:
    """The registry's display name for ``model_id``, or the id itself."""
    from chaoscypher_cli.benchmark.models_registry import load_registry

    entry = load_registry().get(model_id)
    return entry.label.replace(" (local)", "") if entry else model_id


async def _build_index(pack: ReferencePack) -> None:
    """Open the pack's indexed engine once, so the first MCP task does not have to."""
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    async with pack.indexed_engine(build_engine_settings(get_settings())):
        pass


@click.group("reference")
def reference_group() -> None:
    """Export and list the reference graphs grounded-chat MCP runs retrieve from."""


@reference_group.command("export")
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "Benchmark workspace holding graph_cache/ (default: the one `benchmark run` "
        "uses, <data_dir>/benchmark/workspace)."
    ),
)
@click.option(
    "--dataset", "dataset_id", required=True, help="Dataset id, e.g. war_and_peace_book1."
)
@click.option(
    "--extractor",
    required=True,
    help="provider/model that built the graph, e.g. ollama/gemma4:31b.",
)
@click.option(
    "--embedder",
    required=True,
    help="provider/model the local chat rows retrieved with, e.g. ollama/qwen3-embedding:0.6b.",
)
@click.option("--name", default=None, help="Pack name (default: the dataset id).")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Data directory to write the pack under (default: the configured one).",
)
@click.option("--force", is_flag=True, help="Replace an existing pack of the same name.")
@click.option(
    "--index/--no-index",
    default=True,
    show_default=True,
    help="Re-embed the pack with its embedder now (needs the embedder running).",
)
def export_cmd(
    workspace: Path | None,
    dataset_id: str,
    extractor: str,
    embedder: str,
    name: str | None,
    data_dir: Path | None,
    force: bool,
    index: bool,
) -> None:
    """Export a cached reference graph and its chat fixture as a reference pack."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle, user_benchmark_root
    from chaoscypher_cli.benchmark.graph_cache import GraphCache
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_core.benchmark.reference import (
        PACK_NAME_RE,
        ReferencePackError,
        load_pack,
        reference_root,
        split_model_id,
        write_manifest,
    )
    from chaoscypher_core.benchmark.snapshot import snapshot_sqlite

    console = Console()
    pack_name = name or dataset_id
    if not PACK_NAME_RE.match(pack_name):
        msg = f"{pack_name!r} is not a valid pack name"
        raise click.BadParameter(msg, param_hint="--name")
    try:
        ext_provider, ext_model = split_model_id(extractor)
        split_model_id(embedder)
    except ReferencePackError as exc:
        raise click.BadParameter(str(exc)) from exc
    try:
        bundle = load_dataset_bundle(dataset_id)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if bundle.queries_path is None:
        msg = f"dataset {dataset_id!r} has no chat fixture (queries_path)"
        raise click.ClickException(msg)

    workspace = workspace or (user_benchmark_root() / "workspace")
    cache = GraphCache(root=workspace / "graph_cache")
    model = ModelConfig(provider=ext_provider, model=ext_model, label=extractor)
    key = cache.key_for(corpus_id=bundle.id, corpus_version=bundle.version, extractor=model)
    snapshot = workspace / "graph_cache" / key / "app.db"
    if not snapshot.is_file():
        msg = (
            f"no cached graph for {dataset_id} {bundle.version} by {extractor} "
            f"(looked for {snapshot}); run the chat benchmark with that extractor first"
        )
        raise click.ClickException(msg)

    base = data_dir or _default_data_dir()
    root = reference_root(base) / pack_name
    if root.exists():
        if not force:
            msg = f"pack {pack_name!r} exists at {root}; pass --force"
            raise click.ClickException(msg)
        shutil.rmtree(root)
    root.mkdir(parents=True)
    try:
        snapshot_sqlite(snapshot, root / "app.db")
        shutil.copyfile(bundle.queries_path, root / "queries.yaml")
        write_manifest(
            root,
            {
                "name": pack_name,
                "fixture_id": bundle.id,
                "fixture_version": bundle.version,
                "corpus_id": bundle.id,
                "extractor": extractor,
                "extractor_label": _extractor_label(extractor),
                "embedder": embedder,
                "graph_cache_key": key,
                "created": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                "snapshot_file": "app.db",
                "queries_file": "queries.yaml",
            },
        )
        pack = load_pack(base, pack_name)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    console.print(f"[green]Wrote reference pack[/green] {pack_name} to {root}")
    if index:
        console.print(f"Indexing with {embedder} ...")
        asyncio.run(_build_index(pack))
        console.print("[green]Indexed.[/green]")
    else:
        console.print("Not indexed: the first grounded-chat task indexes it.")


@reference_group.command("list")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Data directory to look under (default: the configured one).",
)
def list_cmd(data_dir: Path | None) -> None:
    """List the reference packs a grounded-chat MCP run can use."""
    from chaoscypher_core.benchmark.reference import list_packs, reference_root

    console = Console()
    root = data_dir or _default_data_dir()
    packs = list_packs(root)
    if not packs:
        console.print(
            f"[yellow]No reference packs under {reference_root(root)}.[/yellow] "
            "Create one with `chaoscypher benchmark reference export`."
        )
        return
    table = Table(title=f"Reference packs ({reference_root(root)})")
    for column in ("Name", "Fixture", "Extractor", "Embedder", "Created", "Indexed"):
        table.add_column(column, overflow="fold")
    for pack in packs:
        table.add_row(
            pack.name,
            f"{pack.fixture_id} {pack.fixture_version}",
            pack.extractor,
            pack.embedder,
            pack.created,
            "yes" if pack.index_is_ready() else "no",
        )
    console.print(table)


__all__ = ["reference_group"]
