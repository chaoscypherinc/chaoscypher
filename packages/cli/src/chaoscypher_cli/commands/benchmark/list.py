# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher benchmark list` - show available configs and datasets.

Lists named benchmark configs first (these are what ``bench run`` accepts
as positional arguments), then datasets (what configs reference by id).
Both sections show provenance: built-in (ships in the pip package) vs
user (lives under ``<data_dir>/benchmark/``).
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.benchmark.config import list_configs
from chaoscypher_cli.benchmark.discovery import discover_datasets
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset


@click.command("list")
def list_cmd() -> None:
    """List available benchmark configs and datasets."""
    console = Console()

    # Configs first - they're what `bench run` accepts as a positional arg.
    configs = list_configs()
    if configs:
        config_table = Table(title="Benchmark configs (`bench run [NAME]`)")
        config_table.add_column("Name", style="cyan")
        config_table.add_column("Source")
        config_table.add_column("Description")
        for name, src, desc in configs:
            src_label = "[green]builtin[/green]" if src == "builtin" else "[yellow]user[/yellow]"
            config_table.add_row(name, src_label, desc or "-")
        console.print(config_table)
        console.print()
    else:
        console.print("[yellow]No benchmark configs found.[/yellow]")

    # Datasets - referenced by configs by id.
    datasets = discover_datasets()
    if datasets:
        ds_table = Table(title="Datasets (referenced by configs)")
        ds_table.add_column("ID", style="cyan")
        ds_table.add_column("Source")
        ds_table.add_column("Kind")
        ds_table.add_column("Version")
        ds_table.add_column("Domain")
        ds_table.add_column("Corpus")
        for ds in datasets:
            src_label = (
                "[green]builtin[/green]" if ds.source == "builtin" else "[yellow]user[/yellow]"
            )
            domain = ds.domain if isinstance(ds, ExtractionDataset) else "-"
            corpus = ds.corpus_path.name if isinstance(ds, ExtractionDataset) else "-"
            ds_table.add_row(ds.id, src_label, ds.kind, ds.version, domain, corpus)
        console.print(ds_table)
    else:
        console.print("[yellow]No datasets found.[/yellow]")


__all__ = ["list_cmd"]
