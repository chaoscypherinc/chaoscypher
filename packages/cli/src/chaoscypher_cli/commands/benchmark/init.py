# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher benchmark init NAME` - scaffold a user benchmark config.

Drops a starter yaml file at ``<data_dir>/benchmark/config/<NAME>.yaml`` so
the user can edit it locally. After init, ``chaoscypher benchmark run NAME``
will load the user config (which overrides any built-in with the same
name).
"""

from __future__ import annotations

import click
from rich.console import Console

from chaoscypher_cli.benchmark.config import user_config_root
from chaoscypher_cli.benchmark.discovery import user_benchmark_root


_TEMPLATE = """# {name} - user benchmark config
#
# Run with: chaoscypher benchmark run {name}
# Datasets are referenced by id; see `chaoscypher benchmark list` for the
# available built-in datasets, or add your own under
# <data_dir>/benchmark/datasets/<id>/.

name: "{name}"
description: "User-defined benchmark config."

seed: 42
temperature: 0.0

datasets:
  - war_and_peace_tiny

extractors:
  - provider: ollama
    model: llama3.1:8b
    label: "Llama 3.1 8B (local)"
"""


@click.command("init")
@click.argument("name")
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing user config with the same name.",
)
def init_cmd(name: str, force: bool) -> None:
    """Scaffold a user benchmark config under <data_dir>/benchmark/config/."""
    console = Console()
    config_dir = user_config_root()
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / f"{name}.yaml"

    if target.exists() and not force:
        console.print(f"[red]Config already exists:[/red] {target}\nUse --force to overwrite.")
        raise click.Abort

    target.write_text(_TEMPLATE.format(name=name), encoding="utf-8")

    # Also ensure datasets/ + results/ subdirs exist so the user has a
    # complete tree to drop datasets and accept run output.
    (user_benchmark_root() / "datasets").mkdir(parents=True, exist_ok=True)
    (user_benchmark_root() / "results").mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Wrote[/green] {target}")
    console.print(f"[dim]Edit it, then run:[/dim] chaoscypher benchmark run {name}")


__all__ = ["init_cmd"]
