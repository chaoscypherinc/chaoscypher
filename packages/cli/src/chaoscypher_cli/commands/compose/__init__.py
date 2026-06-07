# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Compose Commands - Chaos Cypher CLI.

Commands for multi-package orchestration using axiomatize.yaml:
- build: Compile axiomatize.yaml into a single .ccx package
- up: Start the composition defined in axiomatize.yaml
- down: Stop and remove composition services
- run: Execute a one-off command in the composition

Compose allows you to combine multiple knowledge packages into
a unified knowledge system with merged graphs and shared contexts.

Example:
    chaoscypher compose build
    chaoscypher compose up
    chaoscypher compose up --detach
    chaoscypher compose down
    chaoscypher compose run cortex pytest
"""

import click

from chaoscypher_cli.lazy import LazyGroup


LAZY_SUBCOMMANDS = {
    "build": ("chaoscypher_cli.commands.compose.build:build", "Build composition package"),
    "up": ("chaoscypher_cli.commands.compose.up:up", "Start composition services"),
    "down": ("chaoscypher_cli.commands.compose.down:down", "Stop composition services"),
    "run": ("chaoscypher_cli.commands.compose.run:run", "Run a one-off command"),
}


@click.group(cls=LazyGroup, lazy_subcommands=LAZY_SUBCOMMANDS)
def compose() -> None:
    """Multi-package orchestration and composition.

    Compose enables combining multiple .ccx packages defined
    in axiomatize.yaml into a unified knowledge system.
    """


__all__ = ["compose"]
