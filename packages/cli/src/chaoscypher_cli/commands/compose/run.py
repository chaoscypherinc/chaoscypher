# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Run command - Execute a command in the composition context.

Uses the core ComposeService to run commands with the composed database.

Example:
    chaoscypher compose run python script.py
    chaoscypher compose run pytest tests/
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from chaoscypher_cli.utils.console import get_console, print_error
from chaoscypher_core.services.compose import ComposeConfig, ComposeError, ComposeService


@click.command()
@click.argument("command", nargs=-1, required=True)
@click.option(
    "--config",
    "-c",
    default="axiomatize.yaml",
    type=click.Path(exists=True),
    help="Path to composition config file",
)
def run(command: tuple[str, ...], config: str) -> None:
    """Execute a command in the composition context.

    Runs a command with environment variables set for the composed database.
    Useful for running tests, scripts, or tools against the composed data.

    Example:
        chaoscypher compose run python script.py
        chaoscypher compose run pytest tests/
        chaoscypher compose run --config my-compose.yaml python analyze.py
    """
    console = get_console()

    if not command:
        print_error("No command specified")
        sys.exit(1)

    # Load configuration
    try:
        compose_config = ComposeConfig.from_yaml(Path(config))
    except FileNotFoundError:
        print_error(f"Config file not found: {config}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Failed to load config: {e}")
        sys.exit(1)

    console.print(f"[cyan]Running in composition:[/cyan] {compose_config.name}")
    console.print(f"  [dim]Command:[/dim] {' '.join(command)}")
    console.print()

    async def do_run() -> int:
        """Run the async ComposeService.run call."""
        service = ComposeService()
        return await service.run(compose_config, list(command))

    try:
        exit_code = asyncio.run(do_run())
        sys.exit(exit_code)

    except ComposeError as e:
        print_error(f"Command failed: {e.message}")
        sys.exit(1)
