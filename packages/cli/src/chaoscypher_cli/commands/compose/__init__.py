# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Compose Commands - Chaos Cypher CLI.

Commands for multi-package orchestration using axiomatize.yaml:
- init: Write a starter axiomatize.yaml
- build: Merge the listed packages into one database
- up: Serve the composition over HTTP (builds if needed)
- down: Stop the detached HTTP server
- mcp: Serve the composition to an MCP host over stdio (builds if needed)
- run: Execute a one-off command with the composition as current database

Compose allows you to combine multiple knowledge packages into
a unified knowledge system with merged graphs and shared contexts.

Example:
    chaoscypher compose init ./research.ccx acme/eu-ai-act
    chaoscypher compose build
    chaoscypher compose mcp
    chaoscypher compose up --detach
    chaoscypher compose down
"""

import click

from chaoscypher_cli.lazy import LazyGroup


LAZY_SUBCOMMANDS = {
    "init": ("chaoscypher_cli.commands.compose.init:init", "Write a starter axiomatize.yaml"),
    "build": (
        "chaoscypher_cli.commands.compose.build:build",
        "Merge the packages into one database",
    ),
    "up": ("chaoscypher_cli.commands.compose.up:up", "Serve the composition over HTTP"),
    "down": ("chaoscypher_cli.commands.compose.down:down", "Stop the HTTP server"),
    "mcp": ("chaoscypher_cli.commands.compose.mcp:mcp", "Serve the composition to an MCP host"),
    "run": ("chaoscypher_cli.commands.compose.run:run", "Run a command against the composition"),
}


@click.group(cls=LazyGroup, lazy_subcommands=LAZY_SUBCOMMANDS)
def compose() -> None:
    """Multi-package orchestration and composition.

    Compose enables combining multiple .ccx packages defined
    in axiomatize.yaml into a unified knowledge system.
    """


__all__ = ["compose"]
