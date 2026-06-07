# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Down command - Stop composition services.

Uses the core ComposeService to stop the running server.

Example:
    chaoscypher compose down
    chaoscypher compose down --config my-compose.yaml
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from chaoscypher_cli.utils.console import get_console, print_error, print_success
from chaoscypher_core.services.compose import ComposeConfig, ComposeError, ComposeService


@click.command()
@click.option(
    "--config",
    "-c",
    default="axiomatize.yaml",
    type=click.Path(exists=True),
    help="Path to composition config file",
)
def down(config: str) -> None:
    """Stop composition services.

    Stops the server started by 'compose up --detach'.

    Example:
        chaoscypher compose down
        chaoscypher compose down --config my-compose.yaml
    """
    console = get_console()

    # Load configuration
    try:
        compose_config = ComposeConfig.from_yaml(Path(config))
    except FileNotFoundError:
        print_error(f"Config file not found: {config}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Failed to load config: {e}")
        sys.exit(1)

    console.print(f"[cyan]Stopping composition:[/cyan] {compose_config.name}")

    async def do_down() -> None:
        """Run the async ComposeService.down call."""
        service = ComposeService()
        await service.down(compose_config)

    try:
        asyncio.run(do_down())
        print_success("Composition stopped")

    except ComposeError as e:
        print_error(f"Failed to stop: {e.message}")
        sys.exit(1)
