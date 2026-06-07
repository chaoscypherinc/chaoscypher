# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Click group for `chaoscypher benchmark` subcommands."""

from __future__ import annotations

import click

from chaoscypher_cli.commands.benchmark.fixture import fixture_group
from chaoscypher_cli.commands.benchmark.init import init_cmd
from chaoscypher_cli.commands.benchmark.list import list_cmd
from chaoscypher_cli.commands.benchmark.run import run
from chaoscypher_cli.commands.benchmark.show import show


@click.group("benchmark")
def benchmark() -> None:
    """Run and inspect the extraction benchmark."""


benchmark.add_command(run, name="run")
benchmark.add_command(list_cmd, name="list")
benchmark.add_command(show, name="show")
benchmark.add_command(init_cmd, name="init")
benchmark.add_command(fixture_group, name="fixture")


__all__ = ["benchmark"]
